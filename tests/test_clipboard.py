from PySide6.QtCore import QObject, QTimer, Signal

from codex_stt_tray import clipboard
from codex_stt_tray.clipboard import ClipboardJob
from codex_stt_tray.models import ErrorCode


def test_qt_clipboard_delivery(qtbot, qapp):
    assert qapp.platformName() == "offscreen", "This test must not change the desktop clipboard"
    job = ClipboardJob("synthetic text")
    outcomes = []
    job.succeeded.connect(lambda: outcomes.append("success"))
    job.start()
    assert outcomes == ["success"]
    assert job._text == "" and qapp.clipboard().text() == "synthetic text"
    qapp.clipboard().clear()


class Watcher(QObject):
    finished = Signal(object)

    def __init__(self, reply, parent):
        super().__init__(parent)
        self._reply = reply
        QTimer.singleShot(0, lambda: self.finished.emit(self))

    def reply(self):
        return self._reply

    def isFinished(self):
        return False


class Bus:
    def __init__(self, error=False):
        self.error = error
        self.message = None

    def asyncCall(self, message, timeout):
        self.message = message
        assert timeout == 3000
        if self.error:
            return message.createErrorReply("org.example.Error", "untrusted diagnostic")
        return message.createReply()


def prepare_wayland(monkeypatch, error=False):
    bus = Bus(error)
    monkeypatch.setattr(clipboard.QGuiApplication, "platformName", lambda: "wayland")
    monkeypatch.setattr(clipboard.QDBusConnection, "sessionBus", lambda: bus)
    monkeypatch.setattr(clipboard, "QDBusPendingCallWatcher", Watcher)
    return bus


def test_wayland_uses_native_async_klipper(qtbot, monkeypatch):
    bus = prepare_wayland(monkeypatch)
    job = ClipboardJob("synthetic text")
    outcomes = []
    job.succeeded.connect(lambda: outcomes.append("success"))
    job.start()
    assert not outcomes
    qtbot.waitUntil(lambda: job.done)
    assert outcomes == ["success"]
    assert bus.message.service() == "org.kde.klipper"
    assert bus.message.path() == "/klipper"
    assert bus.message.member() == "setClipboardContents"


def test_wayland_failure_preserves_retryable_error(qtbot, monkeypatch):
    prepare_wayland(monkeypatch, error=True)
    job = ClipboardJob("synthetic text")
    errors = []
    job.failed.connect(errors.append)
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert errors[0].code == ErrorCode.CLIPBOARD and errors[0].retryable
    assert "untrusted" not in str(errors[0])


def test_cancel_suppresses_late_clipboard_completion(qtbot, monkeypatch):
    prepare_wayland(monkeypatch)
    job = ClipboardJob("synthetic text")
    outcomes = []
    job.succeeded.connect(lambda: outcomes.append("success"))
    job.start()
    job.cancel()
    qtbot.wait(10)
    assert not outcomes and not job._text
