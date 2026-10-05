"""Live channels end to end (headless mpv + daemon + local HTTP server, no internet): no watch_later for
channels, HLS options (fresh connection per request, list options merged), browser User-Agent when the list
gives none, quality/ads/duplicate hints, automatic fallback to another copy of a channel, Spanish labels."""

from __future__ import annotations

import http.server
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import start_mpv
from tests.test_mu_iptv import free_port, send_event, wait_view


@pytest.fixture(scope="module")
def hls_dir(tmp_path_factory) -> Path:
    """A 30 s VOD HLS (320x180, 25 fps, 1 s segments) made with ffmpeg."""
    d = tmp_path_factory.mktemp("hls")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=48000", "-t", "30", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "25", "-c:a", "aac", "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
         "-hls_segment_filename", str(d / "seg%03d.ts"), str(d / "media.m3u8")],
        check=True, capture_output=True, timeout=120)
    return d


class Web:
    """Local server for playlists and streams. /hls/ and /plain/ answer 403 unless the User-Agent looks like a
    browser (as Canal Sur's CDN does with mpv's default "libmpv"). HTTP/1.1 keep-alive, so connection reuse
    is visible in ``requests`` (client port per request)."""

    def __init__(self, files: dict[str, bytes], hls: Path):
        self.files = files
        self.hls = hls
        self.requests: list[dict[str, str | int]] = []
        web = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                ua = self.headers.get("User-Agent", "")
                body = web.body(path)
                status = 200 if body is not None else 404
                if path.startswith(("/hls/", "/plain/")) and "Mozilla/5.0" not in ua:
                    status, body = 403, None
                web.requests.append({"path": path, "port": self.client_address[1], "ua": ua, "status": status})
                self.send_response(status)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                if body:
                    self.wfile.write(body)

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass  # players drop connections mid-segment when they stop or switch: expected

        self.httpd = Server(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def body(self, path: str) -> bytes | None:
        if path in self.files:
            return self.files[path]
        if path.startswith("/hls/"):
            f = self.hls / path[len("/hls/"):]
            return f.read_bytes() if f.is_file() and f.parent == self.hls else None
        return None

    def segment_requests(self, since: int = 0) -> list[dict[str, str | int]]:
        return [r for r in self.requests[since:] if str(r["path"]).endswith(".ts")]

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.fixture
def live(daemon_env, media_dir, hls_dir, request):
    m = media_dir
    dead = free_port()  # nothing listens here: "connection refused"
    files: dict[str, bytes] = {}
    web = Web(files, hls_dir)
    b = web.base
    files["/hls/master.m3u8"] = (
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720,FRAME-RATE=50.000,CODECS=\"avc1.64001f,mp4a.40.2\"\n"
        "media.m3u8\n").encode()
    # no RESOLUTION/FRAME-RATE: the health check takes them from ffprobe (180p25)
    files["/plain/master.m3u8"] = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\n{b}/hls/media.m3u8\n".encode()
    files["/tv.m3u8"] = (
        "#EXTM3U\n"
        f'#EXTINF:-1 group-title="Directos",Canal HLS\n{b}/hls/master.m3u8\n'
        f'#EXTINF:-1 group-title="Directos",Canal Lista\n'
        "#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Lista)\n#EXTVLCOPT:demuxer-lavf-o=max_reload=5\n"
        f"{b}/plain/master.m3u8\n"
        f'#EXTINF:-1 group-title="Dobles",Canal Doble\nhttp://127.0.0.1:{dead}/dead/playlist.m3u8\n'
        f'#EXTINF:-1 group-title="Dobles",Canal Doble\nfile://{m}/video30.mkv\n'
        f'#EXTINF:-1 group-title="Dobles",Otro Canal\nfile://{m}/chapters.mkv\n'
        f'#EXTINF:-1 group-title="Archivos",Directo Uno\n{b}/hls/media.m3u8\n'
        f'#EXTINF:-1 group-title="Archivos",Archivo Dos\nfile://{m}/serie/ep02.mkv\n'
    ).encode()
    files["/radio.m3u8"] = (
        f'#EXTM3U\n#EXTINF:-1 radio="true" group-title="Radio_C. Valenciana",Emisora Voz\nfile://{m}/voz_es.flac\n'
    ).encode()
    files["/world.m3u"] = (  # distinct URLs: a list repeating a URL keeps only its first entry
        "#EXTM3U\n"
        f'#EXTINF:-1 tvg-id="Uno.es@SD" group-title="General;Public",Uno ES\nfile://{m}/video30.mkv\n'
        f'#EXTINF:-1 tvg-id="Dos.es@SD" group-title="Undefined",Dos ES\nfile://{m}/chapters.mkv\n'
        f'#EXTINF:-1 tvg-id="Info.fr@SD" group-title="News",France Test (720p)\nfile://{m}/voz_es_en.mkv\n'
        f'#EXTINF:-1 tvg-id="Erste.de@SD" group-title="General",Erste Test\nfile://{m}/serie/ep01.mkv\n'
        f'#EXTINF:-1 tvg-id="Beeb.uk@SD" group-title="News",Beeb Test\nfile://{m}/serie/ep03.mkv\n'
    ).encode()
    files["/json/countries"] = json.dumps([{"name": "Germany", "iso_3166_1": "DE", "stationcount": 3000},
                                           {"name": "Spain", "iso_3166_1": "ES", "stationcount": 900},
                                           {"name": "Afghanistan", "iso_3166_1": "AF", "stationcount": 5}]).encode()
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
    # watch_later on as for users (conftest turns it off for every other test): later options win
    extra = getattr(request, "param", [])
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=5,mu-iptv-osd_seconds=1", *extra],
                  env=env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env, web
    finally:
        h.stop()
        web.close()


def channels(d, source="tdt_tv", merge=True):
    return {c["name"]: c for c in d.call("iptv.channels", {"source": source, "compact": True, "merge": merge})["items"]}


def wait_menu(h, title: str) -> dict:
    """The menu mu-iptv handed to uosc once loaded (published in user-data/mu/iptv-menu)."""
    return h.wait_property("user-data/mu/iptv-menu", lambda v: bool(v) and v.get("title") == title and bool(v["items"])
                           and v["items"][0]["title"] != "Cargando…", timeout=15)


def play(h, channel_id):
    send_event(h, {"type": "activate", "index": 1, "value": {"play": channel_id}})


def test_hls_options_user_agent_and_fresh_connections(live):
    h, d, web = live
    chans = channels(d)

    # no User-Agent in the list: mpv sends a browser one, so the "CDN" does not answer 403
    play(h, chans["Canal HLS"]["id"])
    h.wait_property("path", lambda v: bool(v) and v.endswith("/hls/master.m3u8"), timeout=20)
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 1, timeout=30)
    assert h.get("options/demuxer-lavf-o") == {"http_persistent": "0", "seg_max_retry": "3"}
    assert "Chrome/" in h.get("options/user-agent") and h.get("options/save-position-on-quit") is False
    hls = [r for r in web.requests if str(r["path"]).startswith("/hls/")]
    assert hls and all(r["status"] == 200 and "Chrome/" in str(r["ua"]) for r in hls), hls[:3]
    # http_persistent=0: every segment on its own connection
    time.sleep(2)
    segs = web.segment_requests()
    assert len(segs) >= 3 and len({r["port"] for r in segs}) == len(segs), segs

    # control: the same stream without our options reuses one connection for several segments (and the default
    # user agent is refused)
    mark = len(web.requests)
    h.command("loadfile", f"{web.base}/hls/master.m3u8", "replace", -1,
              {"user-agent": "Mozilla/5.0 (control)", "force-media-title": "control"})
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 1, timeout=30)
    time.sleep(2)
    segs = web.segment_requests(mark)
    assert len(segs) >= 3 and len({r["port"] for r in segs}) < len(segs), segs
    mark = len(web.requests)
    h.command("loadfile", f"{web.base}/hls/master.m3u8", "replace", -1, {"force-media-title": "libmpv"})
    d.wait(lambda: len(web.requests) > mark, timeout=20)
    assert web.requests[mark]["status"] == 403 and "Mozilla" not in str(web.requests[mark]["ua"])
    h.command("stop")  # (mpv's yt-dlp fallback would try next; not what this is about)

    # the list's own User-Agent and demuxer-lavf-o are kept; ours are added, not replacing them
    play(h, chans["Canal Lista"]["id"])
    h.wait_property("path", lambda v: bool(v) and v.endswith("/plain/master.m3u8"), timeout=20)
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.5, timeout=30)
    assert h.get("options/demuxer-lavf-o") == {"max_reload": "5", "http_persistent": "0", "seg_max_retry": "3"}
    assert h.get("options/user-agent") == "Mozilla/5.0 (Lista)"
    assert h.script_errors() == []


