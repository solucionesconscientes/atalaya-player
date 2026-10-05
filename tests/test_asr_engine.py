"""whisper.cpp engine: argv against the real CLI options, JSON parsing, model catalogue/tier choice, and one real
transcription of the Spanish test voice (skipped when whisper.cpp is not vendored)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mpvd.asr.audio import extract_wav, wav_duration
from mpvd.asr.engine import WhisperEngine, dtw_preset, parse_cli_json
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
    # H36: ya no hay ASR en vivo; los propósitos son «prepare» (lo espera quien mira) y «precompute» (fondo).
    assert store.pick("small", "prepare") == "tiny"          # small-q8_0 wanted but only tiny present
    assert store.pick("medium", "prepare") == "tiny"         # idem: cae al único que hay en disco
    assert store.pick("medium", "prepare", prefer_present=False) == "medium-q5_0"
    assert store.pick("large", "prepare", prefer_present=False) == "large-v3-turbo-q5_0"
    assert store.pick("large", "precompute", prefer_present=False) == "medium-q5_0"
    # portátil de 4 núcleos: small-q8_0 en los dos casos (el único bueno más rápido que el vídeo, docs/BENCHMARKS.md)
    assert store.pick("small", "prepare", prefer_present=False) == "small-q8_0"
    assert store.pick("small", "precompute", prefer_present=False) == "small-q8_0"
    assert store.pick("small", "precompute") == "tiny"      # small-q8_0 absent → cheaper model on disk
    # un propósito desconocido no elige a ciegas: se trata como «prepare»
    assert store.pick("small", "vete-a-saber", prefer_present=False) == "small-q8_0"
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
    for flag in ("-ojf", "-tr", "-nf", "--vad"):
        assert flag in argv
    assert "-np" not in argv and "--dtw" not in argv   # the log carries the VAD time table (H16)
    no_vad = WhisperEngine(store, ROOT, threads=3, cli=Path("/x/whisper-cli"), use_vad=False)
    argv = no_vad.argv(Path("/tmp/a.wav"), Path("/m/ggml-small-q8_0.bin"), Path("/tmp/a"), "es")
    assert argv[argv.index("--dtw") + 1] == "small" and "-nfa" in argv and "--vad" not in argv
    assert [dtw_preset(m) for m in ("tiny-q5_1", "base.en", "medium-q5_0", "large-v3", "large-v3-turbo-q5_0", "x")] == [
        "tiny", "base.en", "medium", "large.v3", "large.v3.turbo", None]
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
    # H63 · el umbral que había aquí (rtf < 3,0) era una apuesta sobre lo rápido que va la máquina, metida en la
    # batería de corrección: con el escritorio abierto whisper se fue a 3,89 y pintó de rojo las TRES pasadas de una
    # tanda en la que no falló nada más. Un umbral así mide el equipo, no el código, y una batería que se pone roja
    # porque tienes el navegador abierto es una batería que se deja de leer: así se colaron los once fallos de H55.
    # Queda un tope de PATOLOGÍA —lo peor medido con la máquina cargada es 3,9, así que 15 solo salta si algo se ha
    # roto de verdad (el modelo mal cargado, un hilo en vez de cuatro)— y el dato exacto se imprime. El umbral fino
    # sigue disponible para medirlo a mano con la máquina en reposo: MU_BENCH=1.
    print(f"rtf={res.rtf:.2f} ({res.elapsed:.1f}s para {wav_duration(wav):.1f}s de audio)")
    assert res.rtf < 15 and eng.stats["runs"] == 1
    if os.environ.get("MU_BENCH") == "1":
        assert res.rtf < 3.0, f"con la máquina en reposo deberían bastar 3x, y van {res.rtf:.2f}"
