"""Readiness checks without recording audio, writing the clipboard, or sending tokens to probes."""

import os
import shutil
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QSystemTrayIcon

from .auth import AuthRefresh, read_credentials
from .clipboard import supports_primary
from .constants import ENDPOINT
from .models import AppError, CopyTarget
from .recorder import recording_format, selected_device


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str


def configuration_check(home: Path) -> Check:
    if not home.is_dir():
        return Check(
            "Codex configuration", "error", "Directory does not exist. Choose your Codex home."
        )
    path = home / "config.toml"
    try:
        if not path.exists():
            return Check(
                "Codex configuration", "warning", "No config.toml; Codex defaults will apply."
            )
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
            raise ValueError
        with path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError
        config = tomllib.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return Check(
            "Codex configuration", "error", "config.toml is unreadable, invalid, or too large."
        )
    mode = config.get("cli_auth_credentials_store", "file")
    if mode != "file":
        return Check(
            "Codex configuration",
            "error",
            'Set cli_auth_credentials_store = "file"; keyring/auto storage is unsupported.',
        )
    return Check("Codex configuration", "ok", "File credential storage configured.")


class DoctorJob(QObject):
    updated = Signal(object)
    finished = Signal(object)
    settled = Signal()

    def __init__(
        self,
        manager,
        home: Path,
        device_id: bytes,
        parent=None,
        *,
        copy_target=CopyTarget.CLIPBOARD,
    ):
        super().__init__(parent)
        self.manager, self.home, self.device_id = manager, home, device_id
        self.copy_target = copy_target
        self.checks = {}
        self.pending = {"session", "network", "clipboard"}
        self.done = False
        self._settled = False
        self.session = None
        self.reply = None
        self.timer = QTimer(self, singleShot=True, interval=35_000)
        self.timer.timeout.connect(self._timeout)

    def _report(self, check: Check, completed=None):
        if self.done:
            return
        self.checks[check.name] = check
        self.updated.emit(list(self.checks.values()))
        if completed:
            self.pending.discard(completed)
        if not self.pending:
            self._finish()

    def start(self):
        self.timer.start()
        tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._report(
            Check(
                "System tray",
                "ok" if tray_available else "error",
                "System tray available."
                if tray_available
                else "No system tray yet. Open Settings and retry after the desktop starts.",
            )
        )
        configuration = configuration_check(self.home)
        self._report(configuration)
        credentials_ok = True
        try:
            read_credentials(self.home)
        except AppError as error:
            credentials_ok = False
            self._report(Check("Credentials", "error", error.message))
        else:
            self._report(Check("Credentials", "ok", "Owned 0600 credential file is valid locally."))
        binary = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
        binary = shutil.which(binary) if binary else None
        self._report(
            Check(
                "Codex CLI",
                "ok" if binary else "error",
                "Executable found." if binary else "Install Codex or set CODEX_CLI_PATH.",
            )
        )
        try:
            fmt = recording_format(selected_device(self.device_id))
        except AppError as error:
            self._report(Check("Microphone", "error", error.message))
        else:
            self._report(
                Check(
                    "Microphone",
                    "ok",
                    f"Available at {fmt.sampleRate()} Hz; capture is checked when recording.",
                )
            )
        if binary and credentials_ok and configuration.status != "error":
            self._report(
                Check("Codex session", "checking", "Checking ChatGPT login with app-server…")
            )
            self.session = AuthRefresh(self.home, self, check_only=True)
            self.session.succeeded.connect(
                lambda: self._report(
                    Check("Codex session", "ok", "Codex reports a ChatGPT login for this home."),
                    "session",
                )
            )
            self.session.failed.connect(
                lambda error: self._report(
                    Check("Codex session", "error", error.message), "session"
                )
            )
            self.session.settled.connect(self._maybe_settled)
            self.session.start()
        else:
            self._report(
                Check(
                    "Codex session",
                    "warning",
                    "Fix configuration, credentials, and CLI issues before checking login.",
                ),
                "session",
            )
        self._clipboard()
        self._network()

    def _clipboard(self):
        if self.copy_target.includes_primary and not supports_primary():
            self._report(
                Check(
                    "Clipboard",
                    "error",
                    "PRIMARY copying requires X11 selection support. "
                    "Choose CLIPBOARD in Settings on other desktops.",
                ),
                "clipboard",
            )
            return
        if not QGuiApplication.platformName().startswith("wayland"):
            label = (
                "CLIPBOARD and PRIMARY"
                if self.copy_target == CopyTarget.BOTH
                else self.copy_target.value.upper()
            )
            self._report(
                Check(
                    "Clipboard",
                    "ok",
                    f"{label} available; contents unchanged.",
                ),
                "clipboard",
            )
            return
        self._report(Check("Clipboard", "checking", "Checking KDE Klipper availability…"))
        message = QDBusMessage.createMethodCall(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner"
        )
        message.setArguments(["org.kde.klipper"])
        call = QDBusConnection.sessionBus().asyncCall(message, 3000)
        watcher = QDBusPendingCallWatcher(call, self)
        watcher.finished.connect(self._clipboard_checked)
        if watcher.isFinished():
            QTimer.singleShot(0, lambda: self._clipboard_checked(watcher))

    def _clipboard_checked(self, watcher):
        if self.done or "clipboard" not in self.pending:
            return
        reply = watcher.reply()
        ok = reply.type() != QDBusMessage.MessageType.ErrorMessage and reply.arguments() == [True]
        self._report(
            Check(
                "Clipboard",
                "ok" if ok else "error",
                "KDE Klipper is available; clipboard contents unchanged."
                if ok
                else "Enable KDE's Clipboard applet (Klipper) for background copying.",
            ),
            "clipboard",
        )

    def _network(self):
        self._report(
            Check("HTTPS", "checking", "Checking server reachability without credentials…")
        )
        request = QNetworkRequest(QUrl(ENDPOINT))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieLoadControlAttribute, QNetworkRequest.LoadControl.Manual
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieSaveControlAttribute, QNetworkRequest.LoadControl.Manual
        )
        request.setTransferTimeout(10_000)
        self.reply = self.manager.head(request)
        self.reply.finished.connect(self._network_checked)

    def _network_checked(self):
        if self.done or self.reply is None:
            return
        reply, self.reply = self.reply, None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        reply.deleteLater()
        if status is None or error == QNetworkReply.NetworkError.SslHandshakeFailedError:
            check = Check(
                "HTTPS",
                "error",
                "Cannot reach the server securely. Check network and TLS settings.",
            )
        elif status >= 500:
            check = Check("HTTPS", "error", f"Server returned HTTP {status}; try again later.")
        else:
            check = Check("HTTPS", "ok", "Server reachable.")
        self._report(check, "network")

    def _timeout(self):
        for name in tuple(self.pending):
            label = {"session": "Codex session", "network": "HTTPS", "clipboard": "Clipboard"}[name]
            self.checks[label] = Check(
                label, "error", "Check timed out; retry when the service is available."
            )
        self._finish()

    def _finish(self, cancelled=False):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.abort()
            reply.deleteLater()
        if self.session:
            self.session.cancel()
        if not cancelled:
            self.finished.emit(list(self.checks.values()))
        self._maybe_settled()

    def _maybe_settled(self):
        if self.done and not self._settled and (self.session is None or self.session._settled):
            self._settled = True
            self.settled.emit()

    def cancel(self):
        self._finish(cancelled=True)