@pytest.mark.parametrize("live", [["--resume-playback=yes", "--save-position-on-quit=yes"]], indirect=True)
def test_channels_never_resume_nor_save_watch_later(live, media_dir):
    h, d, _ = live
    wl = d.runtime_dir / "watch_later"

    def saved() -> list[str]:
        """Paths with a watch_later entry (write-filename-in-watch-later-config puts them in a comment); the
        "redirect entry" files mpv adds for the parent directories are left out."""
        if not wl.is_dir():
            return []
        first = [f.read_text(encoding="utf-8", errors="replace").splitlines()[0][2:] for f in wl.iterdir()]
        return sorted(p for p in first if p != "redirect entry")

    def playing(pred=lambda v: v >= 0, timeout=20.0):
        return h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and pred(v), timeout=timeout)

    def saltar_a(segundo):
        """H63 · que haya `time-pos` NO quiere decir que se pueda saltar: en un HLS el demuxer puede no tener aún
        el rango y mpv contesta «error running command» al seek. `seekable` es el estado que lo dice, así que se
        espera por él en vez de confiar en que ya habrá llegado."""
        h.wait_property("seekable", lambda v: v is True, timeout=20)
        h.command("seek", segundo, "absolute")

    chans = channels(d)
    live_url = d.call("iptv.play", {"id": chans["Directo Uno"]["id"]})["url"]  # HLS over http
    ua = {"user-agent": "Mozilla/5.0 (control)"}
    ep02 = f"{media_dir}/serie/ep02.mkv"

    # the setup is meaningful: with these options an entry for the channel URL resumes playback, and leaving a
    # stream by loading another one (what zapping does) writes an entry
    h.command("loadfile", live_url, "replace", -1, ua)
    playing()
    saltar_a(12)
    playing(lambda v: v > 11)
    h.command("loadfile", f"file://{media_dir}/chapters.mkv", "replace")
    h.wait_property("path", lambda v: bool(v) and v.endswith("chapters.mkv"), timeout=20)
    assert live_url in saved(), saved()
    h.command("loadfile", live_url, "replace", -1, ua)
    assert playing(lambda v: v > 0.5) > 10  # resumed (mpv consumes the entry)

    # stale entries, as older versions left them, for the HLS channel and for a local-file channel
    for f in wl.iterdir():
        f.unlink()
    saltar_a(12)
    playing(lambda v: v > 11)
    h.command("write-watch-later-config")
    h.command("loadfile", ep02, "replace")
    playing()
    saltar_a(12)
    playing(lambda v: v > 11)
    h.command("write-watch-later-config")
    h.command("stop")
    h.wait_property("idle-active", lambda v: v is True, timeout=10)
    assert saved() == sorted([live_url, ep02]), saved()

    # through mu-iptv the entry is deleted before loading: the channel starts from the beginning
    play(h, chans["Directo Uno"]["id"])
    h.wait_property("path", lambda v: v == live_url, timeout=20)
    assert playing() < 5
    assert saved() == [ep02]
    playing(lambda v: v > 1)

    # zapping away writes nothing for the channel left (save-position-on-quit=no per file); the file:// channel
    # also starts from 0 (mpv keys it by its plain path)
    h.command("script-binding", "mu_iptv/zap-next")
    h.wait_property("path", lambda v: bool(v) and v.endswith("ep02.mkv"), timeout=20)
    assert playing() < 5
    assert saved() == []
    playing(lambda v: v > 1)
    h.stop()
    assert saved() == []
    assert h.proc.returncode == 0


