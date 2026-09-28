from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QNetworkReply
from test_auth import dummy_credentials
from test_recorder import Device
from test_transcription import Manager

from codex_stt_tray import doctor
from codex_stt_tray.doctor import DoctorJob, configuration_check
from codex_stt_tray.models import AppError, CopyTarget, ErrorCode


class Session(QObject):
    succeeded = Signal()
    failed = Signal(object)
    settled = Signal()

    def __init__(self, home, parent, *, check_only):
        super().__init__(parent)
        assert check_only
        self.home = home
        self._settled = False
        self.done = False

    def start(self):
        QTimer.singleShot(0, self.complete)

    def complete(self):
        if not self.done:
            self.done = self._settled = True
            self.succeeded.emit()
            self.settled.emit()

    def cancel(self):
        self.done = self._settled = True
        self.settled.emit()


@pytest.fixture
def ready(monkeypatch, isolated_home):
    (isolated_home / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
    dummy_credentials(isolated_home)
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/dummy/codex")
    monkeypatch.setattr(doctor, "AuthRefresh", Session)
    monkeypatch.setattr(doctor, "selected_device", lambda _: Device())
    monkeypatch.setattr(doctor.QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    return isolated_home


def test_doctor_reports_ready_without_sending_credentials(qtbot, ready):
    manager = Manager([{"status": 405, "payload": b""}])
    job = DoctorJob(manager, ready, b"")
    results = []
    job.finished.connect(results.append)
    job.start()
    qtbot.waitUntil(lambda: job.done)
    rows = {row.name: row for row in results[0]}
    assert all(row.status == "ok" for row in rows.values())
    assert job.session.home == ready
    assert not manager.requests[0].hasRawHeader("Authorization")
    assert not manager.requests[0].hasRawHeader("ChatGPT-Account-Id")
    assert not manager.uploads[0]
    assert rows["HTTPS"].message == "Server reachable."


def test_missing_auth_and_microphone_do_not_stop_other_checks(qtbot, ready, monkeypatch):
    (ready / "auth.json").unlink()

    def unavailable(_):
        raise AppError(ErrorCode.AUDIO_DEVICE, "No microphone.", "audio")

    monkeypatch.setattr(doctor, "selected_device", unavailable)
    manager = Manager([{"status": None, "error": QNetworkReply.NetworkError.HostNotFoundError}])
    job = DoctorJob(manager, ready, b"")
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert job.checks["Credentials"].status == "error"
    assert job.checks["Microphone"].status == "error"
    assert job.checks["HTTPS"].status == "error"
    assert job.session is None


def test_doctor_deadline(qtbot, ready):
    manager = Manager([{"delay": None}])
    job = DoctorJob(manager, ready, b"")
    job.start()
    job.timer.start(10)
    qtbot.waitUntil(lambda: job.done)
    assert job.checks["HTTPS"].status == "error"


def test_doctor_checks_primary_support_without_copying(qtbot, ready, monkeypatch):
    monkeypatch.setattr(doctor, "supports_primary", lambda: False)
    manager = Manager([{"status": 403}])
    job = DoctorJob(manager, ready, b"", copy_target=CopyTarget.PRIMARY)
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert job.checks["Clipboard"].status == "error"
    assert "PRIMARY" in job.checks["Clipboard"].message


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_probe_does_not_claim_transcription_access(qtbot, ready, status):
    manager = Manager([{"status": status, "payload": b""}])
    job = DoctorJob(manager, ready, b"")
    job.start()
    qtbot.waitUntil(lambda: job.done)
    check = job.checks["HTTPS"]
    assert check.status == "ok"
    assert check.message == "Server reachable."


def test_real_transcription_verifies_current_home_across_doctor_runs(qtbot, qapp, ready):
    settings = SimpleNamespace(codex_home=ready, device_id=b"", copy_target=CopyTarget.CLIPBOARD)
    service = doctor.Doctor(settings, qapp)
    service.manager = Manager([{"status": 403}, {"status": 403}, {"status": 403}])
    service.run()
    qtbot.waitUntil(lambda: not service.running)
    assert next(row for row in service.results if row.name == "HTTPS").status == "ok"
    service.record_transcription_success()
    service.run()
    qtbot.waitUntil(lambda: not service.running)
    rows = {row.name: row for row in service.results}
    assert rows["HTTPS"].status == "ok"
    assert rows["HTTPS"].message == "Server reachable."
    assert rows["Transcription"].status == "ok"

    settings.codex_home = ready / "different-home"
    service.run()
    qtbot.waitUntil(lambda: not service.running)
    rows = {row.name: row for row in service.results}
    assert rows["HTTPS"].status == "ok"
    assert "Transcription" not in rows


def test_reject_keyring_and_invalid_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('cli_auth_credentials_store = "keyring"')
    assert configuration_check(tmp_path).status == "error"
    path.write_text("invalid = [")
    assert configuration_check(tmp_path).status == "error"
    assert configuration_check(tmp_path / "missing").status == "error"


def test_new_settings_cancel_old_doctor_and_ignore_its_results(qtbot, qapp, ready):
    settings = SimpleNamespace(codex_home=ready, device_id=b"", copy_target=CopyTarget.CLIPBOARD)
    service = doctor.Doctor(settings, qapp)
    service.manager = Manager([{"delay": None}, {"status": 405}])
    finished = []
    service.finished.connect(finished.append)
    service.run()
    old = service.current
    settings.codex_home = ready / "missing"
    service.run()
    assert old.done
    assert service.current.home == settings.codex_home
    old.finished.emit([doctor.Check("Stale", "ok", "Obsolete result")])
    assert not finished
    qtbot.waitUntil(lambda: not service.running)
    assert len(finished) == 1
    rows = {row.name: row for row in finished[0]}
    assert "Stale" not in rows
    assert rows["Codex configuration"].status == "error"
    qtbot.waitUntil(lambda: not service.jobs)


def test_transcriber_snapshots_selected_home(qtbot, qapp, tmp_path):
    from codex_stt_tray.models import AudioClip
    from codex_stt_tray.transcription import Transcriber

    settings = SimpleNamespace(codex_home=tmp_path / "first")
    service = Transcriber(qapp, home_provider=lambda: settings.codex_home)
    first = service.create_job(AudioClip(b"synthetic", 1))
    settings.codex_home = tmp_path / "second"
    second = service.create_job(AudioClip(b"synthetic", 1))
    assert first.home == tmp_path / "first" and second.home == tmp_path / "second"
    service.shutdown()
