"""Live channels, unit level: per-file mpv options (no watch_later, HLS options, browser User-Agent), HLS master
quality, FAST copies, repeated channels merged with alternatives, Spanish labels (countries, categories, groups)."""

from __future__ import annotations

import asyncio
import http.server
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.iptv import labels
from mpvd.iptv.m3u import parse_m3u
from mpvd.iptv.model import (BROWSER_USER_AGENT, Channel, best_variant, entry_to_channel, hls_variants, is_hls,
                             merge_kv_list, split_kv_list)
from mpvd.iptv.radiobrowser import RadioBrowser
from mpvd.iptv.service import _best_video, _fps
from mpvd.iptv.sources import Source
from mpvd.iptv.store import IptvStore
from mpvd.server import MpvdServer

HLS_OPTS = "http_persistent=0,seg_max_retry=3"


def chan(url: str, **kw) -> Channel:
    return Channel(id="x", name=kw.pop("name", "Canal"), url=url, kind=kw.pop("kind", "tv"), source="s", **kw)


# -- per-file options ------------------------------------------------------------------------------------


def test_mpv_options_for_live_channels(monkeypatch):
    monkeypatch.delenv("MPV_UOS_USER_AGENT", raising=False)
    # HLS without headers in the list: browser User-Agent, lavf options, never watch_later
    assert chan("https://rtvelivestream.rtve.es/rtvesec/la1/la1_main_dvr.m3u8").mpv_options() == {
        "force-media-title": "Canal", "save-position-on-quit": "no", "user-agent": BROWSER_USER_AGENT,
        "demuxer-lavf-o": HLS_OPTS}
    # Icecast radio: same User-Agent, no HLS options
    assert chan("https://playerservices.streamtheworld.com/api/livestream-redirect/CADENASER.mp3").mpv_options() == {
        "force-media-title": "Canal", "save-position-on-quit": "no", "user-agent": BROWSER_USER_AGENT}
    # local files: nothing HTTP
    assert chan("file:///tmp/a.m3u8").mpv_options() == {"force-media-title": "Canal", "save-position-on-quit": "no"}
    # the list's User-Agent and Referer win
    opts = chan("http://x/y.m3u8", headers={"User-Agent": "Lista/1", "Referer": "https://r/"}).mpv_options()
    assert opts["user-agent"] == "Lista/1" and opts["referrer"] == "https://r/"
    # Radio Browser says hls=1 even without .m3u8 in the URL
    assert chan("https://radio.example/live", extra={"hls": "1"}).mpv_options()["demuxer-lavf-o"] == HLS_OPTS
    monkeypatch.setenv("MPV_UOS_USER_AGENT", "Propio/2")
    assert chan("https://x/a.m3u8").mpv_options()["user-agent"] == "Propio/2"
    assert chan("https://x/a.m3u8").request_headers() == {"User-Agent": "Propio/2"}


def test_list_demuxer_lavf_o_is_merged_not_replaced():
    pl = parse_m3u(
        "#EXTM3U\n#EXTINF:-1,Uno\n#EXTVLCOPT:demuxer-lavf-o=http_persistent=1,max_reload=5\nhttps://x/uno.m3u8\n"
        "#EXTINF:-1,Dos\n#EXTVLCOPT:stream-lavf-o=reconnect=1\nhttps://x/dos.mp3\n")
    uno, dos = (entry_to_channel(e, "s") for e in pl.entries)
    assert uno.extra == {"mpv:demuxer-lavf-o": "http_persistent=1,max_reload=5"}
    assert uno.mpv_options()["demuxer-lavf-o"] == "http_persistent=1,max_reload=5,seg_max_retry=3"
    assert dos.mpv_options()["stream-lavf-o"] == "reconnect=1" and "demuxer-lavf-o" not in dos.mpv_options()


