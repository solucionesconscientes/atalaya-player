"""H30 · the channel's own tracks with readable names (real RTVE/3Cat master playlists and mpv track-list), their
storage per channel (CC / VO / AD badges) and «Buscar en esta lista» (iptv.search limited to one list)."""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.iptv import tracks as T
from mpvd.iptv.radiobrowser import RadioBrowser
from mpvd.iptv.service import _as_track_list
from mpvd.iptv.sources import Source
from mpvd.iptv.store import IptvStore
from mpvd.rpc import INVALID_PARAMS, RpcError
from mpvd.server import MpvdServer

FIX = Path(__file__).parent / "fixtures" / "iptv"
LA1_MASTER = (FIX / "rtve_la1_master.m3u8").read_text(encoding="utf-8")
CAT_MASTER = (FIX / "3cat_324_master.m3u8").read_text(encoding="utf-8")
LA1_TRACKS = json.loads((FIX / "rtve_la1_tracks.json").read_text(encoding="utf-8"))["track-list"]


def labels(tracks, kind):
    return [t["label"] for t in tracks if t["kind"] == kind]


# -- names ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "name"), [
    ("spa", "Español"), ("es", "Español"), ("SPA", "Español"), ("es-ES", "Español"), ("eng", "Inglés"),
    ("cat", "Catalán"), ("ca", "Catalán"), ("glg", "Gallego"), ("eus", "Euskera"), ("baq", "Euskera"),
    ("eu", "Euskera"), ("fre", "Francés"), ("ger", "Alemán"), ("pt-BR", "Portugués"), ("ara", "Árabe"),
    ("qaa", None), ("qad", None), ("ads", None), ("mul", None), ("mis", None), ("und", None), ("", None),
    (None, None), ("klare sprache", None),
])
def test_language_names(code, name):
    assert T.language_name(code) == name


def test_language_names_without_iso_codes(monkeypatch):
    T._iso639.cache_clear()
    monkeypatch.setattr(T, "ISO_DIRS", [Path("/nonexistent")])
    try:
        assert T.lang_code("spa") == "es" and T.lang_code("baq") == "eu" and T.lang_code("ger") == "de"
        assert T.language_name("fra") == "Francés" and T.language_name("eu") == "Euskera"
        assert T.language_name("xho") is None  # not in the short list: the menu falls back to NAME or the code
    finally:
        T._iso639.cache_clear()


# -- real master playlists ------------------------------------------------------------------------------------------------


def test_rtve_master_names_and_badges():
    r = T.parse_master_media(LA1_MASTER)
    assert [(t["kind"], t["lang"], t["role"]) for t in r] == [
        ("audio", "es", "main"), ("audio", "qaa", "vo"), ("audio", "ads", "ad"),
        ("sub", "es", "sub"), ("sub", "en", "sub"), ("sub", "gl", "sub"), ("sub", "ca", "sub"), ("sub", "eu", "sub")]
    s = T.summary([dict(t) for t in r], "master")
    assert [a["label"] for a in s["audio"]] == ["Español", "Versión original", "Audiodescripción"]
    assert [a["label"] for a in s["subs"]] == ["Español", "Inglés", "Gallego", "Catalán", "Euskera"]
    assert s["badges"] == ["CC", "VO", "AD"]
    assert r[1]["uri"].endswith("la1_main_dvr_a_39.m3u8") and r[0]["uri"] is None  # the main audio is muxed


def test_3cat_master_uses_qad_for_audio_description():
    s = T.summary(T.parse_master_media(CAT_MASTER), "master")
    assert [(a["lang"], a["label"]) for a in s["audio"]] == [
        ("ca", "Catalán"), ("qaa", "Versión original"), ("qad", "Audiodescripción")]
    assert [a["label"] for a in s["subs"]] == ["Catalán"] and s["badges"] == ["CC", "VO", "AD"]


