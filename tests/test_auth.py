import json
import os
import sys

import pytest
from PySide6.QtCore import QProcess

from codex_stt_tray.auth import AuthRefresh, read_credentials
from codex_stt_tray.models import AppError, ErrorCode


def dummy_credentials(home, mode=0o600):
    path = home / "auth.json"
    path.write_text(
        json.dumps({"tokens": {"access_token": "dummy-token", "account_id": "dummy-id"}})
    )
    path.chmod(mode)
    return path


def test_credentials_are_read_only_and_hidden(isolated_home):
    path = dummy_credentials(isolated_home)
    before = path.read_bytes()
    credentials = read_credentials(isolated_home)
    assert credentials.access_token == "dummy-token"
    assert "dummy" not in repr(credentials)
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400, 0o700])
def test_require_exact_permissions(isolated_home, mode):
    dummy_credentials(isolated_home, mode)
    with pytest.raises(AppError) as error:
        read_credentials(isolated_home)
    assert error.value.code == ErrorCode.AUTH_PERMISSIONS


def test_symlink_rejected(isolated_home, tmp_path):
    target = tmp_path / "dummy"
    target.write_text("{}")
    target.chmod(0o600)
    (isolated_home / "auth.json").symlink_to(target)
    with pytest.raises(AppError):
        read_credentials(isolated_home)


def test_missing_and_invalid_credentials(isolated_home):
    with pytest.raises(AppError) as error:
        read_credentials(isolated_home)
    assert error.value.code == ErrorCode.AUTH_MISSING
    path = dummy_credentials(isolated_home)
    path.write_bytes(b"\xff")
    with pytest.raises(AppError) as error:
        read_credentials(isolated_home)
    assert error.value.code == ErrorCode.AUTH_INVALID


def test_header_injection_rejected(isolated_home):
    path = dummy_credentials(isolated_home)
    path.write_text(
        json.dumps({"tokens": {"access_token": "dummy\r\nheader", "account_id": "dummy"}})
    )
    with pytest.raises(AppError):
        read_credentials(isolated_home)


def child_script(tmp_path, monkeypatch, source):
    path = tmp_path / "fake-codex"
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o700)
    monkeypatch.setenv("CODEX_CLI_PATH", str(path))


def test_refresh_handshake_incremental(qtbot, tmp_path, isolated_home, monkeypatch):
    child_script(
        tmp_path,
        monkeypatch,
        """
import json, sys
first = json.loads(sys.stdin.readline())
assert first['method'] == 'initialize'
sys.stdout.write('{"id":1,'); sys.stdout.flush()
sys.stdout.write('"result":{}}\\n'); sys.stdout.flush()
assert json.loads(sys.stdin.readline())['method'] == 'initialized'
request = json.loads(sys.stdin.readline())
assert request['method'] == 'account/read' and request['params']['refreshToken']
print('{"method":"account/updated","params":{}}', flush=True)
print('{"id":2,"result":{"account":{"type":"chatgpt"}}}', flush=True)
sys.stdin.read()
""",
    )
    refresh = AuthRefresh(isolated_home)
    outcomes = []
    refresh.succeeded.connect(lambda: outcomes.append("success"))
    refresh.failed.connect(lambda _: outcomes.append("failure"))
    refresh.start()
    qtbot.waitUntil(lambda: refresh._settled, timeout=4000)
    assert outcomes == ["success"]
    assert refresh.process.state() == QProcess.ProcessState.NotRunning


def test_partial_line_timeout_kills_stubborn_child(qtbot, tmp_path, isolated_home, monkeypatch):
    child_script(
        tmp_path,
        monkeypatch,
        """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdin.readline()
sys.stdout.write('{"id":'); sys.stdout.flush()
while True: time.sleep(.01)
""",
    )
    refresh = AuthRefresh(isolated_home)
    errors = []
    refresh.failed.connect(errors.append)
    refresh.start()
    qtbot.waitUntil(lambda: len(refresh.buffer) > 0)
    refresh.timer.start(20)
    qtbot.waitUntil(lambda: refresh._settled, timeout=4000)
    assert len(errors) == 1
    assert errors[0].code == ErrorCode.AUTH_REFRESH
    assert refresh.process.state() == QProcess.ProcessState.NotRunning


def test_refresh_failed_start_settles(qtbot, isolated_home):
    refresh = AuthRefresh(isolated_home)
    errors = []
    refresh.failed.connect(errors.append)
    refresh.start()
    qtbot.waitUntil(lambda: refresh._settled)
    assert len(errors) == 1


def test_cancel_refresh_has_no_success(qtbot, isolated_home, tmp_path, monkeypatch):
    child_script(tmp_path, monkeypatch, "import time\ntime.sleep(10)\n")
    refresh = AuthRefresh(isolated_home)
    outcomes = []
    refresh.succeeded.connect(lambda: outcomes.append("success"))
    refresh.failed.connect(outcomes.append)
    refresh.start()
    refresh.cancel()
    qtbot.waitUntil(lambda: refresh._settled, timeout=4000)
    assert not outcomes


def test_fifo_rejected_without_blocking(isolated_home):
    os.mkfifo(isolated_home / "auth.json", 0o600)
    with pytest.raises(AppError):
        read_credentials(isolated_home)


def test_refresh_output_is_bounded(qtbot, tmp_path, isolated_home, monkeypatch):
    child_script(
        tmp_path,
        monkeypatch,
        """
import sys, time
sys.stdin.readline()
sys.stdout.write('x' * (1024 * 1024 + 1)); sys.stdout.flush()
time.sleep(10)
""",
    )
    refresh = AuthRefresh(isolated_home)
    errors = []
    refresh.failed.connect(errors.append)
    refresh.start()
    qtbot.waitUntil(lambda: refresh._settled, timeout=4000)
    assert len(errors) == 1 and not refresh.buffer
    assert refresh.received <= 1024 * 1024 + 1


def test_doctor_session_check_does_not_force_refresh(qtbot, tmp_path, isolated_home, monkeypatch):
    child_script(
        tmp_path,
        monkeypatch,
        """
import json, sys
sys.stdin.readline()
print('{"id":1,"result":{}}', flush=True)
sys.stdin.readline()
assert json.loads(sys.stdin.readline())['params']['refreshToken'] is False
print('{"id":2,"result":{"account":{"type":"chatgpt"}}}', flush=True)
sys.stdin.read()
""",
    )
    check = AuthRefresh(isolated_home, check_only=True)
    results = []
    check.succeeded.connect(lambda: results.append("success"))
    check.start()
    qtbot.waitUntil(lambda: check._settled, timeout=4000)
    assert results == ["success"]