def test_kv_lists_and_hls_detection():
    assert split_kv_list('a=1,b=[x,y],c="z,w",,d=') == [("a", "1"), ("b", "[x,y]"), ("c", '"z,w"'), ("d", "")]
    assert merge_kv_list(None, {"k": "v"}) == "k=v"
    assert merge_kv_list("seg_max_retry=9,b=[1,2]", {"http_persistent": "0", "seg_max_retry": "3"}) == \
        "seg_max_retry=9,b=[1,2],http_persistent=0"
    yes = ["https://liveingesta318.cdnmedia.tv/101televisionlive/smil:malagaott.smil/playlist.m3u8?DVR",
           "http://x/a.m3u8;session=live_stream_1341", "https://x/b.M3U8", "https://x/c.m3u8s",
           "https://x/manifest(format=m3u8-aapl)?format=m3u8"]
    no = ["https://x/a.mp3", "file:///a.m3u8", "rtmp://x/a.m3u8", "https://x/m3u8/stream", "https://x/a.mpd"]
    assert all(is_hls(u) for u in yes) and not any(is_hls(u) for u in no)


# -- HLS quality -----------------------------------------------------------------------------------------

RTVE_MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="es",DEFAULT=YES,URI="la1_main_dvr_audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1155072,RESOLUTION=640x360,CODECS="avc1.640029,mp4a.40.2",AUDIO="aac"
la1_main_dvr_360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3012608,RESOLUTION=1280x720,CODECS="avc1.640029,mp4a.40.2",AUDIO="aac"
la1_main_dvr_720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2025472,RESOLUTION=1024x576,CODECS="avc1.640029,mp4a.40.2",AUDIO="aac"
la1_main_dvr_576.m3u8
"""
FAST_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4215200,RESOLUTION=1920x1080,FRAME-RATE=25.000,CODECS="avc1.640028,mp4a.40.2"
1920x1080_4215200.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3071200,AVERAGE-BANDWIDTH=2500000,RESOLUTION=1280x720,FRAME-RATE=25.000
1280x720_3071200.m3u8
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1400000,RESOLUTION=1280x720,FRAME-RATE=50
low_720p50.m3u8
"""


def test_hls_master_best_variant_and_labels():
    rtve = hls_variants(RTVE_MASTER)
    assert [v["height"] for v in rtve] == [360, 720, 576] and rtve[0]["codecs"] == "avc1.640029,mp4a.40.2"
    best = best_variant(rtve)
    assert best["height"] == 720 and best["bandwidth"] == 3012608 and best["fps"] is None
    assert labels.quality_label(best) == "720p · 3,0 Mb" and not labels.low_bitrate(best)
    fast = best_variant(hls_variants(FAST_MASTER))
    assert labels.quality_label(fast) == "1080p25 · 4,2 Mb"
    low = hls_variants(FAST_MASTER)[2]
    assert labels.quality_label(low) == "720p50 · 1,4 Mb" and labels.low_bitrate(low)
    assert best_variant(hls_variants("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg1.ts\n")) is None
    assert labels.quality_label({"height": 576, "fps": 29.97}) == "576p30"
    assert labels.quality_label({"bandwidth": 128000}) == "128 kb"
    assert labels.quality_label({}) is None and labels.quality_label(None) is None
    assert not labels.low_bitrate({"height": 576, "bandwidth": 900_000})  # SD: not "HD with a low bitrate"


