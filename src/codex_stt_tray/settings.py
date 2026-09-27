"""Persist preferences only; never audio, transcripts, or credentials."""

import os
from pathlib import Path

from PySide6.QtCore import QSettings

from .auth import codex_home
from .constants import DEFAULT_RECORDING_SECONDS, MAX_RECORDING_SECONDS


class Settings:
    def __init__(self):
        root = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
        if not root.is_absolute():
            root = Path.home() / ".config"
        self.directory = root / "codex-stt-tray"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "settings.ini"
        migrate = not self.path.exists()
        self.store = QSettings(str(self.path), QSettings.Format.IniFormat)
        self.store.setFallbacksEnabled(False)
        if migrate:
            legacy = QSettings(
                str(self.directory / "codex-stt-tray.conf"), QSettings.Format.IniFormat
            )
            for key in ("microphone", "volume", "duration_limit"):
                if legacy.contains(key):
                    self.store.setValue(key, legacy.value(key))

    @property
    def codex_home(self) -> Path:
        configured = str(self.store.value("codex_home", "")).strip()
        return Path(configured).expanduser().absolute() if configured else codex_home()

    @codex_home.setter
    def codex_home(self, value: str | Path):
        value = str(value).strip()
        self.store.setValue("codex_home", str(Path(value).expanduser().absolute()) if value else "")

    def sync(self):
        self.store.sync()
        if self.store.status() != QSettings.Status.NoError:
            raise OSError("Cannot save application settings.")
        if self.path.exists():
            self.path.chmod(0o600)

    @property
    def device_id(self) -> bytes:
        try:
            return bytes.fromhex(str(self.store.value("microphone", "")))
        except ValueError:
            return b""

    @device_id.setter
    def device_id(self, value: bytes):
        self.store.setValue("microphone", value.hex())

    @property
    def volume(self) -> float:
        try:
            return min(1.0, max(0.0, float(self.store.value("volume", 0.1))))
        except (ValueError, TypeError):
            return 0.1

    @volume.setter
    def volume(self, value: float):
        self.store.setValue("volume", min(1.0, max(0.0, value)))

    @property
    def auto_paste(self) -> bool:
        return str(self.store.value("auto_paste", "false")).lower() == "true"

    @auto_paste.setter
    def auto_paste(self, value: bool):
        self.store.setValue("auto_paste", bool(value))

    @property
    def paste_keys(self) -> str:
        value = str(self.store.value("paste_keys", "Ctrl+V"))
        return value if value in ("Ctrl+V", "Ctrl+Shift+V", "Shift+Insert") else "Ctrl+V"

    @paste_keys.setter
    def paste_keys(self, value: str):
        self.store.setValue("paste_keys", value)

    @property
    def duration_limit(self) -> int:
        try:
            return min(
                MAX_RECORDING_SECONDS,
                max(1, int(self.store.value("duration_limit", DEFAULT_RECORDING_SECONDS))),
            )
        except (ValueError, TypeError):
            return DEFAULT_RECORDING_SECONDS

    @duration_limit.setter
    def duration_limit(self, value: int):
        self.store.setValue("duration_limit", min(MAX_RECORDING_SECONDS, max(1, value)))
