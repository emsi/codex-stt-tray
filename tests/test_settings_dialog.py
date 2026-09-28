from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaDevices

from codex_stt_tray import settings_dialog
from codex_stt_tray.models import CopyTarget, PasteMethod
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
    assert not hasattr(dialog, "results")


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


def test_middle_click_requires_primary_without_changing_preferences(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "supports_x11_input", lambda: True)
    monkeypatch.setattr(settings_dialog, "supports_primary", lambda: True)
    settings = Settings()
    doctor = Doctor(settings)
    dialog = SettingsDialog(settings, doctor, Controller())
    qtbot.addWidget(dialog)
    dialog.auto_paste.setChecked(True)
    dialog.paste_method.setCurrentIndex(
        dialog.paste_method.findData(PasteMethod.MIDDLE_CLICK.value)
    )
    assert not dialog.paste_keys.isEnabled()
    dialog.save()
    assert "requires PRIMARY" in dialog.summary.text()
    assert not doctor.checked and not settings.auto_paste
    dialog.copy_target.setCurrentIndex(dialog.copy_target.findData(CopyTarget.BOTH.value))
    with qtbot.waitSignal(dialog.doctor_requested):
        dialog.save()
    assert settings.copy_target == CopyTarget.BOTH
    assert settings.paste_method == PasteMethod.MIDDLE_CLICK
    assert settings.auto_paste and doctor.checked


def test_primary_cannot_be_silently_saved_on_unsupported_desktop(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "supports_primary", lambda: False)
    settings = Settings()
    doctor = Doctor(settings)
    dialog = SettingsDialog(settings, doctor, Controller())
    qtbot.addWidget(dialog)
    dialog.copy_target.setCurrentIndex(dialog.copy_target.findData(CopyTarget.PRIMARY.value))
    dialog.save()
    assert settings.copy_target == CopyTarget.CLIPBOARD and not doctor.checked
    assert "requires X11" in dialog.summary.text()


def test_save_failure_restores_all_preferences(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "supports_primary", lambda: True)
    settings = Settings()
    settings.paste_keys = "Ctrl+Shift+V"
    settings.codex_home = ""  # Preserve environment fallback, not its resolved path.
    before = {key: settings.store.value(key) for key in settings.store.allKeys()}
    doctor = Doctor(settings)
    dialog = SettingsDialog(settings, doctor, Controller())
    qtbot.addWidget(dialog)
    dialog.copy_target.setCurrentIndex(dialog.copy_target.findData(CopyTarget.BOTH.value))
    dialog.duration.setValue(600)

    def fail():
        raise OSError("synthetic error")

    monkeypatch.setattr(settings, "sync", fail)
    dialog.save()
    assert {key: settings.store.value(key) for key in settings.store.allKeys()} == before
    assert not doctor.checked
    assert "Cannot save" in dialog.summary.text()
