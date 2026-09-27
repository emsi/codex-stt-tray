from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal

from codex_stt_tray.controller import Controller
from codex_stt_tray.models import AppError, AudioClip, ErrorCode, State, Transcript


class Recorder(QObject):
    clip_ready = Signal(object)
    failed = Signal(object)
    elapsed = Signal(int)

    def __init__(self):
        super().__init__()
        self.starts = self.stops = 0

    def start(self, *_):
        self.starts += 1

    def stop(self):
        self.stops += 1
        self.clip_ready.emit(AudioClip(b"synthetic audio", 1))

    def cancel(self):
        pass


class Job(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(int, int)

    def __init__(self, parent):
        super().__init__(parent)
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class Service(QObject):
    def __init__(self):
        super().__init__()
        self.jobs = []
        self.inputs = []
        self.closed = False

    def create_job(self, value):
        self.inputs.append(value)
        job = Job(self)
        self.jobs.append(job)
        return job

    def shutdown(self):
        self.closed = True


def controller():
    return Controller(
        Recorder(), Service(), Service(), SimpleNamespace(device_id=b"", duration_limit=60)
    )


def record(c):
    c.toggle()
    c.toggle()
    return c.transcriber.jobs[-1]


def test_workflow_and_feedback_only_after_copy(qtbot):
    c = controller()
    notifications = []
    c.copied.connect(lambda: notifications.append("copied"))
    transcription = record(c)
    assert c.state == State.TRANSCRIBING
    c.toggle()
    assert c.recorder.starts == 1
    transcription.succeeded.emit(Transcript("synthetic text"))
    assert c.state == State.COPYING and c.clip is None
    assert not notifications
    c.clipboard.jobs[-1].succeeded.emit(None)
    assert c.state == State.COPIED
    assert notifications == ["copied"] and c.transcript is None
    c.success_timer.start(1)
    qtbot.waitUntil(lambda: c.state == State.IDLE)


def test_cancel_prevents_stale_transcript_from_copying(qtbot):
    c = controller()
    old = record(c)
    c.cancel()
    assert old.cancelled and c.clip is None
    current = record(c)
    old.succeeded.emit(Transcript("obsolete"))
    assert c.state == State.TRANSCRIBING
    assert not c.clipboard.jobs
    current.succeeded.emit(Transcript("current"))
    assert c.clipboard.inputs == ["current"]
    c.cancel()


def test_copy_retry_does_not_retranscribe(qtbot):
    c = controller()
    record(c).succeeded.emit(Transcript("synthetic text"))
    c.clipboard.jobs[-1].failed.emit(
        AppError(ErrorCode.CLIPBOARD, "copy failed", "clipboard", True)
    )
    assert c.can_retry
    c.retry()
    assert c.state == State.COPYING
    assert len(c.transcriber.jobs) == 1
    assert len(c.clipboard.jobs) == 2
    c.clipboard.jobs[-1].succeeded.emit(None)
    assert c.state == State.COPIED
    c.cancel()


def test_retryable_failure_retains_audio_temporarily(qtbot):
    c = controller()
    record(c).failed.emit(AppError(ErrorCode.NETWORK, "offline", "transcription", True))
    assert c.can_retry and c.clip is not None
    c.retry()
    assert len(c.transcriber.jobs) == 2
    c.transcriber.jobs[-1].failed.emit(
        AppError(ErrorCode.NETWORK, "offline", "transcription", True)
    )
    c.retention_timer.start(1)
    qtbot.waitUntil(lambda: c.state == State.IDLE)
    assert c.clip is None and not c.can_retry


def test_nonretryable_failure_discards_audio(qtbot):
    c = controller()
    record(c).failed.emit(AppError(ErrorCode.AUTH_INVALID, "sign in", "authentication"))
    assert c.state == State.ERROR and not c.can_retry
    assert c.clip is None
    c.cancel()


def test_shutdown_cancels_job_and_ignores_callbacks(qtbot):
    c = controller()
    job = record(c)
    c.shutdown()
    job.succeeded.emit(Transcript("late"))
    c.toggle()
    assert not c.clipboard.jobs and c.transcriber.closed
    assert job.cancelled and c.recorder.starts == 1


def test_auto_paste_requested_only_after_successful_copy(qtbot):
    c = controller()
    c.settings.auto_paste = True
    requested = []
    c.paste_requested.connect(requested.append)
    record(c).succeeded.emit(Transcript("synthetic text"))
    assert not requested
    c.clipboard.jobs[-1].succeeded.emit(None)
    assert requested == ["synthetic text"]
    assert c.state == State.COPIED and c.transcript is None
    c.cancel()
