"""H23 · subscriptions end to end in an in-process mpvd with the fake yt-dlp (tests/fixtures/ytdlp/fake_ytdlp.py): a
channel (flat list, the newest N on subscribing, nothing twice), a podcast served over local HTTP with the time window,
the limit per window and the metered pause (clock and network injected), and «conservar N» / «borrar lo visto» on a
temporary tree (never outside the subscription's folders)."""

from __future__ import annotations

import asyncio
import datetime as dt
import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer

FIX = Path(__file__).parent / "fixtures"
FAKE = FIX / "ytdlp" / "fake_ytdlp.py"
CHANNEL = "https://www.youtube.com/@BlenderOfficial"
FLAT = json.loads((FIX / "ytdlp" / "playlist_flat.json").read_text(encoding="utf-8"))
IDS = [e["id"] for e in FLAT["entries"]]      # newest first, as YouTube lists a channel


@pytest.fixture
def env(tmp_path, media_dir, monkeypatch):
    arglog = tmp_path / "argv.log"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_ARGLOG", str(arglog))
    monkeypatch.setenv("FAKE_YTDLP_MEDIA", str(media_dir))
    monkeypatch.setenv("FAKE_YTDLP_DELAY", "0.01")
    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    monkeypatch.setenv("MPV_UOS_METERED", "0")
    monkeypatch.setenv("MPV_UOS_FEEDS_TICK", "3600")     # the tests drive checks and dispatch themselves
    return tmp_path, arglog


def run(tmp_path: Path, fn):
    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=3)
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def argv_lines(arglog: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()] if arglog.exists() else []


