"""ASR building blocks that need no model: ffmpeg window extraction and incremental SRT merging."""

import asyncio
import wave

from mpvd.asr.audio import extract_wav, probe_duration, wav_duration
from mpvd.asr.srt import Segment, merge_segments, parse_srt, render_srt, split_long, srt_time


def test_extract_window_16k_mono(media_dir, tmp_path):
    out = asyncio.run(extract_wav(str(media_dir / "voz_es_en.mkv"), 2.0, 3.0, tmp_path / "w.wav", audio_track=1))
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000 and w.getsampwidth() == 2
        assert abs(w.getnframes() / 16000 - 3.0) < 0.1
    assert abs(wav_duration(out) - 3.0) < 0.1
    d = probe_duration(str(media_dir / "voz_es.flac"))
    assert d is not None and 11 < d < 12.5
    # window past the end → short or empty output is an error only when nothing was produced
    tail = asyncio.run(extract_wav(str(media_dir / "voz_es.flac"), 10.0, 5.0, tmp_path / "t.wav"))
    assert 1.0 < wav_duration(tail) < 2.5


def test_srt_render_parse_and_merge():
    assert srt_time(3661.5) == "01:01:01,500" and srt_time(0) == "00:00:00,000"
    a = [Segment(0, 2, "Hola"), Segment(2, 4, "mundo")]
    text = render_srt(a)
    assert text.startswith("1\n00:00:00,000 --> 00:00:02,000\nHola\n\n2\n")
    assert [s.text for s in parse_srt(text)] == ["Hola", "mundo"]
    # a later chunk [3, 8) replaces what overlapped it and trims the previous cue
    merged = merge_segments(a, [Segment(3.5, 6, "otra vez"), Segment(6, 7.5, "[BLANK_AUDIO]")], 3.0, 8.0)
    assert [s.text for s in merged] == ["Hola", "mundo", "otra vez"]  # "mundo" started before the chunk: kept, trimmed
    assert merged[0].end == 2 and merged[1].end <= 3.5 and "[BLANK_AUDIO]" not in render_srt(merged)
    # an earlier chunk arriving late is inserted in order; overlaps are trimmed, never stacked
    merged = merge_segments(merged, [Segment(1.5, 3.4, "tarde")], 1.0, 3.0)
    assert [s.text for s in merged] == ["Hola", "tarde", "otra vez"]  # "mundo" started inside [1,3) → replaced
    assert merged[0].end <= merged[1].start and merged[1].end <= merged[2].start
    # long segments are split into readable pieces
    long = Segment(0, 20, " ".join(["palabra"] * 40))
    pieces = split_long(long)
    assert len(pieces) > 2 and all(len(p.text) <= 84 for p in pieces) and abs(pieces[-1].end - 20) < 1e-6