def test_other_real_patterns():
    """Renditions seen in the TDTChannels scan of 2026-09-30 (docs/FUENTES_IPTV.md)."""
    senado = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a1",LANGUAGE="spa",NAME="Traducido",DEFAULT=YES,URI="t.m3u8"\n'
              '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a1",LANGUAGE="spa",NAME="Sonido Sala",URI="s.m3u8"\n'
              '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a2",LANGUAGE="spa",NAME="Traducido",URI="t2.m3u8"\n'
              '#EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS,GROUP-ID="cc",LANGUAGE="es",NAME="Subtítulos",INSTREAM-ID="CC1"\n')
    s = T.summary(T.parse_master_media(senado), "master")
    assert [a["label"] for a in s["audio"]] == ["Español · Traducido", "Español · Sonido Sala"]
    assert [a["label"] for a in s["subs"]] == ["Español · CC"] and s["badges"] == ["CC"]

    parlamento = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="qaa",NAME="Version Original",URI="o.m3u8"\n'
                  '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="es",NAME="Castellano",DEFAULT=YES,URI="e.m3u8"\n'
                  '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",LANGUAGE="qad",NAME="Castellano",URI="x.m3u8"\n')
    s = T.summary(T.parse_master_media(parlamento), "master")
    assert [a["label"] for a in s["audio"]] == ["Versión original", "Español"]
    assert [a["label"] for a in s["subs"]] == ["Castellano"] and s["badges"] == ["CC", "VO"]

    fast = ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",LANGUAGE="SPA",NAME="spa_full",URI="f.m3u8"\n'
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",LANGUAGE="SPA",NAME="spa_forced",FORCED=YES,URI="g.m3u8"\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="eng",NAME="aac1",URI="a.m3u8"\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="de",NAME="Audiodeskription",URI="d.m3u8"\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="klare sprache",NAME="Klare Sprache",URI="k.m3u8"\n'
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",LANGUAGE="en",NAME="English SDH",'
            'CHARACTERISTICS="public.accessibility.transcribes-spoken-dialog,public.accessibility.describes-music-and-sound",'
            'URI="h.m3u8"\n')
    tracks = T.label_all(T.parse_master_media(fast))
    assert labels(tracks, "sub") == ["Español", "Español · forzados", "Inglés · para sordos"]
    assert labels(tracks, "audio") == ["Inglés", "Audiodescripción", "Klare Sprache"]
    assert [t["name"] for t in tracks if t["kind"] == "audio"][0] is None  # "aac1" is not a name


# -- mpv track-list ------------------------------------------------------------------------------------------------------


def test_real_la1_track_list_is_named_and_deduplicated():
    """mpv repeats the muxed audio once per HLS variant (program): one entry each, the one playing kept."""
    named = T.label_player_tracks(LA1_TRACKS, T.parse_master_media(LA1_MASTER))
    audio = [(t["id"], t["label"], t["selected"]) for t in named if t["type"] == "audio"]
    subs = [(t["id"], t["label"], t["selected"]) for t in named if t["type"] == "sub"]
    assert audio == [(3, "Español", True), (1, "Versión original", False), (2, "Audiodescripción", False)]
    assert subs == [(1, "Español", True), (2, "Inglés", False), (3, "Gallego", False), (4, "Catalán", False),
                    (5, "Euskera", False)]
    assert next(t for t in named if t["id"] == 3 and t["type"] == "audio")["name"] == "Castellano"
    # without the master (mpvd could not read it) the names come from the codes and flags alone
    alone = T.label_player_tracks(LA1_TRACKS)
    assert [t["label"] for t in alone] == [t["label"] for t in named]
    assert T.badges(alone) == ["CC", "VO", "AD"]