def test_ffprobe_stream_quality():
    assert _fps("25/1") == 25.0 and _fps("30000/1001") == 29.97 and _fps("0/0") is None and _fps(None) is None
    streams = [{"codec_type": "audio"}, {"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "25/1"},
               {"codec_type": "video", "width": 1280, "height": 720, "avg_frame_rate": "0/0", "r_frame_rate": "50/1"}]
    assert _best_video(streams) == {"height": 720, "width": 1280, "fps": 50.0}
    assert _best_video([{"codec_type": "audio"}]) is None


def test_fast_copies_are_marked():
    fast = [
        "https://stream.ads.ottera.tv/playlist.m3u8?network_id=15619",
        "https://negociostv-negociostv-samsunges.amagi.tv/hls/amagi_hls_data_negociost/CDN/playlist.m3u8",
        "https://dfk2a268yviz9.cloudfront.net/v1/master/3722c60a8/cc-ddiii1m6jt6of/CanalSurAndaluciaES.m3u8",
        "https://euronews-live-spa-es.fast.rakuten.tv/v1/master/0547f18649/production-LiveChannel-6571/euronews-es.m3u8",
        "https://cdn-uw2-prod.tsv2.amagi.tv/linear/amg00861-terrastudio-inspiration-samsungtvplus/playlist.m3u8",
        "https://jmp2.uk/plu-62ba60f059624e000781c436.m3u8",
        "https://service-stitcher.clusters.pluto.tv/v1/stitch/embed/hls/channel/5ff/master.m3u8",
        "https://x.getpublica.com/playlist.m3u8",
    ]
    official = [
        "https://rtvelivestream.rtve.es/rtvesec/la1/la1_main_dvr.m3u8",
        "https://d2ymuyhevki1a1.cloudfront.net/wct-ddd2992e/continuous/9be76611/index.m3u8",  # cloudfront, no ads
        "https://live-24-canalsur.interactvty.pro/9bb0f4edcb/74cdbd1b401a.m3u8",
        "not a url",
    ]
    assert all(labels.has_ads(u) for u in fast)
    assert not any(labels.has_ads(u) for u in official)


# -- Spanish labels --------------------------------------------------------------------------------------


def test_categories_and_groups_in_spanish():
    assert labels.category_label("News") == "Noticias" and labels.category_label("undefined") == "Sin categoría"
    assert labels.category_label("Kids") == "Infantil" and labels.category_label("Generalistas") == "Generalistas"
    assert labels.category_label(None) is None
    assert labels.group_label("General;Public") == "General · Público"
    assert labels.group_label("Radio_C. Valenciana") == "Radio C. Valenciana"
    assert labels.group_label("Radio_Populares") == "Radio Populares"
    assert labels.group_label("Int. América") == "Int. América" and labels.group_label(None) is None


def test_country_names_in_spanish():
    for code, name in {"es": "España", "ES": "España", "de": "Alemania", "gb": "Reino Unido", "uk": "Reino Unido",
                       "us": "Estados Unidos", "kr": "Corea del Sur", "bo": "Bolivia", "cd": "República Democrática del Congo",
                       "ru": "Rusia", "xk": "Kosovo", "fm": "Micronesia"}.items():
        assert labels.country_name(code) == name, code
    assert labels.country_name("zz") == "ZZ" and labels.country_name("zz", "Zeta") == "Zeta"
    assert labels.flag("es") == "🇪🇸" and labels.flag("uk") == "🇬🇧" and labels.flag("") == ""
    assert not any(", " in n for n in labels.country_names().values())


def test_bundled_country_names_match_the_system(monkeypatch, tmp_path):
    system = labels.system_country_names()
    bundled = json.loads(labels.BUNDLED_COUNTRIES.read_text(encoding="utf-8"))
    assert len(bundled) >= 245 and bundled["es"] == "España"
    if system:  # the JSON is a copy of what iso-codes gives here (regenerate it if the system catalogue changes)
        assert bundled == system
    # without iso-codes (Windows, macOS, minimal Linux) the bundled names are used
    monkeypatch.setattr(labels, "ISO_DIRS", [tmp_path])
    labels.country_names.cache_clear()
    try:
        assert labels.system_country_names() == {}
        assert labels.country_name("de") == "Alemania" and labels.country_name("ru") == "Rusia"
    finally:
        labels.country_names.cache_clear()


@pytest.mark.parametrize("env,expected", [
    ({"MPV_UOS_COUNTRY": "MX", "LANG": "es_ES.UTF-8"}, "mx"),
    ({"LANG": "es_AR.UTF-8"}, "ar"),
    ({"LC_ALL": "en_GB.UTF-8", "LANG": "es_ES.UTF-8"}, "gb"),
    ({"LANG": "ca_ES@valencia"}, "es"),
    ({"LANGUAGE": "es:en", "LANG": "C.UTF-8"}, "es"),
    ({"LANG": "fr"}, "fr"),
    ({"LANG": "C"}, "es"),
    ({}, "es"),
])
def test_user_country(env, expected):
    assert labels.user_country(env) == expected


# -- store -------------------------------------------------------------------------------------------------


def test_store_migrates_health_and_keeps_quality(tmp_path):
    db = tmp_path / "iptv.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE health (channel_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, checked_at REAL NOT NULL,"
                 " detail TEXT)")
    conn.execute("INSERT INTO health VALUES ('old', 1, 1.0, 'video')")
    conn.commit()
    conn.close()
    store = IptvStore(db)
    try:
        assert store.health()["old"] == {"ok": True, "checked_at": 1.0, "detail": "video", "quality": None}
        store.set_health("new", True, "audio,video", {"height": 720, "fps": 50.0, "bandwidth": 2_700_000})
        store.set_health("dead", False, "403")
        h = store.health()
        assert h["new"]["quality"] == {"height": 720, "fps": 50.0, "bandwidth": 2_700_000}
        assert h["dead"]["ok"] is False and h["dead"]["quality"] is None
    finally:
        store.close()


