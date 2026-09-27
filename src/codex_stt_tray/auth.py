"""Read credentials without modifying them; let Codex own token refresh."""

import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .constants import MAX_AUTH_BYTES, MAX_REFRESH_BYTES, REFRESH_DEADLINE_MS
from .models import AppError, ErrorCode


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().absolute()


def read_credentials(home: Path) -> Credentials:
    """Bounded read of an owned regular file; never follow a credential symlink."""
    try:
        fd = os.open(home / "auth.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
            ):
                raise AppError(
                    ErrorCode.AUTH_PERMISSIONS,
                    "Codex credentials must be an owned regular file with mode 0600.",
                    "authentication",
                )
            if info.st_size > MAX_AUTH_BYTES:
                raise ValueError
            raw = stream.read(MAX_AUTH_BYTES + 1)
        if len(raw) > MAX_AUTH_BYTES:
            raise ValueError
        tokens = json.loads(raw)["tokens"]
        token, account = tokens["access_token"], tokens["account_id"]
        if not all(isinstance(item, str) and item for item in (token, account)):
            raise ValueError
        # Header values must not allow injection or ambiguous encoding.
        if any(any(ord(c) < 33 or ord(c) > 126 for c in item) for item in (token, account)):
            raise ValueError
        return Credentials(token, account)
    except FileNotFoundError:
        raise AppError(
            ErrorCode.AUTH_MISSING,
            "Sign in with Codex using file credential storage first.",
            "authentication",
        ) from None
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise AppError(
            ErrorCode.AUTH_INVALID,
            "Cannot read valid Codex file credentials. Sign in again.",
            "authentication",
        ) from None


class AuthRefresh(QObject):
    """A single nonblocking app-server handshake, including bounded child cleanup."""

    succeeded = Signal()
    failed = Signal(object)
    settled = Signal()  # Child is gone; safe to release this object.

    def __init__(self, home: Path, parent: QObject | None = None, *, check_only=False):
        super().__init__(parent)
        self.home = home
        self.check_only = check_only
        self.process = QProcess(self)
        self.process.setStandardErrorFile(QProcess.nullDevice())
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._exited)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._timed_out)
        self.kill_timer = QTimer(self, singleShot=True)
        self.kill_timer.timeout.connect(self.process.kill)
        self.buffer = bytearray()
        self.received = 0
        self.expected_id = 1
        self.done = False
        self._settled = False

    def start(self):
        binary = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
        if not binary:
            self._fail("Codex CLI is unavailable. Set CODEX_CLI_PATH or install Codex.")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CODEX_HOME", str(self.home))
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.home))
        self.timer.start(REFRESH_DEADLINE_MS)
        self.process.start(binary, ["app-server", "--listen", "stdio://"])

    def _send(self, message: dict):
        self.process.write(json.dumps(message).encode() + b"\n")

    def _started(self):
        if self.done:
            self._stop_child()
            return
        self._send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codex-stt-tray", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )

    def _read(self):
        if self.done:
            # Drain without allocating an arbitrarily large discarded response.
            while self.process.bytesAvailable():
                self.process.read(65536)
            return
        while self.process.bytesAvailable() and not self.done:
            chunk = bytes(self.process.read(min(65536, MAX_REFRESH_BYTES + 1 - self.received)))
            self.received += len(chunk)
            if self.received > MAX_REFRESH_BYTES:
                self._fail("Codex refresh response exceeded its limit.")
                return
            self.buffer.extend(chunk)
            while b"\n" in self.buffer and not self.done:
                line, _, remainder = self.buffer.partition(b"\n")
                self.buffer = bytearray(remainder)
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    self._fail("Codex refresh returned an invalid response.")
                    return
                if not isinstance(message, dict) or message.get("id") != self.expected_id:
                    continue
                if "error" in message or not isinstance(message.get("result"), dict):
                    self._fail("Codex could not refresh the session. Sign in again.")
                    return
                if self.expected_id == 1:
                    self.expected_id = 2
                    self._send({"method": "initialized", "params": {}})
                    self._send(
                        {
                            "id": 2,
                            "method": "account/read",
                            "params": {"refreshToken": not self.check_only},
                        }
                    )
                else:
                    if self.check_only:
                        account = message["result"].get("account")
                        if not isinstance(account, dict) or account.get("type") != "chatgpt":
                            self._fail(
                                "This Codex home has no active ChatGPT login. Sign in again."
                            )
                            return
                    self.done = True
                    self._stop_child()
                    self.succeeded.emit()

    def _timed_out(self):
        self._fail("Codex session refresh timed out.")

    def _process_error(self, _error):
        if not self.done:
            self._fail("Codex session refresh could not run.")
        elif self.process.state() == QProcess.ProcessState.NotRunning:
            self._mark_settled()

    def _exited(self, *_args):
        if not self.done:
            self._read()
        if not self.done:
            self._fail("Codex exited before refreshing the session.")
        self.kill_timer.stop()
        self._mark_settled()

    def _mark_settled(self):
        if not self._settled:
            self._settled = True
            self.settled.emit()

    def _stop_child(self):
        self.timer.stop()
        self.buffer.clear()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._mark_settled()
        else:
            self.process.closeWriteChannel()
            self.process.terminate()
            self.kill_timer.start(1000)

    def _fail(self, message: str):
        if self.done:
            return
        self.done = True
        self._stop_child()
        self.failed.emit(AppError(ErrorCode.AUTH_REFRESH, message, "authentication", True))

    def cancel(self):
        if not self.done:
            self.done = True
            self._stop_child()
