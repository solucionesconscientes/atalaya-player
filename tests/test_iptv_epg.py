"""TV guide (H21): XMLTV parsing (real TDTChannels sample), time zones, now/next, grid, matching by tvg-id and by
name, and the iptv.epg.* methods against an in-process mpvd with the list and the guide on a local HTTP server."""

from __future__ import annotations

import asyncio
import calendar
import gzip
import http.server
import re
import threading
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.iptv.epg import EpgStore, fill_stops, id_key, iter_xmltv, name_key, parse_time, Programme
from mpvd.iptv.sources import Source
from mpvd.server import MpvdServer

FIXTURE = Path(__file__).parent / "fixtures" / "iptv" / "epg_tdtchannels_sample.xml"


def utc(y, mo, d, h, mi, s=0) -> float:
    return float(calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)))


@pytest.fixture
def sample_gz(tmp_path) -> Path:
    """The real sample gzipped, as TDTChannels serves it (TV.xml.gz)."""
    out = tmp_path / "TV.xml.gz"
    out.write_bytes(gzip.compress(FIXTURE.read_bytes()))
    return out


def test_parse_time_zones_and_partial_dates():
    assert parse_time("20260930055000 +0000") == utc(2026, 9, 30, 5, 50)
    assert parse_time("20260930075000 +0200") == utc(2026, 9, 30, 5, 50)  # Madrid summer time
    assert parse_time("20260930005000 -0500") == utc(2026, 9, 30, 5, 50)
    assert parse_time("20260930075000 +02:00") == utc(2026, 9, 30, 5, 50)
    assert parse_time("20260930055000") == utc(2026, 9, 30, 5, 50)  # no offset: UTC (XMLTV DTD)
    assert parse_time("202609300550") == utc(2026, 9, 30, 5, 50)
    assert parse_time("20260930") == utc(2026, 9, 30, 0, 0)
    assert parse_time("") is None and parse_time("mañana") is None and parse_time("20261345000000 +0000") is None


def test_names_and_ids_reduce_to_the_same_key():
    assert name_key("La 1") == id_key("La1.TV") == "la1"
    assert name_key("Aragón TV") == id_key("AragonTV.TV") == "aragontv"
    assert id_key("Aragón_Noticias.TV") == "aragonnoticias"
    assert id_key("FranceInfo.fr@SD") == "franceinfo"
    assert id_key("Canal.Radio") == "canal"


def test_iter_xmltv_streams_the_real_sample(sample_gz):
    with gzip.open(sample_gz, "rb") as fh:
        items = list(iter_xmltv(fh))
    chans = [i for k, i in items if k == "channel"]
    progs = [i for k, i in items if k == "programme"]
    assert [c[0] for c in chans] == ["La1.TV", "La2.TV", "24Horas.TV", "TV3.TV"]
    assert chans[0][1] == ["La1.TV"]  # TDTChannels' display-name is the id itself
    assert len(progs) == 27
    first = progs[0]
    assert first.channel == "La1.TV" and first.start == utc(2026, 9, 30, 15, 45) and first.stop == utc(2026, 9, 30, 16, 35)
    assert first.title and first.desc and first.icon.startswith("https://") and first.category
    assert all(p.stop > p.start for p in progs)


def test_missing_stop_ends_at_next_start():
    progs = [Programme("a", 100.0, 100.0, "uno"), Programme("a", 400.0, 400.0, "dos")]
    fill_stops(progs)
    assert progs[0].stop == 400.0 and progs[1].stop == 400.0 + 3600


