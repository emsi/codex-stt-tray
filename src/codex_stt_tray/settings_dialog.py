"""A persistent settings window with non-invasive readiness diagnostics."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    quit_requested = Signal()

    def __init__(self, settings, doctor, controller, parent=None):
        super().__init__(parent)
        self.settings, self.doctor, self.controller = settings, doctor, controller
        self.setWindowTitle("Codex STT Tray — Settings")
        self.resize(720, 510)
        layout = QVBoxLayout(self)
        form = QFormLayout()
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
        self.microphone = QComboBox()
        form.addRow("Microphone", self.microphone)
        self.volume = QComboBox()
        for label, value in (("Muted", 0.0), ("Quiet", 0.1), ("Normal", 0.25)):
            self.volume.addItem(label, value)
        form.addRow("Completion chime", self.volume)
        self.duration = QSpinBox()
        self.duration.setRange(1, 300)
        self.duration.setSuffix(" seconds")
        form.addRow("Recording limit", self.duration)
        layout.addLayout(form)
        self.location = QLabel(f"Application settings: {settings.path}")
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.location)
        self.summary = QLabel("Doctor checks setup without recording or changing the clipboard.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        report_row = QHBoxLayout()
        report_row.addWidget(QLabel("Doctor results"))
        report_row.addStretch()
        self.copy_button = QPushButton("Copy report")
        self.copy_button.clicked.connect(self.copy_report)
        report_row.addWidget(self.copy_button)
        layout.addLayout(report_row)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Check", "Status", "Details"])
        self.results.setRootIsDecorated(False)
        self.results.setWordWrap(True)
        layout.addWidget(self.results)
        self.notice = QLabel(
            "A successful recording is still needed to verify server transcription access."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.save_button = buttons.addButton(
            "Save and check", QDialogButtonBox.ButtonRole.ApplyRole
        )
        self.check_button = buttons.addButton("Run doctor", QDialogButtonBox.ButtonRole.ActionRole)
        exit_button = buttons.addButton("Exit app", QDialogButtonBox.ButtonRole.ActionRole)
        exit_button.clicked.connect(self.quit_requested.emit)
        self.save_button.clicked.connect(self.save)
        self.check_button.clicked.connect(self.check)
        buttons.rejected.connect(self.hide)
        layout.addWidget(buttons)
        doctor.updated.connect(self._results)
        doctor.finished.connect(self._completed)
        doctor.running_changed.connect(self._checking)
        controller.changed.connect(lambda _: self._availability())
        self.load()

    def load(self):
        self.home.setText(str(self.settings.codex_home))
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
        index = self.volume.findData(self.settings.volume)
        if index < 0:
            self.volume.addItem(f"Custom ({self.settings.volume:.0%})", self.settings.volume)
            index = self.volume.count() - 1
        self.volume.setCurrentIndex(index)
        self.duration.setValue(self.settings.duration_limit)
        self._results(self.doctor.results)
        self._checking(self.doctor.running)

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
        self.settings.codex_home = self.home.text()
        self.settings.device_id = self.microphone.currentData()
        self.settings.volume = self.volume.currentData()
        self.settings.duration_limit = self.duration.value()
        try:
            self.settings.sync()
        except OSError:
            self.summary.setText(
                "Cannot save settings. Check permissions on the application settings directory."
            )
            return
        # Invalidate retry audio/results associated with the previous configuration.
        self.controller.cancel()
        self.doctor.run()

    def check(self):
        if not self.controller.busy:
            self.doctor.run()

    def _availability(self):
        self.save_button.setEnabled(not self.controller.busy)
        self.check_button.setEnabled(not self.controller.busy and not self.doctor.running)

    def _checking(self, checking):
        self._availability()
        if checking:
            self.summary.setText("Checking saved configuration…")

    def _results(self, rows):
        self.copy_button.setEnabled(bool(rows))
        self.copy_button.setText("Copy report")
        self.results.clear()
        for row in rows:
            self.results.addTopLevelItem(
                QTreeWidgetItem([row.name, row.status.upper(), row.message])
            )
        self.results.resizeColumnToContents(0)
        self.results.resizeColumnToContents(1)

    def copy_report(self):
        lines = ["Codex STT Tray — Doctor report", ""]
        for index in range(self.results.topLevelItemCount()):
            row = self.results.topLevelItem(index)
            lines.append(f"[{row.text(1)}] {row.text(0)}: {row.text(2)}")
        lines.extend(["", self.notice.text()])
        # This is a user action in a focused window, so Qt can publish on Wayland too.
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.copy_button.setText("Copied")

    def _completed(self, rows):
        self._results(rows)
        errors = sum(row.status == "error" for row in rows)
        self.summary.setText(
            f"Doctor found {errors} issue(s). Fix them and check again. The tray stays running."
            if errors
            else "Readiness checks completed. Record to verify capture and transcription."
        )
