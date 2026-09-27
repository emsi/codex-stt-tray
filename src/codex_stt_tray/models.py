"""Small value types shared by the UI and services; no Qt dependency."""

from dataclasses import dataclass, field
from enum import StrEnum, auto


class State(StrEnum):
    IDLE = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()
    COPYING = auto()
    COPIED = auto()
    ERROR = auto()


class ErrorCode(StrEnum):
    INTERNAL = auto()
    AUDIO_DEVICE = auto()
    AUDIO_FORMAT = auto()
    EMPTY_AUDIO = auto()
    AUDIO_LIMIT = auto()
    AUTH_MISSING = auto()
    AUTH_PERMISSIONS = auto()
    AUTH_INVALID = auto()
    AUTH_REFRESH = auto()
    NETWORK = auto()
    HTTP = auto()
    TIMEOUT = auto()
    RESPONSE_INVALID = auto()
    RESPONSE_LIMIT = auto()
    CLIPBOARD = auto()
    PASTE = auto()


@dataclass
class AppError(Exception):
    code: ErrorCode
    message: str
    stage: str
    retryable: bool = False
    http_status: int | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class AudioClip:
    data: bytes = field(repr=False)
    duration_seconds: float
    mime_type: str = "audio/wav"
    filename: str = "recording.wav"


@dataclass(frozen=True)
class Transcript:
    text: str = field(repr=False)
