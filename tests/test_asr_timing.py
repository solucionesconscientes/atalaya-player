"""H16 · subtitle timing from word timestamps (mpvd/asr/timing.py): tokens → words, energy VAD, cuts at real word times,
snapping to the voice and reading rules. Pure unit tests (the real whisper run is in test_asr_timing_real.py)."""

from __future__ import annotations

import math
import wave
from array import array

from mpvd.asr.timing import (MAX_CHARS, MIN_DURATION, MIN_GAP, Word, apply_reading_rules, build_cues, group_words,
                             snap_end, snap_start, speech_regions, words_from_cli_json)
from mpvd.asr.srt import Segment


def tok(text, a, b, dtw=-1):
    return {"text": text, "offsets": {"from": a, "to": b}, "t_dtw": dtw}


def test_words_from_tokens_skip_specials():
    data = {"transcription": [
        {"tokens": [tok("[_BEG_]", 0, 0), tok(" Hola", 100, 400), tok(",", 400, 450), tok(" mun", 500, 700),
                    tok("do", 700, 900), tok(".", 900, 950), tok("[_TT_45]", 950, 950)]},
        {"tokens": [tok(" Adiós", 2000, 2400), tok(" [BLANK_AUDIO]", 2400, 2500)]},
    ]}
    words = words_from_cli_json(data)
    assert [(w.text, round(w.start, 2), round(w.end, 2)) for w in words] == [
        ("Hola,", 0.1, 0.45), ("mundo.", 0.5, 0.95), ("Adiós", 2.0, 2.4)]


def test_dtw_time_marks_the_end_of_a_token():
    data = {"transcription": [{"tokens": [tok("[_BEG_]", 0, 0), tok(" Bien", 10, 520, 14), tok("ven", 540, 870, 44),
                                          tok(" a", 1310, 1430, 72), tok(" casa", 1430, 1900, 120)]}]}
    words = words_from_cli_json(data)
    assert [(w.text, round(w.start, 2), round(w.end, 2)) for w in words] == [
        ("Bienven", 0.01, 0.44), ("a", 0.44, 0.72), ("casa", 0.72, 1.2)]


def test_speech_stretch_boundaries_move_words_to_the_right_stretch():
    from mpvd.asr.timing import remap_words
    # VAD time: stretch 1 = 0–1.41 (orig 0.83–2.24), stretch 2 = 1.61–4.87 (orig 4.16–7.42); token times drift
    table = [(0.83, 2.24, 0.0, 1.41), (4.16, 7.42, 1.61, 4.87)]
    words = [W("Buenos", 0.0, 0.74), W("días", 0.74, 1.19), W("a", 1.23, 1.31), W("todos.", 1.65, 2.15),
             W("Hoy", 2.15, 2.28), W("vamos", 2.32, 2.59)]
    out = remap_words(words, table)
    assert [round(w.start, 2) for w in out[:4]] == [0.83, 1.57, 2.06, 2.24] and round(out[3].end, 2) == 2.24
    assert out[4].start == 4.16                   # the stretch after the gap starts with "Hoy"
    cues = build_cues(out, [(0.83, 2.24), (4.16, 7.42)])
    assert [c.text for c in cues] == ["Buenos días a todos.", "Hoy vamos"]
    assert cues[0].start == 0.83 and cues[1].start == 4.16


def W(text, a, b):
    return Word(a, b, text)


def test_pause_and_sentence_end_start_new_cues():
    words = [W("Hola.", 0.0, 0.4), W("¿Qué", 0.7, 0.9), W("tal?", 0.9, 1.2), W("Bien", 2.0, 2.3)]
    groups = group_words(words)
    assert [[w.text for w in g] for g in groups] == [["Hola."], ["¿Qué", "tal?"], ["Bien"]]


def test_long_line_is_cut_at_a_real_word_time_and_a_good_boundary():
    text = ("Ayer fui al mercado con mi hermana para comprar fruta fresca, y después volvimos a casa "
            "caminando por el parque").split()
    words = [W(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate(text)]
    groups = group_words(words)
    assert len(groups) == 2 and all(len(" ".join(w.text for w in g)) <= MAX_CHARS for g in groups)
    assert groups[0][-1].text == "fresca,"                      # the comma beats the character target
    cues = build_cues(words)
    assert math.isclose(cues[1].start, words[len(groups[0])].start)  # the cut uses the word's own time


def test_never_ends_a_line_on_an_article_or_preposition():
    text = "Esta noche vamos a cenar en la casa de los abuelos que viven cerca del río grande y del puente".split()
    words = [W(t, i * 0.35, i * 0.35 + 0.3) for i, t in enumerate(text)]
    for g in group_words(words)[:-1]:
        assert g[-1].text.lower() not in {"a", "en", "la", "de", "los", "del", "y", "que"}


def test_snap_to_voice_onset_and_offset():
    regions = [(1.0, 2.5), (4.0, 5.0)]
    assert snap_start(1.2, regions) == 1.0        # inside, voice started a bit earlier
    assert snap_start(3.8, regions) == 4.0        # in silence just before the onset
    assert snap_start(3.0, regions) == 3.0        # too far from any onset
    assert snap_end(2.3, regions) == 2.5
    assert snap_end(2.7, regions) == 2.5          # in silence just after the offset
    assert snap_end(3.3, regions) == 3.3


def test_reading_rules_min_duration_cps_gap_and_no_overlap():
    cues = [Segment(0.0, 0.3, "Sí."), Segment(0.5, 1.0, "Esto es una frase bastante larga para leer"),
            Segment(1.1, 1.5, "Vale")]
    out = apply_reading_rules(cues, limit=3.0)
    assert out[0].end == 0.5 - MIN_GAP             # wants MIN_DURATION, but the next cue comes first
    assert out[1].end == 1.1 - MIN_GAP             # wants 42/17 s, capped by the next cue
    assert math.isclose(out[2].end, 1.1 + MIN_DURATION)
    for a, b in zip(out, out[1:]):
        assert a.end <= b.start


def test_speech_regions_on_synthetic_tone_bursts(tmp_path):
    rate = 16000
    samples = array("h")
    bursts = [(0.5, 1.5), (2.2, 3.0)]
    for i in range(int(rate * 4.0)):
        t = i / rate
        on = any(a <= t < b for a, b in bursts)
        samples.append(int((8000 if on else 30) * math.sin(2 * math.pi * 220 * t)))
    path = tmp_path / "b.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    regions = speech_regions(path)
    assert len(regions) == 2
    for (a, b), (ra, rb) in zip(bursts, regions):
        assert abs(a - ra) <= 0.03 and abs(b - rb) <= 0.03
