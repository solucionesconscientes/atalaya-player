"""subs.* through a real mpvd: info/shift on an SRT, and resync of a deliberately delayed SRT against the Whisper
transcription of the Spanish test voice (pending → done, offset recovered)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mpvd.asr.srt import Segment, parse_srt, render_srt
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError
from tests.asr_helpers import asr_model, whisper_available

pytestmark = pytest.mark.skipif(not whisper_available(), reason="whisper.cpp not vendored (tools/vendor_whisper.sh)")


def test_subs_info_shift_and_resync(daemon_env, media_dir, tmp_path):
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    assert d.call("capabilities")["services"]["subs"] is True
    src = str(media_dir / "voz_es.flac")

    # 1. resync before any transcription exists → pending with a precompute task
    srt = tmp_path / "pelicula.srt"
    # the voice says these phrases at 0.03, 4.09 and 7.76 s (silencedetect): the file is 2.5 s late throughout
    srt.write_text(render_srt([Segment(2.53, 6.2, "Bienvenido a MPV-UOS, el reproductor del futuro."),
                               Segment(6.59, 9.9, "El rápido zorro marrón salta sobre el perro perezoso."),
                               Segment(10.26, 13.8, "Hoy es un buen día para ver una película con subtítulos.")]),
                   encoding="utf-8")
    info = d.call("subs.info", {"srt": str(srt)})
    assert info["cues"] == 3 and info["start"] == 2.53
    r = d.call("subs.resync", {"path": src, "srt": str(srt), "language": "es", "model": asr_model()})
    assert r["status"] == "pending" and r["task"]["purpose"] == "precompute"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and d.call("asr.status", {"id": r["task"]["id"]})["status"] not in ("done", "failed"):
        time.sleep(0.5)
    assert d.call("asr.status", {"id": r["task"]["id"]})["status"] == "done"

    # 2. now it aligns: the SRT was written ~2.5 s late with respect to the speech (which starts near 0.0–0.5 s)
    r = d.call("subs.resync", {"path": src, "srt": str(srt), "language": "es", "model": asr_model()})
    assert r["status"] == "done" and r["stats"]["ok"], r["stats"]
    assert r["stats"]["matched"] >= 2 and -3.0 < r["stats"]["offset_median"] < -2.0, r["stats"]
    out = parse_srt(open(r["srt"], encoding="utf-8").read())
    assert len(out) == 3 and out[0].start < 0.6 and out[0].text.startswith("Bienvenido")
    assert all(out[i].end <= out[i + 1].start + 1e-6 for i in range(2))
    assert r["srt"].startswith(str(d.cache_dir)) and r["srt"].endswith("pelicula.resync.srt")

    # 3. constant shift helper
    s = d.call("subs.shift", {"srt": str(srt), "offset": -1.0})
    sh = parse_srt(open(s["srt"], encoding="utf-8").read())
    assert s["cues"] == 3 and abs(sh[0].start - 1.53) < 1e-6 and "shift-1.00" in s["srt"]


@pytest.mark.skipif(not __import__("mpvd.subs.translate", fromlist=["runtime_available"]).runtime_available(),
                    reason="translation runtime not installed (uv sync --extra translate)")
def test_subs_translate_job_cache_and_errors(daemon_env, media_dir, tmp_path):
    from mpvd.subs.translate import ArgosStore
    from tests.asr_helpers import ROOT, fold

    if ArgosStore([ROOT / "vendor" / "models" / "argos"], ROOT / "vendor" / "dl").find("es", "en") is None:
        pytest.skip("Argos es→en package not vendored")
    d = daemon_env
    d.extra_env["MPV_UOS_OPUS_MODELS"] = str(tmp_path / "opus")      # OPUS-MT big not downloaded here
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    assert d.call("capabilities")["services"]["translate"] is True
    st = d.call("subs.translate.models")
    assert st["engine"]["available"] and any(p["source"] == "es" and p["target"] == "en" and p["present"] for p in st["packages"])
    engines = {e["id"]: e for e in st["engines"]}
    assert engines["argos"]["name"] == "Rápido (Argos)" and "es_en" in engines["argos"]["present"]
    big = engines["opus-big"]
    assert big["size_mb"] == 234 and big["present"] == [] and "CC-BY" in big["license"]
    # H36/C6: con los modelos de francés, el triángulo es/en/fr es OPUS-MT entero (es↔fr pivotando por inglés)
    assert {(p["source"], p["target"]) for p in big["pairs"]} == {("es", "en"), ("en", "es"), ("ca", "en"),
                                                                 ("en", "ca"), ("fr", "en"), ("en", "fr")}

    srt = tmp_path / "pelicula.srt"
    srt.write_text(render_srt([Segment(0.0, 2.0, "Bienvenido a MPV-UOS,"), Segment(2.0, 4.0, "el reproductor del futuro."),
                               Segment(5.0, 8.0, "El rápido zorro marrón salta sobre el perro perezoso."),
                               Segment(9.0, 11.0, "Hoy es un buen día para ver una película con subtítulos.")]), encoding="utf-8")
    # errors: unknown source, same language, missing package
    with pytest.raises(RpcError) as exc:
        d.call("subs.translate", {"srt": str(srt), "target": "en"})
    assert exc.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as exc:
        d.call("subs.translate", {"srt": str(srt), "target": "de", "source": "fr"})
    assert exc.value.code == UNAVAILABLE and exc.value.data and exc.value.data["missing"]
    # engine choice: OPUS-MT asked for but not downloaded → the missing model says which engine; unknown engine
    with pytest.raises(RpcError) as exc:
        d.call("subs.translate", {"srt": str(srt), "target": "en", "source": "spa", "engine": "opus-big"})
    assert exc.value.code == UNAVAILABLE and exc.value.data["missing"] == [["es", "en", "opus-big"]]
    with pytest.raises(RpcError) as exc:
        d.call("subs.translate", {"srt": str(srt), "target": "en", "source": "es", "engine": "deepl"})
    assert exc.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as exc:
        # un par que OPUS-MT no cubre (sí cubre es/ca/fr ↔ en): no se puede pedir ese modelo
        d.call("subs.translate.download", {"source": "de", "target": "en", "engine": "opus-big"})
    assert exc.value.code == INVALID_PARAMS and "OPUS-MT" in exc.value.message

    # 1. job → done → English SRT with the same timing
    r = d.call("subs.translate", {"srt": str(srt), "target": "en", "source": "es", "path": str(media_dir / "voz_es.flac")})
    assert r["status"] == "queued" and r["route"] == "argos:es_en" and r["cues"] == 4 and r["engines"] == ["argos"]
    job_id = r["job"]["id"]
    deadline = time.monotonic() + 120
    job = None
    while time.monotonic() < deadline:
        job = next((j for j in d.call("jobs.list") if j["id"] == job_id), None)
        if job and job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.3)
    assert job and job["status"] == "done", job
    out = parse_srt(open(r["srt"], encoding="utf-8").read())
    assert [(c.start, c.end) for c in out] == [(0.0, 2.0), (2.0, 4.0), (5.0, 8.0), (9.0, 11.0)]
    text = fold(" ".join(c.text for c in out))
    assert sum(k in text for k in ("welcome", "player", "future", "fox", "dog", "movie", "subtitles")) >= 5, text
    assert r["srt"].endswith("pelicula.en.srt")

    # 2. same request → served from the artifact cache without a job
    r2 = d.call("subs.translate", {"srt": str(srt), "target": "en", "source": "es", "path": str(media_dir / "voz_es.flac")})
    assert r2["status"] == "done" and r2["cached"] is True and r2["srt"] == r["srt"]

    # 3. an embedded text track: subs.extract → SRT in the cache → translated like any file (language tag "spa")
    from tests.test_subs_save import make_video

    video = make_video(media_dir, tmp_path / "peli.mkv")
    ex = d.call("subs.extract", {"path": str(video), "ff_index": 1})
    if ex["status"] == "queued":
        d.wait(lambda: d.call("subs.extract", {"path": str(video), "ff_index": 1})["status"] == "done", timeout=30)
    r3 = d.call("subs.translate", {"srt": ex["srt"], "target": "en", "source": ex["lang"], "path": str(video),
                                   "engine": "auto"})
    assert r3["source"] == "es" and r3["route"] == "argos:es_en"
    if r3["status"] == "queued":
        d.wait(lambda: Path(r3["srt"]).is_file() and len(parse_srt(Path(r3["srt"]).read_text(encoding="utf-8"))) == 3,
               timeout=120, interval=0.3)
    out = parse_srt(Path(r3["srt"]).read_text(encoding="utf-8"), keep_lines=True)
    assert "welcome" in fold(out[0].text) and out[2].text.startswith("- ") and "\n- " in out[2].text, out
