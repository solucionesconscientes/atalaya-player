"""H29: subtitles the website offers for an internet video — track list from a real YouTube -J, de-rolled automatic
captions (native SRT and VTT recorded on 2026-09-30), fetching through mpvd and the mu-subs menu (fake yt-dlp)."""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
import time
from pathlib import Path

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.subs import web
from tests.conftest import ROOT

FIX = ROOT / "tests" / "fixtures" / "websubs"
INFO = json.loads((FIX / "youtube_ted_info.json").read_text(encoding="utf-8"))


def test_tracks_offered_manual_and_original_automatic_only():
    tracks = web.list_tracks(INFO, prefer="es")
    assert [(t["lang"], t["kind"]) for t in tracks] == [
        ("es", "manual"), ("es-ES", "manual"),        # the user's language first
        ("en", "manual"), ("en", "auto"),              # then the video's own language (manual before automatic)
        ("fr", "manual")]
    assert tracks[3]["label"] == "Inglés (automáticos)" and tracks[1]["label"] == "Español (España)"
    # YouTube's machine translations (tlang=) answer 429 and are never offered; neither are the HLS entries
    auto_es = web.entry_for(INFO, "es", "auto")
    assert auto_es is None or "tlang=" not in auto_es["url"]
    assert all(e.get("protocol") is None for t in tracks for e in [web.entry_for(INFO, t["lang"], t["kind"])])
    assert web.entry_for(INFO, "en", "auto")["ext"] == "srt" and "kind=asr" in web.entry_for(INFO, "en", "auto")["url"]


def no_overlap(cues):
    return all(b.start >= a.end - 1e-6 for a, b in zip(cues, cues[1:]))


def test_youtube_native_srt_is_derolled_into_two_line_cues():
    raw = (FIX / "auto_en-orig_80s.srt").read_text(encoding="utf-8")
    assert not no_overlap(web.parse_srt(raw))          # as served: every cue overlaps the next one
    cues = web.to_cues(raw, "srt", "auto")
    assert no_overlap(cues) and 12 <= len(cues) <= 16
    assert cues[0].text == "Hear that? That's nothing." and cues[0].start == 19.56
    assert cues[1].text == "Which is what I, as a speaker at today's\nconference,"
    assert all(len(c.text.split("\n")) <= 2 for c in cues)


def test_youtube_rolling_vtt_gives_the_same_lines():
    cues = web.to_cues((FIX / "auto_en-orig_60s.vtt").read_text(encoding="utf-8"), "vtt", "auto")
    assert no_overlap(cues) and "<" not in "".join(c.text for c in cues)
    assert [c.text for c in cues[:3]] == ["Hear that? That's nothing.",
                                         "Which is what I, as a speaker at today's\nconference,",
                                         "have for you all. I have nothing."]


def test_manual_vtt_is_kept_as_is():
    cues = web.to_cues((FIX / "manual_es.vtt").read_text(encoding="utf-8"), "vtt", "manual")
    assert cues[1].text == "¿Oyeron eso?" and cues[3].text == "Y es lo que, como orador\nen la conferencia de hoy,"


class _Subs(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}
    hits: list[str] = []

    def log_message(self, *a):  # noqa: D401
        pass

    def do_GET(self):  # noqa: N802
        _Subs.hits.append(self.path)
        if "tlang=" in self.path or self.path.startswith("/busy"):
            self.send_response(429)
            self.end_headers()
            return
        body = self.files.get(self.path.split("?", 1)[0])
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        if body is not None:
            self.wfile.write(body)


