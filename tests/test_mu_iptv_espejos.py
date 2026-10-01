"""H39/E1-E2 · los espejos de un canal: pasar al siguiente cuando el primero NO suena, y decir en la lista lo que dijo
la comprobación.

Lo que le pasó a Ser: la lista trae «Cadena SER ×6», los primeros espejos no dan error (se quedan conectando) y nadie
pasaba al siguiente, así que la radio no se oía. Aquí el primer espejo es un servidor que responde 200 y luego no manda
nada —exactamente ese caso— y el segundo es un directo de verdad hecho con ffmpeg.
"""

from __future__ import annotations

import http.server
import json
import socket
import subprocess
import threading
import time

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=5,mu-iptv-osd_seconds=1,mu-iptv-stall_seconds=4"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_live_stream(port: int) -> subprocess.Popen:
    cmd = ["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "120",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "25", "-c:a", "aac",
           "-f", "mpegts", "-listen", "1", f"http://127.0.0.1:{port}/live.ts"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serve(files: dict[str, bytes]):
    """Servidor con hilos: una petición colgada (el espejo muerto) no puede bloquear a las demás."""

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/muerta.ts":
                # el caso de verdad: contesta, dice que viene un vídeo… y no manda nada
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.end_headers()
                time.sleep(60)
                return
            body = files.get(path)
            if body is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def espejos(daemon_env):
    live_port = free_port()
    live = start_live_stream(live_port)
    httpd = serve({})
    base = f"http://127.0.0.1:{httpd.server_port}"
    # el mismo canal dos veces (eso es un espejo para mpvd: misma lista, tipo, nombre y grupo)
    m3u = ("#EXTM3U\n"
           f'#EXTINF:-1 radio="true" group-title="Radio_Pruebas",Cadena Prueba\n{base}/muerta.ts\n'
           f'#EXTINF:-1 radio="true" group-title="Radio_Pruebas",Cadena Prueba\nhttp://127.0.0.1:{live_port}/live.ts\n')
    lista = daemon_env.base / "espejos.m3u"
    lista.write_text(m3u, encoding="utf-8")
    sources = [{"id": "tdt_radio", "name": "Radio Prueba", "url": lista.as_uri(), "kind": "radio",
                "region": "es", "country": "es"}]
    src_path = daemon_env.base / "sources.json"
    src_path.write_text(json.dumps(sources), encoding="utf-8")
    env = {**daemon_env.env, "MPV_UOS_IPTV_SOURCES": str(src_path), "MPV_UOS_COUNTRY": "es"}
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS], env=env)
    try:
        yield h, daemon_env, f"{base}/muerta.ts", f"http://127.0.0.1:{live_port}/live.ts"
    finally:
        h.stop()
        httpd.shutdown()
        live.kill()
        live.wait(timeout=10)


def test_un_espejo_que_no_suena_pasa_al_siguiente(espejos):
    h, d, muerta, viva = espejos
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    # merge=true es como lo pide el menú: una fila por canal y las copias como alternativas
    canales = d.call("iptv.channels", {"kind": "radio", "limit": 10, "merge": True})
    fila = next(c for c in canales["items"] if c["name"] == "Cadena Prueba")
    assert fila["alternatives"] == 1, "las dos copias tienen que verse como espejos del mismo canal"

    # se reproduce: la primera copia es la muerta (responde pero no manda nada)
    d.call("iptv.play", {"id": fila["id"]})
    h.command("script-message-to", "mu_iptv", "mu-iptv-play", fila["id"])
    h.wait_property("path", lambda v: v == muerta, timeout=20)
    assert h.get("user-data/mu/iptv")["fallbacks"] == 0

    # nadie da ningún error; a los 4 s de no sonar, mu-iptv pasa al espejo siguiente y ESE sí suena
    h.wait_property("path", lambda v: v == viva, timeout=30)
    st = h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("fallbacks") == 1, timeout=10)
    assert st["alternatives_left"] == 0
    h.wait_property("audio-bitrate", lambda v: isinstance(v, (int, float)) and v > 0, timeout=30)
    assert h.script_errors() == [], h.script_errors()


def test_la_lista_dice_lo_que_dijo_la_comprobacion(espejos):
    h, d, muerta, viva = espejos
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    canales = d.call("iptv.channels", {"kind": "radio", "limit": 10, "merge": True})
    fila = next(c for c in canales["items"] if c["name"] == "Cadena Prueba")
    assert "health" in fila and fila["health"] is None, "sin comprobar todavía: no se inventa nada"

    job = d.call("iptv.health.check", {"source": "tdt_radio", "limit": 10})
    d.wait(lambda: next((j for j in d.call("jobs.list") if j["id"] == job["id"]), {}).get("status")
           in ("done", "failed"), timeout=180)
    canales = d.call("iptv.channels", {"kind": "radio", "limit": 10, "merge": True})
    fila = next(c for c in canales["items"] if c["name"] == "Cadena Prueba")
    # la copia viva responde → el canal se considera vivo, y se dice cuántas de sus copias respondieron
    assert fila["health"] is True and fila["health_checked"] == 2 and fila["health_alive"] == 1

    # y la copia muerta, mirada sola, dice POR QUÉ no se pudo abrir (no un «✕» a secas)
    todas = d.call("iptv.channels", {"kind": "radio", "limit": 10})
    muertas = [c for c in todas["items"] if c.get("url") == muerta]
    if muertas:
        assert muertas[0]["health"] is False and muertas[0].get("health_detail"), muertas[0]
        assert "no" in muertas[0]["health_detail"]
