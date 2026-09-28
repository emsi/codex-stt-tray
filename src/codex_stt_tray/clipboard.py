"""Native clipboard delivery; KDE Wayland uses Klipper without stealing focus."""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
from PySide6.QtGui import QClipboard, QGuiApplication

from .models import AppError, CopyTarget, ErrorCode


def supports_primary():
    # Background PRIMARY publication is supported and tested only on X11.
    return (
        QGuiApplication.platformName() == "xcb" and QGuiApplication.clipboard().supportsSelection()
    )


def target_modes(target):
    modes = []
    if target.includes_clipboard:
        modes.append(QClipboard.Mode.Clipboard)
    if target.includes_primary:
        modes.append(QClipboard.Mode.Selection)
    return modes


class ClipboardJob(QObject):
    succeeded = Signal()
    failed = Signal(object)
    finished = Signal()

    def __init__(self, text: str, parent=None, *, target=CopyTarget.CLIPBOARD):
        super().__init__(parent)
        self._text = text
        self.done = False
        self.watcher = None
        self.target = CopyTarget(target)

    def start(self):
        if self.done:
            return
        if self.target.includes_primary and not supports_primary():
            self._finish(False, "PRIMARY copying requires an X11 session with selection support.")
            return
        if QGuiApplication.platformName().startswith("wayland"):
            message = QDBusMessage.createMethodCall(
                "org.kde.klipper",
                "/klipper",
                "org.kde.klipper.klipper",
                "setClipboardContents",
            )
            message.setArguments([self._text])
            call = QDBusConnection.sessionBus().asyncCall(message, 3000)
            self.watcher = QDBusPendingCallWatcher(call, self)
            self.watcher.finished.connect(self._dbus_finished)
            if self.watcher.isFinished():
                QTimer.singleShot(0, lambda: self._dbus_finished(self.watcher))
        else:
            clipboard = QGuiApplication.clipboard()
            try:
                for mode in target_modes(self.target):
                    clipboard.setText(self._text, mode)
                ok = all(clipboard.text(mode) == self._text for mode in target_modes(self.target))
            except Exception:
                ok = False
            self._finish(ok)

    def _dbus_finished(self, watcher):
        self._finish(watcher.reply().type() != QDBusMessage.MessageType.ErrorMessage)

    def _finish(self, success: bool, message=None):
        if self.done:
            return
        self.done = True
        self._text = ""
        if success:
            self.succeeded.emit()
        else:
            self.failed.emit(
                AppError(
                    ErrorCode.CLIPBOARD,
                    message
                    or "Cannot publish all selected copy destinations. Retry copying. "
                    "On KDE Wayland, enable the Clipboard applet (Klipper).",
                    "clipboard",
                    True,
                )
            )
        self.finished.emit()

    def cancel(self):
        # A D-Bus call already delivered to Klipper cannot be recalled.
        if not self.done:
            self.done = True
            self._text = ""
            self.finished.emit()


class Clipboard(QObject):
    def __init__(self, parent=None, *, target_provider=lambda: CopyTarget.CLIPBOARD):
        super().__init__(parent)
        self.target_provider = target_provider

    def create_job(self, text: str) -> ClipboardJob:
        job = ClipboardJob(text, self, target=self.target_provider())
        job.finished.connect(job.deleteLater)
        return job