def test_player_tracks_flags_and_external_files():
    tl = [
        {"id": 1, "type": "video", "selected": True, "program-id": 0},
        {"id": 1, "type": "audio", "lang": "eng", "title": "hearing impaired", "hearing-impaired": True},
        {"id": 2, "type": "audio", "lang": "spa", "title": "visual impaired", "visual-impaired": True},
        {"id": 1, "type": "sub", "lang": "eng", "hearing-impaired": True, "codec": "subrip"},
        {"id": 2, "type": "sub", "lang": "spa", "forced": True, "codec": "ass"},
        {"id": 3, "type": "sub", "lang": "es", "codec": "eia_608"},
        {"id": 4, "type": "sub", "lang": "es", "codec": "subrip", "external": True, "title": "mis subtítulos"},
        {"id": 5, "type": "sub", "codec": "hdmv_pgs_subtitle", "image": True},
    ]
    named = T.label_player_tracks(tl)
    assert [(t["type"], t["id"], t["label"]) for t in named] == [
        ("audio", 1, "Inglés"), ("audio", 2, "Audiodescripción"),
        ("sub", 1, "Inglés · para sordos"), ("sub", 2, "Español · forzados"), ("sub", 3, "Español · CC"),
        ("sub", 4, "Español")]


def test_ffprobe_streams_as_tracks():
    streams = [{"codec_type": "video", "codec_name": "h264", "height": 720},
               {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "qaa"},
                "disposition": {"visual_impaired": 0}},
               {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "ads"},
                "disposition": {"visual_impaired": 1}},
               {"codec_type": "subtitle", "codec_name": "webvtt", "tags": {"language": "es"}},
               {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "spa"}},
               {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "spa"}}]  # other variant
    s = T.summary(T.label_player_tracks(_as_track_list(streams)), "probe")
    assert [a["label"] for a in s["audio"]] == ["Español", "Versión original", "Audiodescripción"]
    assert s["badges"] == ["CC", "VO", "AD"]


# -- storage ----------------------------------------------------------------------------------------------------------------


def test_store_keeps_tracks_per_channel(tmp_path):
    store = IptvStore(tmp_path / "iptv.sqlite3")
    try:
        r = T.parse_master_media(LA1_MASTER)
        store.set_tracks("la1", T.summary([dict(t) for t in r], "master"), "master", r)
        assert store.tracks()["la1"]["badges"] == ["CC", "VO", "AD"]
        # a report from the player without renditions keeps the ones of the master
        store.set_tracks("la1", {"audio": [], "subs": [], "badges": ["VO"], "from": "player"}, "player")
        info = store.track_info("la1")
        assert info["source"] == "player" and info["summary"]["badges"] == ["VO"] and len(info["renditions"]) == 8
        assert store.track_info("nope") is None
    finally:
        store.close()


# -- RPC: iptv.tracks, badges in the lists, «Buscar en esta lista» -------------------------------------------------------

TV = """#EXTM3U
#EXTINF:-1 tvg-id="La1.TV" group-title="Generalistas",La 1
{base}/rtve/la1.m3u8
#EXTINF:-1 tvg-id="La1.TV" group-title="Generalistas",La 1
https://stream.ads.ottera.tv/playlist.m3u8?network_id=15619
#EXTINF:-1 tvg-id="La2.TV" group-title="Generalistas",La 2
{base}/rtve/la2.m3u8
#EXTINF:-1 group-title="Informativos",Canal 24 Horas
{base}/rtve/24h.m3u8
#EXTINF:-1 group-title="Cataluña",TV3 Catalunya
{base}/cat/tv3.m3u8
#EXTINF:-1 group-title="Andalucía",Canal Sur Andalucía
{base}/and/cs.m3u8
"""
WORLD = """#EXTM3U
#EXTINF:-1 tvg-id="A.fr@SD" group-title="News",Télé Première
https://w.example/fr1.m3u8
#EXTINF:-1 tvg-id="B.fr@SD" group-title="General",France Télévision Deux
https://w.example/fr2.m3u8
#EXTINF:-1 tvg-id="C.be@SD" group-title="General",Télé Bruxelles
https://w.example/be.m3u8
#EXTINF:-1 tvg-id="D.es@SD" group-title="General",Tele Madrid
https://w.example/es.m3u8
"""
STATIONS_ES = [
    {"stationuuid": "u1", "name": "Ràdio Estel", "url": "https://r.example/estel", "countrycode": "ES", "votes": 5},
    {"stationuuid": "u2", "name": "Cadena Dial", "url": "https://r.example/dial", "countrycode": "ES", "votes": 9},
    {"stationuuid": "u3", "name": "Radio Nacional", "url": "https://r.example/rne", "countrycode": "ES", "votes": 7},
]
STATIONS_SEARCH = [
    {"stationuuid": "u9", "name": "Radio Paradise", "url": "https://r.example/rp", "countrycode": "US", "votes": 99},
    {"stationuuid": "u3", "name": "Radio Nacional", "url": "https://r.example/rne", "countrycode": "ES", "votes": 7},
]


