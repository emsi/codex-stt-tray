"""Bounded PCM capture with an in-memory WAV handoff."""

import io
import wave

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource, QMediaDevices

from .constants import MAX_AUDIO_BYTES, MAX_RECORDING_SECONDS
from .models import AppError, AudioClip, ErrorCode


def wav_clip(pcm: bytes, rate: int, channels: int = 1) -> AudioClip:
    frame_size = 2 * channels
    pcm = pcm[: len(pcm) - len(pcm) % frame_size]
    duration = len(pcm) / (rate * frame_size)
    if duration < 0.1:
        raise AppError(ErrorCode.EMPTY_AUDIO, "Recording was too short.", "audio")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return AudioClip(output.getvalue(), duration)


def selected_device(device_id: bytes = b""):
    device = QMediaDevices.defaultAudioInput()
    if device_id:
        device = next((d for d in QMediaDevices.audioInputs() if bytes(d.id()) == device_id), None)
    if device is None or device.isNull():
        raise AppError(
            ErrorCode.AUDIO_DEVICE,
            "Selected microphone is unavailable. Open Settings to choose one.",
            "audio",
        )
    return device


def recording_format(device) -> QAudioFormat:
    preferred_rate = device.preferredFormat().sampleRate()
    for rate in dict.fromkeys((preferred_rate, 24000, 48000, 44100, 16000)):
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isFormatSupported(fmt):
            return fmt
    raise AppError(
        ErrorCode.AUDIO_FORMAT,
        "Microphone does not support mono 16-bit PCM recording.",
        "audio",
    )


class Recorder(QObject):
    clip_ready = Signal(object)
    failed = Signal(object)
    elapsed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.starting = False
        self.stream = None
        self.pcm = bytearray()
        self.rate = 24000
        self.device_id = b""
        self.duration_limit = MAX_RECORDING_SECONDS
        self.byte_limit = MAX_AUDIO_BYTES - 44
        self.clock = QElapsedTimer()
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._tick)
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self._devices_changed)

    def start(self, device_id: bytes = b"", duration_limit: int = MAX_RECORDING_SECONDS):
        self.cancel()
        device = selected_device(device_id)
        fmt = recording_format(device)
        self.rate = fmt.sampleRate()
        self.device_id = bytes(device.id())
        self.duration_limit = min(MAX_RECORDING_SECONDS, max(1, duration_limit))
        self.byte_limit = min(MAX_AUDIO_BYTES - 44, self.duration_limit * self.rate * 2)
        source = QAudioSource(device, fmt, self)
        self.source = source
        source.stateChanged.connect(self._audio_state)
        self.starting = True
        try:
            self.stream = source.start()
        finally:
            self.starting = False
        if (
            self.source is None
            or self.stream is None
            or self.source.error() != QAudio.Error.NoError
        ):
            self.cancel()
            raise AppError(
                ErrorCode.AUDIO_DEVICE,
                "Cannot start microphone capture. Check the audio service and permissions, "
                "or choose another microphone in Settings.",
                "audio",
            )
        self.stream.readyRead.connect(self._audio_ready)
        self.clock.start()
        self.timer.start()

    def _drain(self):
        if self.stream is None:
            return
        while self.stream.bytesAvailable() and len(self.pcm) < self.byte_limit:
            chunk = bytes(self.stream.read(min(65536, self.byte_limit - len(self.pcm))))
            if not chunk:
                break
            self.pcm.extend(chunk)
        if len(self.pcm) >= self.byte_limit:
            source = self.source
            QTimer.singleShot(0, lambda: self.stop() if self.source is source else None)

    def _audio_ready(self):
        if self.sender() is self.stream:
            self._drain()

    def _tick(self):
        seconds = self.clock.elapsed() // 1000
        self.elapsed.emit(seconds)
        if seconds >= self.duration_limit:
            self.stop()

    def _audio_state(self, state):
        source = self.sender()
        if (
            not self.starting
            and self.source is source
            and state == QAudio.State.StoppedState
            and source.error() != QAudio.Error.NoError
        ):
            # Leave the backend's stateChanged stack before stopping its device.
            QTimer.singleShot(0, lambda: self._capture_failed(source))

    def _capture_failed(self, source):
        if self.source is source:
            self.cancel()
            self.failed.emit(
                AppError(
                    ErrorCode.AUDIO_DEVICE,
                    "Microphone capture stopped unexpectedly.",
                    "audio",
                )
            )

    def _devices_changed(self):
        if self.source and self.device_id not in {
            bytes(d.id()) for d in QMediaDevices.audioInputs()
        }:
            self.cancel()
            self.failed.emit(AppError(ErrorCode.AUDIO_DEVICE, "Microphone disconnected.", "audio"))

    def _release_source(self):
        self.timer.stop()
        source, self.source = self.source, None
        self.stream = None
        if source:
            source.stop()
            source.deleteLater()

    def stop(self):
        if self.source is None:
            return
        self._drain()
        self._release_source()
        try:
            clip = wav_clip(bytes(self.pcm), self.rate)
        except AppError as error:
            self.failed.emit(error)
        else:
            self.clip_ready.emit(clip)
        finally:
            self.pcm.clear()

    def cancel(self):
        self._release_source()
        self.pcm.clear()
