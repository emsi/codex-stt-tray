import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from Xlib import XK, X

from codex_stt_tray import shortcuts
from codex_stt_tray.models import AppError
from codex_stt_tray.shortcuts import GlobalShortcut, binding, parse_shortcut


class Display:
    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        self.grabs = set()
        self.conflicts = set()
        self.events = []
        self.held = set()

    def fileno(self):
        return self.read_fd

    def keysym_to_keycode(self, symbol):
        special = {
            "Control_L": 37,
            "Control_R": 105,
            "Shift_L": 50,
            "Shift_R": 62,
            "Alt_L": 64,
            "Alt_R": 108,
            "Super_L": 133,
            "Super_R": 134,
            "Num_Lock": 77,
            "Scroll_Lock": 78,
        }
        return {XK.string_to_keysym(k): v for k, v in special.items()}.get(symbol, symbol % 100)

    def get_modifier_mapping(self):
        return [[50, 62], [66], [37, 105], [64, 108], [77], [78], [133, 134], []]

    def screen(self):
        return SimpleNamespace(root=self)

    def grab_key(self, code, modifiers, *_args, onerror):
        if (code, modifiers) in self.conflicts:
            onerror(SimpleNamespace(), None)
        else:
            self.grabs.add((code, modifiers))

    def ungrab_key(self, code, modifiers):
        self.grabs.discard((code, modifiers))

    def sync(self):
        pass

    def flush(self):
        pass

    def pending_events(self):
        return len(self.events)

    def next_event(self):
        return self.events.pop(0)

    def query_keymap(self):
        keys = [0] * 32
        for code in self.held:
            keys[code // 8] |= 1 << (code % 8)
        return keys

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)
        self.grabs.clear()


@pytest.fixture
def service(qtbot, monkeypatch):
    monkeypatch.setattr(shortcuts, "supports_x11_input", lambda: True)
    display = Display()
    backend = SimpleNamespace(display=display, close=display.close)
    service = GlobalShortcut(backend_factory=lambda: backend)
    yield service, display
    service.shutdown()


def test_toggle_once_per_press_and_ignore_autorepeat(service):
    service, display = service
    service.configure("Ctrl+Alt+R")
    activations = []
    service.activated.connect(lambda: activations.append(True))
    display.held = {service.keycode}
    event = SimpleNamespace(type=X.KeyPress, detail=service.keycode)
    display.events = [event, event, event]
    service._events()
    assert len(activations) == 1
    display.held.clear()
    service._events()
    display.events = [event]
    service._events()
    assert len(activations) == 2


def test_conflicting_change_preserves_previous_shortcut(service):
    service, display = service
    service.configure("Ctrl+Alt+R")
    previous = display.grabs.copy()
    _, _, desired = binding(display, "Ctrl+Alt+T")
    display.conflicts = {next(iter(desired))}
    with pytest.raises(AppError, match="already in use"):
        service.configure("Ctrl+Alt+T")
    assert display.grabs == previous
    assert service.grabs == previous
    # Variants cover Caps Lock, Num Lock and Scroll Lock without stealing other modifiers.
    assert len(previous) == 8


def test_parse_requires_one_deliberate_combination():
    _, _, modifiers = parse_shortcut("Ctrl+Alt+R")
    assert modifiers == Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    for value in ("R", "Ctrl+R, Ctrl+T", ""):
        with pytest.raises(AppError):
            parse_shortcut(value)
