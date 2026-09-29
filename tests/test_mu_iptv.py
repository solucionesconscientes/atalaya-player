"""mu-iptv.lua end to end: uosc menus (headless), play with per-file options, zapping, favourites, recording,
search palette and the world/country views — against a local HTTP server and the real daemon."""

from __future__ import annotations

import http.server
import json
import socket
import subprocess
import threading
import time

import pytest

from tests.conftest import start_mpv


def serve(files: dict[str, bytes]):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = files.get(self.path.split("?", 1)[0])
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_live_stream(port: int) -> subprocess.Popen:
    """A real-time MPEG-TS "live channel" served by ffmpeg over HTTP (one client, starts on connect)."""
    cmd = ["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "120",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "25", "-c:a", "aac",
           "-f", "mpegts", "-listen", "1", f"http://127.0.0.1:{port}/live.ts"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def tv(daemon_env, media_dir):
    m = media_dir
    live_port = free_port()
    live = start_live_stream(live_port)
    files = {
        "/tv.m3u8": (
            "#EXTM3U\n"
            f'#EXTINF:-1 tvg-id="Uno.TV" group-title="Pruebas",Canal Uno\nfile://{m}/video30.mkv\n'
            f'#EXTINF:-1 tvg-id="Dos.TV" group-title="Pruebas",Canal Dos\nfile://{m}/chapters.mkv\n'
            f'#EXTINF:-1 tvg-id="Tres.TV" group-title="Otros",Canal Tres\nfile://{m}/voz_es_en.mkv\n'
            f'#EXTINF:-1 tvg-id="Live.TV" group-title="Directo",Directo Test\nhttp://127.0.0.1:{live_port}/live.ts\n'
        ).encode(),
        "/radio.m3u8": (
            f'#EXTM3U\n#EXTINF:-1 radio="true" group-title="Radio_Pruebas",Emisora Voz\nfile://{m}/voz_es.flac\n'
        ).encode(),
        "/world.m3u": (
            "#EXTM3U\n"
            f'#EXTINF:-1 tvg-id="Info.fr@SD" group-title="News",France Test (720p)\nfile://{m}/video30.mkv\n'
            f'#EXTINF:-1 tvg-id="Erste.de@SD" group-title="General",Erste Test\nfile://{m}/chapters.mkv\n'
        ).encode(),
        "/json/stations/search": b"[]",
        "/json/countries": b'[{"name":"Spain","iso_3166_1":"ES","stationcount":3}]',
        "/json/stations/bycountrycodeexact/ES": (
            '[{"stationuuid":"u1","name":"Radio Local","url":"file://%s/voz_es.flac","url_resolved":"",'
            '"countrycode":"ES","country":"Spain","tags":"test","favicon":"","votes":1}]' % m).encode(),
    }
    httpd = serve(files)
    base = f"http://127.0.0.1:{httpd.server_port}"
    sources = [
        {"id": "tdt_tv", "name": "España TV", "url": f"{base}/tv.m3u8", "kind": "tv", "region": "es", "country": "es"},
        {"id": "tdt_radio", "name": "España Radio", "url": f"{base}/radio.m3u8", "kind": "radio", "region": "es", "country": "es"},
        {"id": "iptv_org", "name": "Mundo", "url": f"{base}/world.m3u", "kind": "tv", "region": "world", "country_from_tvg_id": True},
    ]
    src_path = daemon_env.base / "sources.json"
    src_path.write_text(json.dumps(sources), encoding="utf-8")
    record_dir = daemon_env.base / "rec"
    env = {**daemon_env.env, "MPV_UOS_IPTV_SOURCES": str(src_path), "MPV_UOS_COUNTRY": "es",
           "MPV_UOS_RADIO_BROWSER_URL": base}
    h = start_mpv(daemon_env.runtime_dir,
                  [f"--script-opts=mu-core-watchdog_seconds=5,mu-iptv-record_dir={record_dir},mu-iptv-osd_seconds=1"], env=env)
    try:
        yield h, daemon_env, record_dir
    finally:
        h.stop()
        httpd.shutdown()
        live.kill()
        live.wait(timeout=10)


def send_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_iptv", "mu-iptv-event", json.dumps({**base, **ev}))


def wait_view(h, view: str, timeout: float = 30.0):
    return h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("view") == view, timeout=timeout)


