"""ytdl.search: YouTube search through the same yt-dlp as everything else (``--flat-playlist -J -- ytsearchN:``),
mapped to compact rows and cached in memory for an hour. In-process mpvd with the fake yt-dlp, plus a real search."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError
from mpvd.server import MpvdServer
from mpvd.ytdl import service as service_mod
from mpvd.ytdl.info import search_results

FIX = Path(__file__).parent / "fixtures" / "ytdlp"
FAKE = FIX / "fake_ytdlp.py"
KEYS = {"url", "title", "duration", "channel", "view_count", "is_live"}


@pytest.fixture
def search_env(tmp_path, monkeypatch):
    arglog = tmp_path / "argv.log"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_ARGLOG", str(arglog))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    monkeypatch.delenv("FAKE_YTDLP_SEARCH_DELAY", raising=False)
    return tmp_path, arglog


def with_server(tmp_path: Path, fn):
    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=2)
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def search_runs(arglog: Path) -> list[list[str]]:
    if not arglog.exists():
        return []
    rows = [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()]
    return [a for a in rows if a and a[-1].startswith("ytsearch")]


def test_search_results_mapping():
    data = json.loads((FIX / "playlist_flat_search.json").read_text(encoding="utf-8"))
    rows = search_results(data)
    assert len(rows) == 5 and all(set(r) == KEYS for r in rows)
    assert rows[0] == {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
                       "title": "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film",
                       "duration": 635.0, "channel": "Blender", "view_count": 23398108, "is_live": False}
    assert len(search_results(data, 2)) == 2
    odd = {"entries": [
        {"ie_key": "Youtube", "id": "abc", "title": "sin url", "live_status": "is_live", "uploader": "U"},
        {"ie_key": "Generic", "id": "zzz", "title": "sin url ni YouTube"},
        {"url": "https://x.test/v", "is_live": True, "duration": "12.5", "view_count": "7"},
        {"url": "https://x.test/p", "live_status": "is_upcoming"},
        "basura",
    ]}
    assert search_results(odd) == [
        {"url": "https://www.youtube.com/watch?v=abc", "title": "sin url", "duration": None, "channel": "U",
         "view_count": None, "is_live": True},
        {"url": "https://x.test/v", "title": "https://x.test/v", "duration": 12.5, "channel": "", "view_count": 7,
         "is_live": True},
        {"url": "https://x.test/p", "title": "https://x.test/p", "duration": None, "channel": "", "view_count": None,
         "is_live": False},
    ]


def test_search_runs_cache_limits_and_errors(search_env):
    tmp_path, arglog = search_env

    async def fn(server, c):
        caps = await c.call("capabilities")
        method = next(m for m in caps["methods"] if m["name"] == "ytdl.search")
        assert method["params"] == ["query", "limit"] and caps["services"]["ytdl_search"] is True

        rows = await c.call("ytdl.search", {"query": "big buck bunny", "limit": 5})
        assert len(rows) == 5 and rows[0]["channel"] == "Blender" and rows[0]["duration"] == 635
        runs = search_runs(arglog)
        assert len(runs) == 1
        argv = runs[0]
        assert argv[-2:] == ["--", "ytsearch5:big buck bunny"]
        assert "--flat-playlist" in argv and "-J" in argv and "--no-update" in argv and "--no-remote-components" in argv
        st = await c.call("ytdl.status")
        if st["binary"]["js_runtime"]:  # same JS runtime as ytdl.info/downloads
            assert argv[argv.index("--js-runtimes") + 1].startswith(st["binary"]["js_runtime"]["name"] + ":")

        # cached per normalised query + limit
        assert await c.call("ytdl.search", {"query": "  Big   buck BUNNY ", "limit": 5}) == rows
        assert len(search_runs(arglog)) == 1
        three = await c.call("ytdl.search", {"query": "big buck bunny", "limit": 3})
        assert three == rows[:3] and search_runs(arglog)[-1][-1] == "ytsearch3:big buck bunny"
        await c.call("ytdl.search", {"query": "big buck bunny", "limit": 500})
        assert search_runs(arglog)[-1][-1] == "ytsearch50:big buck bunny"  # clamped
        default = await c.call("ytdl.search", {"query": "otra cosa"})
        assert len(default) == 5 and search_runs(arglog)[-1][-1] == "ytsearch15:otra cosa"

        # synthetic results: live entry, channel falling back to uploader, entry without URL skipped
        fake = await c.call("ytdl.search", {"query": "fake things"})
        assert [(r["url"], r["is_live"], r["duration"], r["channel"]) for r in fake] == [
            ("https://fake.test/a", False, 30, "MPV-UOS tests"),
            ("https://fake.test/live", True, None, "MPV-UOS live"),
            ("https://fake.test/c", False, 3725, "Uploader C"),
        ]

        # concurrent identical searches (two clients) share one yt-dlp run
        os.environ["FAKE_YTDLP_SEARCH_DELAY"] = "0.6"
        n = len(search_runs(arglog))
        async with MpvdClient(str(server.settings.socket_path)) as c2:
            a, b = await asyncio.gather(c.call("ytdl.search", {"query": "fake together"}),
                                        c2.call("ytdl.search", {"query": "fake together"}))
        assert a == b and len(a) == 3 and len(search_runs(arglog)) == n + 1

        # errors: invalid params, yt-dlp failure (not cached), timeout
        for bad in ({"query": ""}, {"query": "   "}, {"query": "x", "limit": "muchos"}):
            with pytest.raises(RpcError) as exc:
                await c.call("ytdl.search", bad)
            assert exc.value.code == INVALID_PARAMS, bad
        n = len(search_runs(arglog))
        for _ in range(2):
            with pytest.raises(RpcError) as exc:
                await c.call("ytdl.search", {"query": "fail please"})
            assert exc.value.code == UNAVAILABLE and "Unsupported URL" in exc.value.message
        assert len(search_runs(arglog)) == n + 2
        service_mod.SEARCH_TIMEOUT = 0.3
        try:
            with pytest.raises(RpcError) as exc:
                await c.call("ytdl.search", {"query": "fake slow"})
            assert exc.value.code == UNAVAILABLE and "timed out" in exc.value.message
        finally:
            service_mod.SEARCH_TIMEOUT = 30.0
        os.environ["FAKE_YTDLP_SEARCH_DELAY"] = "0"
        assert len(await c.call("ytdl.search", {"query": "fake slow"})) == 3  # the timeout was not cached

    with_server(tmp_path, fn)


@pytest.mark.network
def test_real_youtube_search(tmp_path, monkeypatch):
    monkeypatch.delenv("MPV_UOS_YTDLP", raising=False)
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")

    async def fn(server, c):
        rows = await c.call("ytdl.search", {"query": "big buck bunny blender", "limit": 5}, timeout=60)
        assert 3 <= len(rows) <= 5 and all(set(r) == KEYS for r in rows)
        assert all(r["url"].startswith("https://www.youtube.com/") for r in rows)
        assert any("bunny" in r["title"].lower() for r in rows)
        assert any(isinstance(r["duration"], (int, float)) and r["duration"] > 0 for r in rows)
        assert any(r["channel"] for r in rows)

    with_server(tmp_path, fn)
