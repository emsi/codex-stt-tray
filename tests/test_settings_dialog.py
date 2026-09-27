from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaDevices

from codex_stt_tray.doctor import Check
from codex_stt_tray.settings import Settings
from codex_stt_tray.settings_dialog import SettingsDialog


class Controller(QObject):
    changed = Signal(object)
    busy = False

    def cancel(self):
        pass


class Doctor(QObject):
    updated = Signal(object)
    finished = Signal(object)
    running_changed = Signal(bool)
    results = []
    running = False

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.checked = []

    def run(self):
        self.checked.append(self.settings.codex_home)


def test_save_persists_home_and_runs_doctor(qtbot, tmp_path):
    settings = Settings()
    doctor = Doctor(settings)
    controller = Controller()
    dialog = SettingsDialog(settings, doctor, controller)
    qtbot.addWidget(dialog)
    selected = tmp_path / "different-codex"
    dialog.home.setText(str(selected))
    dialog.save_button.click()
    assert Settings().codex_home == selected
    assert doctor.checked == [selected]
    doctor.finished.emit([Check("Credentials", "error", "Sign in first.")])
    assert dialog.results.topLevelItemCount() == 1
    assert "stays running" in dialog.summary.text()


def test_settings_are_not_changed_mid_recording(qtbot, tmp_path):
    settings = Settings()
    original = settings.codex_home
    doctor = Doctor(settings)
    controller = Controller()
    dialog = SettingsDialog(settings, doctor, controller)
    qtbot.addWidget(dialog)
    controller.busy = True
    controller.changed.emit(None)
    dialog.home.setText(str(tmp_path / "new"))
    dialog.save()
    assert settings.codex_home == original
    assert not doctor.checked and not dialog.save_button.isEnabled()


def test_saved_microphone_is_selected_after_reopening(qtbot, monkeypatch):
    class Device:
        def description(self):
            return "Synthetic USB microphone"

        def id(self):
            # Return a fresh bytes object on each enumeration, as Qt does.
            return bytes.fromhex("01020304")

    monkeypatch.setattr(QMediaDevices, "audioInputs", lambda: [Device()])
    settings = Settings()
    doctor = Doctor(settings)
    dialog = SettingsDialog(settings, doctor, Controller())
    qtbot.addWidget(dialog)
    dialog.microphone.setCurrentIndex(1)
    dialog.save_button.click()
    reopened = SettingsDialog(Settings(), doctor, Controller())
    qtbot.addWidget(reopened)
    assert reopened.microphone.currentText() == "Synthetic USB microphone"
    assert reopened.microphone.count() == 2
    assert reopened.microphone.currentData() == bytes.fromhex("01020304")
