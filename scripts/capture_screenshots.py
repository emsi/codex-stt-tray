"""Capture actual Qt widgets with example data, without desktop or network access.

Run from the checkout: uv run --frozen python scripts/capture_screenshots.py
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QFont, QKeySequence  # noqa: E402
from PySide6.QtMultimedia import QMediaDevices  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from codex_stt_tray.doctor import Check  # noqa: E402
from codex_stt_tray.doctor_dialog import DoctorDialog  # noqa: E402
from codex_stt_tray.models import CopyTarget, PasteMethod, State  # noqa: E402
from codex_stt_tray.settings_dialog import SettingsDialog  # noqa: E402
from codex_stt_tray.silence import TrimOptions  # noqa: E402
from codex_stt_tray.tray import Tray  # noqa: E402


class PreviewController(QObject):
    changed = Signal(object)
    status = Signal(str)
    error_raised = Signal(object)
    state = State.IDLE
    busy = checking = can_retry = False
    transcript = None

    def toggle(self):
        raise RuntimeError("Screenshot previews cannot record or change preferences")

    retry = cancel = start_recording = toggle


class PreviewDoctor(QObject):
    updated = Signal(object)
    finished = Signal(object)
    running_changed = Signal(bool)
    running = False
    # Illustrative readiness results; no credentials are read and no checks run.
    results = [
        Check("System tray", "ok", "System tray available."),
        Check("Codex configuration", "ok", "File credential storage configured."),
        Check("Credentials", "ok", "Owned 0600 credential file is valid locally."),
        Check("Codex CLI", "ok", "Executable found."),
        Check("Microphone", "ok", "Available at 48000 Hz; capture is checked when recording."),
        Check("Codex session", "ok", "Codex reports a ChatGPT login for this home."),
        Check("Clipboard", "ok", "CLIPBOARD and PRIMARY available; contents unchanged."),
        Check("HTTPS", "ok", "Server reachable."),
    ]

    def run(self):
        raise RuntimeError("Screenshot previews cannot run diagnostics")


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("DejaVu Sans", 10))
    preferences = SimpleNamespace(
        path=Path("/home/demo/.config/codex-stt-tray/settings.ini"),
        codex_home=Path("/home/demo/.codex"),
        device_id=b"",
        volume=0.1,
        duration_limit=300,
        copy_target=CopyTarget.BOTH,
        auto_paste=True,
        paste_method=PasteMethod.KEYBOARD,
        paste_keys="Ctrl+Shift+V",
        recording_shortcut="Ctrl+Alt+R",
        trim_options=TrimOptions(),
    )

    def capture(widget, name):
        widget.show()
        app.processEvents()
        if not widget.grab().save(str(output / name)):
            raise RuntimeError(f"Cannot save screenshot: {name}")

    # Render X11 controls without connecting to a real display, audio device,
    # clipboard, shortcut service, or user configuration directory.
    with (
        patch.object(QMediaDevices, "audioInputs", return_value=[]),
        patch("codex_stt_tray.settings_dialog.supports_x11_input", return_value=True),
        patch("codex_stt_tray.settings_dialog.supports_primary", return_value=True),
    ):
        controller, doctor = PreviewController(), PreviewDoctor()
        settings = SettingsDialog(preferences, doctor, controller)
        settings.hotkey.setKeySequence(QKeySequence("Ctrl+Alt+R"))
        tabs = settings.findChild(QTabWidget)
        for index, name in enumerate(("recording", "delivery", "codex")):
            tabs.setCurrentIndex(index)
            capture(settings, f"settings-{name}.png")
        settings.hide()

        report = DoctorDialog(doctor, controller)
        doctor.finished.emit(doctor.results)
        capture(report, "doctor.png")
        report.hide()

        tray = Tray(controller, preferences)
        tray._populate_preferences()
        capture(tray.menu, "tray-menu.png")
        tray.menu.hide()
        tray.timer.stop()
    print(f"Saved five screenshots to {output}")


if __name__ == "__main__":
    main()
