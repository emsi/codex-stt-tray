import io
import wave

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QObject, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QtAudio

from codex_stt_tray import recorder
from codex_stt_tray.constants import MAX_AUDIO_BYTES
from codex_stt_tray.models import AppError, ErrorCode
from codex_stt_tray.recorder import Recorder, wav_clip
from codex_stt_tray.silence import TrimOptions


def test_wav_duration_and_framing():
    clip = wav_clip(b"\x00\x00" * 2400 + b"\x00", 24000)
    with wave.open(io.BytesIO(clip.data)) as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24000
        assert audio.getnframes() == 2400
    assert clip.duration_seconds == 0.1
    assert "\\x00" not in repr(clip)


def test_reject_short_recording():
    with pytest.raises(AppError) as error:
        wav_clip(b"\0" * 100, 24000)
    assert error.value.code == ErrorCode.EMPTY_AUDIO


class Device:
    def preferredFormat(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(24000)
        return fmt

    def isNull(self):
        return False

    def id(self):
        return b"test-microphone"

    def isFormatSupported(self, fmt):
        return fmt.sampleRate() == 24000


class Devices(QObject):
    audioInputsChanged = Signal()
    inputs = [Device()]

    @staticmethod
    def defaultAudioInput():
        return Device()

    @classmethod
    def audioInputs(cls):
        return cls.inputs


class Source(QObject):
    stateChanged = Signal(object)

    def __init__(self, device, fmt, parent):
        super().__init__(parent)
        self.buffer = QBuffer(self)
        self.buffer.setData(b"\0" * 100_000)
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)

    def start(self):
        return self.buffer

    def error(self):
        return QtAudio.Error.NoError

    def state(self):
        return QtAudio.State.StoppedState

    def stop(self):
        pass


@pytest.fixture
def fake_capture(monkeypatch):
    monkeypatch.setattr(recorder, "QMediaDevices", Devices)
    monkeypatch.setattr(recorder, "QAudioSource", Source)
    monkeypatch.setattr(Devices, "inputs", [Device()])


def test_duration_limit_caps_buffer_and_auto_stops(qtbot, fake_capture):
    capture = Recorder()
    clips = []
    capture.clip_ready.connect(clips.append)
    capture.start(duration_limit=1)
    capture._drain()
    assert len(capture.pcm) == 48_000
    qtbot.waitUntil(lambda: len(clips) == 1)
    assert clips[0].duration_seconds == 1
    assert capture.source is None and not capture.pcm


def test_cancel_discards_and_does_not_emit(qtbot, fake_capture):
    capture = Recorder()
    clips = []
    capture.clip_ready.connect(clips.append)
    capture.start()
    capture._drain()
    capture.cancel()
    capture.stop()
    assert not clips and not capture.pcm


def test_silent_recording_is_not_submitted_when_trimming_enabled(qtbot, fake_capture):
    capture = Recorder()
    capture.trim_options = TrimOptions()
    clips, errors = [], []
    capture.clip_ready.connect(clips.append)
    capture.failed.connect(errors.append)
    capture.start()
    capture.stop()
    assert not clips
    assert errors[0].code == ErrorCode.NO_SPEECH
    assert not capture.pcm and capture.trimmer is None


def test_fifty_minute_capture_is_not_cut_short_by_byte_limit(qtbot, fake_capture, monkeypatch):
    class Device48k(Device):
        def preferredFormat(self):
            fmt = super().preferredFormat()
            fmt.setSampleRate(48000)
            return fmt

        def isFormatSupported(self, fmt):
            return fmt.sampleRate() == 48000

    monkeypatch.setattr(Devices, "defaultAudioInput", Device48k)
    capture = Recorder()
    capture.start(duration_limit=3000)
    assert capture.duration_limit == 3000
    assert capture.byte_limit == 3000 * 48000 * 2
    assert capture.byte_limit + 44 <= MAX_AUDIO_BYTES
    capture.cancel()


def test_high_rate_microphone_uses_bounded_speech_format():
    class HighRateDevice(Device):
        def preferredFormat(self):
            fmt = super().preferredFormat()
            fmt.setSampleRate(192000)
            return fmt

        def isFormatSupported(self, fmt):
            return True

    assert recorder.recording_format(HighRateDevice()).sampleRate() == 24000


def test_disconnection_fails_and_cleans_up(qtbot, fake_capture, monkeypatch):
    capture = Recorder()
    errors = []
    capture.failed.connect(errors.append)
    capture.start()
    monkeypatch.setattr(Devices, "inputs", [])
    capture.devices.audioInputsChanged.emit()
    assert errors[0].code == ErrorCode.AUDIO_DEVICE
    assert capture.source is None


def test_selected_missing_device_is_not_silently_replaced(qtbot, fake_capture):
    capture = Recorder()
    with pytest.raises(AppError):
        capture.start(b"missing")
    assert capture.source is None


def test_synchronous_start_failure_emits_no_duplicate_error(qtbot, fake_capture, monkeypatch):
    class FailingSource(Source):
        def start(self):
            self.stateChanged.emit(QtAudio.State.StoppedState)
            return None

        def error(self):
            return QtAudio.Error.OpenError

    monkeypatch.setattr(recorder, "QAudioSource", FailingSource)
    capture = Recorder()
    errors = []
    capture.failed.connect(errors.append)
    for _ in range(2):
        with pytest.raises(AppError, match="Qt OpenError"):
            capture.start()
        assert capture.source is None
    assert not errors


def test_old_source_error_cannot_stop_new_capture(qtbot, fake_capture, monkeypatch):
    capture = Recorder()
    capture.start()
    old = capture.source
    capture.start()
    current = capture.source
    capture._capture_failed(old)
    assert capture.source is current
    capture.cancel()


@pytest.mark.parametrize("signal_state", [QtAudio.State.StoppedState, QAudio.State.StoppedState])
def test_native_error_state_stops_capture(qtbot, fake_capture, monkeypatch, signal_state):
    class InterruptedSource(Source):
        current_error = QtAudio.Error.NoError

        def error(self):
            return self.current_error

        def stop(self):
            # Qt clears the error on stop; diagnostics must snapshot it first.
            self.current_error = QtAudio.Error.NoError

    monkeypatch.setattr(recorder, "QAudioSource", InterruptedSource)
    capture = Recorder()
    errors = []
    capture.failed.connect(errors.append)
    capture.start()
    capture.source.current_error = QtAudio.Error.IOError
    # The native signal retains the legacy enum name; the accessor returns QtAudio.
    capture.source.stateChanged.emit(signal_state)
    qtbot.waitUntil(lambda: len(errors) == 1)
    assert capture.source is None and not capture.pcm
    assert "Qt IOError" in errors[0].message
