from PySide6.QtGui import QGuiApplication
from test_settings_dialog import Controller, Doctor

from codex_stt_tray.doctor import Check
from codex_stt_tray.doctor_dialog import DoctorDialog
from codex_stt_tray.settings import Settings


def test_report_remains_copyable_in_independent_window(qtbot, qapp):
    assert qapp.platformName() == "offscreen"
    doctor = Doctor(Settings())
    dialog = DoctorDialog(doctor, Controller())
    qtbot.addWidget(dialog)
    rows = [Check("Credentials", "error", "Sign in first.")]
    doctor.finished.emit(rows)
    assert dialog.results.topLevelItemCount() == 1
    assert "stays running" in dialog.summary.text()
    dialog.copy_button.click()
    assert "[ERROR] Credentials: Sign in first." in QGuiApplication.clipboard().text()
    dialog.close()
    doctor.updated.emit([Check("Transcription", "ok", "Verified in this session.")])
    dialog.open_doctor()
    assert "succeeded" in dialog.notice.text()
    QGuiApplication.clipboard().clear()


def test_doctor_checks_only_when_idle(qtbot):
    doctor = Doctor(Settings())
    controller = Controller()
    dialog = DoctorDialog(doctor, controller)
    qtbot.addWidget(dialog)
    controller.busy = True
    controller.changed.emit(None)
    dialog.check()
    assert not doctor.checked and not dialog.check_button.isEnabled()
    controller.busy = False
    doctor.running = True
    doctor.running_changed.emit(True)
    dialog.check()
    assert not doctor.checked and not dialog.check_button.isEnabled()
    doctor.running = False
    doctor.running_changed.emit(False)
    dialog.check_button.click()
    assert doctor.checked
