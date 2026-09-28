"""iptv.* RPC methods against an in-process server with sources served by a local HTTP server."""

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.iptv.sources import Source
from mpvd.rpc import NOT_FOUND, RpcError
from mpvd.server import MpvdServer

TV = """#EXTM3U url-tvg="https://epg.example/guide.xml"
#EXTINF:-1 tvg-id="La1.es" tvg-logo="https://x/la1.png" group-title="Generalistas",La 1
#EXTVLCOPT:http-user-agent=UA-TDT
https://tv.example/la1.m3u8
#EXTINF:-1 tvg-id="La2.es" group-title="Generalistas",La 2
https://tv.example/la2.m3u8
#EXTINF:-1 tvg-id="Teledeporte.es" group-title="Deportes",Teledeporte
https://tv.example/tdp.m3u8
#EXTINF:-1 tvg-id="Canal24h.es" group-title="Informativos",Canal 24 Horas
https://tv.example/24h.m3u8
"""
WORLD = """#EXTM3U
#EXTINF:-1 tvg-id="FranceInfo.fr" tvg-language="French" group-title="News",France Info
https://w.example/fi.m3u8
#EXTINF:-1 tvg-id="ARD.de" group-title="General",Das Erste
https://w.example/ard.m3u8
#EXTINF:-1 tvg-id="RaiNews.it" group-title="News",Rai News 24
https://w.example/rai.m3u8
#EXTINF:-1 tvg-id="Antena3.es" group-title="General",Antena 3 Internacional
https://w.example/a3.m3u8
"""


class Files:
    data = {"/tv.m3u8": TV, "/world.m3u": WORLD, "/radio.m3u8": None}
    hits: list[str] = []


def make_handler(files):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            files.hits.append(self.path)
            body = files.data.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-mpegurl")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return H


@pytest.fixture
def web():
    files = Files()
    files.hits = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), make_handler(files))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield files, f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def with_iptv_server(tmp_path: Path, base: str, fn):
    sources = [
        Source("tdt_tv", "España TV", f"{base}/tv.m3u8", "tv", "es", country="es"),
        Source("tdt_radio", "España Radio", f"{base}/radio.m3u8", "radio", "es", country="es"),
        Source("iptv_org", "Mundo", f"{base}/world.m3u", "tv", "world", country_from_tvg_id=True),
    ]

    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=2), iptv_sources=sources)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_refresh_facets_channels_search_play_zap(tmp_path, web):
    files, base = web

    async def fn(server, c):
        states = {s["id"]: s for s in await c.call("iptv.refresh")}
        groups = await c.call("iptv.facets", {"facet": "group", "source": "tdt_tv"})
        countries = await c.call("iptv.facets", {"facet": "country", "source": "iptv_org"})
        news_fr = await c.call("iptv.facets", {"facet": "category", "source": "iptv_org", "country": "fr"})
        page = await c.call("iptv.channels", {"source": "tdt_tv", "group": "Generalistas"})
        paged = await c.call("iptv.channels", {"source": "iptv_org", "offset": 1, "limit": 2})
        found = await c.call("iptv.search", {"q": "antena"})
        found_es = await c.call("iptv.search", {"q": "la", "source": "tdt_tv"})
        la1 = page["items"][0]
        play = await c.call("iptv.play", {"id": la1["id"]})
        nxt = await c.call("iptv.zap", {"id": la1["id"], "delta": 1})
        prev = await c.call("iptv.zap", {"id": la1["id"], "delta": -1})
        recents = await c.call("iptv.recents.list")
        again = await c.call("iptv.refresh")  # cached: no new HTTP hits
        with pytest.raises(RpcError) as e:
            await c.call("iptv.channel", {"id": "nope"})
        return states, groups, countries, news_fr, page, paged, found, found_es, play, nxt, prev, recents, again, e.value.code

    (states, groups, countries, news_fr, page, paged, found, found_es, play, nxt, prev, recents, again,
     code) = with_iptv_server(tmp_path, base, fn)
    assert states["tdt_tv"]["channels"] == 4 and states["tdt_tv"]["epg_url"] == "https://epg.example/guide.xml"
    assert states["iptv_org"]["channels"] == 4
    assert states["tdt_radio"]["channels"] == 0 and "404" in states["tdt_radio"]["error"]
    assert groups == [{"value": "Deportes", "count": 1}, {"value": "Generalistas", "count": 2}, {"value": "Informativos", "count": 1}]
    assert [c["value"] for c in countries] == ["de", "es", "fr", "it"]
    assert news_fr == [{"value": "News", "count": 1}]
    assert page["total"] == 2 and [i["name"] for i in page["items"]] == ["La 1", "La 2"]
    assert paged["total"] == 4 and [i["name"] for i in paged["items"]] == ["Das Erste", "Rai News 24"]
    assert [f["name"] for f in found] == ["Antena 3 Internacional"]
    assert [f["name"] for f in found_es] == ["La 1", "La 2"]
    assert play["url"] == "https://tv.example/la1.m3u8"
    assert play["options"] == {"force-media-title": "La 1", "user-agent": "UA-TDT"}
    assert play["channel"]["favorite"] is False and play["channel"]["health"] is None
    assert nxt["channel"]["name"] == "La 2" and prev["channel"]["name"] == "La 2"  # group of 2 wraps
    assert [r["name"] for r in recents] == ["La 2", "La 1"]
    # Successful lists are served from cache on the second refresh; the 404 one is retried.
    assert files.hits.count("/tv.m3u8") == 1 and files.hits.count("/world.m3u") == 1 and files.hits.count("/radio.m3u8") == 2
    assert code == NOT_FOUND