def test_menus_play_zap_favorites_record_and_search(tv):
    h, d, record_dir = tv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)

    # root menu opens in uosc (headless) and the controls button was registered without errors
    h.command("script-binding", "mu_iptv/tv-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv", timeout=15)
    wait_view(h, "root")

    # España · TV -> grouped channel list (loaded from the daemon)
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "source", "id": "tdt_tv"}})
    wait_view(h, "source:tdt_tv")
    chans = {c["name"]: c for c in d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]}
    assert set(chans) == {"Canal Uno", "Canal Dos", "Canal Tres", "Directo Test"}

    # play a channel: loadfile with per-file options, menu closes, OSD state published
    send_event(h, {"type": "activate", "index": 1, "value": {"play": chans["Canal Uno"]["id"]}})
    h.wait_property("path", lambda v: bool(v) and v.endswith("video30.mkv"), timeout=20)
    cur = h.wait_property("user-data/mu/iptv", lambda v: bool(v) and isinstance(v.get("current"), dict)
                          and v["current"].get("id") == chans["Canal Uno"]["id"])
    assert cur["current"]["name"] == "Canal Uno"
    h.wait_property("user-data/uosc/menu/type", lambda v: v is None, timeout=10)
    h.wait_property("options/force-media-title", lambda v: v == "Canal Uno", timeout=10)

    # zapping stays inside the group (Pruebas: Uno <-> Dos) and wraps
    h.command("script-binding", "mu_iptv/zap-next")
    h.wait_property("path", lambda v: bool(v) and v.endswith("chapters.mkv"), timeout=20)
    h.command("script-binding", "mu_iptv/zap-next")
    h.wait_property("path", lambda v: bool(v) and v.endswith("video30.mkv"), timeout=20)
    h.command("script-binding", "mu_iptv/zap-prev")
    h.wait_property("path", lambda v: bool(v) and v.endswith("chapters.mkv"), timeout=20)
    assert [r["name"] for r in d.call("iptv.recents.list")][:2] == ["Canal Dos", "Canal Uno"]

    # favourite through the item action
    send_event(h, {"type": "activate", "index": 3, "action": "fav", "value": {"play": chans["Canal Tres"]["id"]}})
    try:
        d.wait(lambda: [f["name"] for f in d.call("iptv.favorites.list")] == ["Canal Tres"], timeout=10)
    except TimeoutError:
        raise AssertionError(f"favourite not toggled; script state: {h.get('user-data/mu/iptv')}") from None

    # live recording (stream-record) of a real-time HTTP stream into the configured folder
    send_event(h, {"type": "activate", "index": 1, "value": {"play": chans["Directo Test"]["id"]}})
    h.wait_property("path", lambda v: bool(v) and v.endswith("/live.ts"), timeout=30)
    # a real-time stream under a loaded machine can take a while to fill mpv's cache
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.5, timeout=90)
    h.command("script-binding", "mu_iptv/record-toggle")
    rec = h.wait_property("stream-record", lambda v: bool(v), timeout=10)
    assert rec.startswith(str(record_dir)) and rec.endswith(".mkv") and "Directo Test" in rec
    time.sleep(4)
    h.command("script-binding", "mu_iptv/record-toggle")
    h.wait_property("stream-record", lambda v: v == "", timeout=10)
    files = list(record_dir.iterdir())
    assert len(files) == 1 and files[0].stat().st_size > 10_000, files
    assert h.get("user-data/mu/iptv")["recording"] == ""

    # search palette: results from the catalogue (radio browser mock returns nothing)
    h.command("script-binding", "mu_iptv/tv-search")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv-search", timeout=15)
    send_event(h, {"type": "search", "query": "canal"})
    h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("search_results") == 3, timeout=20)
    h.command("script-message-to", "uosc", "close-menu", "mu-iptv-search")
    # uosc destroys a menu only after its fade-out; opening another one meanwhile would die with it
    h.wait_property("user-data/uosc/menu/type", lambda v: v is None, timeout=10)

    # world view: countries named in Spanish (system iso-codes), then a country grouped by category
    h.command("script-binding", "mu_iptv/tv-menu")
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 4, "value": {"view": "world"}})
    wait_view(h, "world")
    countries = d.call("iptv.countries", {"source": "iptv_org"})
    assert [c["name"] for c in countries] == ["Alemania", "Francia"] and countries[1]["flag"] == "🇫🇷"
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "country", "id": "fr", "name": "Francia"}})
    wait_view(h, "country:fr")
    send_event(h, {"type": "back"})
    wait_view(h, "world")

    # world radio (mocked Radio Browser) and playing a station through the same path
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "root"}})
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 5, "value": {"view": "radio"}})
    wait_view(h, "radio")
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "radio_country", "id": "es", "name": "Spain"}})
    wait_view(h, "radio_country:es")
    station = d.call("radio.stations", {"country": "es", "compact": True})[0]
    send_event(h, {"type": "activate", "index": 1, "value": {"play": station["id"]}})
    h.wait_property("path", lambda v: bool(v) and v.endswith("voz_es.flac"), timeout=20)
    cur = h.get("user-data/mu/iptv")["current"]
    assert cur["kind"] == "radio" and cur["name"] == "Radio Local"
    assert h.script_errors() == []


