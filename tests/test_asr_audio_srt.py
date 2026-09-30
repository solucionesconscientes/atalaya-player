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


def test_split_long_even_pieces_cut_at_punctuation():
    # old: 84 characters + a 44-character leftover cut mid-phrase ("… con los" / "hermanos Lumière y sigue.")
    text = ("Hoy vamos a hablar de la historia del cine español, que empieza a finales del siglo diecinueve con los "
            "hermanos Lumière y sigue.")
    pieces = split_long(Segment(0, 9, text))
    assert [p.text for p in pieces] == ["Hoy vamos a hablar de la historia del cine español,",
                                        "que empieza a finales del siglo diecinueve con los hermanos Lumière y sigue."]
    assert pieces[0].start == 0 and pieces[-1].end == 9 and pieces[0].end == pieces[1].start
    # no punctuation at all: equal halves instead of 84 + leftover
    words = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete"
    pieces = split_long(Segment(0, 6, words + " dieciocho diecinueve"))
    assert len(pieces) == 2 and abs(len(pieces[0].text) - len(pieces[1].text)) <= 10
    # short but long in time and sparse: not chopped into tiny pieces
    assert len(split_long(Segment(0, 20, "Sí, claro que sí, ya voy."))) == 1


def test_chunk_plan_prompt_and_legacy_state():
    from mpvd.asr.service import DEFAULT_CHUNK, PRE_ROLL, TAIL, AsrTask, plan_chunks, prompt_tail

    assert DEFAULT_CHUNK == 28.5 and DEFAULT_CHUNK + PRE_ROLL + TAIL <= 30.0     # one 30 s whisper window per chunk
    assert plan_chunks(60.0, 28.5) == [(0.0, 28.5), (28.5, 57.0), (57.0, 60.0)]
    assert plan_chunks(58.0, 28.5) == [(0.0, 28.5), (28.5, 58.0)]              # 1 s tail absorbed into the last chunk
    tail = prompt_tail("palabra " * 60)
    assert len(tail) <= 200 and tail.split()[0] == "palabra" and not tail.startswith(" ")

    t = AsrTask(id="t", key="k", path="/x", duration=100.0, model="base", language="es", chunk_seconds=28.5)
    t.plan()
    t.segments = [Segment(1, 5, "Hola, ¿qué tal?"), Segment(20, 27, "Muy bien, gracias."), Segment(30, 33, "Otra.")]
    assert t.prompt_for(0) is None and t.prompt_for(1) is None          # chunk 0 not transcribed yet
    t.done = {0}
    assert t.prompt_for(1) == "Hola, ¿qué tal? Muy bien, gracias."      # only chunk 0's text, not chunk 1's
    assert t.prompt_for(2) is None

    # state cached with the old 20 s plan: [0,20) [20,40) [40,60) done → only new chunks fully inside are kept
    old = {"segments": [s.to_dict() for s in t.segments], "done": [0, 1, 2], "detected": "es", "chunk_seconds": 20.0,
           "duration": 100.0, "complete": False, "saved": [{"path": "/v/a.es.srt", "complete": False}]}
    n = AsrTask(id="n", key="k", path="/x", duration=100.0, model="base", language="es", chunk_seconds=28.5)
    n.plan()
    n.load_state(old)
    assert n.chunks[:2] == [(0.0, 28.5), (28.5, 57.0)] and n.done == {0, 1} and n.detected == "es"
    assert n.saved == [{"path": "/v/a.es.srt", "complete": False}] and len(n.segments) == 3
    n.load_state({**old, "complete": True})
    assert n.complete


def test_the_pre_roll_does_not_duplicate_lines_nor_write_cues_backwards():
    """H34 · the chunk is transcribed with 0.8 s of context before it: that speech belongs to the previous chunk."""
    from mpvd.asr.srt import Segment, merge_segments

    # the previous chunk (0 → 28.5) heard the line that crosses the border; this one (28.5 → 57) hears it again
    existing = [Segment(20.0, 22.0, "Antes de nada."), Segment(27.8, 28.9, "y entonces llegó a casa")]
    new = [Segment(27.7, 28.4, "llegó a casa"),          # entirely inside the pre-roll
           Segment(27.8, 29.2, "y entonces llegó a casa"),  # the same line, heard again across the border
           Segment(30.0, 32.0, "Abrió la puerta.")]
    merged = merge_segments(existing, new, 28.5, 57.0)

    assert all(s.end > s.start for s in merged), [(s.start, s.end, s.text) for s in merged]
    assert [s.text for s in merged].count("y entonces llegó a casa") == 1
    assert "llegó a casa" not in [s.text for s in merged]          # the pre-roll-only copy is gone
    assert [s.text for s in merged] == ["Antes de nada.", "y entonces llegó a casa", "Abrió la puerta."]
    assert merged[1].start == 27.8                                  # kept the earlier, truer start


def test_a_cue_only_in_the_pre_roll_is_dropped_instead_of_being_inverted():
    from mpvd.asr.srt import Segment, merge_segments, render_srt

    merged = merge_segments([], [Segment(27.9, 28.3, "Sí.")], 28.5, 57.0)
    assert merged == []
    assert render_srt(merge_segments([], [Segment(28.0, 29.0, "Sí, claro.")], 28.5, 57.0)).count("-->") == 1