def serve_subs():
    _Subs.files = {"/manual_es.vtt": (FIX / "manual_es.vtt").read_bytes(),
                   "/auto_en.srt": (FIX / "auto_en-orig_80s.srt").read_bytes()}
    _Subs.hits = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Subs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_fetch_through_mpvd_caches_and_reports_429(tmp_path, monkeypatch):
    from tests.test_mu_ytdl import FAKE
    httpd, base = serve_subs()
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_SUBS_URL", base)
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=2)

    async def go():
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                url = "https://fake.test/websubs"
                lst = await c.call("subs.web.list", {"url": url})
                assert [(t["lang"], t["kind"]) for t in lst["tracks"]] == [("es", "manual"), ("en", "auto")]
                r = await c.call("subs.web.fetch", {"url": url, "lang": "en", "kind": "auto"})
                assert r["cues"] >= 12 and not r["cached"] and r["title"] == "Inglés (automáticos) · web"
                srt = Path(r["srt"]).read_text(encoding="utf-8")
                assert srt.startswith("1\n00:00:19,560 --> ") and "Hear that? That's nothing." in srt
                again = await c.call("subs.web.fetch", {"url": url, "lang": "en", "kind": "auto"})
                assert again["cached"] and again["srt"] == r["srt"]
                assert sum(1 for h in _Subs.hits if h.startswith("/auto_en.srt")) == 1
                m = await c.call("subs.web.fetch", {"url": url, "lang": "es", "kind": "manual"})
                assert "¿Oyeron eso?" in Path(m["srt"]).read_text(encoding="utf-8")
                # the saved .srt can be translated/saved like any external track: subs.info reads it
                info = await c.call("subs.info", {"srt": m["srt"]})
                assert info["cues"] == m["cues"]
                # a track the site does not offer, and one it refuses (429)
                try:
                    await c.call("subs.web.fetch", {"url": url, "lang": "fr", "kind": "manual"})
                    raise AssertionError("fr manual does not exist")
                except RpcError as exc:
                    assert "no ofrece" in str(exc)
        finally:
            await server.stop()

    try:
        asyncio.run(go())
    finally:
        httpd.shutdown()


def test_429_is_a_clear_error(tmp_path, monkeypatch):
    """The site rate-limits subtitle downloads (seen with YouTube's machine translations): the menu says so."""
    from tests.test_mu_ytdl import FAKE
    httpd, base = serve_subs()
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_SUBS_URL", base + "/busy")
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=2)

    async def go():
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                try:
                    await c.call("subs.web.fetch", {"url": "https://fake.test/websubs", "lang": "es", "kind": "manual"})
                    raise AssertionError("429 accepted")
                except RpcError as exc:
                    assert "429" in str(exc)
        finally:
            await server.stop()

    try:
        asyncio.run(go())
    finally:
        httpd.shutdown()


def test_mu_subs_web_menu_adds_and_translates(daemon_env, media_dir, tmp_path):
    """mu-subs › Subtítulos de la web: lists the site's tracks for the playing URL, Enter adds the clean SRT as a
    selected external track."""
    from tests.conftest import start_mpv
    from tests.test_mu_iptv import serve
    from tests.test_mu_ytdl import FAKE

    httpd_media = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    httpd, base = serve_subs()
    env = {**daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_SUBS_URL": base,
           "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{httpd_media.server_address[1]}",
           "MPV_UOS_YTDLP_AUTO_UPDATE": "0"}
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-ytdl-ytdl_path={FAKE}", "--keep-open=yes", "--pause=yes"], env=env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        url = "https://fake.test/websubs"
        h.command("loadfile", url)
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True and v.get("url") == url,
                        timeout=40)
        h.command("script-binding", "mu_subs/subs-menu")
        st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("view") == "root" and v.get("items"),
                             timeout=20)
        titles = [i["title"] for i in st["items"]]
        assert "Subtítulos de la web" in titles
        ev = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False,
              "type": "activate", "index": titles.index("Subtítulos de la web") + 1, "value": {"view": "web"}}
        h.command("script-message-to", "mu_subs", "mu-subs-event", json.dumps(ev))
        st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("view") == "web" and any(
            i["title"] == "Inglés (automáticos)" for i in v.get("items", [])), timeout=30)
        titles = [i["title"] for i in st["items"]]
        assert not any(t.startswith("Traducir") for t in titles)   # Spanish exists already: nothing to translate
        assert titles[:2] == ["Español", "Inglés (automáticos)"]
        ev.update({"index": 2, "value": {"web": {"lang": "en", "kind": "auto"}}})
        h.command("script-message-to", "mu_subs", "mu-subs-event", json.dumps(ev))
        st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("web_status") == "done", timeout=30)
        tracks = [t for t in h.get("track-list") if t["type"] == "sub" and t.get("external-filename") == st["web_srt"]]
        assert tracks and tracks[0]["selected"] and tracks[0]["title"] == "Inglés (automáticos) · web"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not h.get("sub-text"):
            h.command("seek", 20, "absolute")
            time.sleep(0.5)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
        httpd.shutdown()
        httpd_media.shutdown()
