"""H39/E3-E4 · SponsorBlock al reproducir y los tres estados del icono de saltar.

Ser preguntó «¿sponsorblock está activado?»: lo estaba al descargar, pero no al ver. Aquí se comprueba con un servidor
de SponsorBlock falso (que además guarda lo que se le pidió, para ver que NO se manda el id del vídeo) y el yt-dlp falso
que reproduce medios locales con una URL de YouTube.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from mpvd.sponsorblock import hash_prefix
from tests.conftest import start_mpv
from tests.test_mu_ytdl import FAKE, serve

VID = "aqz-KE-bpKQ"
URL = f"https://www.youtube.com/watch?v={VID}"
# dos tramos dentro de los 30 s del vídeo de prueba
SEGMENTS = [
    {"category": "sponsor", "actionType": "skip", "segment": [4.0, 9.0], "votes": 5, "UUID": "a"},
    {"category": "selfpromo", "actionType": "skip", "segment": [20.0, 25.0], "votes": 2, "UUID": "b"},
]


def fake_sponsorblock():
    pedidos: list[str] = []
    payload = [{"videoID": "otro", "segments": [{"category": "sponsor", "actionType": "skip", "segment": [0, 30]}]},
               {"videoID": VID, "segments": SEGMENTS}]

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            pedidos.append(self.path)
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, pedidos


@pytest.fixture
def sb_mpv(daemon_env, media_dir, tmp_path):
    media = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    sb, pedidos = fake_sponsorblock()
    env = {
        **daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_MEDIA": str(media_dir),
        "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{media.server_address[1]}",
        "FAKE_YTDLP_FAKE_YOUTUBE": "1", "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
        "MPV_UOS_SPONSORBLOCK_URL": f"http://127.0.0.1:{sb.server_port}/api/skipSegments",
    }
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-intro-enabled=yes,mu-intro-sponsorblock=yes,mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-ytdl-ytdl_path={FAKE},mu-intro-poll_seconds=0.2,mu-intro-countdown_seconds=0,mu-intro-indicator=no",
        "--keep-open=yes", "--pause=yes",
    ], env=env)
    try:
        yield h, daemon_env, pedidos
    finally:
        h.stop()
        media.shutdown()
        sb.shutdown()


def intro_state(h, pred, timeout: float = 30.0):
    return h.wait_property("user-data/mu/intro", lambda v: bool(v) and pred(v), timeout=timeout)


def test_sponsorblock_al_reproducir_y_los_tres_estados_del_icono(sb_mpv):
    h, d, pedidos = sb_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)

    # sin nada cargado, el icono no aplica
    st = h.get("user-data/mu/intro")
    assert st["sponsors"] == 0 and st["web_url"] == ""

    h.command("loadfile", URL)
    h.wait_property("path", lambda v: v == URL, timeout=30)
    st = intro_state(h, lambda v: v.get("sponsor_status") == "done" and v.get("sponsors") == 2, timeout=60)
    tipos = {s["type"]: s for s in st["segments"]}
    assert set(tipos) == {"sponsor", "selfpromo"}
    assert tipos["sponsor"]["start"] == 4.0 and tipos["sponsor"]["label"] == "patrocinio"
    assert all(s["source"] == "sponsorblock" for s in st["segments"])

    # privacidad: lo único que viajó fue el prefijo del hash, nunca el id ni la URL
    assert pedidos and all(VID not in p and "youtube" not in p for p in pedidos)
    assert any(hash_prefix(VID) in p for p in pedidos)

    # dentro de un tramo: se salta solo (auto_skip_sponsor viene activado y la cuenta atrás es 0 en el test)
    assert st["auto_skip_sponsor"] is True
    h.command("set", "pause", "no")
    h.command("seek", 5.0, "absolute")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= 8.9, timeout=20)

    # el menú se abre sin errores (mu-intro no publica sus filas) y el interruptor apaga SponsorBlock
    h.command("script-binding", "mu_intro/intro-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-intro", timeout=15)
    h.command("script-message-to", "mu_intro", "mu-intro-set", "sponsorblock", "no")
    st = intro_state(h, lambda v: v.get("sponsorblock") is False and v.get("sponsor_status") == "off", timeout=20)
    h.command("script-message-to", "mu_intro", "mu-intro-set", "sponsorblock", "yes")
    intro_state(h, lambda v: v.get("sponsorblock") is True and v.get("sponsors") == 2, timeout=30)
    h.command("script-message-to", "uosc", "close-menu", "mu-intro")
    assert h.script_errors() == [], h.script_errors()