def test_store_now_next_grid_and_matching(tmp_path, sample_gz):
    st = EpgStore(tmp_path / "epg.sqlite3")
    at = utc(2026, 9, 30, 19, 0)  # 21:00 in Madrid
    info = st.import_file("tdt", sample_gz, now=at)
    assert info == {"channels": 4, "programmes": 27, "first": utc(2026, 9, 30, 15, 0), "last": utc(2026, 9, 30, 22, 30)}

    # by tvg-id (case does not matter) and, without it, by the name without accents/spaces
    assert st.match("tdt", "La1.TV", "La 1") == "La1.TV"
    assert st.match("tdt", "la1.tv", "otra cosa") == "La1.TV"
    assert st.match("tdt", None, "La 2") == "La2.TV"
    assert st.match("tdt", None, "LA-2") == "La2.TV"
    assert st.match("tdt", "La2.es@SD", "Otra") == "La2.TV"  # unknown id: its name part still says La 2
    assert st.match("tdt", None, "24h") is None  # TDTChannels names it 24Horas: only its tvg-id matches
    assert st.match("tdt", "24Horas.TV", "24h") == "24Horas.TV"
    assert st.match("other", "La1.TV", "La 1") is None  # another list's guide

    cur, nxt = st.now_next("tdt", "La1.TV", at)
    assert (cur.start, cur.stop) == (utc(2026, 9, 30, 18, 55), utc(2026, 9, 30, 19, 40))
    assert nxt.start == cur.stop and nxt.title != cur.title
    # exactly at a boundary the new programme is "now"
    cur2, _ = st.now_next("tdt", "La1.TV", cur.stop)
    assert cur2.title == nxt.title
    # before the first programme there is only "next"; after the last, nothing
    cur3, nxt3 = st.now_next("tdt", "La1.TV", utc(2026, 9, 30, 15, 0))
    assert cur3 is None and nxt3.start == utc(2026, 9, 30, 15, 45)
    assert st.now_next("tdt", "La1.TV", utc(2026, 10, 1, 3, 0)) == (None, None)

    grid = st.grid("tdt", "La2.TV", at, at + 2 * 3600)
    assert grid[0].start <= at < grid[0].stop
    assert all(a.stop <= b.start for a, b in zip(grid, grid[1:]))
    assert grid[-1].start < at + 2 * 3600

    # a new import replaces the old one; programmes over for more than 6 h are not kept
    later = st.import_file("tdt", sample_gz, now=utc(2026, 10, 1, 4, 0))
    assert later["programmes"] < 27
    st.close()


def test_accents_and_display_names(tmp_path):
    xml = tmp_path / "g.xml"
    xml.write_text(
        "<?xml version='1.0' encoding='utf-8'?>\n<tv>\n"
        '<channel id="c1"><display-name>Aragón Televisión</display-name></channel>\n'
        '<programme channel="c1" start="20260930190000 +0200" stop="20260930200000 +0200">'
        "<title>Informativo</title></programme>\n"
        '<programme channel="c2" start="20260930190000" stop="20260930200000"><title>Sin canal</title></programme>\n'
        "</tv>\n", encoding="utf-8")
    st = EpgStore(tmp_path / "e.sqlite3")
    info = st.import_file("x", xml, now=utc(2026, 9, 30, 17, 30))
    assert info["channels"] == 2  # a programme's channel without <channel> still counts
    assert st.match("x", None, "ARAGON television") == "c1"
    cur, _ = st.now_next("x", "c1", utc(2026, 9, 30, 17, 30))
    assert cur.title == "Informativo" and cur.start == utc(2026, 9, 30, 17, 0)
    st.close()


# -- iptv.epg.* over JSON-RPC -------------------------------------------------------------------------------------


def serve(files: dict[str, bytes]):
    hits: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, hits


def shifted_sample(delta: float) -> bytes:
    """The real sample with every time moved by ``delta`` seconds (imports drop what ended over 6 h ago)."""
    def move(m: re.Match) -> str:
        ts = parse_time(m.group(2)) + delta
        return f'{m.group(1)}="{time.strftime("%Y%m%d%H%M%S", time.gmtime(ts))} +0000"'

    text = re.sub(r'(start|stop)="(\d{14} \+0000)"', move, FIXTURE.read_text(encoding="utf-8"))
    return gzip.compress(text.encode("utf-8"))


