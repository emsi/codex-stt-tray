"""Conservative edge detection for mono Int16 PCM; interior pauses are never edited."""

import sys
from array import array
from dataclasses import dataclass

from .models import AppError, ErrorCode


@dataclass(frozen=True)
class TrimOptions:
    enabled: bool = True
    threshold_db: int = -55
    padding_ms: int = 300


class EdgeSilence:
    """Analyze small frames as audio arrives, avoiding a full scan at Stop."""

    def __init__(self, rate: int, options: TrimOptions):
        self.rate = rate
        self.frame_bytes = max(1, rate // 50) * 2  # 20 ms
        self.threshold_squared = (32768 * 10 ** (options.threshold_db / 20)) ** 2
        self.padding = rate * options.padding_ms // 1000
        self.minimum_edge = rate // 2  # Leave silence shorter than 500 ms alone.
        self.pending = bytearray()
        self.position = 0
        self.first = None
        self.last = 0

    def feed(self, pcm):
        self.pending.extend(pcm)
        end = len(self.pending) // self.frame_bytes * self.frame_bytes
        for offset in range(0, end, self.frame_bytes):
            self._frame(self.pending[offset : offset + self.frame_bytes])
        del self.pending[:end]

    def _frame(self, pcm):
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        count = len(samples)
        if count and sum(sample * sample for sample in samples) >= self.threshold_squared * count:
            if self.first is None:
                self.first = self.position
            self.last = self.position + count
        self.position += count

    def bounds(self):
        self._frame(self.pending[: len(self.pending) // 2 * 2])
        self.pending.clear()
        if self.first is None:
            raise AppError(
                ErrorCode.NO_SPEECH,
                "No speech detected. For quiet speech, lower the silence threshold "
                "or disable trimming in Settings.",
                "audio",
            )
        start = max(0, self.first - self.padding) if self.first >= self.minimum_edge else 0
        end = (
            min(self.position, self.last + self.padding)
            if self.position - self.last >= self.minimum_edge
            else self.position
        )
        return start * 2, end * 2
