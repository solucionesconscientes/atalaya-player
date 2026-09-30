"""H30 end to end (headless mpv + daemon + local HTTP server, no internet): the channel's own tracks with readable
names in «Audio y subtítulos del canal» (an HLS like RTVE's: Spanish audio muxed, «qaa» original version and «ads»
audio description as renditions, WebVTT subtitles), the CC / VO / AD hints in the lists, and «Buscar en esta lista»
inside a country of Mundo and inside Favoritos."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import start_mpv
from tests.test_mu_iptv import send_event, wait_view
from tests.test_mu_iptv_live import Web

MASTER = (
    "#EXTM3U\n"
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audios",NAME="Castellano",LANGUAGE="spa",CHANNELS="2",DEFAULT=YES,AUTOSELECT=YES\n'
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audios",NAME="Original",LANGUAGE="qaa",CHANNELS="2",DEFAULT=NO,URI="qaa.m3u8"\n'
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audios",NAME="Audio Descripcion",LANGUAGE="ads",CHANNELS="2",DEFAULT=NO,'
    'AUTOSELECT=YES,CHARACTERISTICS="public.accessibility.describes-video",URI="ads.m3u8"\n'
    '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subtitulos",NAME="Español",LANGUAGE="es",DEFAULT=NO,FORCED=NO,URI="es.m3u8"\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=320x180,CODECS="avc1.640015,mp4a.40.2",AUDIO="audios",'
    'SUBTITLES="subtitulos"\nvideo.m3u8\n'
)


@pytest.fixture(scope="module")
def tracks_hls(tmp_path_factory) -> Path:
    """30 s VOD HLS shaped like RTVE's master: video + Spanish audio muxed, two audio renditions, WebVTT."""
    d = tmp_path_factory.mktemp("hls-tracks")
    hls = ["-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod"]
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000", "-t", "30", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "25", "-c:a", "aac", "-metadata:s:a:0", "language=spa", *hls,
                    "-hls_segment_filename", str(d / "v%03d.ts"), str(d / "video.m3u8")],
                   check=True, capture_output=True, timeout=120)
    for name, freq in (("qaa", 660), ("ads", 880)):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000",
                        "-t", "30", "-c:a", "aac", *hls, "-hls_segment_filename", str(d / f"{name}%03d.ts"),
                        str(d / f"{name}.m3u8")], check=True, capture_output=True, timeout=120)
    vtt = "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:126000,LOCAL:00:00:00.000\n\n"
    vtt += "".join(f"00:00:{i * 2:02d}.000 --> 00:00:{i * 2 + 1:02d}.500\nLínea {i}\n\n" for i in range(15))
    (d / "es0.vtt").write_text(vtt, encoding="utf-8")
    (d / "es.m3u8").write_text("#EXTM3U\n#EXT-X-TARGETDURATION:30\n#EXT-X-VERSION:3\n#EXT-X-MEDIA-SEQUENCE:0\n"
                               "#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:30.0,\nes0.vtt\n#EXT-X-ENDLIST\n", encoding="utf-8")
    (d / "master.m3u8").write_text(MASTER, encoding="utf-8")
    return d