def test_rpc_now_channel_refresh(tmp_path):
    # the sample's 21:00 (Madrid) becomes the current minute
    at = float(int(time.time()) // 60 * 60)
    files: dict[str, bytes] = {"/epg/TV.xml.gz": shifted_sample(at - utc(2026, 9, 30, 19, 0))}
    httpd, hits = serve(files)
    base = f"http://127.0.0.1:{httpd.server_port}"
    files["/tv.m3u8"] = (
        f'#EXTM3U url-tvg="{base}/epg/TV.xml.gz"\n'
        '#EXTINF:-1 tvg-id="La1.TV" group-title="Generalistas",La 1\nhttps://tv.example/la1.m3u8\n'
        '#EXTINF:-1 group-title="Generalistas",La 2\nhttps://tv.example/la2.m3u8\n'
        '#EXTINF:-1 tvg-id="24Horas.TV" group-title="Informativos",24h\nhttps://tv.example/24h.m3u8\n'
        '#EXTINF:-1 tvg-id="Local.TV" group-title="Locales",Tele Local\nhttps://tv.example/local.m3u8\n'
    ).encode()
    files["/world.m3u"] = b'#EXTM3U\n#EXTINF:-1 tvg-id="La1.es@SD",La 1 World\nhttps://w.example/la1.m3u8\n'
    sources = [Source("tdt_tv", "España TV", f"{base}/tv.m3u8", "tv", "es", country="es"),
               Source("iptv_org", "Mundo", f"{base}/world.m3u", "tv", "world", country_from_tvg_id=True)]

    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=2), iptv_sources=sources)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                chans = {ch["name"]: ch["id"] for ch in
                         (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True}))["items"]}
                world = (await c.call("iptv.channels", {"source": "iptv_org", "compact": True}))["items"][0]["id"]
                ids = [*chans.values(), world]
                first = await c.call("iptv.epg.now", {"ids": ids, "at": at})
                deadline = time.monotonic() + 20
                res = first
                while time.monotonic() < deadline:
                    res = await c.call("iptv.epg.now", {"ids": ids, "at": at})
                    if res["channels"] and not res["loading"]:
                        break
                    await asyncio.sleep(0.1)
                grid = await c.call("iptv.epg.channel", {"id": chans["La 2"], "start": at, "hours": 3})
                none = await c.call("iptv.epg.channel", {"id": chans["Tele Local"], "start": at})
                fetches = hits.count("/epg/TV.xml.gz")
                again = await c.call("iptv.epg.now", {"ids": ids, "at": at + 3600})  # fresh: no new download
                forced = await c.call("iptv.epg.refresh", {"force": True, "wait": True})
                return chans, world, first, res, grid, none, fetches, again, forced, hits.count("/epg/TV.xml.gz")
        finally:
            await server.stop()
            httpd.shutdown()

    chans, world, first, res, grid, none, fetches, again, forced, fetches2 = asyncio.run(go())
    assert first["loading"] is True and first["channels"] == {}  # first call: the download runs in the background
    got = res["channels"]
    assert set(got) == {chans["La 1"], chans["La 2"], chans["24h"]}  # by id, by name, by id; no guide for the rest
    la1 = got[chans["La 1"]]
    assert la1["now"]["start"] <= at < la1["now"]["stop"] and la1["next"]["start"] == la1["now"]["stop"]
    assert "desc" not in la1["now"]  # the batch answer stays small
    assert world not in got  # iptv-org announces no guide
    progs = grid["programmes"]
    assert grid["epg_id"] == "La2.TV" and progs[0]["now"] is True and not any(p["now"] for p in progs[1:])
    assert progs[0]["desc"] and all(p["scheduled"] is False for p in progs)
    assert none["epg_id"] is None and none["programmes"] == [] and none["has_guide"] is True
    assert fetches == 1 and again["channels"][chans["La 1"]]["now"]["start"] >= la1["now"]["stop"]
    assert forced["guides"][0]["meta"]["programmes"] == 27 and forced["guides"][0]["error"] is None
    assert fetches2 == 2  # force: asked the server again (conditional request)


@pytest.mark.network
def test_real_tdtchannels_guide(tmp_path):
    """The real list and guide: at least 20 TDTChannels channels have a programme on now."""
    from mpvd.iptv.sources import BUILTIN_SOURCES

    sources = [s for s in BUILTIN_SOURCES if s.id == "tdt_tv"]

    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=2), iptv_sources=sources)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                items = (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True, "merge": True,
                                                        "limit": 5000}, timeout=120))["items"]
                refreshed = await c.call("iptv.epg.refresh", {"wait": True}, timeout=180)
                now = await c.call("iptv.epg.now", {"ids": [i["id"] for i in items]}, timeout=60)
                return refreshed, now, {i["id"]: i["name"] for i in items}
        finally:
            await server.stop()

    refreshed, now, names = asyncio.run(go())
    guide = refreshed["guides"][0]
    assert guide["url"].endswith("/epg/TV.xml.gz") and guide["error"] is None, guide
    on_now = {names[cid]: v["now"]["title"] for cid, v in now["channels"].items() if v.get("now")}
    print(f"{len(on_now)} canales con programa ahora, p. ej.:", dict(list(on_now.items())[:5]))
    assert len(on_now) >= 20, on_now
