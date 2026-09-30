"""H27 · «¿Qué me he perdido?»: mpvd/recap.py picks representative, non-repeated sentences of a stretch of dialogue
(word scores or embeddings), reads them from SRT files, embedded text tracks and cues; mu-recap (headless mpv + mpvd)
follows the time away and shows the lines with their times, Enter seeks there."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.recap import content_words, pick_count, select_by_vectors, select_by_words, summarize
from mpvd.semantic.fake import FakeEmbedder
from mpvd.semantic.index import Sentence
from mpvd.server import MpvdServer
from tests.conftest import start_mpv

MU_OPTS = ("--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
           "mu-recap-min_away=5")

# a small story: the treasure is the topic that recurs; small talk around it should not be picked
STORY = [
    "Hola, ¿qué tal?",
    "El mapa del tesoro estaba escondido en la biblioteca del abuelo.",
    "Bueno, vale.",
    "Marta encontró el mapa del tesoro dentro de un libro de piratas.",
    "¿Quieres un café?",
    "El tesoro está enterrado bajo el faro viejo del puerto, según el mapa.",
    "Sí, gracias.",
    "Esa noche fueron al faro con una pala y una linterna.",
    "Hace frío, ¿verdad?",
    "Bajo el faro encontraron un cofre con monedas de oro: era el tesoro del abuelo.",
    "Adiós.",
    "Llovía.",
]


def cues(step: float = 2.5) -> list[dict]:
    return [{"start": i * step, "end": i * step + step - 0.2, "text": t} for i, t in enumerate(STORY)]


def write_srt(path: Path, rows: list[dict]) -> Path:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    path.write_text("\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n" for i, c in enumerate(rows, 1)),
                    encoding="utf-8")
    return path


def test_content_words_and_count():
    assert content_words("El tesoro, ¿está bajo el FARO?") == ["tesoro", "faro"]
    assert content_words("también también porque") == []
    assert pick_count(60) == 3 and pick_count(600) == 7 and pick_count(3600) == 7


def test_selection_prefers_the_topic_without_repeats():
    sents = [Sentence(i, i + 1, t) for i, t in enumerate(STORY)]
    for idx in (select_by_words(sents, 4), select_by_vectors(FakeEmbedder().encode([s.text for s in sents]), 4)):
        picked = [STORY[i] for i in idx]
        assert idx == sorted(idx) and len(idx) == 4
        assert sum("tesoro" in p or "faro" in p for p in picked) >= 3, picked
        assert not any(p in ("Hola, ¿qué tal?", "Bueno, vale.", "Sí, gracias.", "Adiós.") for p in picked), picked


def test_summarize_window_and_short_stretches():
    out = summarize(cues(), 0, 30)
    assert out["method"] == "words" and 3 <= len(out["sentences"]) <= 4
    assert all(0 <= s["start"] < 30 for s in out["sentences"])
    # a window: only what overlaps it
    out = summarize(cues(), 12.0, 20.0)
    assert all(12.0 - 2.5 <= s["start"] < 20.0 for s in out["sentences"])
    # too little to choose from: everything is returned, in order
    out = summarize(cues()[:2], 0, 10)
    assert out["method"] == "all" and " ".join(s["text"] for s in out["sentences"]) == " ".join(STORY[:2])
    out = summarize(cues(), 0, 30, embed=FakeEmbedder().encode)
    assert out["method"] == "embeddings"


def run(tmp_path: Path, fn):
    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=2)
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(c)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_rpc_sources(tmp_path, media_dir):
    srt = write_srt(tmp_path / "story.srt", cues())
    mkv = tmp_path / "with_subs.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media_dir / "video30.mkv"), "-i", str(srt), "-map", "0",
                    "-map", "1", "-c", "copy", "-c:s", "srt", str(mkv)], check=True)
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(mkv)],
                                        capture_output=True, text=True, check=True).stdout)["streams"]
    ff_index = next(s["index"] for s in streams if s["codec_type"] == "subtitle")

    async def fn(c):
        got = await c.call("recap.summarize", {"sub_path": str(srt), "start": 0, "end": 30, "method": "words"})
        assert got["source"] == "subtitles" and got["method"] == "words" and len(got["sentences"]) >= 3
        got = await c.call("recap.summarize", {"cues": cues(), "start": 0, "end": 30, "method": "words"})
        assert got["source"] == "cues"
        # embedded text track: ffmpeg's stream index, extracted once to the cache
        got = await c.call("recap.summarize", {"path": str(mkv), "ff_index": ff_index, "start": 0, "end": 30,
                                               "method": "words"})
        assert got["source"] == "subtitles" and any("tesoro" in s["text"] for s in got["sentences"])
        # nothing to read, bad window
        with pytest.raises(Exception, match="no hay subtítulos"):
            await c.call("recap.summarize", {"path": str(media_dir / "video30.mkv"), "start": 0, "end": 30})
        with pytest.raises(Exception, match="end"):
            await c.call("recap.summarize", {"cues": cues(), "start": 10, "end": 5})

    run(tmp_path, fn)


def test_mu_recap_after_being_away(daemon_env, media_dir, tmp_path):
    srt = write_srt(tmp_path / "story.srt", cues())
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=no", f"--sub-file={srt}"],
                  env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.5, timeout=20)
        h.command("script-message-to", "mu_recap", "mu-recap-away")
        h.wait_property("user-data/mu/recap", lambda v: bool(v) and v.get("away"), timeout=10)
        h.command("seek", "26", "absolute")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= 26, timeout=10)
        h.command("set", "pause", "yes")          # a pause while away closes the stretch
        st = h.wait_property("user-data/mu/recap", lambda v: bool(v) and v.get("missed"), timeout=10)
        assert st["missed"]["from"] < 3 and st["missed"]["to"] >= 26

        h.command("script-binding", "mu_recap/recap")
        st = h.wait_property("user-data/mu/recap", lambda v: bool(v) and v.get("status") == "done", timeout=60)
        assert st["result"]["away"] is True and st["result"]["source"] == "subtitles" and st["result"]["count"] >= 3
        titles = [i["title"] for i in st["items"]]
        assert any("tesoro" in t for t in titles) and titles[-1].startswith("Volver a verlo desde")
        nav = h.wait_property("user-data/mu/nav", lambda v: bool(v) and "¿Qué me he perdido?" in v.get("title", ""),
                              timeout=10)
        assert nav["title"].startswith("MPV-UOS › ")

        # Enter on a line jumps there and closes the menu
        target = next(i for i in st["items"] if "faro viejo" in i["title"] or "tesoro" in i["title"])
        seek_to = float(next(c["start"] for c in cues() if c["text"] in target["title"] or target["title"] in c["text"]))
        base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
        h.command("script-message-to", "mu_recap", "mu-recap-event",
                  json.dumps({**base, "type": "activate", "index": 2, "value": {"seek": seek_to}}))
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - seek_to) < 1.0, timeout=10)
        h.wait_property("user-data/uosc/menu/type", lambda v: v in (None, ""), timeout=10)
    finally:
        h.stop()


def test_mu_recap_without_subtitles(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"), "replace", "0", "start=20")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= 19, timeout=20)
        h.command("script-binding", "mu_recap/recap")
        st = h.wait_property("user-data/mu/recap", lambda v: bool(v) and v.get("status") == "error", timeout=30)
        assert "no hay subtítulos" in st["last_error"]
    finally:
        h.stop()
