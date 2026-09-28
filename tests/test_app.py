import os
import subprocess
import sys


def test_headless_application_lifecycle(tmp_path):
    """Compose the real services and resources, then quit without recording or networking."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = os.environ | {
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = """
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from codex_stt_tray.app import main
from codex_stt_tray.doctor import Doctor
from codex_stt_tray.doctor import Check
from codex_stt_tray.doctor_dialog import DoctorDialog
from codex_stt_tray.settings_dialog import SettingsDialog
from codex_stt_tray.tray import Tray
Doctor.run = lambda self: None  # No desktop/network readiness probes in this test.
original_exec = QApplication.exec
def run():
    app = QApplication.instance()
    tray = app.findChild(Tray)
    assert tray is not None and not tray.icon().isNull()
    settings = next(w for w in app.topLevelWidgets() if isinstance(w, SettingsDialog))
    report = next(w for w in app.topLevelWidgets() if isinstance(w, DoctorDialog))
    tray.settings_requested.emit()
    assert settings.isVisible() and not report.isVisible()
    settings.home.setText("unsaved choice")
    tray.doctor_requested.emit()
    assert report.isVisible() and settings.home.text() == "unsaved choice"
    settings.close()
    report.close()
    app.findChild(Doctor).finished.emit([Check("Synthetic", "error", "Test error")])
    assert report.isVisible() and not settings.isVisible()
    report.settings_requested.emit()
    assert settings.isVisible()
    settings.close()
    report.close()
    QTimer.singleShot(20, tray.quit_requested.emit)
    return original_exec()
QApplication.exec = staticmethod(run)
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
raise SystemExit(main([]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
    assert not (runtime / "codex-stt-tray.lock").exists()


def test_version_without_display():
    env = os.environ.copy()
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    result = subprocess.run(
        [sys.executable, "-m", "codex_stt_tray", "--version"],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout.decode()


def test_application_survives_errors_and_repeated_recordings(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = os.environ | {
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = """
import sys
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from codex_stt_tray.app import main
from codex_stt_tray.controller import Controller
from codex_stt_tray.doctor import Doctor
from codex_stt_tray.models import AppError, AudioClip, ErrorCode, State, Transcript
from codex_stt_tray.tray import Tray
Doctor.run = lambda self: setattr(self, "startup_checked", True)
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
original_exec = QApplication.exec
class Job(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(int, int)
    def start(self):
        QTimer.singleShot(0, lambda: self.succeeded.emit(Transcript("synthetic result")))
    def cancel(self): pass
def run():
    app = QApplication.instance()
    c = app.findChild(Controller)
    d = app.findChild(Doctor)
    tray = app.findChild(Tray)
    completed = []
    def begin():
        assert d.startup_checked
        def fail(*args):
            raise AppError(ErrorCode.AUDIO_DEVICE, "Cannot start microphone capture.", "audio")
        c.recorder.start = fail
        c.start_recording()
        assert c.state == State.ERROR and not c.closed
        c.start_recording()
        assert c.state == State.ERROR and not c.closed
        sys.excepthook(RuntimeError, RuntimeError("ignored"), None)
        assert c.state == State.ERROR and not c.closed
        c.recorder.start = lambda *args: None
        c.recorder.stop = lambda: c.recorder.clip_ready.emit(AudioClip(b"synthetic", 1))
        c.transcriber.create_job = lambda clip: Job(c.transcriber)
        c.start_recording()
        c.toggle()
    def copied():
        completed.append(True)
        if len(completed) == 1:
            QTimer.singleShot(0, lambda: (c.toggle(), c.toggle()))
        else:
            QTimer.singleShot(30, finish)
    def finish():
        assert not c.closed and len(completed) == 2
        print("SURVIVED")
        tray.quit_requested.emit()
    c.copied.connect(copied)
    QTimer.singleShot(10, begin)
    return original_exec()
QApplication.exec = staticmethod(run)
raise SystemExit(main([]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
    assert b"SURVIVED" in result.stdout
