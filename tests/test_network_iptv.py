"""Real sources (needs internet): download the lists, check sizes, and play channels in headless mpv.

Geo-blocked or dead streams are tolerated but recorded in the test output; at least one channel per source
must play so that a broken pipeline is still detected.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.server import MpvdServer

pytestmark = pytest.mark.network


async def try_play(client, url: str, options: dict[str, str], timeout: float = 25.0) -> tuple[bool, str]:
    """loadfile with per-file options; success = file-loaded (or playback-restart) before end-file/error."""
    await client.command("loadfile", url, "replace", -1, options)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False, "timeout"
        try:
            ev = await asyncio.wait_for(client.events.get(), remaining)
        except asyncio.TimeoutError:
            return False, "timeout"
        if ev.get("event") == "file-loaded":
            return True, "file-loaded"
        if ev.get("event") == "end-file" and ev.get("reason") in ("error", "eof"):
            return False, f"{ev.get('reason')}: {ev.get('file_error', '')}"


def play_some(h, items, want=3, tries=6):
    """Try up to `tries` channels; return (successes, log lines)."""
    log = []
    ok = 0

    async def go(c):
        nonlocal ok
        for ch in items[:tries]:
            t0 = time.monotonic()
            good, why = await try_play(c, ch["url"], ch["options"])
            log.append(f"{'OK ' if good else 'ERR'} {ch['name'][:40]:40s} {time.monotonic() - t0:5.1f}s {why[:60]}")
            ok += int(good)
            await c.command("stop")
            if ok >= want:
                break

    h.run(go, timeout=tries * 40)
    return ok, log


@pytest.fixture
def real_server(tmp_path):
    async def go(fn):
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=2))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return lambda fn: asyncio.run(go(fn))


def test_real_sources_download_and_parse(real_server):
    async def fn(server, c):
        states = {s["id"]: s for s in await c.call("iptv.refresh", timeout=180)}
        groups = await c.call("iptv.facets", {"facet": "group", "source": "tdt_tv"})
        countries = await c.call("iptv.facets", {"facet": "country", "source": "iptv_org"})
        found = await c.call("iptv.search", {"q": "la 1", "source": "tdt_tv"})
        return states, groups, countries, found

    states, groups, countries, found = real_server(fn)
    print({k: (v["channels"], v["error"]) for k, v in states.items()})
    assert states["tdt_tv"]["channels"] >= 100 and states["tdt_tv"]["error"] is None
    assert states["iptv_org"]["channels"] >= 3000 and states["iptv_org"]["error"] is None
    assert len(groups) >= 5 and len(countries) >= 50
    assert any("la 1" in f["name"].lower() for f in found)
    if states["tdt_radio"]["error"]:
        print("tdt_radio not available:", states["tdt_radio"]["error"])
    else:
        assert states["tdt_radio"]["channels"] >= 20


def test_radio_browser_directory(real_server):
    async def fn(server, c):
        countries = await c.call("radio.countries", timeout=60)
        es = await c.call("radio.stations", {"country": "es", "limit": 20}, timeout=60)
        found = await c.call("radio.stations", {"q": "radio 3", "country": "es", "limit": 5}, timeout=60)
        return countries, es, found

    countries, es, found = real_server(fn)
    assert any(c["code"] == "es" for c in countries) and len(countries) > 100
    assert len(es) >= 10 and all(s["kind"] == "radio" and s["url"] for s in es)
    assert found and any("radio 3" in s["name"].lower() for s in found)


def test_play_real_channels_headless(real_server, mpv_headless):
    # iptv-org: channels of the tester's country (MPV_UOS_TEST_COUNTRY, default "es") that are not flagged
    # geo-blocked / not 24/7; far fewer false negatives than a foreign news list.
    country = os.environ.get("MPV_UOS_TEST_COUNTRY", "es").lower()

    async def fn(server, c):
        await c.call("iptv.refresh", timeout=180)
        tdt = await c.call("iptv.channels", {"source": "tdt_tv", "group": "Generalistas", "limit": 8})
        if tdt["total"] < 3:
            tdt = await c.call("iptv.channels", {"source": "tdt_tv", "limit": 8})
        world = await c.call("iptv.channels", {"source": "iptv_org", "country": country, "limit": 400, "compact": True})
        clean = [ch for ch in world["items"] if not ch.get("geo_blocked") and not ch.get("not_24_7")]
        world["items"] = (clean or world["items"])[:12]
        radio = await c.call("radio.stations", {"country": "es", "limit": 6}, timeout=60)
        out = []
        for group in (tdt["items"], world["items"], radio):
            infos = []
            for ch in group:
                info = await c.call("iptv.play", {"id": ch["id"]})
                infos.append({"name": ch["name"], "url": info["url"], "options": info["options"]})
            out.append(infos)
        return out

    tdt, world, radio = real_server(fn)
    results = {}
    logs = {}
    for label, items in (("tdtchannels", tdt), ("iptv-org", world), ("radio-browser", radio)):
        ok, log = play_some(mpv_headless, items, want=3, tries=10 if label == "iptv-org" else 6)
        results[label] = ok
        logs[label] = log
        print(f"\n{label}: {ok} reproducidos")
        print("\n".join(log))
    assert results["tdtchannels"] >= 1, "no TDTChannels stream played"
    assert results["radio-browser"] >= 1, "no radio stream played"
    assert mpv_headless.script_errors() == []
    if results["iptv-org"] == 0:
        # The pipeline is proven by the two sources above; iptv-org streams die or get geo-blocked all the time,
        # so this is recorded as an expected failure with the per-channel log instead of failing the run.
        pytest.xfail("no iptv-org stream played (geo-blocking or dead streams):\n" + "\n".join(logs["iptv-org"]))


# Canal Sur Andalucía, broadcaster's own CDN (TDTChannels, 2026-09-30): answers 403 to mpv's default User-Agent.
CANAL_SUR = ("https://live-24-canalsur.interactvty.pro/9bb0f4edcb8946e79f5017ddca6c02b0/"
             "74cdbd1b401a7598ab8f3e9b5652c9909daa60dd4f6f96f28396a3f1cfd926f2.m3u8")


def test_canal_sur_official_opens_without_ytdlp(real_server):
    """With the browser User-Agent mpvd adds when the list gives none, the official Canal Sur stream opens
    directly in mpv; --ytdl=no rules out yt-dlp's fallback."""
    from mpvd.iptv.model import BROWSER_USER_AGENT, Channel
    from tests.conftest import TMP, start_mpv

    async def fn(server, c):
        await c.call("iptv.refresh", {"source": "tdt_tv"}, timeout=180)
        found = await c.call("iptv.search", {"q": "canal sur andalucia", "source": "tdt_tv", "limit": 10})
        official = next((f for f in found if "canalsur" in f["url"] and "cloudfront" not in f["url"]), None)
        if official is None:
            return None
        return await c.call("iptv.play", {"id": official["id"]})

    info = real_server(fn)
    if info is None:  # the list changed: still check the known URL with what mpvd would send
        ch = Channel(id="cs", name="Canal Sur", url=CANAL_SUR, kind="tv", source="t")
        info = {"url": CANAL_SUR, "options": ch.mpv_options()}
    assert info["options"]["user-agent"] == BROWSER_USER_AGENT
    h = start_mpv(TMP / "test-mpv-net", ["--script-opts=mu-core-autostart=no", "--ytdl=no"])
    try:
        async def go(c):
            without = await try_play(c, info["url"], {"force-media-title": "libmpv"}, timeout=20)
            await c.command("stop")
            with_ua = await try_play(c, info["url"], info["options"], timeout=30)
            fmt = await c.get_property("file-format") if with_ua[0] else None
            lavf = await c.get_property("options/demuxer-lavf-o") if with_ua[0] else None
            await c.command("stop")
            return without, with_ua, fmt, lavf

        without, with_ua, fmt, lavf = h.run(go, timeout=90)
        print(f"\nCanal Sur ({info['url'][:60]}…): libmpv → {without}; navegador → {with_ua}, formato {fmt}")
        assert with_ua[0], with_ua
        assert fmt == "hls" and lavf == {"http_persistent": "0", "seg_max_retry": "3"}
        if without[0]:
            print("aviso: el CDN ya no rechaza el User-Agent por defecto de mpv")
    finally:
        h.stop()