# -- service: repeated channels, zapping, countries --------------------------------------------------------

TV = """#EXTM3U
#EXTINF:-1 tvg-id="La1.TV" group-title="Generalistas",La 1
https://stream.ads.ottera.tv/playlist.m3u8?network_id=15619
#EXTINF:-1 tvg-id="La1.TV" group-title="Generalistas",La 1
https://rtvelivestream.rtve.es/rtvesec/la1/la1_main_dvr.m3u8
#EXTINF:-1 tvg-id="La2.TV" group-title="Generalistas",La 2
https://rtvelivestream.rtve.es/rtvesec/la2/la2_main_dvr.m3u8
#EXTINF:-1 group-title="Generalistas",LA 1
https://ztnr.rtve.es/ztnr/1688877.m3u8
#EXTINF:-1 group-title="Eventuales",La 1
https://ztnr.rtve.es/ztnr/otra.m3u8
"""
WORLD = """#EXTM3U
#EXTINF:-1 tvg-id="A.de@SD" group-title="General",Das Erste
https://w.example/ard.m3u8
#EXTINF:-1 tvg-id="B.es@SD" group-title="General;Public",La 1 Internacional
https://w.example/la1.m3u8
#EXTINF:-1 tvg-id="C.uk@SD" group-title="News",BBC Test
https://w.example/bbc.m3u8
#EXTINF:-1 tvg-id="D.at@SD" group-title="Undefined",ORF Test
https://w.example/orf.m3u8
"""
RADIO_COUNTRIES = [{"name": "Germany", "iso_3166_1": "DE", "stationcount": 3000},
                   {"name": "United States Of America", "iso_3166_1": "US", "stationcount": 6000},
                   {"name": "Spain", "iso_3166_1": "ES", "stationcount": 900}]


@pytest.fixture
def web():
    files = {"/tv.m3u8": TV.encode(), "/world.m3u": WORLD.encode(),
             "/json/countries": json.dumps(RADIO_COUNTRIES).encode()}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = files.get(self.path.split("?", 1)[0])
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            if body:
                self.wfile.write(body)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