@pytest.fixture
def tv(daemon_env, media_dir, tracks_hls):
    m = media_dir
    files: dict[str, bytes] = {}
    web = Web(files, tracks_hls)  # serves tracks_hls under /hls/ (to browser User-Agents, as mpvd sends)
    b = web.base
    files["/tv.m3u8"] = (
        "#EXTM3U\n"
        f'#EXTINF:-1 group-title="Generalistas",Canal Pistas\n{b}/hls/master.m3u8\n'
        f'#EXTINF:-1 group-title="Generalistas",Canal Uno\nfile://{m}/video30.mkv\n'
        f'#EXTINF:-1 group-title="Autonómicos",Canal Dos\nfile://{m}/chapters.mkv\n'
    ).encode()
    files["/radio.m3u8"] = b"#EXTM3U\n"
    files["/world.m3u"] = (
        "#EXTM3U\n"
        f'#EXTINF:-1 tvg-id="A.fr@SD" group-title="News",Télé Première\nfile://{m}/video30.mkv\n'
        f'#EXTINF:-1 tvg-id="B.fr@SD" group-title="General",France Télévision Deux\nfile://{m}/chapters.mkv\n'
        f'#EXTINF:-1 tvg-id="C.fr@SD" group-title="General",Radio Canal France\nfile://{m}/voz_es_en.mkv\n'
        f'#EXTINF:-1 tvg-id="D.be@SD" group-title="General",Télé Bruxelles\nfile://{m}/serie/ep01.mkv\n'
        f'#EXTINF:-1 tvg-id="E.es@SD" group-title="General",Tele Madrid\nfile://{m}/serie/ep02.mkv\n'
    ).encode()
    files["/json/stations/search"] = b"[]"
    sources = [
        {"id": "tdt_tv", "name": "España TV", "url": f"{b}/tv.m3u8", "kind": "tv", "region": "es", "country": "es"},
        {"id": "tdt_radio", "name": "España Radio", "url": f"{b}/radio.m3u8", "kind": "radio", "region": "es",
         "country": "es"},
        {"id": "iptv_org", "name": "Mundo", "url": f"{b}/world.m3u", "kind": "tv", "region": "world",
         "country_from_tvg_id": True},
    ]
    src_path = daemon_env.base / "sources.json"
    src_path.write_text(json.dumps(sources), encoding="utf-8")
    env = {**daemon_env.env, "MPV_UOS_IPTV_SOURCES": str(src_path), "MPV_UOS_RADIO_BROWSER_URL": b,
           "MPV_UOS_COUNTRY": "es"}
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=5,mu-iptv-osd_seconds=1"], env=env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()
        web.close()


def menu_titles(h) -> list[str]:
    return [r["title"] for r in (h.get("user-data/mu/iptv-menu") or {}).get("items", [])]


def find_row(rows, title):
    for r in rows or []:
        if r.get("title") == title:
            return r
        found = find_row(r.get("items"), title)
        if found:
            return found
    return None


def wait_menu(h, predicate, timeout=20.0):
    return h.wait_property("user-data/mu/iptv-menu", lambda v: bool(v) and predicate(v), timeout=timeout)


def test_channel_tracks_menu_and_badges(tv):
    h, d = tv
    chans = {c["name"]: c for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}
    assert "badges" not in chans["Canal Pistas"]  # nothing known before it plays

    h.command("script-message-to", "mu_iptv", "mu-iptv-play", chans["Canal Pistas"]["id"])
    h.wait_property("path", lambda v: bool(v) and v.endswith("/hls/master.m3u8"), timeout=30)
    st = h.wait_property("user-data/mu/iptv", lambda v: bool(v) and len(v.get("tracks") or []) >= 4, timeout=40)
    named = [(t["type"], t["label"]) for t in st["tracks"]]
    assert named == [("audio", "Español"), ("audio", "Versión original"), ("audio", "Audiodescripción"),
                     ("sub", "Español")]
    assert st["badges"] == ["CC", "VO", "AD"]
    ids = {t["label"]: t["id"] for t in st["tracks"] if t["type"] == "audio"}

    # «Audio y subtítulos del canal» with its own key
    h.command("script-binding", "mu_iptv/tv-tracks")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv", timeout=15)
    wait_view(h, "tracks")
    menu = wait_menu(h, lambda v: "Versión original" in [r["title"] for r in v["items"]])
    assert [r["title"] for r in menu["items"]] == [
        "Audio", "Español", "Versión original", "Audiodescripción", "Subtítulos", "Sin subtítulos", "Español"]
    assert menu["items"][2]["hint"] == "VO" and menu["items"][3]["hint"] == "AD"

    # Enter on «Versión original» switches the audio (and the menu stays open, now marking it)
    send_event(h, {"type": "activate", "index": 3, "keep_open": True,
                   "value": {"track": {"type": "audio", "id": ids["Versión original"], "label": "Versión original"}}})
    h.wait_property("aid", lambda v: v == ids["Versión original"], timeout=10)
    send_event(h, {"type": "activate", "index": 4, "keep_open": True,
                   "value": {"track": {"type": "audio", "id": ids["Audiodescripción"], "label": "Audiodescripción"}}})
    h.wait_property("aid", lambda v: v == ids["Audiodescripción"], timeout=10)
    send_event(h, {"type": "activate", "index": 6, "keep_open": True,
                   "value": {"track": {"type": "sub", "id": "no", "label": "Sin subtítulos"}}})
    h.wait_property("sid", lambda v: v is False, timeout=10)
    # «ads»/«qaa» are not languages: mu-prefs does not make them the preferred audio of every file
    alang = h.get("alang") or []
    assert not alang or alang[0].lower() not in ("ads", "qaa")
    h.command("script-message-to", "uosc", "close-menu", "mu-iptv")
    h.wait_property("user-data/uosc/menu/type", lambda v: v is None, timeout=10)

    # mpvd stored what the channel carries: CC VO AD in the lists and in the root menu entry
    chans = {c["name"]: c for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}
    assert chans["Canal Pistas"]["badges"] == ["CC", "VO", "AD"] and "badges" not in chans["Canal Uno"]
    h.command("script-binding", "mu_iptv/tv-menu")
    wait_view(h, "root")
    root = wait_menu(h, lambda v: v.get("title") == "TV y radio")
    row = find_row(root["items"], "Audio y subtítulos del canal")
    assert row is not None and row["hint"] == "CC VO AD"
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "source", "id": "tdt_tv"}})
    wait_view(h, "source:tdt_tv")
    lst = wait_menu(h, lambda v: find_row(v["items"], "Canal Pistas") is not None)
    assert "CC VO AD" in find_row(lst["items"], "Canal Pistas")["hint"]
    assert lst["items"][0]["title"] == "Buscar en esta lista…"


