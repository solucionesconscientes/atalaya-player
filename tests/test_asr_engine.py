"""whisper.cpp engine: argv against the real CLI options, JSON parsing, model catalogue/tier choice, and one real
transcription of the Spanish test voice (skipped when whisper.cpp is not vendored)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mpvd.asr.audio import extract_wav, wav_duration
from mpvd.asr.engine import WhisperEngine, parse_cli_json
from mpvd.asr.models import CATALOG, VAD_MODEL, ModelStore, model_filename, model_url
from tests.asr_helpers import ROOT, keywords_hit, min_keywords, asr_model, whisper_available


def test_parse_cli_json_offsets_ms_and_language():
    data = {"result": {"language": "es"}, "transcription": [
        {"offsets": {"from": 30, "to": 4200}, "text": " Hola mundo."},
        {"offsets": {"from": 4200, "to": 5000}, "text": " [BLANK_AUDIO]"},
        {"offsets": {"from": 5000, "to": 5000}, "text": " nada"},
        {"text": "sin offsets"},
    ]}
    segs, lang = parse_cli_json(data)
    assert lang == "es" and [(s.start, s.end, s.text) for s in segs] == [(0.03, 4.2, "Hola mundo.")]


def test_model_store_catalogue_and_pick(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    (d / model_filename("tiny")).write_bytes(b"lmgg" + b"\0" * 2048)
    (d / model_filename("base")).write_bytes(b"xxxx" + b"\0" * 2048)   # wrong magic → ignored
    store = ModelStore([d])
    assert store.find("tiny") is not None and store.find("base") is None and store.present() == ["tiny"]
    assert store.pick("small", "live") == "tiny"
    assert store.pick("medium", "live") == "tiny"            # base wanted but only tiny present → cheaper present one
    assert store.pick("medium", "live", prefer_present=False) == "base"
    assert store.pick("large", "precompute", prefer_present=False) == "small"
    # 4-core laptop: live stays on base, pre-subtitling uses small-q8_0 (28.5 s chunks, docs/BENCHMARKS.md)
    assert store.pick("small", "live", prefer_present=False) == "base"
    assert store.pick("small", "precompute", prefer_present=False) == "small-q8_0"
    assert store.pick("small", "precompute") == "tiny"      # small-q8_0 absent → cheaper model on disk
    assert model_url("tiny").endswith("/ggml-tiny.bin") and "whisper-vad" in model_url(VAD_MODEL)
    names = [m.name for m in store.list()]
    assert names[:-1] == list(CATALOG) and names[-1] == VAD_MODEL and store.list()[-1].is_vad
    assert store.download_dir() == d


def test_engine_argv_uses_verified_cli_options(tmp_path):
    store = ModelStore([tmp_path])
    (tmp_path / model_filename(VAD_MODEL)).write_bytes(b"lmgg" + b"\0" * 2048)
    eng = WhisperEngine(store, ROOT, threads=3, cli=Path("/x/whisper-cli"), beam_size=2, best_of=1, no_fallback=True)
    argv = eng.argv(Path("/tmp/a.wav"), Path("/m/ggml-base.bin"), Path("/tmp/a"), "es", translate=True, prompt="Hola")
    assert argv[:3] == ["/x/whisper-cli", "-m", "/m/ggml-base.bin"]
    for flag, value in (("-f", "/tmp/a.wav"), ("-t", "3"), ("-l", "es"), ("-of", "/tmp/a"), ("-bs", "2"), ("-bo", "1"),
                        ("--prompt", "Hola"), ("--vad-model", str(tmp_path / model_filename(VAD_MODEL)))):
        assert argv[argv.index(flag) + 1] == value
    for flag in ("-oj", "-np", "-tr", "-nf", "--vad"):
        assert flag in argv
    assert eng.argv(Path("/tmp/a.wav"), Path("/m/x.bin"), Path("/tmp/a"), None)[argv.index("-l") + 1] == "auto"


@pytest.mark.skipif(not whisper_available(), reason="whisper.cpp not vendored (tools/vendor_whisper.sh)")
def test_real_transcription_spanish(media_dir, tmp_path):
    store = ModelStore([ROOT / "vendor" / "whisper" / "models"])
    eng = WhisperEngine(store, ROOT)
    assert eng.available and "whisper" in eng.version().lower()
    wav = asyncio.run(extract_wav(str(media_dir / "voz_es.flac"), 0, 30, tmp_path / "es.wav"))
    res = asyncio.run(eng.transcribe(wav, asr_model(), "es", audio_seconds=wav_duration(wav)))
    text = " ".join(s.text for s in res.segments)
    hits = keywords_hit(text, "es")
    assert len(hits) >= min_keywords(), (text, hits)
    assert res.language == "es" and res.segments[0].start < 1.0 and res.segments[-1].end <= wav_duration(wav) + 0.5
    assert res.rtf < 3.0 and eng.stats["runs"] == 1