def run_server(tmp_path: Path, base: str, fn):
    sources = [Source("tdt_tv", "España TV", f"{base}/tv.m3u8", "tv", "es", country="es"),
               Source("iptv_org", "Mundo", f"{base}/world.m3u", "tv", "world", country_from_tvg_id=True)]

    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                                     idle_timeout=0, workers=2), iptv_sources=sources)
        await server.start()
        server.iptv.radio = RadioBrowser(server.iptv.http, base_url=base)
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_repeated_channels_are_merged_with_alternatives(tmp_path, web, monkeypatch):
    monkeypatch.setenv("MPV_UOS_COUNTRY", "es")

    async def fn(server, c):
        await c.call("iptv.refresh")
        raw = (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True}))["items"]
        merged = (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True, "merge": True}))["items"]
        la1 = next(i for i in merged if i["name"] == "La 1" and i["group"] == "Generalistas")
        play = await c.call("iptv.play", {"id": la1["id"]})
        ottera = next(i for i in raw if i.get("ads"))
        from_copy = await c.call("iptv.zap", {"id": ottera["id"], "delta": 1})
        z1 = await c.call("iptv.zap", {"id": la1["id"], "delta": 1})
        z2 = await c.call("iptv.zap", {"id": z1["channel"]["id"], "delta": 1})
        found = await c.call("iptv.search", {"q": "la 1", "source": "tdt_tv", "compact": True, "merge": True})
        found_raw = await c.call("iptv.search", {"q": "la 1", "source": "tdt_tv", "compact": True})
        countries = await c.call("iptv.countries", {"source": "iptv_org"})
        world = (await c.call("iptv.channels", {"source": "iptv_org", "compact": True}))["items"]
        radio = await c.call("radio.countries")
        return raw, merged, la1, play, ottera, from_copy, z1, z2, found, found_raw, countries, world, radio

    raw, merged, la1, play, ottera, from_copy, z1, z2, found, found_raw, countries, world, radio = \
        run_server(tmp_path, web, fn)
    assert len(raw) == 5 and len(merged) == 3
    # the broadcaster's stream is the entry; the FAST copy and the mirror ("LA 1": same normalized name) are its
    # alternatives, the FAST copy last; the "La 1" of another group is another channel
    assert [(i["name"], i["group"], i.get("alternatives")) for i in merged] == [
        ("La 1", "Generalistas", 2), ("La 2", "Generalistas", None), ("La 1", "Eventuales", None)]
    assert "rtve.es/rtvesec/la1" in play["url"] and "ads" not in la1
    assert ottera["ads"] is True
    assert [a["url"].split("/")[2] for a in play["alternatives"]] == ["ztnr.rtve.es", "stream.ads.ottera.tv"]
    assert [a["ads"] for a in play["alternatives"]] == [False, True]
    alt_opts = play["alternatives"][1]["options"]
    assert alt_opts["demuxer-lavf-o"] == HLS_OPTS and alt_opts["save-position-on-quit"] == "no"
    # zapping skips the copies: La 1 -> La 2 -> La 1, also when starting from a copy
    assert z1["channel"]["name"] == "La 2" and z2["channel"]["id"] == la1["id"]
    assert from_copy["channel"]["name"] == "La 2"
    assert [f["name"] for f in found_raw].count("La 1") + [f["name"] for f in found_raw].count("LA 1") == 4
    assert sorted((f["name"], f["group"]) for f in found) == [("La 1", "Eventuales"), ("La 1", "Generalistas")]
    # countries in Spanish, the user's first and marked, the rest A-Z (accents ignored)
    assert [(r["name"], r["flag"], r.get("home", False)) for r in countries] == [
        ("España", "🇪🇸", True), ("Alemania", "🇩🇪", False), ("Austria", "🇦🇹", False), ("Reino Unido", "🇬🇧", False)]
    labels_by_name = {w["name"]: (w["group_label"], w["category_label"]) for w in world}
    assert labels_by_name["La 1 Internacional"] == ("General · Público", "General")
    assert labels_by_name["ORF Test"] == ("Sin categoría", "Sin categoría") and labels_by_name["BBC Test"][1] == "Noticias"
    assert [(r["name"], r["count"]) for r in radio] == [("España", 900), ("Estados Unidos", 6000), ("Alemania", 3000)]
    assert radio[0]["home"] is True and "home" not in radio[1]
