"""Optional X11 input integration; never spawn keyboard command-line tools."""

import os
from contextlib import suppress

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from Xlib import XK, X, display
from Xlib.ext import xtest

from .models import AppError, ErrorCode

PASTE_KEYS = {
    "Ctrl+V": ("Control_L", "v"),
    "Ctrl+Shift+V": ("Control_L", "Shift_L", "v"),
    "Shift+Insert": ("Shift_L", "Insert"),
}


def supports_x11_input():
    return (
        QGuiApplication.platformName() == "xcb" and os.environ.get("XDG_SESSION_TYPE") != "wayland"
    )


class X11Input:
    def __init__(self):
        self.display = display.Display()

    def focus(self):
        focus = self.display.get_input_focus().focus
        return getattr(focus, "id", 0)

    def keys_held(self):
        return any(self.display.query_keymap())

    def paste(self, sequence):
        if not self.display.has_extension("XTEST"):
            raise RuntimeError("XTEST unavailable")
        codes = [
            self.display.keysym_to_keycode(XK.string_to_keysym(k)) for k in PASTE_KEYS[sequence]
        ]
        if not all(codes):
            raise RuntimeError("Paste keys unavailable")
        pressed = []
        try:
            for code in codes:
                xtest.fake_input(self.display, X.KeyPress, code)
                pressed.append(code)
        finally:
            for code in reversed(pressed):
                xtest.fake_input(self.display, X.KeyRelease, code)
            self.display.sync()

    def close(self):
        self.display.close()


class AutoPaste(QObject):
    """Deliver paste once, after clipboard publication, without stealing focus."""

    failed = Signal(object)
    sent = Signal()

    def __init__(self, settings, parent=None, *, backend_factory=X11Input):
        super().__init__(parent)
        self.settings, self.backend_factory = settings, backend_factory
        self.backend = None
        self.expected = None
        self.target = None
        self.attempts = 0
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._attempt)

    def request(self, text):
        self.cancel()
        if not self.settings.auto_paste:
            return
        if not supports_x11_input():
            self._fail("Automatic paste requires an X11 desktop in this version.")
            return
        if QApplication.activeWindow() is not None:
            self._fail("Automatic paste skipped because this app has focus.")
            return
        try:
            if self.backend is None:
                self.backend = self.backend_factory()
            self.target = self.backend.focus()
            if not self.target:
                self._fail("Automatic paste skipped because no application has keyboard focus.")
                return
        except Exception:
            self._fail("Cannot connect to X11 for automatic paste.")
            return
        self.expected = text
        self.timer.start()

    def _attempt(self):
        if self.expected is None:
            return
        try:
            if (
                QApplication.activeWindow() is not None
                or self.backend.focus() != self.target
                or QGuiApplication.clipboard().text() != self.expected
            ):
                self._fail("Automatic paste skipped because focus or clipboard contents changed.")
                return
            self.attempts += 1
            if self.backend.keys_held():
                if self.attempts >= 40:
                    self._fail("Automatic paste skipped because keys are still held down.")
                return
            self.backend.paste(self.settings.paste_keys)
        except Exception:
            self._fail("Cannot send the automatic paste shortcut.")
            return
        self.cancel()
        self.sent.emit()

    def _fail(self, message):
        self.cancel()
        self.failed.emit(
            AppError(ErrorCode.PASTE, f"{message} The transcript stays on the clipboard.", "paste")
        )

    def cancel(self):
        self.timer.stop()
        self.expected = self.target = None
        self.attempts = 0

    def shutdown(self):
        self.cancel()
        if self.backend:
            with suppress(Exception):
                self.backend.close()
            self.backend = None