def test_search_inside_a_country_and_favorites(tv):
    h, d = tv
    h.command("script-binding", "mu_iptv/tv-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv", timeout=15)
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 4, "value": {"view": "world"}})
    wait_view(h, "world")
    wait_menu(h, lambda v: v.get("title") == "Mundo · TV" and len(v["items"]) == 3)  # its answer came
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "country", "id": "fr", "name": "Francia"}})
    wait_view(h, "country:fr")
    menu = wait_menu(h, lambda v: v.get("title") == "Francia" and len(v["items"]) > 1)
    first = menu["items"][0]
    assert first["title"] == "Buscar en esta lista…"

    # the palette searches only this country's channels, accents ignored (the same engine as alt+f)
    send_event(h, {"type": "activate", "index": 1, "value": first["value"]})
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv-search", timeout=15)
    h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("search_scope") == "country:fr", timeout=10)
    send_event(h, {"type": "search", "query": "tele"})
    h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("search_results") == 2, timeout=20)
    assert sorted(menu_titles(h)) == ["France Télévision Deux", "Télé Première"]  # no Bruxelles, no Madrid
    send_event(h, {"type": "search", "query": "canal"})
    wait_menu(h, lambda v: [r["title"] for r in v["items"]] == ["Radio Canal France"])
    # ⌫ on the empty palette goes back to the country
    send_event(h, {"type": "back"})
    wait_view(h, "country:fr")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv", timeout=15)

    # Favoritos: only the favourites are searched
    tdt = {c["name"]: c for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}
    world = {c["name"]: c for c in d.call("iptv.channels", {"source": "iptv_org", "compact": True})["items"]}
    d.call("iptv.favorites.toggle", {"id": tdt["Canal Uno"]["id"]})
    d.call("iptv.favorites.toggle", {"id": world["Télé Première"]["id"]})
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "root"}})
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 6, "value": {"view": "favorites"}})
    wait_view(h, "favorites")
    favs = wait_menu(h, lambda v: v.get("title") == "Favoritos" and len(v["items"]) == 3)
    assert [r["title"] for r in favs["items"]] == ["Buscar en esta lista…", "Canal Uno", "Télé Première"]
    send_event(h, {"type": "activate", "index": 1, "value": favs["items"][0]["value"]})
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv-search", timeout=15)
    h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("search_scope") == "favorites", timeout=10)
    send_event(h, {"type": "search", "query": "canal"})
    wait_menu(h, lambda v: [r["title"] for r in v["items"]] == ["Canal Uno"])  # Canal Dos/Pistas are not favourites
    send_event(h, {"type": "search", "query": "PREMIERE"})
    rows = wait_menu(h, lambda v: [r["title"] for r in v["items"]] == ["Télé Première"])["items"]

    # a result plays like any channel (and closes the palette)
    send_event(h, {"type": "activate", "index": 1, "value": rows[0]["value"]})
    h.wait_property("path", lambda v: bool(v) and v.endswith("video30.mkv"), timeout=20)
    h.wait_property("user-data/uosc/menu/type", lambda v: v is None, timeout=10)