@pytest.fixture
def web():
    files: dict[str, bytes] = {"/rtve/la1.m3u8": LA1_MASTER.encode(), "/cat/tv3.m3u8": CAT_MASTER.encode(),
                               "/rtve/la2.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n",
                               "/json/stations/bycountrycodeexact/ES": json.dumps(STATIONS_ES).encode(),
                               "/json/stations/search": json.dumps(STATIONS_SEARCH).encode(),
                               "/json/stations/topvote/100": json.dumps(STATIONS_SEARCH).encode()}
    hits: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            body = files.get(self.path.split("?", 1)[0])
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            if body:
                self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    files["/tv.m3u8"] = TV.format(base=base).encode()
    files["/world.m3u"] = WORLD.encode()
    try:
        yield base, hits
    finally:
        httpd.shutdown()


def run_server(tmp_path: Path, base: str, fn):
    sources = [Source("tdt_tv", "España TV", f"{base}/tv.m3u8", "tv", "es", country="es"),
               Source("iptv_org", "Mundo", f"{base}/world.m3u", "tv", "world", country_from_tvg_id=True)]

    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=2), iptv_sources=sources)
        await server.start()
        server.iptv.radio = RadioBrowser(server.iptv.http, base_url=base)
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_rpc_tracks_are_named_stored_and_shown_as_badges(tmp_path, web):
    base, hits = web

    async def fn(server, c):
        await c.call("iptv.refresh")
        items = {i["name"]: i for i in (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True,
                                                                         "merge": True}))["items"]}
        before = items["La 1"].get("badges")
        named = await c.call("iptv.tracks", {"tracks": LA1_TRACKS, "id": items["La 1"]["id"]})
        again = await c.call("iptv.tracks", {"tracks": LA1_TRACKS, "id": items["La 1"]["id"]})
        anonymous = await c.call("iptv.tracks", {"tracks": LA1_TRACKS})  # no channel: names only, nothing stored
        stored = await c.call("iptv.tracks.get", {"id": items["La 1"]["id"]})
        # the health check reads the master of 3Cat (ffprobe is not needed for that part)
        tv3 = server.iptv.get(items["TV3 Catalunya"]["id"])

        async def alive(ch, timeout=10.0):
            server.iptv._probed[ch.id] = []
            return True, "audio,video", None

        server.iptv.probe = alive
        await server.iptv.check_one(tv3)
        after = {i["name"]: i for i in (await c.call("iptv.channels", {"source": "tdt_tv", "compact": True,
                                                                        "merge": True}))["items"]}
        found = await c.call("iptv.search", {"q": "la 1", "source": "tdt_tv", "compact": True, "merge": True})
        favs_before = await c.call("iptv.favorites.toggle", {"id": items["La 1"]["id"]})
        favs = await c.call("iptv.favorites.list", {"compact": True})
        with pytest.raises(RpcError) as e:
            await c.call("iptv.tracks", {"tracks": "nope"})
        return before, named, again, anonymous, stored, after, found, favs_before, favs, e.value.code

    before, named, again, anonymous, stored, after, found, _, favs, code = run_server(tmp_path, base, fn)
    assert before is None
    assert [(t["type"], t["id"], t["label"], t["role"]) for t in named["tracks"]][:3] == [
        ("audio", 3, "Español", "main"), ("audio", 1, "Versión original", "vo"), ("audio", 2, "Audiodescripción", "ad")]
    assert named["badges"] == ["CC", "VO", "AD"] and named["channel"]
    assert again == named and anonymous["channel"] is None and anonymous["tracks"] == named["tracks"]
    assert hits.count("/rtve/la1.m3u8") == 1  # the master is read once per channel
    assert stored["source"] == "player" and stored["summary"]["badges"] == ["CC", "VO", "AD"]
    assert after["La 1"]["badges"] == ["CC", "VO", "AD"] and after["TV3 Catalunya"]["badges"] == ["CC", "VO", "AD"]
    assert "badges" not in after["La 2"] and found[0]["badges"] == ["CC", "VO", "AD"]
    assert favs[0]["name"] == "La 1" and favs[0]["badges"] == ["CC", "VO", "AD"]
    assert code == INVALID_PARAMS