async def until(pred, timeout: float = 30.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = await pred()
        if v:
            return v
        await asyncio.sleep(0.05)
    raise TimeoutError(what)


def test_channel_subscription_newest_n_archive_and_nothing_twice(env):
    tmp_path, arglog = env

    async def fn(server, c):
        det = await c.call("feeds.detect", {"url": CHANNEL})
        assert det["kind"] == "channel" and det["url"] == CHANNEL + "/videos" and det["title"] == "Blender"
        flat = [a for a in argv_lines(arglog) if "--flat-playlist" in a][-1]
        assert flat[flat.index("-I") + 1] == "1:5" and flat[-1] == CHANNEL + "/videos"

        sub = await c.call("feeds.add", {"url": CHANNEL, "rules": {"initial": 2, "preset": "video_360"}})
        assert sub["kind"] == "channel" and sub["kind_label"] == "canal" and sub["title"] == "Blender"
        assert sub["folder"] == str(tmp_path / "dl" / "Suscripciones" / "Blender")
        with pytest.raises(RpcError, match="ya estás suscrito"):
            await c.call("feeds.add", {"url": CHANNEL + "/videos"})

        async def two_files():
            g = await c.call("feeds.get", {"id": sub["id"]})
            return g if g["files"] == 2 and g["status"] == "idle" else None

        g = await until(two_files, what="two downloads")
        # the newest two, the older of them first; the other three count as seen
        downloads = [a for a in argv_lines(arglog) if "--flat-playlist" not in a and "--version" not in a]
        assert [a[-1] for a in downloads] == [f"https://www.youtube.com/watch?v={IDS[1]}",
                                              f"https://www.youtube.com/watch?v={IDS[0]}"]
        for a in downloads:
            assert "--download-archive" in a and "--no-playlist" in a and a[a.index("-P") + 1] == sub["folder"]
        flats = [a[a.index("-I") + 1] for a in argv_lines(arglog) if "--flat-playlist" in a]
        assert "1:15" in flats                   # the check of a channel only looks at its newest 15 (detect: 5)
        assert all(Path(r["path"]).parent == Path(sub["folder"]) and Path(r["path"]).is_file()
                   for r in g["file_records"])
        assert g["last_found"] == 2 and g["pending"] == 0
        rows = await c.call("ytdl.downloads.list")
        assert all(r["spec"]["extra"]["feed"] == sub["id"] and r["status"] == "done" for r in rows)
        job = next(j for j in server.jobs.list() if j["name"].startswith("download:"))
        assert job["priority"] == "index"                    # subscriptions never jump ahead of the user

        n = len(argv_lines(arglog))
        res = await c.call("feeds.check", {"id": sub["id"], "wait": True})
        assert res["results"][sub["id"]]["found"] == 0 and len(argv_lines(arglog)) == n + 1   # only the listing

        # a new upload appears (the fake list never changes, so forget one id): only that one is downloaded
        server.feeds.store.subs[sub["id"]].seen.remove(IDS[3])
        res = await c.call("feeds.check", {"id": sub["id"], "wait": True})
        assert res["results"][sub["id"]]["found"] == 1
        g = await until(lambda: _files(c, sub["id"], 3), what="third download")
        assert argv_lines(arglog)[-1][-1] == f"https://www.youtube.com/watch?v={IDS[3]}"

        # pause: no checks; remove: forgotten, files kept
        p = await c.call("feeds.pause", {"id": sub["id"]})
        assert p["paused"] and p["status"] == "paused"
        assert (await c.call("feeds.check", {"wait": True}))["results"] == {}
        assert (await c.call("feeds.remove", {"id": sub["id"]}))["removed"]
        assert (await c.call("feeds.list"))["subscriptions"] == []
        assert all(Path(r["path"]).is_file() for r in g["file_records"])
        saved = json.loads((tmp_path / "data" / "feeds.json").read_text())
        assert saved["subscriptions"] == [] and saved["quota"]["items"] == 3

    run(tmp_path, fn)


async def _files(c, sid, n):
    g = await c.call("feeds.get", {"id": sid})
    return g if g["files"] == n else None


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture
def feed_server():
    handler = lambda *a, **kw: _Quiet(*a, directory=str(FIX / "feeds"), **kw)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_podcast_window_limit_and_metered_pause(env, feed_server, monkeypatch):
    tmp_path, arglog = env
    monkeypatch.setenv("FAKE_YTDLP_DELAY", "0.25")     # long enough to pause one in the middle
    url = feed_server + "/npr_news_now.xml"

    async def fn(server, c):
        feeds = server.feeds
        clock = {"now": dt.datetime(2026, 9, 30, 12, 0)}
        feeds.clock = lambda: clock["now"]
        metered = {"v": False}
        feeds.metered_fn = lambda: metered["v"]
        await c.call("feeds.settings.set", {"window": "de 1:00 a 7:00", "max_items": 1})

        det = await c.call("feeds.detect", {"url": url})
        assert det["kind"] == "rss" and det["title"] == "NPR News Now" and len(det["entries"]) == 4
        sub = await c.call("feeds.add", {"url": url})     # podcast defaults: original audio, the 3 newest
        assert sub["kind"] == "rss" and sub["preset"] == "audio_original" and sub["initial"] == 3
        g = await until(lambda: _pending(c, sub["id"], 3), what="3 pending")
        st = await c.call("feeds.state")
        assert not st["allowed"] and st["reason"] == "fuera de la franja (de 1:00 a 7:00)"
        assert st["window_text"] == "de 1:00 a 7:00"
        assert g["pending_titles"] == ["NPR News: 09-30-2026 5AM EDT", "NPR News: 09-30-2026 6AM EDT",
                                       "NPR News: 09-30-2026 7AM EDT"]   # oldest first
        assert not [a for a in argv_lines(arglog) if "-x" in a]

        clock["now"] = dt.datetime(2026, 10, 1, 2, 0)      # inside the window: one download, then the limit
        assert (await c.call("feeds.dispatch"))["started"] == 1
        g = await until(lambda: _files(c, sub["id"], 1), what="first episode")
        name = Path(g["file_records"][0]["path"]).name
        assert name == "2026-09-30 - NPR News 09-30-2026 5AM EDT.m4a"      # «fecha - título», no «:»
        st = await c.call("feeds.state")
        assert st["reason"] == "límite de la franja: 1 descargas" and st["quota"]["items"] == 1
        assert (await c.call("feeds.dispatch"))["started"] == 0

        # no limit, but the connection becomes metered while the next one downloads: it goes back to the queue
        await c.call("feeds.settings.set", {"max_items": 0})
        await until(lambda: _running(server), what="second download running")
        metered["v"] = True
        await c.call("feeds.dispatch")
        g = await until(lambda: _pending(c, sub["id"], 2), what="paused back to pending")
        assert g["pending_titles"][0] == "NPR News: 09-30-2026 6AM EDT" and g["files"] == 1
        st = await c.call("feeds.state")
        assert st["metered"] is True and st["reason"] == "conexión medida: en pausa"
        assert (await c.call("feeds.check", {"id": sub["id"], "wait": True}))["results"][sub["id"]]["found"] == 0

        metered["v"] = False
        await c.call("feeds.dispatch")
        g = await until(lambda: _files(c, sub["id"], 3), timeout=40, what="all three")
        dl = [a for a in argv_lines(arglog) if "-x" in a]
        assert all("--download-archive" not in a and "-S" in a for a in dl)
        assert sorted(Path(r["path"]).name for r in g["file_records"]) == [
            "2026-09-30 - NPR News 09-30-2026 5AM EDT.m4a", "2026-09-30 - NPR News 09-30-2026 6AM EDT.m4a",
            "2026-09-30 - NPR News 09-30-2026 7AM EDT.m4a"]
        assert Path(sub["folder"]).parent.name == "Suscripciones"

    run(tmp_path, fn)


async def _pending(c, sid, n):
    g = await c.call("feeds.get", {"id": sid})
    return g if g["pending"] == n and g["downloading"] == 0 else None


async def _running(server):
    return any(i.status == "running" and i.stage == "download" for i in server.ytdl.downloads.items.values())


def test_keep_n_and_delete_watched_on_a_temp_tree(env):
    tmp_path, _ = env
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"x" * 1000)

    async def fn(server, c):
        sub = await c.call("feeds.add", {"url": CHANNEL, "check": False,
                                         "rules": {"folder": str(tmp_path / "subs" / "B"), "keep": 2}})
        s = server.feeds.store.subs[sub["id"]]
        folder = Path(s.folder)
        folder.mkdir(parents=True)
        recs = []
        for i in range(4):
            f = folder / f"v{i}.mkv"
            f.write_bytes(bytes([i]) * (70_000 + i))       # different content → different watch keys
            srt = folder / f"v{i}.es.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhola\n", encoding="utf-8")
            key = await server.watch.key_for(str(f))
            recs.append({"id": f"e{i}", "title": f"v{i}", "path": str(f), "extra": [str(srt)], "at": 1000.0 + i,
                         "published": None, "key": key})
        # a record that points outside the subscription's folders (tampered file): never deleted
        recs.insert(0, {"id": "evil", "title": "evil", "path": str(outside), "extra": [], "at": 1.0, "key": "",
                        "published": None})
        s.files = recs

        # keep 2, only watched ones may go: nothing watched yet → nothing deleted
        res = await c.call("feeds.rules.apply", {"id": sub["id"]})
        assert res["removed"] == 0 and res["files"] == 5
        # v0 and v1 watched (finished): the two oldest watched go, the outside one stays
        for i in (0, 1):
            server.watch.store.update(recs[i + 1]["key"], recs[i + 1]["path"], duration=30, position=30)
        res = await c.call("feeds.rules.apply", {"id": sub["id"]})
        assert res["removed"] == 2 and outside.is_file()
        assert sorted(p.name for p in folder.iterdir()) == ["v2.es.srt", "v2.mkv", "v3.es.srt", "v3.mkv"]

        # by age (not only watched): keep 1 → the outside record is «oldest» but refused; v2 goes
        await c.call("feeds.update", {"id": sub["id"], "keep": 1, "keep_watched_only": False})
        res = await c.call("feeds.rules.apply", {"id": sub["id"]})
        assert outside.is_file() and not (folder / "v2.mkv").exists() and (folder / "v3.mkv").is_file()

        # «borrar lo visto» with the grace period: watched just now → stays until the grace is over
        await c.call("feeds.update", {"id": sub["id"], "keep": 0, "delete_watched": True})
        server.watch.store.update(recs[4]["key"], recs[4]["path"], duration=30, position=29.5)
        res = await c.call("feeds.rules.apply", {"id": sub["id"]})
        assert (folder / "v3.mkv").is_file()
        await c.call("feeds.settings.set", {"watched_grace_h": 0})
        res = await c.call("feeds.rules.apply", {"id": sub["id"]})
        assert not (folder / "v3.mkv").exists() and not (folder / "v3.es.srt").exists() and outside.is_file()
        # files deleted by hand disappear from the records
        assert (await c.call("feeds.get", {"id": sub["id"]}))["file_records"] == [
            r for r in server.feeds.store.subs[sub["id"]].files]

    run(tmp_path, fn)


def test_detect_errors_and_settings_validation(env):
    tmp_path, _ = env

    async def fn(server, c):
        with pytest.raises(RpcError, match="vídeo suelto"):
            await c.call("feeds.detect", {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"})
        with pytest.raises(RpcError, match="http"):
            await c.call("feeds.detect", {"url": "ftp://x"})
        with pytest.raises(RpcError, match="franja no válida"):
            await c.call("feeds.settings.set", {"window": "por la noche"})
        s = await c.call("feeds.settings.set", {"window": "23 a 6", "interval_h": 6, "chain": {"loudnorm": True}})
        assert s["window"] == "23:00-06:00" and s["interval_h"] == 6 and s["chain"]["loudnorm"] is True
        caps = await c.call("capabilities")
        assert caps["services"]["feeds"] is True and any(m["name"] == "feeds.add" for m in caps["methods"])
        lst = await c.call("feeds.list")
        assert lst["state"]["metered"] is False and "{date} - {title}" in lst["rename_presets"]
        assert [p["id"] for p in lst["presets"]][:2] == ["video_best", "video_1080"]

    run(tmp_path, fn)
