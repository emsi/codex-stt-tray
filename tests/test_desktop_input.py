from types import SimpleNamespace

import pytest
from PySide6.QtGui import QGuiApplication

from codex_stt_tray import desktop_input
from codex_stt_tray.desktop_input import AutoPaste


class Backend:
    target = 123
    held = False

    def __init__(self):
        self.pastes = []

    def focus(self):
        return self.target

    def keys_held(self):
        return self.held

    def paste(self, sequence):
        self.pastes.append(sequence)

    def close(self):
        pass


@pytest.fixture
def paste(qtbot, monkeypatch):
    monkeypatch.setattr(desktop_input, "supports_x11_input", lambda: True)
    monkeypatch.setattr(desktop_input.QApplication, "activeWindow", lambda: None)
    backend = Backend()
    service = AutoPaste(
        SimpleNamespace(auto_paste=True, paste_keys="Ctrl+V"), backend_factory=lambda: backend
    )
    QGuiApplication.clipboard().setText("synthetic text")
    yield service, backend
    service.shutdown()


def test_paste_waits_for_key_release_and_sends_once(qtbot, paste):
    service, backend = paste
    backend.held = True
    service.request("synthetic text")
    service._attempt()
    assert not backend.pastes
    backend.held = False
    service._attempt()
    service._attempt()
    assert backend.pastes == ["Ctrl+V"]
    assert service.expected is None and not service.timer.isActive()


@pytest.mark.parametrize("changed", ["focus", "clipboard", "held", "cancel"])
def test_paste_skips_changed_target_or_content_and_timeout(paste, changed):
    service, backend = paste
    errors = []
    service.failed.connect(errors.append)
    service.request("synthetic text")
    if changed == "focus":
        backend.target = 456
    elif changed == "clipboard":
        QGuiApplication.clipboard().setText("replacement")
    elif changed == "held":
        backend.held = True
        service.attempts = 39
    else:
        service.cancel()
    service._attempt()
    assert not backend.pastes
    assert bool(errors) == (changed != "cancel")
    assert service.expected is None


def test_disabled_auto_paste_does_not_connect(paste):
    service, backend = paste
    service.settings.auto_paste = False
    service.request("synthetic text")
    assert service.backend is None and not service.timer.isActive()
