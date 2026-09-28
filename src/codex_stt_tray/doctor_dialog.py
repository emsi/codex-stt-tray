"""Read-only doctor results, independent of editable application preferences."""

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


class DoctorDialog(QDialog):
    settings_requested = Signal()

    def __init__(self, doctor, controller, parent=None):
        super().__init__(parent)
        self.doctor, self.controller = doctor, controller
        self.setWindowTitle("Codex STT Tray — Doctor")
        self.resize(740, 440)
        layout = QVBoxLayout(self)
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
        layout.addWidget(self.results, 2)
        self.notice = QLabel(
            "A successful recording is still needed to verify server transcription access."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.check_button = buttons.addButton("Run doctor", QDialogButtonBox.ButtonRole.ActionRole)
        self.check_button.clicked.connect(self.check)
        settings_button = buttons.addButton("Settings…", QDialogButtonBox.ButtonRole.ActionRole)
        settings_button.clicked.connect(self.settings_requested.emit)
        buttons.rejected.connect(self.hide)
        layout.addWidget(buttons)
        doctor.updated.connect(self._results)
        doctor.finished.connect(self._completed)
        doctor.running_changed.connect(self._checking)
        controller.changed.connect(lambda _: self._availability())
        self._results(doctor.results)
        self._checking(doctor.running)

    def open_doctor(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def check(self):
        if not self.controller.busy and not self.doctor.running:
            self.doctor.run()

    def _availability(self):
        self.check_button.setEnabled(not self.controller.busy and not self.doctor.running)

    def _checking(self, checking):
        self._availability()
        if checking:
            self.summary.setText("Checking saved configuration…")

    def _results(self, rows):
        verified = any(row.name == "Transcription" and row.status == "ok" for row in rows)
        self.notice.setText(
            "Capture, transcription, and clipboard delivery succeeded in this app session."
            if verified
            else "A successful recording is still needed to verify server transcription access."
        )
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
            else "Readiness checks completed."
        )
