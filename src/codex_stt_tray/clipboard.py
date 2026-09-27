"""Native clipboard delivery; KDE Wayland uses Klipper without stealing focus."""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
from PySide6.QtGui import QClipboard, QGuiApplication

from .models import AppError, ErrorCode


class ClipboardJob(QObject):
    succeeded = Signal()
    failed = Signal(object)
    finished = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.done = False
        self.watcher = None

    def start(self):
        if self.done:
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
            clipboard.setText(self._text, QClipboard.Mode.Clipboard)
            self._finish(clipboard.text(QClipboard.Mode.Clipboard) == self._text)

    def _dbus_finished(self, watcher):
        self._finish(watcher.reply().type() != QDBusMessage.MessageType.ErrorMessage)

    def _finish(self, success: bool):
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
                    "Cannot publish the clipboard. On KDE Wayland, enable "
                    "the Clipboard applet (Klipper).",
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
    def create_job(self, text: str) -> ClipboardJob:
        job = ClipboardJob(text, self)
        job.finished.connect(job.deleteLater)
        return job
