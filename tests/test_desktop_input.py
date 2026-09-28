from types import SimpleNamespace

import pytest
from PySide6.QtGui import QClipboard, QGuiApplication

from codex_stt_tray import desktop_input
from codex_stt_tray.desktop_input import AutoPaste
from codex_stt_tray.models import CopyTarget, PasteMethod


class Backend:
    target = 123
    held = False
    buttons = False
    pointer_position = (123, 50, 60)
    over_target = True

    def __init__(self):
        self.pastes = []

    def focus(self):
        return self.target

    def keys_held(self):
        return self.held

    def buttons_held(self):
        return self.buttons

    def pointer(self):
        return self.pointer_position

    def pointer_over_target(self, target, pointer):
        return self.over_target

    def middle_click(self):
        self.pastes.append("middle_click")

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
        SimpleNamespace(
            auto_paste=True,
            paste_keys="Ctrl+V",
            copy_target=CopyTarget.CLIPBOARD,
            paste_method=PasteMethod.KEYBOARD,
        ),
        backend_factory=lambda: backend,
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


def prepare_middle(paste, monkeypatch, target=CopyTarget.PRIMARY):
    from test_clipboard import Selections

    service, backend = paste
    selections = Selections()
    for mode in selections.values:
        selections.values[mode] = "synthetic text"
    monkeypatch.setattr(desktop_input.QGuiApplication, "clipboard", lambda: selections)
    monkeypatch.setattr(desktop_input, "supports_primary", lambda: True)
    service.settings.copy_target = target
    service.settings.paste_method = PasteMethod.MIDDLE_CLICK
    return service, backend, selections


def test_middle_click_uses_primary_and_does_not_require_clipboard(paste, monkeypatch):
    service, backend, selections = prepare_middle(paste, monkeypatch)
    selections.values[QClipboard.Mode.Clipboard] = "unrelated"
    service.request("synthetic text")
    backend.buttons = True
    service._attempt()
    assert not backend.pastes
    backend.buttons = False
    service._attempt()
    service._attempt()
    assert backend.pastes == ["middle_click"]


@pytest.mark.parametrize("change", ["pointer", "focus", "primary", "buttons"])
def test_middle_click_skips_changed_destination(paste, monkeypatch, change):
    service, backend, selections = prepare_middle(paste, monkeypatch)
    errors = []
    service.failed.connect(errors.append)
    service.request("synthetic text")
    if change == "pointer":
        backend.pointer_position = (123, 70, 80)
    elif change == "focus":
        backend.target = 456
    elif change == "primary":
        selections.values[QClipboard.Mode.Selection] = "replacement"
    else:
        backend.buttons = True
        service.attempts = 39
    service._attempt()
    assert errors and not backend.pastes and not service.timer.isActive()


@pytest.mark.parametrize("invalid", ["outside", "no_primary", "unsupported"])
def test_middle_click_rejects_invalid_setup(paste, monkeypatch, invalid):
    service, backend, _ = prepare_middle(paste, monkeypatch)
    if invalid == "outside":
        backend.over_target = False
    elif invalid == "no_primary":
        service.settings.copy_target = CopyTarget.CLIPBOARD
    else:
        monkeypatch.setattr(desktop_input, "supports_primary", lambda: False)
    errors = []
    service.failed.connect(errors.append)
    service.request("synthetic text")
    assert errors and not backend.pastes and not service.timer.isActive()


def test_both_destinations_checked_before_paste(paste, monkeypatch):
    service, backend, selections = prepare_middle(paste, monkeypatch, CopyTarget.BOTH)
    service.request("synthetic text")
    selections.values[QClipboard.Mode.Clipboard] = "replacement"
    service._attempt()
    assert not backend.pastes and service.expected is None


def test_native_middle_click_releases_button_after_error(monkeypatch):
    from Xlib import X

    from codex_stt_tray.desktop_input import X11Input

    events = []

    def fake_input(display, event, button):
        events.append((event, button))
        if event == X.ButtonPress:
            raise RuntimeError("synthetic failure")

    monkeypatch.setattr(desktop_input.xtest, "fake_input", fake_input)
    native = object.__new__(X11Input)
    native.display = SimpleNamespace(has_extension=lambda _: True, sync=lambda: None)
    with pytest.raises(RuntimeError):
        native.middle_click()
    assert events == [(X.ButtonPress, 2), (X.ButtonRelease, 2)]
