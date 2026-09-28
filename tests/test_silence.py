import struct

import pytest

from codex_stt_tray.models import AppError, ErrorCode
from codex_stt_tray.silence import EdgeSilence, TrimOptions

RATE = 16000


def pcm(seconds, amplitude=0):
    return struct.pack("<h", amplitude) * int(seconds * RATE)


def trim(data, options=None, chunk_size=791):
    detector = EdgeSilence(RATE, options or TrimOptions())
    for offset in range(0, len(data), chunk_size):
        detector.feed(data[offset : offset + chunk_size])
    start, end = detector.bounds()
    return data[start:end]


def test_only_edges_are_trimmed_and_long_internal_pause_is_preserved():
    middle = pcm(0.5, 1000) + pcm(2) + pcm(0.5, 1000)
    data = pcm(1) + middle + pcm(1)
    assert trim(data) == pcm(0.3) + middle + pcm(0.3)


def test_quiet_speech_is_kept_and_short_edges_are_not_touched():
    data = pcm(0.2) + pcm(0.5, 80) + pcm(0.2)
    assert trim(data) == data


def test_padding_and_threshold_are_configurable():
    data = pcm(1, 10) + pcm(0.5, 100) + pcm(1, 10)
    assert trim(data, TrimOptions(padding_ms=500)) == pcm(0.5, 10) + pcm(0.5, 100) + pcm(0.5, 10)
    with pytest.raises(AppError) as failure:
        trim(data, TrimOptions(threshold_db=-40))
    assert failure.value.code == ErrorCode.NO_SPEECH


@pytest.mark.parametrize("data", [b"", pcm(2), pcm(1, 5)])
def test_silence_has_a_local_structured_error(data):
    with pytest.raises(AppError, match="No speech detected"):
        trim(data)


def test_partial_final_frame_preserves_a_quiet_ending():
    data = pcm(1) + pcm(0.113, 80)
    assert trim(data) == pcm(0.3) + pcm(0.113, 80)