class Doctor(QObject):
    updated = Signal(object)
    finished = Signal(object)
    running_changed = Signal(bool)
    idle = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.manager = QNetworkAccessManager(self)
        self.jobs = set()
        self.current = None
        self.results = []
        self.running = False
        self.verified_home = None

    def record_transcription_success(self):
        self.verified_home = self.settings.codex_home
        self.results = self._with_verification(self.results)
        self.updated.emit(self.results)

    def _with_verification(self, rows):
        rows = [row for row in rows if row.name != "Transcription"]
        if self.settings.codex_home != self.verified_home:
            return rows
        rows.append(
            Check(
                "Transcription",
                "ok",
                "Recording transcribed and copied successfully for this Codex home "
                "during this app session.",
            )
        )
        return rows

    def run(self):
        if self.current:
            self.current.cancel()
        job = DoctorJob(
            self.manager,
            self.settings.codex_home,
            self.settings.device_id,
            self,
            copy_target=self.settings.copy_target,
        )
        self.current = job
        self.jobs.add(job)
        job.updated.connect(lambda rows: self._updated(job, rows))
        job.finished.connect(lambda rows: self._finished(job, rows))
        job.settled.connect(lambda: self._release(job))
        self.results = []
        self.running = True
        self.running_changed.emit(True)
        job.start()

    def _updated(self, job, rows):
        if job is self.current:
            self.results = self._with_verification(rows)
            self.updated.emit(self.results)

    def _finished(self, job, rows):
        if job is self.current:
            self.current = None
            self.results = self._with_verification(rows)
            self.running = False
            self.running_changed.emit(False)
            self.finished.emit(self.results)

    def _release(self, job):
        self.jobs.discard(job)
        job.deleteLater()
        if not self.jobs:
            self.idle.emit()

    def shutdown(self):
        self.current = None
        self.running = False
        self.running_changed.emit(False)
        for job in tuple(self.jobs):
            job.cancel()
        if not self.jobs:
            self.idle.emit()