def test_search_inside_one_list(tmp_path, web):
    base, _ = web

    async def fn(server, c):
        await c.call("iptv.refresh")
        chans = {i["name"]: i for i in (await c.call("iptv.channels", {"source": "tdt_tv", "merge": True}))["items"]}
        out = {}
        out["all"] = await c.call("iptv.search", {"q": "tele"})
        out["fr"] = await c.call("iptv.search", {"q": "tele", "source": "iptv_org", "country": "fr"})
        out["fr_accent"] = await c.call("iptv.search", {"q": "TÉLÉ premiere", "source": "iptv_org", "country": "FR"})
        out["es_list"] = await c.call("iptv.search", {"q": "andalucia", "source": "tdt_tv"})
        out["group"] = await c.call("iptv.search", {"q": "canal", "source": "tdt_tv", "group": "Informativos"})
        out["merged"] = await c.call("iptv.search", {"q": "la 1", "source": "tdt_tv", "merge": True})
        for name in ("La 2", "TV3 Catalunya"):
            await c.call("iptv.favorites.toggle", {"id": chans[name]["id"]})
        await c.call("iptv.play", {"id": chans["Canal 24 Horas"]["id"]})
        await c.call("iptv.play", {"id": chans["Canal Sur Andalucía"]["id"]})
        out["fav"] = await c.call("iptv.search", {"q": "catalunya", "scope": "favorites"})
        out["fav_none"] = await c.call("iptv.search", {"q": "canal", "scope": "favorites"})
        out["rec"] = await c.call("iptv.search", {"q": "canal", "scope": "recents"})
        out["radio_es"] = await c.call("iptv.search", {"q": "radio", "scope": "radio", "country": "es", "pool": 300})
        out["radio_es_accent"] = await c.call("iptv.search", {"q": "estel", "scope": "radio", "country": "es"})
        out["radio_all"] = await c.call("iptv.search", {"q": "radio", "scope": "radio"})
        out["radio_top"] = await c.call("iptv.search", {"q": "paradise", "scope": "radio_top", "pool": 100})
        with pytest.raises(RpcError) as e:
            await c.call("iptv.search", {"q": "x", "scope": "nope"})
        out["code"] = e.value.code
        return out

    out = run_server(tmp_path, base, fn)
    names = {k: [r["name"] for r in v] for k, v in out.items() if isinstance(v, list)}
    assert set(names["all"]) == {"Télé Première", "France Télévision Deux", "Télé Bruxelles", "Tele Madrid"}
    assert names["fr"] == ["Télé Première", "France Télévision Deux"]
    assert names["fr_accent"] == ["Télé Première"]
    assert names["es_list"] == ["Canal Sur Andalucía"]
    assert names["group"] == ["Canal 24 Horas"]
    assert names["merged"] == ["La 1"] and out["merged"][0]["alternatives"] == 1
    assert names["fav"] == ["TV3 Catalunya"] and names["fav_none"] == []
    assert names["rec"] == ["Canal 24 Horas", "Canal Sur Andalucía"]  # ranked like the global search
    assert names["radio_es"] == ["Ràdio Estel", "Radio Nacional"]  # only that country's list, accents ignored
    assert names["radio_es_accent"] == ["Ràdio Estel"]
    assert "Radio Paradise" in names["radio_all"] and "Radio Nacional" in names["radio_all"]
    assert names["radio_top"] == ["Radio Paradise"]
    assert out["code"] == INVALID_PARAMS