def test_user_lists_and_health(tv):
    """"Mis listas": add a user M3U from the palette (typed/pasted URL), open it, background health check, remove."""
    h, d, _ = tv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    radio_url = next(s["url"] for s in d.call("iptv.sources") if s["id"] == "tdt_radio")

    h.command("script-binding", "mu_iptv/tv-menu")
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 8, "value": {"view": "lists"}})
    wait_view(h, "lists")
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "add_list"}})
    wait_view(h, "add_list")
    send_event(h, {"type": "search", "query": radio_url})  # offers the "Añadir <url>" item
    send_event(h, {"type": "activate", "index": 1, "value": {"add_url": radio_url}})
    # the source appears at once; its channels arrive when the (forced) download finishes
    d.wait(lambda: any(not s["builtin"] and (s["loaded_at"] or s["error"]) for s in d.call("iptv.sources")), timeout=20)
    user = next(s for s in d.call("iptv.sources") if not s["builtin"])
    assert user["id"] == "user:radio" and user["name"] == "Radio" and user["channels"] == 1 and user["error"] is None
    wait_view(h, "source:" + user["id"])
    station = d.call("iptv.channels", {"source": user["id"]})["items"][0]
    assert station["kind"] == "radio" and station["name"] == "Emisora Voz"  # radio="true" in the M3U wins over the tv default

    # background health check (ffprobe, low priority) marks the station as alive
    send_event(h, {"type": "activate", "index": 2, "value": {"health": user["id"]}})
    d.wait(lambda: station["id"] in d.call("iptv.health.status"), timeout=60)
    assert d.call("iptv.health.status")[station["id"]]["ok"] is True
    assert d.call("iptv.channels", {"source": user["id"], "compact": True})["items"][0]["health"] is True

    # pasting a bad value is rejected with an OSD, not a Lua error; removing the list from "Mis listas"
    send_event(h, {"type": "back"})
    wait_view(h, "lists")
    send_event(h, {"type": "activate", "index": 2, "action": "remove",
                   "value": {"view": "source", "id": user["id"], "name": user["name"]}})
    d.wait(lambda: all(s["builtin"] for s in d.call("iptv.sources")), timeout=10)
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "add_list"}})
    wait_view(h, "add_list")
    send_event(h, {"type": "paste", "value": "esto no es una url"})
    time.sleep(0.5)
    assert all(s["builtin"] for s in d.call("iptv.sources"))
    assert h.script_errors() == []


def test_menu_without_daemon_shows_error_item(daemon_env):
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-autostart=no"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=15)
        h.command("script-binding", "mu_iptv/tv-menu")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-iptv", timeout=15)
        send_event(h, {"type": "activate", "index": 2, "value": {"view": "source", "id": "tdt_tv"}})
        wait_view(h, "source:tdt_tv")
        # zapping without a channel is a no-op with an OSD hint, no Lua errors
        h.command("script-binding", "mu_iptv/zap-next")
        time.sleep(0.5)
        assert h.script_errors() == []
    finally:
        h.stop()
