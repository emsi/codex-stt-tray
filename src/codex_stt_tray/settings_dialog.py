"""Editable preferences; diagnostics are displayed in a separate window."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .clipboard import supports_primary
from .constants import MAX_RECORDING_SECONDS
from .desktop_input import PASTE_KEYS, supports_x11_input
from .models import AppError, CopyTarget, PasteMethod
from .silence import TrimOptions


class SettingsDialog(QDialog):
    doctor_requested = Signal()

    def __init__(self, settings, doctor, controller, parent=None, *, shortcuts=None):
        super().__init__(parent)
        self.settings, self.doctor, self.controller = settings, doctor, controller
        self.shortcuts = shortcuts
        self.setWindowTitle("Codex STT Tray — Settings")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        forms = {}
        for title in ("Recording", "Delivery", "Codex"):
            form_widget = QWidget()
            form = QFormLayout(form_widget)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(form_widget)
            tabs.addTab(scroll, title)
            forms[title] = form
        layout.addWidget(tabs)
        form = forms["Codex"]
        self.home = QLineEdit()
        self.home.setPlaceholderText("Use CODEX_HOME from the environment, or ~/.codex")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        home_row = QHBoxLayout()
        home_row.addWidget(self.home)
        home_row.addWidget(browse)
        form.addRow("Codex home", home_row)
        description = QLabel(
            "Choose the directory containing Codex config.toml and auth.json. "
            "Credentials stay there; this app never copies them."
        )
        description.setWordWrap(True)
        form.addRow(description)
        form = forms["Recording"]
        self.microphone = QComboBox()
        form.addRow("Microphone", self.microphone)
        self.volume = QComboBox()
        for label, value in (("Muted", 0.0), ("Quiet", 0.1), ("Normal", 0.25)):
            self.volume.addItem(label, value)
        forms["Delivery"].addRow("Completion chime", self.volume)
        self.duration = QSpinBox()
        self.duration.setRange(1, MAX_RECORDING_SECONDS)
        self.duration.setSuffix(" seconds")
        form.addRow("Recording limit", self.duration)
        form = forms["Delivery"]
        self.copy_target = QComboBox()
        for label, target in (
            ("CLIPBOARD", CopyTarget.CLIPBOARD),
            ("PRIMARY (mouse selection)", CopyTarget.PRIMARY),
            ("CLIPBOARD and PRIMARY", CopyTarget.BOTH),
        ):
            self.copy_target.addItem(label, target.value)
        form.addRow("Copy transcript to", self.copy_target)
        self.auto_paste = QCheckBox("Paste into the focused application after copying")
        self.auto_paste.setEnabled(supports_x11_input())
        form.addRow("Automatic paste", self.auto_paste)
        self.paste_method = QComboBox()
        self.paste_method.addItem("Keyboard shortcut", PasteMethod.KEYBOARD.value)
        self.paste_method.addItem("Middle mouse click (PRIMARY)", PasteMethod.MIDDLE_CLICK.value)
        form.addRow("Paste method", self.paste_method)
        self.paste_keys = QComboBox()
        self.paste_keys.addItems(PASTE_KEYS)
        form.addRow("Paste shortcut", self.paste_keys)
        self.paste_note = QLabel()
        self.paste_note.setWordWrap(True)
        form.addRow(self.paste_note)
        copy_note = QLabel(
            "PRIMARY replaces the mouse selection and requires X11. "
            "Clipboard managers may synchronize or retain either destination."
        )
        copy_note.setWordWrap(True)
        form.addRow(copy_note)
        self.auto_paste.toggled.connect(self._delivery_controls)
        self.paste_method.currentIndexChanged.connect(self._delivery_controls)
        self.copy_target.currentIndexChanged.connect(self._delivery_controls)
        form = forms["Recording"]
        self.hotkey = QKeySequenceEdit()
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey.setClearButtonEnabled(True)
        self.hotkey.setEnabled(supports_x11_input())
        form.addRow("Start / stop shortcut", self.hotkey)
        self.hotkey_status = QLabel(
            "Same combination starts and stops recording. Clear to disable."
        )
        self.hotkey_status.setWordWrap(True)
        form.addRow(self.hotkey_status)
        if shortcuts:
            shortcuts.status_changed.connect(self.hotkey_status.setText)
        self.trim_silence = QCheckBox("Trim silence only at the beginning and end")
        form.addRow("Silence trimming", self.trim_silence)
        self.silence_threshold = QSpinBox()
        self.silence_threshold.setRange(-70, -20)
        self.silence_threshold.setSuffix(" dBFS")
        self.silence_threshold.setToolTip("Lower values preserve quieter speech.")
        form.addRow("Silence threshold", self.silence_threshold)
        self.silence_padding = QSpinBox()
        self.silence_padding.setRange(100, 1000)
        self.silence_padding.setSuffix(" ms")
        form.addRow("Speech edge padding", self.silence_padding)
        self.trim_silence.toggled.connect(self.silence_threshold.setEnabled)
        self.trim_silence.toggled.connect(self.silence_padding.setEnabled)
        trim_note = QLabel(
            "Conservative default: −55 dBFS with 300 ms padding. "
            "Pauses within speech and edge silence shorter than 500 ms are kept."
        )
        trim_note.setWordWrap(True)
        form.addRow(trim_note)
        self.location = QLabel(f"Application settings: {settings.path}")
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.location)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.save_button = buttons.addButton(
            "Save and check", QDialogButtonBox.ButtonRole.ApplyRole
        )
        doctor_button = buttons.addButton("Doctor…", QDialogButtonBox.ButtonRole.ActionRole)
        doctor_button.clicked.connect(self.doctor_requested.emit)
        self.save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.hide)
        layout.addWidget(buttons)
        controller.changed.connect(lambda _: self._availability())
        self.load()

    def load(self):
        self.summary.clear()
        self.home.setText(str(self.settings.codex_home))
        self.copy_target.setCurrentIndex(self.copy_target.findData(self.settings.copy_target.value))
        self.auto_paste.setChecked(self.settings.auto_paste)
        self.paste_method.setCurrentIndex(
            self.paste_method.findData(self.settings.paste_method.value)
        )
        self.paste_keys.setCurrentText(self.settings.paste_keys)
        self._delivery_controls()
        self.hotkey.setKeySequence(QKeySequence(self.settings.recording_shortcut))
        trimming = self.settings.trim_options
        self.trim_silence.setChecked(trimming.enabled)
        self.silence_threshold.setValue(trimming.threshold_db)
        self.silence_padding.setValue(trimming.padding_ms)
        self.silence_threshold.setEnabled(trimming.enabled)
        self.silence_padding.setEnabled(trimming.enabled)
        self.refresh_preference("device_id")
        self.refresh_preference("volume")
        self.refresh_preference("duration_limit")
        self._availability()

    def refresh_preference(self, name):
        """Reflect a tray edit without overwriting unrelated, unsaved dialog fields."""
        if name == "device_id":
            self._load_microphones()
        elif name == "volume":
            index = self.volume.findData(self.settings.volume)
            if index < 0:
                self.volume.addItem(f"Custom ({self.settings.volume:.0%})", self.settings.volume)
                index = self.volume.count() - 1
            self.volume.setCurrentIndex(index)
        elif name == "duration_limit":
            self.duration.setValue(self.settings.duration_limit)

    def _load_microphones(self):
        self.microphone.clear()
        self.microphone.addItem("System default", b"")
        for device in QMediaDevices.audioInputs():
            self.microphone.addItem(device.description(), bytes(device.id()))
        # QVariant lookup does not compare Python bytes by value in PySide6.
        selected_id = self.settings.device_id
        index = next(
            (
                i
                for i in range(self.microphone.count())
                if self.microphone.itemData(i) == selected_id
            ),
            -1,
        )
        if index < 0:
            self.microphone.addItem(
                "Previously selected microphone (unavailable)", self.settings.device_id
            )
            index = self.microphone.count() - 1
        self.microphone.setCurrentIndex(index)

    def open_settings(self):
        if not self.isVisible():
            self.load()
        self.show()
        self.raise_()
        self.activateWindow()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Codex home", self.home.text())
        if path:
            self.home.setText(path)

    def save(self):
        if self.controller.busy:
            return
        target = CopyTarget(self.copy_target.currentData())
        method = PasteMethod(self.paste_method.currentData())
        if target.includes_primary and not supports_primary():
            self.summary.setText("PRIMARY requires X11 selection support. Choose CLIPBOARD here.")
            return
        if (
            self.auto_paste.isChecked()
            and method == PasteMethod.MIDDLE_CLICK
            and not target.includes_primary
        ):
            self.summary.setText("Middle-click paste requires PRIMARY or CLIPBOARD and PRIMARY.")
            return
        sequence = self.hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if (
            self.auto_paste.isChecked()
            and method == PasteMethod.KEYBOARD
            and sequence == self.paste_keys.currentText()
        ):
            self.hotkey_status.setText(
                "Choose a recording shortcut different from the paste shortcut."
            )
            self.summary.setText(self.hotkey_status.text())
            return
        previous_shortcut = self.settings.recording_shortcut
        previous = {key: self.settings.store.value(key) for key in self.settings.store.allKeys()}
        if self.shortcuts:
            try:
                self.shortcuts.configure(sequence)
            except AppError as error:
                self.hotkey_status.setText(error.message)
                self.summary.setText(error.message)
                return
        self.settings.recording_shortcut = sequence
        self.settings.codex_home = self.home.text()
        self.settings.device_id = self.microphone.currentData()
        self.settings.volume = self.volume.currentData()
        self.settings.duration_limit = self.duration.value()
        self.settings.copy_target = CopyTarget(self.copy_target.currentData())
        self.settings.auto_paste = self.auto_paste.isChecked()
        self.settings.paste_method = method
        self.settings.paste_keys = self.paste_keys.currentText()
        self.settings.trim_options = TrimOptions(
            self.trim_silence.isChecked(),
            self.silence_threshold.value(),
            self.silence_padding.value(),
        )
        try:
            self.settings.sync()
        except OSError:
            for key in set(self.settings.store.allKeys()) - previous.keys():
                self.settings.store.remove(key)
            for key, value in previous.items():
                self.settings.store.setValue(key, value)
            if self.shortcuts:
                try:
                    self.shortcuts.configure(previous_shortcut)
                except AppError:
                    self.hotkey_status.setText(
                        "Cannot restore the previous shortcut. Save again to retry."
                    )
            self.summary.setText(
                "Cannot save settings. Check permissions on the application settings directory."
            )
            return
        # Invalidate retry audio/results associated with the previous configuration.
        self.controller.cancel()
        self.summary.setText("Settings saved. Doctor is checking the saved configuration.")
        self.doctor_requested.emit()
        self.doctor.run()

    def _availability(self):
        self.save_button.setEnabled(not self.controller.busy)

    def _delivery_controls(self, *_):
        keyboard = self.paste_method.currentData() == PasteMethod.KEYBOARD.value
        enabled = self.auto_paste.isChecked() and supports_x11_input()
        self.paste_method.setEnabled(enabled)
        self.paste_keys.setEnabled(enabled and keyboard)
        self.paste_note.setText(
            "X11 only. Uses the application focused when transcription finishes. "
            "The shortcut must paste from a selected copy destination. "
            "Konsole's Ctrl+Shift+V and Shift+Insert use CLIPBOARD."
            if keyboard
            else "X11 only. Requires PRIMARY copying. Point at the text area in the focused "
            "application before transcription finishes. The pointer is never moved; "
            "movement while waiting cancels the click. Some applications intercept middle-click."
        )