def test_favorites_user_sources_and_health(tmp_path, web, media_dir):
    files, base = web
    files.data["/mine.m3u"] = f"#EXTM3U\n#EXTINF:-1 group-title=\"Local\",Prueba local\nfile://{media_dir}/video30.mkv\n" \
                              "#EXTINF:-1,Rota\nhttp://127.0.0.1:1/dead.m3u8\n"

    async def fn(server, c):
        await c.call("iptv.refresh", {"source": "tdt_tv"})
        ch = (await c.call("iptv.channels", {"source": "tdt_tv"}))["items"][2]
        t1 = await c.call("iptv.favorites.toggle", {"id": ch["id"]})
        favs = await c.call("iptv.favorites.list")
        t2 = await c.call("iptv.favorites.toggle", {"id": ch["id"]})
        added = await c.call("iptv.sources.add", {"url": f"{base}/mine.m3u", "name": "Mi lista"})
        srcs = await c.call("iptv.sources")
        mine = await c.call("iptv.channels", {"source": "user:mi-lista"})
        job = await c.call("iptv.health.check", {"source": "user:mi-lista"})
        for _ in range(200):
            j = await c.call("jobs.get", {"id": job["id"]})
            if j["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.1)
        status = await c.call("iptv.health.status")
        after = await c.call("iptv.channels", {"source": "user:mi-lista"})
        removed = await c.call("iptv.sources.remove", {"id": "user:mi-lista"})
        srcs2 = await c.call("iptv.sources")
        return t1, favs, t2, added, srcs, mine, j, status, after, removed, srcs2

    t1, favs, t2, added, srcs, mine, j, status, after, removed, srcs2 = with_iptv_server(tmp_path, base, fn)
    assert t1 == {"id": t1["id"], "favorite": True} and [f["name"] for f in favs] == ["Teledeporte"]
    assert favs[0]["favorite"] is True and t2["favorite"] is False
    assert added["id"] == "user:mi-lista" and added["channels"] == 2 and added["builtin"] is False
    assert [s["id"] for s in srcs] == ["tdt_tv", "tdt_radio", "iptv_org", "user:mi-lista"]
    assert [i["name"] for i in mine["items"]] == ["Prueba local", "Rota"]
    assert j["status"] == "done" and j["result"] == {"checked": 2, "ok": 1} if "result" in j else j["status"] == "done"
    by_name = {i["name"]: i["health"] for i in after["items"]}
    assert by_name == {"Prueba local": True, "Rota": False}
    assert status[mine["items"][0]["id"]]["ok"] is True and "video" in status[mine["items"][0]["id"]]["detail"]
    assert removed == {"removed": True} and [s["id"] for s in srcs2] == ["tdt_tv", "tdt_radio", "iptv_org"]
