"""Subtitle loading (SRT/VTT/ASS, encodings) and resynchronisation against a reference transcription: constant delay,
drift, a cut in the middle, noisy wording, and the no-match case."""

from __future__ import annotations

import random

from mpvd.asr.srt import Segment, render_srt
from mpvd.subs.formats import load_cues, parse_ass, parse_vtt
from mpvd.subs.resync import best_chain, candidates, resync, similarity, tokens

SENTENCES = [
    "Bienvenido a MPV-UOS, el reproductor del futuro.", "El rápido zorro marrón salta sobre el perro perezoso.",
    "Hoy es un buen día para ver una película con subtítulos.", "La nave despegó al amanecer rumbo a Marte.",
    "Nadie esperaba encontrar agua bajo la superficie.", "El capitán ordenó revisar los motores principales.",
    "Una tormenta de arena cubrió la base durante tres días.", "Los ingenieros trabajaron sin descanso toda la noche.",
    "Al final, la señal llegó desde la Tierra con buenas noticias.", "Volverían a casa antes del invierno.",
    "El doctor guardó las muestras en la cámara fría.", "Cada uno escribió una carta para su familia.",
    "El silencio del espacio pesaba más que cualquier palabra.", "Encendieron las luces del hangar por última vez.",
    "Y así terminó la primera misión tripulada.", "Los créditos empiezan ahora, gracias por ver.",
]


def reference(start: float = 1.0, gap: float = 4.0) -> list[Segment]:
    out, t = [], start
    for s in SENTENCES:
        out.append(Segment(t, t + 3.0, s))
        t += gap
    return out


def shifted(ref, offset, factor=1.0, noise=0.0, seed=1):
    rnd = random.Random(seed)
    out = []
    for c in ref:
        words = c.text.split()
        if noise:
            words = [w for w in words if rnd.random() > noise]
            if rnd.random() < noise:
                words.append("eh")
        out.append(Segment(c.start * factor + offset, c.end * factor + offset, " ".join(words) or c.text))
    return out


def test_tokens_and_similarity():
    a, b = tokens("El rápido zorro marrón"), tokens("el rapido zorro, marron!")
    assert a == b and similarity(a, b) == 1.0 and "el" not in a
    assert similarity(tokens("hola mundo"), tokens("adiós mundo")) > 0.4 > similarity(tokens("uno dos"), tokens("tres"))


def test_constant_delay_is_recovered():
    ref = reference()
    cues = shifted(ref, -2.5)            # subtitles show 2.5 s too early
    res = resync(cues, ref)
    assert res.stats["ok"] and res.stats["matched"] == len(cues)
    assert abs(res.stats["offset_median"] - 2.5) < 0.05
    for a, b in zip(res.cues, ref, strict=True):
        assert abs(a.start - b.start) < 0.05 and a.text == b.text


def test_drift_and_noise_are_corrected_piecewise():
    ref = reference(gap=20.0)            # ~5 minutes of dialogue
    cues = shifted(ref, 1.0, factor=1.04, noise=0.2)   # 4 % drift (25 vs 24 fps) + wording differences
    res = resync(cues, ref)
    assert res.stats["ok"] and res.stats["coverage"] >= 0.8 and len(res.knots) >= 2
    assert res.stats["drift_ppm"] < -20000            # negative: cues have to move back more and more
    errors = [abs(a.start - b.start) for a, b in zip(res.cues, ref, strict=True)]
    assert max(errors) < 1.5 and sum(errors) / len(errors) < 0.6, errors


def test_cut_in_the_middle_gives_two_offsets():
    ref = reference(gap=30.0)
    cues = []
    for k, c in enumerate(ref):
        off = 0.0 if k < 8 else 12.0    # an advert of 12 s was cut from the video after sentence 8
        cues.append(Segment(c.start + off, c.end + off, c.text))
    res = resync(cues, ref)
    assert res.stats["ok"]
    first, last = res.cues[0], res.cues[-1]
    assert abs(first.start - ref[0].start) < 0.5 and abs(last.start - ref[-1].start) < 1.0
    assert res.stats["offset_first"] > -2 and res.stats["offset_last"] < -10


def test_no_overlap_and_no_match():
    ref = reference()
    unrelated = [Segment(1 + 4 * i, 3 + 4 * i, f"lorem ipsum dolor {i}") for i in range(10)]
    res = resync(unrelated, ref)
    assert not res.stats["ok"] and res.cues == unrelated
    # the chain is monotonic even with repeated sentences in the reference
    rep = ref + [Segment(200 + i * 4, 202 + i * 4, s) for i, s in enumerate(SENTENCES[:4])]
    ch = best_chain(candidates(ref, rep), len(rep))
    assert all(ch[i].ref <= ch[i + 1].ref and ch[i].cue < ch[i + 1].cue for i in range(len(ch) - 1))


def test_load_srt_vtt_ass_and_encodings(tmp_path):
    ref = reference()
    srt = tmp_path / "a.srt"
    srt.write_text("﻿" + render_srt([Segment(c.start, c.end, f"<i>{c.text}</i>") for c in ref[:3]]), encoding="utf-8")
    cues = load_cues(srt)
    assert [c.text for c in cues] == [c.text for c in ref[:3]]     # BOM and <i> tags removed
    latin = tmp_path / "b.srt"
    latin.write_bytes(render_srt(ref[:2]).encode("cp1252"))
    assert load_cues(latin)[1].text == ref[1].text
    vtt = "WEBVTT\n\nNOTE hola\n\n1\n00:00:01.000 --> 00:00:04.000 align:start\nHola <b>mundo</b>\n\n00:05.500 --> 00:07.000\nSegunda\n"
    v = parse_vtt(vtt)
    assert [(c.start, c.end, c.text) for c in v] == [(1.0, 4.0, "Hola mundo"), (5.5, 7.0, "Segunda")]
    ass = ("[Script Info]\nTitle: x\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,{\\an8}Hola,\\Nmundo\n"
           "Dialogue: 0,0:00:00.50,0:00:01.00,Default,,0,0,0,,Antes\n")
    a = parse_ass(ass)
    assert [(c.start, c.text) for c in a] == [(0.5, "Antes"), (1.5, "Hola, mundo")]
    (tmp_path / "c.ass").write_text(ass, encoding="utf-8")
    assert len(load_cues(tmp_path / "c.ass")) == 2
