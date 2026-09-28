"""Optional X11 input integration; never spawn keyboard command-line tools."""

import os
from contextlib import suppress

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from Xlib import XK, X, display
from Xlib.ext import xtest

from .clipboard import supports_primary, target_modes
from .models import AppError, ErrorCode, PasteMethod

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

    def buttons_held(self):
        mask = self.display.screen().root.query_pointer().mask
        return bool(
            mask & (X.Button1Mask | X.Button2Mask | X.Button3Mask | X.Button4Mask | X.Button5Mask)
        )

    def pointer(self):
        root = self.display.screen().root
        pointer = root.query_pointer()
        if not pointer.same_screen:
            raise RuntimeError("Pointer is on another screen")
        return (getattr(pointer.child, "id", 0), pointer.root_x, pointer.root_y)

    def pointer_over_target(self, target, pointer):
        # Match the root's pointer child to the focused window's outermost ancestor,
        # accounting for window-manager frames and Qt child windows.
        window = self.display.create_resource_object("window", target)
        position = window.query_pointer()
        geometry = window.get_geometry()
        if not position.same_screen or not (
            0 <= position.win_x < geometry.width and 0 <= position.win_y < geometry.height
        ):
            return False
        for _ in range(64):
            tree = window.query_tree()
            if tree.parent.id == tree.root.id:
                return window.id == pointer[0]
            window = tree.parent
        return False

    def middle_click(self):
        if not self.display.has_extension("XTEST"):
            raise RuntimeError("XTEST unavailable")
        try:
            xtest.fake_input(self.display, X.ButtonPress, 2)
        finally:
            xtest.fake_input(self.display, X.ButtonRelease, 2)
            self.display.sync()

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
        self.pointer_target = None
        self.copy_target = settings.copy_target
        self.method = settings.paste_method
        self.sequence = settings.paste_keys
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._attempt)

    def request(self, text):
        self.cancel()
        if not self.settings.auto_paste:
            return
        self.copy_target = self.settings.copy_target
        self.method = self.settings.paste_method
        self.sequence = self.settings.paste_keys
        if self.method == PasteMethod.MIDDLE_CLICK and (
            not self.copy_target.includes_primary or not supports_primary()
        ):
            self._fail("Middle-click paste requires copying to PRIMARY on X11.")
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
            if self.method == PasteMethod.MIDDLE_CLICK:
                self.pointer_target = self.backend.pointer()
                if not self.backend.pointer_over_target(self.target, self.pointer_target):
                    self._fail(
                        "Middle-click skipped: point inside the focused application's text area."
                    )
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
                or any(
                    QGuiApplication.clipboard().text(mode) != self.expected
                    for mode in target_modes(self.copy_target)
                )
            ):
                self._fail("Automatic paste skipped because focus or clipboard contents changed.")
                return
            if self.method == PasteMethod.MIDDLE_CLICK and (
                self.backend.pointer() != self.pointer_target
                or not self.backend.pointer_over_target(self.target, self.pointer_target)
            ):
                self._fail("Middle-click skipped because the pointer or destination moved.")
                return
            self.attempts += 1
            if self.backend.keys_held() or self.backend.buttons_held():
                if self.attempts >= 40:
                    self._fail(
                        "Automatic paste skipped because keys or mouse buttons are held down."
                    )
                return
            if self.method == PasteMethod.MIDDLE_CLICK:
                self.backend.middle_click()
            else:
                self.backend.paste(self.sequence)
        except Exception:
            self._fail("Cannot send the automatic paste input.")
            return
        self.cancel()
        self.sent.emit()

    def _fail(self, message):
        self.cancel()
        self.failed.emit(
            AppError(
                ErrorCode.PASTE, f"{message} Copying completed before this paste attempt.", "paste"
            )
        )

    def cancel(self):
        self.timer.stop()
        self.expected = self.target = None
        self.pointer_target = None
        self.attempts = 0

    def shutdown(self):
        self.cancel()
        if self.backend:
            with suppress(Exception):
                self.backend.close()
            self.backend = None
