"""Semantic index, search and topic chapters with a deterministic fake embedder (bag of words -> vector), plus the real
ONNX model when the ``semantic`` extra and vendor/models/embed are present (cross-lingual ES/EN check), plus the
``semantic.*`` methods through mpvd (fake embedder injected via MPVD_SEMANTIC_FAKE) and the palette in headless mpv."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mpvd.semantic import embed as embed_mod
from mpvd.semantic.index import (Sentence, chapters, ffmetadata, search, sentences_from_segments, vectors_from_bytes,
                                 vectors_to_bytes)

np = pytest.importorskip("numpy")


from mpvd.semantic.fake import FakeEmbedder  # noqa: E402


TOPICS = {
    "cocina": "hoy preparamos una receta con tomate cebolla ajo y aceite de oliva en la sartén a fuego lento",
    "astronomia": "el telescopio observa galaxias lejanas estrellas planetas y la luna durante la noche despejada",
    "futbol": "el equipo marcó un gol en el segundo tiempo y el portero paró el penalti del partido",
}


def transcript(order: list[str], seconds_per_topic: float = 240.0, cue: float = 6.0) -> list[dict]:
    """Whisper-like cues: each topic block repeats its vocabulary with small variations."""
    cues = []
    t = 0.0
    for topic in order:
        words = TOPICS[topic].split()
        n = 0
        end = t + seconds_per_topic
        while t < end:
            k = n % len(words)
            text = " ".join(words[k:] + words[:k]).capitalize() + "."
            cues.append({"start": round(t, 2), "end": round(min(t + cue - 0.5, end), 2), "text": text})
            t += cue
            n += 1
    return cues


def test_sentences_merge_and_split():
    cues = [{"start": 0, "end": 2, "text": "Hola,"}, {"start": 2, "end": 4, "text": "buenos días a todos."},
            {"start": 4.5, "end": 7, "text": "Hoy hablamos de mpv"}, {"start": 12, "end": 14, "text": "y de uosc."},
            {"start": 14, "end": 20, "text": " ".join(["palabra"] * 30)}]
    sents = sentences_from_segments(cues)
    assert [s.text for s in sents][:3] == ["Hola, buenos días a todos.", "Hoy hablamos de mpv", "y de uosc."]
    assert sents[0].start == 0 and sents[0].end == 4  # merged cues keep the outer times
    assert sents[1].end == 7 and sents[2].start == 12  # a 5 s gap splits even without punctuation
    assert len(sents) == 4 and sents[3].text.count("palabra") == 30
    assert sentences_from_segments([]) == []


def test_search_and_blob_roundtrip():
    emb = FakeEmbedder()
    cues = transcript(["cocina", "astronomia", "futbol"], seconds_per_topic=60)
    sents = sentences_from_segments(cues)
    vec = emb.encode([s.text for s in sents])
    back = vectors_from_bytes(vectors_to_bytes(vec), vec.shape[1])
    assert back.shape == vec.shape and np.allclose(back, vec)
    hits = search(emb.encode(["gol y portero"])[0], vec, sents, k=3, text_query="gol y portero")
    assert len(hits) == 3 and all(120 <= h["start"] < 180 for h in hits), hits
    assert hits[0]["score"] >= hits[-1]["score"]
    hits = search(emb.encode(["telescopio galaxias"])[0], vec, sents, k=2)
    assert all(60 <= h["start"] < 120 for h in hits)
    assert search(emb.encode(["x"])[0], np.zeros((0, 64), dtype=np.float32), [], 5) == []


def test_chapters_by_topic_change():
    emb = FakeEmbedder()
    cues = transcript(["cocina", "astronomia", "futbol"], seconds_per_topic=240)
    sents = sentences_from_segments(cues)
    vec = emb.encode([s.text for s in sents])
    res = chapters(vec, sents, duration=720.0, min_seconds=120.0)
    assert res["windows"] > 10 and len(res["chapters"]) == 3, res
    cuts = res["cuts"]
    assert abs(cuts[0] - 240) < 30 and abs(cuts[1] - 480) < 30, cuts
    c = res["chapters"]
    assert c[0]["start"] == 0 and c[-1]["end"] == 720 and all(x["title"] for x in c)
    assert "tomate" in c[0]["title"].lower() and "telescopio" in c[1]["title"].lower() and "gol" in c[2]["title"].lower()
    meta = ffmetadata(c)
    assert meta.startswith(";FFMETADATA1") and meta.count("[CHAPTER]") == 3 and "START=0\n" in meta
    # homogeneous content -> no chapters (valid answer), short content -> nothing
    one = transcript(["cocina"], seconds_per_topic=600)
    s1 = sentences_from_segments(one)
    assert chapters(emb.encode([s.text for s in s1]), s1, duration=600.0)["chapters"] == []
    assert chapters(vec[:2], sents[:2])["chapters"] == []
    # the minimum length is honoured even when the signal has many peaks
    res2 = chapters(vec, sents, duration=720.0, min_seconds=400.0)
    assert len(res2["cuts"]) <= 1


@pytest.mark.skipif(not (embed_mod.runtime_available() and embed_mod.model_present()),
                    reason="semantic extra or vendor/models/embed missing")
def test_real_model_cross_lingual():
    emb = embed_mod.Embedder()
    assert emb.dim == 384 and emb.threads >= 1
    v = emb.encode(["El gato duerme sobre el sofá toda la tarde.", "The cat sleeps on the couch all afternoon.",
                    "La reunión de presupuesto se aplaza hasta el lunes.", "Quantum computers use qubits."])
    assert v.shape == (4, 384) and np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-3)
    sim = v @ v.T
    assert sim[0, 1] > 0.8 and sim[0, 1] > sim[0, 2] + 0.3 and sim[0, 3] < 0.4, sim
    assert emb.encode([]).shape == (0, 384)


def _daemon_with_fake(d):
    d.extra_env["MPVD_SEMANTIC_FAKE"] = "1"
    d.cli("ensure")
    d.wait(d.alive, timeout=30)


def test_semantic_methods_via_daemon(daemon_env, media_dir, tmp_path):
    d = daemon_env
    _daemon_with_fake(d)
    caps = d.call("capabilities")
    assert caps["services"]["semantic"] is True
    st = d.call("semantic.status")
    assert st["model"] == "fake-bow"
    video = tmp_path / "charla.mkv"
    shutil.copy(media_dir / "video30.mkv", video)
    # no transcript yet: search falls back to text (no hits) and indexing is refused
    r = d.call("semantic.search", {"q": "telescopio", "path": str(video)})
    assert r["mode"] == "text" and r["hits"] == [] and r["status"] == "unindexed"
    with pytest.raises(Exception, match="transcripci"):
        d.call("semantic.index", {"path": str(video)})
    # inject a transcript into the asr service (test hook) and index
    cues = transcript(["cocina", "astronomia", "futbol"], seconds_per_topic=240)
    d.call("asr.inject", {"path": str(video), "segments": cues, "duration": 720.0})
    r = d.call("semantic.index", {"path": str(video), "wait": True}, timeout=60)
    assert r["status"] == "done" and r["sentences"] > 30 and r["model"] == "fake-bow"
    r = d.call("semantic.search", {"q": "gol y portero", "path": str(video), "k": 3})
    assert r["mode"] == "semantic" and len(r["hits"]) == 3 and all(480 <= h["start"] < 720 for h in r["hits"]), r
    ch = d.call("semantic.chapters", {"path": str(video), "min_seconds": 120}, timeout=60)
    assert ch["status"] == "done" and len(ch["chapters"]) == 3 and ch["cached"] is False
    ch2 = d.call("semantic.chapters", {"path": str(video), "min_seconds": 120})
    assert ch2["cached"] is True and ch2["chapters"] == ch["chapters"]
    assert d.call("semantic.index", {"path": str(video)})["cached"] is True


def test_palette_dialogue_and_chapters_in_mpv(daemon_env, media_dir, tmp_path):
    from tests.conftest import start_mpv
    from tests.test_mu_menu import send_event, titles, wait_menu

    d = daemon_env
    _daemon_with_fake(d)
    video = tmp_path / "charla.mkv"
    shutil.copy(media_dir / "video30.mkv", video)
    cues = transcript(["cocina", "astronomia", "futbol"], seconds_per_topic=10, cue=2.0)  # 30 s file, 3 topics
    d.call("asr.inject", {"path": str(video), "segments": cues, "duration": 30.0})
    d.call("semantic.index", {"path": str(video), "wait": True}, timeout=60)
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,"
                                  "mu-subs-chapter_min_seconds=8,mu-subs-chapter_window=6", "--pause=yes",
                                  "--keep-open=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(video))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 20, timeout=20)
        # palette: a "Diálogo (semántico)" section with hits from the transcript; activating one seeks there
        h.command("script-binding", "mu_menu/palette")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-palette", timeout=15)
        wait_menu(h, "palette", lambda v: v.get("palette_results", 0) > 0)
        send_event(h, {"type": "search", "query": "telescopio galaxias"})
        st = wait_menu(h, "palette", lambda v: v.get("palette_query") == "telescopio galaxias"
                       and "Diálogo (semántico)" in titles(v))
        hit = next(i for i in st["items"] if isinstance(i.get("value"), dict) and "seek" in i["value"])
        assert 10 <= hit["value"]["seek"] < 20 and "telescopio" in hit["title"].lower(), hit
        send_event(h, {"type": "activate", "index": 1, "value": hit["value"]})
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 9.5 <= v < 20.5, timeout=10)
        # chapters from mu-subs: applied to chapter-list, then removed (originals restored)
        h.command("script-message-to", "mu_subs", "mu-subs-chapters", "yes")
        st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("ai_chapters", 0) > 0, timeout=30)
        cl = h.get("chapter-list")
        assert len(cl) == st["ai_chapters"] >= 2 and all(c.get("title") for c in cl), cl
        h.command("script-message-to", "mu_subs", "mu-subs-chapters", "no")
        h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("ai_chapters") == 0, timeout=10)
        assert h.get("chapter-list") == []
        assert h.script_errors() == []
    finally:
        h.stop()
