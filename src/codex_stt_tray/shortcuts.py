"""One configurable X11 global recording toggle, owned by the Qt event loop."""

from contextlib import suppress
from itertools import combinations

from PySide6.QtCore import QObject, QSocketNotifier, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence
from Xlib import XK, X, error

from .desktop_input import X11Input, supports_x11_input
from .models import AppError, ErrorCode


def shortcut_error(message):
    return AppError(ErrorCode.SHORTCUT, message, "shortcut")


def parse_shortcut(text):
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        raise shortcut_error("Choose one key combination for starting and stopping recording.")
    combo = sequence[0]
    key, modifiers = combo.key(), combo.keyboardModifiers()
    allowed = (
        Qt.KeyboardModifier.ControlModifier
        | Qt.KeyboardModifier.AltModifier
        | Qt.KeyboardModifier.ShiftModifier
        | Qt.KeyboardModifier.MetaModifier
    )
    if modifiers & ~allowed:
        raise shortcut_error("This shortcut uses unsupported modifiers.")
    if not modifiers and not Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        raise shortcut_error("Use Ctrl, Alt, Shift or Meta with the key, or choose a function key.")
    name = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
    aliases = {
        "Esc": "Escape",
        "Backspace": "BackSpace",
        "Del": "Delete",
        "Ins": "Insert",
        "PgUp": "Prior",
        "PgDown": "Next",
        "Enter": "KP_Enter",
    }
    if len(name) == 1:
        number = ord(name.lower())
        keysym = number if number <= 255 else 0x01000000 | number
    else:
        keysym = XK.string_to_keysym(aliases.get(name, name))
    if not keysym:
        raise shortcut_error("This key is not supported as a global shortcut.")
    return sequence.toString(QKeySequence.SequenceFormat.PortableText), keysym, modifiers


def binding(display, text):
    normalized, keysym, modifiers = parse_shortcut(text)
    code = display.keysym_to_keycode(keysym)
    if not code:
        raise shortcut_error("The shortcut key is unavailable in the current keyboard layout.")
    mapping = display.get_modifier_mapping()

    def mask(names):
        codes = {display.keysym_to_keycode(XK.string_to_keysym(name)) for name in names} - {0}
        return sum(1 << index for index, keys in enumerate(mapping) if codes.intersection(keys))

    flags = 0
    for qt_modifier, names in (
        (Qt.KeyboardModifier.ControlModifier, ("Control_L", "Control_R")),
        (Qt.KeyboardModifier.ShiftModifier, ("Shift_L", "Shift_R")),
        (Qt.KeyboardModifier.AltModifier, ("Alt_L", "Alt_R")),
        (Qt.KeyboardModifier.MetaModifier, ("Super_L", "Super_R")),
    ):
        if modifiers & qt_modifier:
            found = mask(names)
            if not found:
                raise shortcut_error("A shortcut modifier is unavailable in this keyboard layout.")
            flags |= found
    ignored = X.LockMask | mask(("Num_Lock", "Scroll_Lock"))
    bits = [1 << index for index in range(8) if ignored & (1 << index)]
    locks = {sum(parts) for n in range(len(bits) + 1) for parts in combinations(bits, n)}
    return normalized, code, {(code, flags | lock) for lock in locks}


class GlobalShortcut(QObject):
    activated = Signal()
    status_changed = Signal(str)
    failed = Signal(object)

    def __init__(self, parent=None, *, backend_factory=X11Input):
        super().__init__(parent)
        self.backend_factory = backend_factory
        self.backend = None
        self.notifier = None
        self.grabs = set()
        self.keycode = None
        self.pressed = False
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._events)

    def configure(self, text):
        if not text:
            self.shutdown()
            self.status_changed.emit("Shortcut disabled.")
            return
        parse_shortcut(text)
        if not supports_x11_input():
            raise shortcut_error("Global recording shortcuts require X11 in this version.")
        try:
            if self.backend is None:
                self.backend = self.backend_factory()
                self.notifier = QSocketNotifier(
                    self.backend.display.fileno(), QSocketNotifier.Type.Read, self
                )
                self.notifier.activated.connect(self._events)
            display = self.backend.display
            normalized, keycode, desired = binding(display, text)
            root = display.screen().root
            new = desired - self.grabs
            caught = error.CatchError()
            for code, modifiers in new:
                root.grab_key(
                    code, modifiers, False, X.GrabModeAsync, X.GrabModeAsync, onerror=caught
                )
            display.sync()
            if caught.get_error():
                for code, modifiers in new:
                    root.ungrab_key(code, modifiers)
                display.sync()
                raise shortcut_error("Shortcut is already in use. Choose another combination.")
            for code, modifiers in self.grabs - desired:
                root.ungrab_key(code, modifiers)
            display.flush()
            self.grabs, self.keycode = desired, keycode
            self.pressed = False
            self.timer.start()
            self.status_changed.emit(f"Shortcut active: {normalized}")
        except AppError:
            raise
        except Exception:
            raise shortcut_error("Cannot register the X11 recording shortcut.") from None

    @Slot()
    def _events(self):
        if self.backend is None:
            return
        try:
            display = self.backend.display
            while display.pending_events():
                event = display.next_event()
                if event.type == X.MappingNotify:
                    display.refresh_keyboard_mapping(event)
                elif event.type == X.KeyPress and event.detail == self.keycode and not self.pressed:
                    self.pressed = True
                    self.activated.emit()
            # Physical state avoids toggling again on X11's synthetic autorepeat releases.
            if self.pressed:
                keys = display.query_keymap()
                if not keys[self.keycode // 8] & (1 << (self.keycode % 8)):
                    self.pressed = False
        except Exception:
            self.shutdown()
            self.failed.emit(
                shortcut_error("The X11 shortcut connection failed. Save Settings to retry.")
            )

    def shutdown(self):
        self.timer.stop()
        if self.notifier:
            self.notifier.setEnabled(False)
            self.notifier.deleteLater()
            self.notifier = None
        if self.backend:
            with suppress(Exception):
                self.backend.close()  # Closing the connection releases only our grabs.
            self.backend = None
        self.grabs = set()
        self.keycode = None
        self.pressed = False