def test_duplicates_fallback_quality_and_spanish_labels(live):
    h, d, web = live
    raw = d.call("iptv.channels", {"source": "tdt_tv", "compact": True})["items"]
    assert [c["name"] for c in raw].count("Canal Doble") == 2
    chans = channels(d)
    assert list(chans) == ["Canal HLS", "Canal Lista", "Canal Doble", "Otro Canal", "Directo Uno", "Archivo Dos"]
    doble = chans["Canal Doble"]
    assert doble["alternatives"] == 1

    # the preferred copy does not open: mu-iptv tries the next one by itself
    play(h, doble["id"])
    h.wait_property("path", lambda v: bool(v) and v.endswith("video30.mkv"), timeout=30)
    st = h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("fallbacks") == 1, timeout=10)
    assert st["alternatives_left"] == 0 and st["current"]["name"] == "Canal Doble"
    assert "Probando otra fuente de «Canal Doble»" in h.log_text()
    # zapping skips the other copy: Canal Doble -> Otro Canal -> Canal Doble
    h.command("script-binding", "mu_iptv/zap-next")
    h.wait_property("path", lambda v: bool(v) and v.endswith("chapters.mkv"), timeout=20)
    assert h.get("user-data/mu/iptv")["current"]["name"] == "Otro Canal"

    # background health check: quality from the HLS master playlist (or ffprobe when it says nothing)
    job = d.call("iptv.health.check", {"source": "tdt_tv"})
    d.wait(lambda: d.call("jobs.get", {"id": job["id"]})["status"] in ("done", "failed"), timeout=90)
    chans = channels(d)
    assert chans["Canal HLS"]["health"] is True and chans["Canal HLS"]["quality_label"] == "720p50 · 1,2 Mb"
    assert chans["Canal HLS"]["low_bitrate"] is True
    assert chans["Canal Lista"]["quality_label"] == "180p25 · 0,5 Mb" and "low_bitrate" not in chans["Canal Lista"]

    # menus: hints and Spanish labels (what mu-iptv handed to uosc)
    h.command("script-binding", "mu_iptv/tv-menu")
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "source", "id": "tdt_tv"}})
    wait_view(h, "source:tdt_tv")
    menu = wait_menu(h, "España · TV")
    groups = {g["title"]: g for g in menu["items"] if g.get("items")}
    hints = {c["title"]: c["hint"] for c in groups["Directos"]["items"]}
    # H39/E2: tras la comprobación, cada canal dice además lo que dijo («✓ comprobado» o el motivo del fallo)
    assert hints == {"Canal HLS": "720p50 · 1,2 Mb · bitrate bajo · ✓ comprobado",
                     "Canal Lista": "180p25 · 0,5 Mb · ✓ comprobado"}
    # the preferred copy of Canal Doble is dead but the other one works: no ✕ on the merged entry, and H39/E2 says
    # how many of its copies answered (que es lo que de verdad te dice si merece la pena intentarlo)
    assert [(c["title"], c["hint"]) for c in groups["Dobles"]["items"]] == [
        ("Canal Doble", "+1 fuente · 1 comprobada OK"), ("Otro Canal", "360p25 · ✓ comprobado")]
    assert chans["Canal Doble"]["health"] is True

    send_event(h, {"type": "back"})
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 3, "value": {"view": "source", "id": "tdt_radio"}})
    menu = wait_menu(h, "España · Radio")
    assert [i["title"] for i in menu["items"]] == ["Buscar en esta lista…", "Emisora Voz",
                                                   "Comprobar canales en segundo plano"]
    radio = channels(d, "tdt_radio")["Emisora Voz"]
    assert radio["group_label"] == "Radio C. Valenciana" and radio["group"] == "Radio_C. Valenciana"

    # world TV: Spanish names, the user's country first and set apart; categories translated
    send_event(h, {"type": "back"})
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 4, "value": {"view": "world"}})
    menu = wait_menu(h, "Mundo · TV")
    assert [(i["title"], i["separator"]) for i in menu["items"]] == [
        ("🇪🇸 España", True), ("🇩🇪 Alemania", False), ("🇫🇷 Francia", False), ("🇬🇧 Reino Unido", False)]
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "country", "id": "es", "name": "España"}})
    menu = wait_menu(h, "España")
    assert [g["title"] for g in menu["items"]] == ["Buscar en esta lista…", "General", "Sin categoría"]
    world = channels(d, "iptv_org")
    assert world["Uno ES"]["group_label"] == "General · Público" and world["Dos ES"]["category_label"] == "Sin categoría"

    # world radio (mocked Radio Browser): Spanish names, Spain first, then by number of stations
    send_event(h, {"type": "back"})
    send_event(h, {"type": "back"})
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 5, "value": {"view": "radio"}})
    menu = wait_menu(h, "Radio mundial")
    assert [i["title"] for i in menu["items"]] == ["Buscar en esta lista…", "Más votadas del mundo", "🇪🇸 España",
                                                   "🇩🇪 Alemania", "🇦🇫 Afganistán"]
    assert h.script_errors() == []
