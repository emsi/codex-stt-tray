"""Tray presentation only; recording and request ownership live elsewhere."""

from functools import partial

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QCursor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .models import AppError, ErrorCode, State


def state_icon(state: State, frame: int = 0) -> QIcon:
    icon = QIcon()
    for size in (22, 32, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 32, size / 32)
        painter.setPen(Qt.PenStyle.NoPen)
        if state in (State.TRANSCRIBING, State.COPYING):
            painter.setPen(QPen(QColor("#f6ad32"), 4))
            painter.drawArc(QRectF(5, 5, 22, 22), -frame * 45 * 16, 260 * 16)
        elif state == State.COPIED:
            painter.setBrush(QColor("#228b55"))
            painter.drawEllipse(QRectF(2, 2, 28, 28))
            painter.setPen(QPen(QColor("white"), 3))
            painter.drawLine(QPointF(8, 16), QPointF(13, 21))
            painter.drawLine(QPointF(13, 21), QPointF(24, 10))
        elif state == State.ERROR:
            painter.setBrush(QColor("#f6ad32"))
            painter.drawEllipse(QRectF(2, 2, 28, 28))
            painter.setPen(QPen(QColor("#202020"), 3))
            painter.drawLine(QPointF(16, 8), QPointF(16, 18))
            painter.drawPoint(QPointF(16, 24))
        else:
            painter.setBrush(
                QColor("#f05252" if state == State.RECORDING and frame % 2 else "#c62828")
            )
            painter.drawEllipse(QRectF(3, 3, 26, 26))
            if state == State.RECORDING:
                painter.setBrush(QColor("white"))
                painter.drawRoundedRect(QRectF(10, 10, 12, 12), 1, 1)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class Tray(QSystemTrayIcon):
    quit_requested = Signal()
    settings_requested = Signal()
    doctor_requested = Signal()
    preference_changed = Signal(str)

    def __init__(self, controller, settings, parent=None):
        super().__init__(parent)
        self.controller, self.settings = controller, settings
        self.frame = 0
        self.menu = QMenu()
        self.status_action = self.menu.addAction("Ready")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.primary = self.menu.addAction("Start recording")
        self.primary.triggered.connect(self._primary)
        self.retry = self.menu.addAction("Retry")
        self.retry.triggered.connect(controller.retry)
        self.discard = self.menu.addAction("Cancel / discard")
        self.discard.triggered.connect(controller.cancel)
        self.menu.addSeparator()
        self.quick_menus = {}
        self.quick_groups = {}
        for key, label in (
            ("device_id", "Microphone"),
            ("duration_limit", "Recording limit"),
            ("volume", "Completion chime"),
        ):
            submenu = self.menu.addMenu(label)
            self.quick_menus[key] = submenu
            self.quick_groups[key] = QActionGroup(submenu)
        self.menu.aboutToShow.connect(self._populate_preferences)
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction("Settings…", self.settings_requested.emit)
        self.doctor_action = self.menu.addAction("Run doctor…", self.doctor_requested.emit)
        self.menu.addSeparator()
        self.menu.addAction("Exit", self.quit_requested.emit)
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)
        controller.changed.connect(self._state_changed)
        controller.status.connect(self._status)
        controller.error_raised.connect(self._error)
        self.timer = QTimer(self, interval=250)
        self.timer.timeout.connect(self._animate)
        self._state_changed(controller.state)

    def _primary(self):
        if self.controller.state == State.ERROR:
            self.controller.start_recording()
        else:
            self.controller.toggle()

    def _populate_preferences(self):
        microphones = [("System default", b"")]
        microphones.extend((d.description(), bytes(d.id())) for d in QMediaDevices.audioInputs())
        choices = {
            "device_id": microphones,
            "duration_limit": [(f"{minutes} minutes", minutes * 60) for minutes in (3, 5, 10)],
            "volume": [("Muted", 0.0), ("Quiet", 0.1), ("Normal", 0.25)],
        }
        for key, items in choices.items():
            menu = self.quick_menus[key]
            menu.clear()
            current = getattr(self.settings, key)
            for label, value in items:
                action = menu.addAction(label)
                action.setCheckable(True)
                action.setData(value)
                self.quick_groups[key].addAction(action)
                action.setChecked(value == current)
                action.triggered.connect(partial(self._save_preference, key, value))
            if not any(value == current for _, value in items):
                label = {
                    "device_id": "Selected microphone (unavailable)",
                    "duration_limit": f"Custom: {self.settings.duration_limit} seconds",
                    "volume": f"Custom: {self.settings.volume:.0%}",
                }[key]
                action = menu.addAction(label)
                action.setCheckable(True)
                self.quick_groups[key].addAction(action)
                action.setChecked(True)
                action.setEnabled(False)
            if key == "duration_limit":
                menu.addSeparator()
                menu.addAction("Custom…", self.settings_requested.emit)

    def _save_preference(self, key, value, _checked=False):
        if self.controller.busy or self.controller.checking:
            return
        previous = getattr(self.settings, key)
        if previous == value:
            return
        setattr(self.settings, key, value)
        try:
            self.settings.sync()
        except OSError:
            setattr(self.settings, key, previous)
            self._error(
                AppError(
                    ErrorCode.INTERNAL,
                    "Cannot save settings. Check permissions on the settings directory.",
                    "settings",
                )
            )
            return
        self.preference_changed.emit(key)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.controller.state == State.ERROR:
                self.menu.popup(QCursor.pos())
            else:
                self.controller.toggle()

    def _status(self, text):
        self.status_action.setText(text)
        self.setToolTip(f"Codex STT Tray — {text}")

    def _state_changed(self, state):
        labels = {
            State.IDLE: "Ready — click to record",
            State.RECORDING: "Recording — click to stop",
            State.TRANSCRIBING: "Transcribing…",
            State.COPYING: "Copying to clipboard…",
            State.COPIED: "Transcript copied",
            State.ERROR: "Failed — open menu for details",
        }
        self._status(labels[state])
        self.primary.setText(
            "Stop and transcribe" if state == State.RECORDING else "Start recording"
        )
        busy = state in (State.TRANSCRIBING, State.COPYING)
        checking = getattr(self.controller, "checking", False)
        self.primary.setEnabled(not busy and not checking)
        self.retry.setEnabled(self.controller.can_retry and not checking)
        self.retry.setText(
            "Retry copy" if self.controller.transcript is not None else "Retry transcription"
        )
        self.discard.setEnabled(state in (State.RECORDING, State.TRANSCRIBING, State.ERROR))
        self.doctor_action.setEnabled(not busy and state != State.RECORDING and not checking)
        for submenu in self.quick_menus.values():
            submenu.setEnabled(not busy and state != State.RECORDING and not checking)
        self.frame = 0
        self.setIcon(state_icon(State.TRANSCRIBING if checking else state))
        if checking:
            self._status("Checking setup…")
        if checking or busy or state == State.RECORDING:
            self.timer.start()
        else:
            self.timer.stop()

    def _animate(self):
        self.frame = (self.frame + 1) % 8
        state = (
            State.TRANSCRIBING
            if getattr(self.controller, "checking", False)
            else self.controller.state
        )
        self.setIcon(state_icon(state, self.frame))

    def set_checking(self, checking):
        self.controller.checking = checking
        self._state_changed(self.controller.state)

    def doctor_completed(self, rows):
        errors = sum(row.status == "error" for row in rows)
        if errors and self.controller.state in (State.IDLE, State.ERROR):
            self.setIcon(state_icon(State.ERROR))
            self._status(f"Doctor found {errors} issue(s) — open Settings")

    def _error(self, error):
        self._status(error.message)
        self.showMessage("Codex STT Tray", error.message, QSystemTrayIcon.MessageIcon.Warning)
