from pathlib import Path

from PySide6.QtCore import QSettings

from codex_stt_tray.models import CopyTarget, PasteMethod
from codex_stt_tray.settings import Settings
from codex_stt_tray.silence import TrimOptions


def test_dedicated_directory_and_home_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "prefs"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "environment"))
    settings = Settings()
    assert settings.path == tmp_path / "prefs/codex-stt-tray/settings.ini"
    assert settings.codex_home == tmp_path / "environment"
    settings.codex_home = tmp_path / "selected"
    settings.volume = 0.25
    settings.sync()
    loaded = Settings()
    assert loaded.codex_home == tmp_path / "selected"
    assert loaded.volume == 0.25
    assert settings.path.stat().st_mode & 0o777 == 0o600
    loaded.codex_home = ""
    assert loaded.codex_home == tmp_path / "environment"


def test_legacy_preferences_migrate_without_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = QSettings(
        str(tmp_path / "codex-stt-tray/codex-stt-tray.conf"), QSettings.Format.IniFormat
    )
    legacy.setValue("volume", 0.25)
    legacy.setValue("microphone", b"synthetic device".hex())
    legacy.setValue("unrelated", "not migrated")
    legacy.sync()
    settings = Settings()
    assert settings.volume == 0.25 and settings.device_id == b"synthetic device"
    assert not settings.store.contains("unrelated")
    settings.sync()


def test_relative_xdg_path_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Settings().directory == tmp_path / ".config/codex-stt-tray"


def test_input_and_trimming_preferences_persist():
    settings = Settings()
    assert not settings.auto_paste and settings.recording_shortcut == ""
    assert settings.trim_options == TrimOptions()
    settings.auto_paste = True
    settings.paste_keys = "Ctrl+Shift+V"
    settings.recording_shortcut = "Ctrl+Alt+R"
    settings.trim_options = TrimOptions(False, -60, 500)
    settings.sync()
    loaded = Settings()
    assert loaded.auto_paste and loaded.paste_keys == "Ctrl+Shift+V"
    assert loaded.recording_shortcut == "Ctrl+Alt+R"
    assert loaded.trim_options == TrimOptions(False, -60, 500)


def test_delivery_preferences_keep_old_defaults_and_validate_values():
    settings = Settings()
    settings.paste_keys = "Ctrl+Shift+V"
    assert settings.copy_target == CopyTarget.CLIPBOARD
    assert settings.paste_method == PasteMethod.KEYBOARD
    settings.copy_target = CopyTarget.BOTH
    settings.paste_method = PasteMethod.MIDDLE_CLICK
    settings.sync()
    loaded = Settings()
    assert loaded.copy_target == CopyTarget.BOTH
    assert loaded.paste_method == PasteMethod.MIDDLE_CLICK
    assert loaded.paste_keys == "Ctrl+Shift+V"
    loaded.store.setValue("copy_target", "invalid")
    loaded.store.setValue("paste_method", "invalid")
    assert loaded.copy_target == CopyTarget.CLIPBOARD
    assert loaded.paste_method == PasteMethod.KEYBOARD
