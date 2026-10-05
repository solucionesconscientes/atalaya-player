"""asr.* through a real mpvd: status/models, a live task on the Spanish/English test file (keywords in the SRT),
instant resume from the artifact cache, segments query, look-ahead cursor, and the local-files-only guard (ADR-023)."""

from __future__ import annotations

import json
import time

import pytest

from mpvd.asr.srt import parse_srt
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from tests.asr_helpers import keywords_hit, min_keywords, asr_model, whisper_available

pytestmark = pytest.mark.skipif(not whisper_available(), reason="whisper.cpp not vendored (tools/vendor_whisper.sh)")


def wait_task(d, task_id: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = d.call("asr.status", {"id": task_id})
        if t["status"] in ("done", "failed", "cancelled"):
            return t
        time.sleep(0.5)
    raise TimeoutError(d.call("asr.status", {"id": task_id}))


def test_asr_service_end_to_end(daemon_env, media_dir):
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    caps = d.call("capabilities")
    assert caps["services"]["asr"] is True
    st = d.call("asr.status")
    assert st["engine"]["available"] and st["tier"] in ("small", "medium", "large")
    models = d.call("asr.models")
    present = [m["name"] for m in models["models"] if m["present"] and not m["vad"]]
    model = asr_model()
    assert model in present and models["recommended"]["prepare"] in present

    # local-only guard (ADR-023), unknown model, bad language code, missing file
    for params, code in (({"path": "https://example.com/a.mp4"}, UNAVAILABLE),
                         ({"path": str(media_dir / "voz_es.flac"), "model": "nope"}, INVALID_PARAMS),
                         ({"path": str(media_dir / "voz_es.flac"), "language": "spanish"}, INVALID_PARAMS),
                         ({"path": str(media_dir / "nope.mkv")}, NOT_FOUND)):
        with pytest.raises(RpcError) as exc:
            d.call("asr.start", params)
        assert exc.value.code == code, (params, exc.value)

    # 1. live task on the English track (audio track 1) of the dual-language file
    src = str(media_dir / "voz_es_en.mkv")
    t = d.call("asr.start", {"path": src, "language": "en", "model": model, "audio_track": 1, "time_pos": 0.0,
                             "chunk_seconds": 6.0})
    # H34: the audio track is part of the identity, so it is in the name too (the dub must not reuse the VO's SRT)
    assert t["status"] in ("queued", "running") and t["total"] == 2 and t["srt"].endswith(f"{model}.en.a1.srt")
    t = wait_task(d, t["id"])
    assert t["status"] == "done" and t["complete"] and t["done"] == 2 and t["cues"] >= 1, t
    srt = open(t["srt"], encoding="utf-8").read()
    segs = parse_srt(srt)
    text = " ".join(s.text for s in segs)
    hits = keywords_hit(text, "en")
    assert len(hits) >= min_keywords(), (text, hits)
    assert all(segs[i].end <= segs[i + 1].start + 1e-6 for i in range(len(segs) - 1)), "cues must not overlap"
    assert segs[0].start < 2.0 and segs[-1].end <= 12.5
    seg = d.call("asr.segments", {"id": t["id"], "start": 0, "end": 5})
    assert seg["segments"] and seg["text"]

    # 2. same request again → served from the artifact cache, complete right away, same SRT
    t2 = d.call("asr.start", {"path": src, "language": "en", "model": model, "audio_track": 1, "chunk_seconds": 6.0})
    assert t2["status"] == "done" and t2["complete"] and t2["id"] == t["id"]
    d.call("shutdown")
    d.wait(lambda: not d.alive(), timeout=15)
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    t3 = d.call("asr.start", {"path": src, "language": "en", "model": model, "audio_track": 1, "chunk_seconds": 6.0})
    assert t3["status"] == "done" and t3["complete"] and t3["cues"] == t["cues"], "state must survive a daemon restart"
    assert open(t3["srt"], encoding="utf-8").read() == srt

    # 3. the Spanish voice with the cursor near the end: the chunk under the cursor is transcribed first
    src_es = str(media_dir / "voz_es.flac")
    t = d.call("asr.start", {"path": src_es, "language": "es", "model": model, "time_pos": 9.0, "chunk_seconds": 4.0})
    assert t["total"] == 3
    first = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        cur = d.call("asr.status", {"id": t["id"]})
        if cur["last_chunk"]:
            first = cur["last_chunk"]
            break
        time.sleep(0.2)
    assert first is not None and first[0] <= 9.0 < first[1], first   # [8, 11.68) contains the cursor
    d.call("asr.seek", {"id": t["id"], "time_pos": 0.5})
    t = wait_task(d, t["id"])
    assert t["status"] == "done" and t["done"] == 3
    text = " ".join(s.text for s in parse_srt(open(t["srt"], encoding="utf-8").read()))
    assert len(keywords_hit(text, "es")) >= min_keywords(), text

    # 4. jobs are visible and the precompute variant survives without a session
    jobs = d.call("jobs.list")
    assert any(j["name"].startswith("asr.") for j in jobs)
    p = d.call("asr.precompute", {"path": src_es, "language": "es", "model": model})
    assert p["status"] == "done" or p["purpose"] == "precompute"


def test_asr_srt_paths_are_safe(daemon_env, media_dir, tmp_path):
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    src = str(media_dir / "voz_es.flac")
    t = d.call("asr.start", {"path": src, "language": "es", "model": asr_model(), "chunk_seconds": 30.0})
    assert "/asr/" in t["srt"] and t["srt"].startswith(str(d.cache_dir)) and " " not in t["srt"]
    d.call("asr.stop", {"id": t["id"]})
    st = d.call("asr.status", {"id": t["id"]})
    assert st["status"] in ("cancelled", "done", "running", "queued")
    json.dumps(d.call("asr.status"))  # serialisable


def test_el_srt_que_se_escribe_lo_puede_abrir_mpv_aunque_no_haya_segmentos(tmp_path):
    """H63 · mpv NO acepta un SRT vacío: `sub-add` lo rechaza («No format found») y no crea pista, así que el
    «lo escribimos siempre, aunque esté vacío, para que mpv lo pueda añadir ya» no funcionaba nunca y dejaba un
    «Can not open external file» en un log que nadie mira. Medido con mpv de verdad antes de arreglarlo.

    Esto comprueba lo que de verdad importa: que el fichero que escribe el demonio sea uno que mpv pueda abrir.
    """
    from mpvd.asr.service import AsrService
    from mpvd.asr.srt import Segment

    class TareaDePega:
        def __init__(self, ruta):
            self.srt_path = ruta
            self.segments: list = []

    tarea = TareaDePega(tmp_path / "base.es.srt")
    svc = object.__new__(AsrService)                              # el servicio sin arrancar: solo se prueba escribir
    svc._write_srt_only(tarea)                                    # sin segmentos: el hueco válido
    texto = tarea.srt_path.read_text(encoding="utf-8")
    assert texto.strip(), "un SRT vacío no lo abre mpv: tiene que llevar al menos un rótulo"
    assert "-->" in texto and texto.lstrip().startswith("1")      # subrip reconocible
    assert not list(tmp_path.glob("*.tmp")), "el temporal del rename atómico no se queda ahí"

    tarea.segments = [Segment(start=1.0, end=2.0, text="hola")]
    svc._write_srt_only(tarea)
    assert "hola" in tarea.srt_path.read_text(encoding="utf-8")   # y el hueco se sustituye por el texto de verdad
