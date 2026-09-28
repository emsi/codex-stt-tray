"""Application composition and orderly shutdown."""

import argparse
import signal
import sys

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication

from . import __version__
from .clipboard import Clipboard
from .controller import Controller
from .desktop_input import AutoPaste
from .doctor import Doctor
from .doctor_dialog import DoctorDialog
from .feedback import Feedback
from .models import AppError, State
from .recorder import Recorder
from .settings import Settings
from .settings_dialog import SettingsDialog
from .shortcuts import GlobalShortcut
from .transcription import Transcriber
from .tray import Tray


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Record speech into the clipboard using Codex.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("codex-stt-tray")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("codex-stt-tray")
    app.setDesktopFileName("codex-stt-tray")
    app.setQuitOnLastWindowClosed(False)
    runtime = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    if not runtime:
        print("codex-stt-tray: no user runtime directory is available", file=sys.stderr)
        return 1
    lock = QLockFile(f"{runtime}/codex-stt-tray.lock")
    if not lock.tryLock(0):
        print(
            "codex-stt-tray: another instance is running or the lock is unavailable",
            file=sys.stderr,
        )
        return 1
    settings = Settings()
    recorder = Recorder(app)
    transcriber = Transcriber(app, home_provider=lambda: settings.codex_home)
    clipboard = Clipboard(app, target_provider=lambda: settings.copy_target)
    controller = Controller(recorder, transcriber, clipboard, settings, app)
    feedback = Feedback(settings, app)
    controller.copied.connect(feedback.play)
    tray = Tray(controller, settings, app)
    auto_paste = AutoPaste(settings, app)
    controller.paste_requested.connect(auto_paste.request)
    controller.changed.connect(lambda state: auto_paste.cancel() if state != State.COPIED else None)
    auto_paste.failed.connect(tray._error)
    doctor = Doctor(settings, app)
    controller.copied.connect(doctor.record_transcription_success)
    shortcuts = GlobalShortcut(app)
    shortcuts.activated.connect(controller.toggle)
    shortcuts.failed.connect(tray._error)
    dialog = SettingsDialog(settings, doctor, controller, shortcuts=shortcuts)
    doctor_dialog = DoctorDialog(doctor, controller)
    dialog.doctor_requested.connect(doctor_dialog.open_doctor)
    doctor_dialog.settings_requested.connect(dialog.open_settings)

    def configure_shortcut():
        try:
            shortcuts.configure(settings.recording_shortcut)
        except AppError as error:
            dialog.hotkey_status.setText(error.message)
            dialog.open_settings()
            tray._error(error)

    tray.settings_requested.connect(dialog.open_settings)
    tray.preference_changed.connect(dialog.refresh_preference)

    def preference_changed(name):
        if name == "device_id":
            doctor.run()

    tray.preference_changed.connect(preference_changed)
    doctor.running_changed.connect(tray.set_checking)
    doctor.finished.connect(tray.doctor_completed)

    def doctor_finished(rows):
        if any(row.status == "error" for row in rows):
            doctor_dialog.open_doctor()

    doctor.finished.connect(doctor_finished)

    def check_setup():
        if not controller.busy:
            doctor_dialog.open_doctor()
            doctor_dialog.check()

    tray.doctor_requested.connect(check_setup)
    app.setWindowIcon(tray.icon())
    closing = False

    def request_quit():
        nonlocal closing
        if closing:
            return
        closing = True
        tray.hide()
        dialog.hide()
        doctor_dialog.hide()
        auto_paste.shutdown()
        shortcuts.shutdown()
        controller.shutdown()
        doctor.shutdown()
        quit_when_idle()

    def quit_when_idle():
        if closing and not transcriber.jobs and not doctor.jobs:
            app.quit()

    transcriber.idle.connect(quit_when_idle)
    doctor.idle.connect(quit_when_idle)
    tray.quit_requested.connect(request_quit)
    app.aboutToQuit.connect(recorder.cancel)
    app.aboutToQuit.connect(settings.sync)
    signal.signal(signal.SIGINT, lambda *_: request_quit())
    signal.signal(signal.SIGTERM, lambda *_: request_quit())
    # Periodically return to Python so Unix signals are handled while Qt is idle.
    signal_timer = QTimer(app, interval=250)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start()

    recovering = False

    def unhandled(_type, _value, _traceback):
        nonlocal recovering
        # Never format arbitrary exceptions: their data may contain credentials or text.
        print("codex-stt-tray: operation failed; the tray remains available", file=sys.stderr)
        if recovering or closing:
            return
        recovering = True
        try:
            doctor.shutdown()
            controller.recover()
        finally:
            recovering = False

    sys.excepthook = unhandled
    tray.show()
    QTimer.singleShot(0, lambda: doctor.run() if not closing else None)
    QTimer.singleShot(0, lambda: configure_shortcut() if not closing else None)
    result = app.exec()
    tray.hide()
    lock.unlock()
    return result
