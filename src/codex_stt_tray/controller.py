"""Own the application state and reject callbacks from superseded operations."""

from PySide6.QtCore import QObject, QTimer, Signal

from .models import AppError, ErrorCode, State


class Controller(QObject):
    changed = Signal(object)
    error_raised = Signal(object)
    copied = Signal()
    paste_requested = Signal(str)
    status = Signal(str)

    def __init__(self, recorder, transcriber, clipboard, settings, parent=None):
        super().__init__(parent)
        self.recorder, self.transcriber = recorder, transcriber
        self.clipboard, self.settings = clipboard, settings
        self.state = State.IDLE
        self.error = None
        self.clip = None
        self.transcript = None
        self.job = None
        self.generation = 0
        self.closed = False
        self.checking = False
        self.success_timer = QTimer(self, singleShot=True, interval=3000)
        self.success_timer.timeout.connect(self._ready)
        self.retention_timer = QTimer(self, singleShot=True, interval=300_000)
        self.retention_timer.timeout.connect(self.cancel)
        recorder.clip_ready.connect(self._recorded)
        recorder.failed.connect(self._recording_failed)
        recorder.elapsed.connect(self._elapsed)

    @property
    def can_retry(self):
        return (
            self.state == State.ERROR
            and self.error is not None
            and self.error.retryable
            and (self.clip is not None or self.transcript is not None)
        )

    def _set_state(self, state):
        self.state = state
        self.changed.emit(state)

    def toggle(self):
        if self.closed or self.checking:
            return
        if self.state == State.RECORDING:
            self.recorder.stop()
        elif self.state in (State.IDLE, State.COPIED, State.ERROR):
            self.start_recording()

    def start_recording(self):
        if (
            self.closed
            or self.checking
            or self.state not in (State.IDLE, State.COPIED, State.ERROR)
        ):
            return
        self.cancel()
        self._set_state(State.RECORDING)
        try:
            self.recorder.start(self.settings.device_id, self.settings.duration_limit)
        except AppError as error:
            self.recorder.cancel()
            self._fail(error)

    @property
    def busy(self):
        return self.state in (State.RECORDING, State.TRANSCRIBING, State.COPYING)

    def recover(self):
        """Recover the current operation without terminating the desktop application."""
        if self.closed:
            return
        self.cancel()
        self._fail(
            AppError(
                ErrorCode.INTERNAL,
                "The operation failed unexpectedly. Open Settings and run the doctor, then retry.",
                "application",
            )
        )

    def _elapsed(self, seconds):
        if self.state == State.RECORDING:
            self.status.emit(f"Recording {seconds // 60}:{seconds % 60:02d} — click to stop")

    def _recording_failed(self, error):
        if self.state == State.RECORDING:
            self._fail(error)

    def _recorded(self, clip):
        if self.closed or self.state != State.RECORDING:
            return
        self.clip = clip
        self._transcribe()

    def _transcribe(self):
        self.retention_timer.stop()
        self.error = None
        self.generation += 1
        generation = self.generation
        self._set_state(State.TRANSCRIBING)
        self.job = self.transcriber.create_job(self.clip)
        self.job.succeeded.connect(lambda result: self._transcribed(generation, result))
        self.job.failed.connect(lambda error: self._job_failed(generation, error))
        self.job.progress.connect(lambda sent, total: self._progress(generation, sent, total))
        self.job.start()

    def _progress(self, generation, sent, total):
        if generation == self.generation and self.state == State.TRANSCRIBING:
            self.status.emit(
                f"Uploading {sent * 100 // total}%"
                if total > 0 and sent < total
                else "Waiting for transcript…"
            )

    def _transcribed(self, generation, result):
        if self.closed or generation != self.generation:
            return
        self.job = None
        self.clip = None
        self.transcript = result
        self._copy()

    def _copy(self):
        self.retention_timer.stop()
        self.error = None
        self.generation += 1
        generation = self.generation
        self._set_state(State.COPYING)
        self.job = self.clipboard.create_job(self.transcript.text)
        self.job.succeeded.connect(lambda: self._copied(generation))
        self.job.failed.connect(lambda error: self._job_failed(generation, error))
        self.job.start()

    def _copied(self, generation):
        if self.closed or generation != self.generation:
            return
        self.job = None
        text = self.transcript.text
        self.transcript = None
        self._set_state(State.COPIED)
        self.copied.emit()
        if getattr(self.settings, "auto_paste", False):
            self.paste_requested.emit(text)
        self.success_timer.start()

    def _job_failed(self, generation, error):
        if not self.closed and generation == self.generation:
            self.job = None
            self._fail(error)

    def _fail(self, error):
        self.error = error
        if not error.retryable:
            self.clip = None
            self.transcript = None
        self._set_state(State.ERROR)
        self.error_raised.emit(error)
        self.retention_timer.start()

    def retry(self):
        if not self.closed and not self.checking and self.can_retry:
            if self.transcript is not None:
                self._copy()
            else:
                self._transcribe()

    def cancel(self):
        self.generation += 1
        self.success_timer.stop()
        self.retention_timer.stop()
        self.recorder.cancel()
        job, self.job = self.job, None
        if job is not None:
            job.cancel()
        self.clip = self.transcript = self.error = None
        self._set_state(State.IDLE)

    def _ready(self):
        if self.state == State.COPIED:
            self._set_state(State.IDLE)

    def shutdown(self):
        self.closed = True
        self.cancel()
        self.transcriber.shutdown()
