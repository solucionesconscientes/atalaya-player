"""H16 acceptance: real whisper.cpp on synthetic Spanish speech with known timing. Four phrases (espeak-ng) are placed at
known offsets in silence; the true onset/offset of each is where its samples rise above a tiny amplitude. The cue that
opens each phrase must start within 150 ms on average (and every one within 300 ms); cues never overlap."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import wave
from array import array

import pytest

from mpvd.asr.engine import WhisperEngine
from mpvd.asr.models import ModelStore, default_model_dirs
from tests.asr_helpers import ROOT, asr_model, whisper_available

PHRASES = [
    (0.8, "Buenos días a todos."),
    (4.2, "Hoy vamos a probar los subtítulos del reproductor."),
    (9.4, "El zorro marrón salta sobre el perro."),
    (13.6, "Muchas gracias por mirar."),
]
RATE = 16000
LEVEL = 300  # |sample| above this is voice (espeak-ng pads its output with digital silence)


def synth(tmp_path):
    clips = []
    for i, (_, text) in enumerate(PHRASES):
        raw, wav16 = tmp_path / f"p{i}.wav", tmp_path / f"p{i}-16k.wav"
        subprocess.run(["espeak-ng", "-v", "es", "-s", "150", "-w", str(raw), text], check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le",
                        str(wav16)], check=True)
        with wave.open(str(wav16), "rb") as w:
            a = array("h")
            a.frombytes(w.readframes(w.getnframes()))
        clips.append(a)
    total = int((PHRASES[-1][0] + len(clips[-1]) / RATE + 1.5) * RATE)
    out = array("h", bytes(2 * total))
    truth = []
    for (at, _), clip in zip(PHRASES, clips, strict=True):
        base = int(at * RATE)
        assert base + len(clip) <= total
        out[base:base + len(clip)] = clip
        loud = [k for k, x in enumerate(clip) if abs(x) > LEVEL]
        truth.append(((base + loud[0]) / RATE, (base + loud[-1]) / RATE))
    for (a, b), (c, _d) in zip(truth, truth[1:]):
        assert c - b > 1.0, "phrases must be separated by silence"
    path = tmp_path / "timed.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(out.tobytes())
    return path, truth


@pytest.mark.skipif(not whisper_available() or not shutil.which("espeak-ng"),
                    reason="whisper.cpp not vendored or espeak-ng missing")
@pytest.mark.parametrize("use_vad", [True, False], ids=["silero-vad", "dtw-energy-vad"])
def test_cue_starts_follow_the_voice_within_150_ms(tmp_path, use_vad):
    wav, truth = synth(tmp_path)
    store = ModelStore(default_model_dirs(ROOT, ROOT / ".cache" / "data"))
    eng = WhisperEngine(store, ROOT, use_vad=use_vad)
    res = asyncio.run(eng.transcribe(wav, asr_model(), "es"))
    cues = res.segments
    assert cues, "no cues"
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start + 1e-6, (a, b)
    errors, report = [], []
    for onset, offset in truth:
        first = min(cues, key=lambda c: abs(c.start - onset))
        errors.append(abs(first.start - onset))
        inside = [c for c in cues if onset - 0.3 <= c.start <= offset]
        assert inside, (onset, offset, cues)
        # the phrase is on screen until its voice ends (reading rules may keep it a little longer)
        assert inside[-1].end >= offset - 0.15, (offset, inside[-1])
        report.append(f"{onset:.2f}→{first.start:.2f} ({first.text})")
    mean = sum(errors) / len(errors)
    assert mean < 0.15 and max(errors) < 0.3, (mean, report)
