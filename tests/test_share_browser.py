"""H25 · a real browser in the room (optional: needs Google Chrome/Chromium and node >= 22). Headless Chrome with a
throwaway profile under tmp/ (closed by its own PID/process group) opens the invitation link, types a name, plays the
relay (native HLS or the vendored hls.js) and follows the host: playing in step (< 1 s), a seek and a pause, and
the «room closed» message. Nothing is downloaded."""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import TMP
from tests.test_share_http import Guest, clip, share_env  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
CHROME = shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium")


def _node_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    try:
        return int(out.lstrip("v").split(".")[0]) >= 22
    except ValueError:
        return False


pytestmark = pytest.mark.skipif(not CHROME or not _node_ok(), reason="sin Chrome/Chromium o node >= 22")


class Browser:
    def __init__(self):
        self.profile = TMP / "chrome-profiles" / uuid.uuid4().hex[:8]
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
             "--autoplay-policy=no-user-gesture-required", "--mute-audio", "--disable-background-networking",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not port_file.exists() or not port_file.read_text().strip():
            if time.monotonic() > deadline or self.proc.poll() is not None:
                self.close()
                pytest.skip("el navegador no arrancó")
            time.sleep(0.1)
        port = port_file.read_text().split()[0]
        self.bridge = subprocess.Popen(["node", str(ROOT / "tests" / "cdp_bridge.js"), port], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, text=True, start_new_session=True)
        assert json.loads(self.bridge.stdout.readline()).get("ready"), "CDP bridge"

    def _call(self, cmd: dict):  # type: ignore[no-untyped-def]
        assert self.bridge.stdin is not None and self.bridge.stdout is not None
        self.bridge.stdin.write(json.dumps(cmd) + "\n")
        self.bridge.stdin.flush()
        out = json.loads(self.bridge.stdout.readline())
        if "error" in out or "fatal" in out:
            raise AssertionError(out)
        return out["value"]

    def nav(self, url: str) -> None:
        self._call({"nav": url})

    def js(self, expr: str):  # type: ignore[no-untyped-def]
        return self._call({"eval": expr})

    def wait(self, expr: str, timeout: float = 30.0):  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.js(expr)
            except AssertionError as exc:
                last = exc
            if last and not isinstance(last, AssertionError):
                return last
            time.sleep(0.2)
        raise AssertionError(f"navegador: {expr} → {last}; página: {self.snapshot()}")

    def snapshot(self):  # type: ignore[no-untyped-def]
        try:
            return self.js("JSON.stringify({ov: document.getElementById('overlay-text').textContent, "
                           "msg: document.getElementById('message-text').textContent, "
                           "ct: document.getElementById('video').currentTime, p: document.getElementById('video').paused, "
                           "rs: document.getElementById('video').readyState, src: document.getElementById('video').currentSrc, "
                           "err: (document.getElementById('video').error||{}).message})")
        except AssertionError as exc:
            return str(exc)

    def close(self) -> None:
        for p in (getattr(self, "bridge", None), self.proc):
            if p is None or p.poll() is not None:
                continue
            try:
                os.killpg(p.pid, signal.SIGTERM)  # our own process group (start_new_session): Chrome's children too
                p.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        shutil.rmtree(self.profile, ignore_errors=True)


VIDEO = "document.getElementById('video')"


@pytest.mark.parametrize("engine", ["auto", "hlsjs"])
def test_browser_guest_follows_the_host(share_env, clip, engine):  # noqa: F811
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    url = d.call("share.create")["url"]
    if engine == "hlsjs":
        url = url.replace("#k=", "?hlsjs=1#k=")
    b = Browser()
    try:
        b.nav(url)
        b.wait("!document.getElementById('join').classList.contains('hidden')")
        b.js("document.getElementById('name').value = 'Navegador'; "
             "document.getElementById('join-form').requestSubmit(); true")
        b.wait("!document.getElementById('room').classList.contains('hidden')")
        assert b.js("location.hash") == ""  # the token leaves the address bar
        assert d.call("share.status")["guests"][0]["name"] == "Navegador"
        b.wait(f"{VIDEO}.readyState >= 1", timeout=40)
        # H44/C3 · «Abrir en mi reproductor»: aparece porque lo que se ve es un archivo del anfitrión, y lleva las
        # tres formas de llevárselo. El enlace es del invitado (credencial en la query: mpv no manda la cookie).
        b.wait("!document.getElementById('own').classList.contains('hidden')", timeout=20)
        b.wait("document.getElementById('own-cmd').textContent.includes('/file?k=')", timeout=20)
        assert b.js("document.getElementById('own-name').textContent") == "peli.mkv"
        assert b.js("document.getElementById('own-cmd').textContent").startswith('mpv "http')
        assert "/file.m3u" in b.js("document.getElementById('own-m3u').getAttribute('href')")
        # este Matroska con H.264 + AAC no lo abre el navegador tal cual: se dice, y aquí se ve el relay
        assert "en tu reproductor lo verás como es" in b.js("document.getElementById('own-why').textContent").lower()
        used = b.js("window.Hls ? 'hls.js' : 'nativo'")
        if engine == "hlsjs":
            assert used == "hls.js"
        h.command("set", "pause", "no")
        b.wait(f"!{VIDEO}.paused && {VIDEO}.currentTime > 2", timeout=40)
        time.sleep(3)  # let the drift correction settle
        diffs = []
        for _ in range(5):
            host = h.get("time-pos")
            guest = b.js(f"{VIDEO}.currentTime")
            diffs.append(abs(host - guest))
            time.sleep(0.5)
        assert min(diffs) < 1.0, (diffs, used)
        h.command("seek", "25", "absolute")
        b.wait(f"Math.abs({VIDEO}.currentTime - 26) < 2", timeout=20)
        h.command("set", "pause", "yes")
        b.wait(f"{VIDEO}.paused", timeout=10)
        host = h.get("time-pos")
        b.wait(f"Math.abs({VIDEO}.currentTime - {host}) < 0.5", timeout=10)
        d.call("share.close")
        b.wait("document.getElementById('message-text').textContent.includes('cerrado la sala')", timeout=10)
        print(f"motor HLS del navegador: {used}; desfase: {min(diffs):.2f} s")
    finally:
        b.close()


def test_browser_chat_as_text_and_public_room(share_env, clip):  # noqa: F811
    """The chat in a real page: another guest's markup is shown as text (no element, no script), the page sends
    messages and reactions; then a public «solo ver» link joins by itself, without name, controls or chat."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    url = d.call("share.create")["url"]
    base, rest = url.split("/s/", 1)
    room, token = rest.split("#k=")
    other = Guest(base, room)
    assert other.req("api/join", {"token": token, "name": "Otro"})[0] == 200
    other.listen()
    b = Browser()
    try:
        b.nav(url)
        b.wait("!document.getElementById('join').classList.contains('hidden')")
        b.js("document.getElementById('name').value = 'Nav'; document.getElementById('join-form').requestSubmit(); true")
        b.wait("!document.getElementById('chat').classList.contains('hidden')")
        b.wait("document.querySelectorAll('#reactions button').length === 6")
        evil = '<img src=x onerror="window.__xss=1"><b>negrita</b>'
        assert other.req("api/chat", {"text": evil})[0] == 200
        b.wait("document.getElementById('chat-list').textContent.includes('negrita')")
        assert b.js("document.querySelector('#chat-list li:last-child .text').textContent") == evil
        assert b.js("document.querySelectorAll('#chat-list img, #chat-list b').length") == 0
        time.sleep(0.5)
        assert b.js("window.__xss === undefined") is True
        b.js("document.getElementById('chat-text').value = 'hola desde el navegador'; "
             "document.getElementById('chat-form').requestSubmit(); true")
        row = other.wait(lambda e, x: e == "chat" and x["who"] == "Nav")
        assert row["text"] == "hola desde el navegador"
        b.wait("document.getElementById('chat-text').value === ''")
        b.js("document.querySelector('#reactions button[data-r=\"clap\"]').click(); true")
        other.wait(lambda e, x: e == "chat" and x.get("reaction") == "clap" and x["who"] == "Nav")
        b.wait("document.querySelectorAll('#floats .float').length >= 1", timeout=5)
        d.call("share.close")
        b.wait("!document.getElementById('message').classList.contains('hidden')", timeout=10)
        other.close()

        pub = d.call("share.create", {"mode": "public", "max_viewers": 5})["url"]
        b.nav(pub)
        b.wait("!document.getElementById('room').classList.contains('hidden')", timeout=20)
        assert b.js("document.getElementById('join').classList.contains('hidden')") is True   # no name asked
        assert b.js("document.getElementById('who').textContent") == "Sala pública · solo ver"
        assert b.js("location.hash") == ""
        for el in ("chat", "play", "ask", "guests"):
            assert b.js(f"document.getElementById('{el}').classList.contains('hidden')") is True, el
        b.wait("document.getElementById('viewers').textContent === '1 persona viendo'", timeout=15)
        assert d.call("share.status")["guests"][0]["name"] == "Espectador 1"
        # a reload keeps the seat (cookie); the page does not ask anything
        b.nav(pub.split("#", 1)[0])
        b.wait("!document.getElementById('room').classList.contains('hidden')", timeout=20)
        assert len(d.call("share.status")["guests"]) == 1
        d.call("share.close")
    finally:
        b.close()


def test_el_invitado_con_el_control_manda_de_verdad(share_env, clip):  # noqa: F811
    """H55 · Ser dice que desde el navegador «sigo sin poder controlarlo correctamente, lo único que funciona bien
    es silenciar» (que es local). Aquí se prueban los tres mandos con un navegador y un mpv de verdad: pausa,
    −10 s y la barra. Lo que se mira es el EFECTO en el reproductor del anfitrión y en el vídeo del invitado."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    h.command("seek", "20", "absolute+exact")
    h.command("set", "pause", "no")
    res = d.call("share.create")
    b = Browser()
    try:
        b.nav(res["url"])
        b.wait("!document.getElementById('join').classList.contains('hidden')")
        b.js("document.getElementById('name').value = 'Navegador'; "
             "document.getElementById('join-form').requestSubmit(); true")
        b.wait("!document.getElementById('room').classList.contains('hidden')")
        b.wait(f"{VIDEO}.readyState >= 1", timeout=40)

        # H55 · en una sala privada entra pudiendo controlar: es lo que significa «ver juntos»
        assert b.js("document.getElementById('play').disabled") is False
        assert b.js("document.getElementById('seek').disabled") is False

        # y si el anfitrión lo quita, se apagan Y SE DICE POR QUÉ (antes parecían rotos)
        d.call("share.open_control", {"on": False})
        b.wait("document.getElementById('play').disabled", timeout=20)
        b.wait("document.getElementById('why-view').textContent.indexOf('solo puedes ver') >= 0", timeout=10)
        d.call("share.open_control", {"on": True})
        b.wait("!document.getElementById('play').disabled", timeout=20)

        # 1. pausa
        b.js("document.getElementById('play').click(); true")
        h.wait_property("pause", lambda v: v is True, timeout=20)
        b.wait(f"{VIDEO}.paused", timeout=20)
        b.js("document.getElementById('play').click(); true")
        h.wait_property("pause", lambda v: v is False, timeout=20)

        # 2. −10 s
        antes = h.get("time-pos")
        b.js("document.getElementById('back10').click(); true")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v < antes - 5, timeout=20)

        # 3. la barra: llevar al segundo ~5 moviéndola y soltándola
        b.js("var s = document.getElementById('seek'); s.value = Math.round(1000 * 5 / 30); "
             "s.dispatchEvent(new Event('change')); true")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v < 9, timeout=25)
        # y el vídeo del invitado acaba donde está el anfitrión, no clavado
        b.wait(f"{VIDEO}.currentTime < 12", timeout=60)
    finally:
        b.close()


def test_browser_guest_by_polling_follows_and_recovers_from_a_cut(share_env, clip):  # noqa: F811
    """F3 · el túnel rápido de Cloudflare retiene el cuerpo del SSE, así que el invitado de navegador que entra por
    él sondea api/me cada segundo. Aquí se fuerza el sondeo (?poll=1) y se comprueba lo que importa: sigue al
    anfitrión (pausa, salto) y, si se cortan las peticiones un rato, se recupera solo cuando vuelven."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    url = d.call("share.create")["url"]
    base, frag = url.split("#", 1)
    b = Browser()
    try:
        b.nav(base + "?poll=1#" + frag)
        b.wait("!document.getElementById('join').classList.contains('hidden')")
        b.js("document.getElementById('name').value = 'Sondeo'; "
             "document.getElementById('join-form').requestSubmit(); true")
        b.wait("!document.getElementById('room').classList.contains('hidden')")
        b.wait("document.documentElement.getAttribute('data-transport') === 'poll'", timeout=10)
        b.wait(f"{VIDEO}.readyState >= 1", timeout=40)

        h.command("set", "pause", "no")
        b.wait(f"!{VIDEO}.paused && {VIDEO}.currentTime > 2", timeout=40)
        # F5 · con sondeo (el caso del túnel) el invitado sigue al anfitrión a menos de medio segundo, no detrás
        # por la edad del último estado
        time.sleep(3)
        diffs = []
        for _ in range(8):
            host = h.get("time-pos")
            diffs.append(abs(b.js(f"{VIDEO}.currentTime") - host))
            time.sleep(0.5)
        assert max(diffs) < 0.5, diffs
        h.command("seek", "25", "absolute")
        b.wait(f"Math.abs({VIDEO}.currentTime - 26) < 2", timeout=25)
        h.command("set", "pause", "yes")
        b.wait(f"{VIDEO}.paused", timeout=15)

        # se cortan las peticiones (como si se fuera la red) y vuelven: el bucle tiene que reanudarse solo
        b.js("window.__realFetch = window.fetch; window.fetch = function () {"
             " return Promise.reject(new TypeError('cut')); }; true")
        time.sleep(3.5)
        b.js("window.fetch = window.__realFetch; true")
        h.command("seek", "10", "absolute")
        b.wait(f"Math.abs({VIDEO}.currentTime - 11) < 2", timeout=30)

        d.call("share.close")
        b.wait("document.getElementById('message-text').textContent.includes('cerrada')", timeout=15)
    finally:
        b.close()


def _join(b: Browser) -> None:
    b.wait("!document.getElementById('join').classList.contains('hidden')")
    b.js("document.getElementById('name').value = 'Navegador'; "
         "document.getElementById('join-form').requestSubmit(); true")
    b.wait("!document.getElementById('room').classList.contains('hidden')")


def test_browser_direct_src_broken_returns_to_the_relay(share_env, clip, tmp_path):  # noqa: F811
    """F6 · el fallo del informe del 4-nov: el invitado se quedaba con el `src` directo, `error = 4` y en negro.
    Aquí se rompe el `src` desde la consola, como se hizo entonces, y se mide cuánto tarda en volver solo a la
    retransmisión y seguir reproduciendo."""
    h, d = share_env
    mp4 = tmp_path / "web.mp4"
    # faststart: con el `moov` al final y un servidor de pruebas sin Range, Chrome no arranca el MP4 directo y
    # el test mediría el fallback antes de tiempo (el navegador da el mismo error 4 que el informe del 4-nov)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-t", "35", "-map", "0:v", "-map", "0:a",
                    "-c", "copy", "-movflags", "+faststart", str(mp4)], check=True)
    class QuietServer(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):  # type: ignore[no-untyped-def]
            pass   # el navegador corta la descarga al romper el src: no es un fallo del test

    httpd = QuietServer(("127.0.0.1", 0), functools.partial(
        type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
        directory=str(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    b = Browser()
    try:
        web = f"http://127.0.0.1:{httpd.server_address[1]}/web.mp4"
        h.command("loadfile", web)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 10, timeout=20)
        # el anfitrión tiene que estar reproduciendo ANTES de que entre el invitado: si no, la página obedece el
        # estado «en pausa» del anfitrión y el test confunde eso con que el directo no arranca
        h.command("set", "pause", "no")
        h.wait_property("pause", lambda v: v is False, timeout=10)
        res = d.call("share.create")
        room = res["url"].split("/s/", 1)[1].split("#", 1)[0]
        b.nav(res["url"])
        _join(b)
        # el directo puede arrancar o fallar solo (según el navegador y el MP4 de prueba); en los dos casos el
        # invitado tiene que acabar en la retransmisión
        b.wait(f"{VIDEO}.readyState >= 2 || {VIDEO}.currentSrc.indexOf('blob:') === 0", timeout=40)
        direct = b.js(f"{VIDEO}.currentSrc").startswith("http")
        elapsed = None
        if direct:
            # el src directo deja de existir: el elemento emite `error` como en la prueba real. No se exige que
            # el directo esté reproduciendo: el fallo del informe del 4-nov es justo que no arranca.
            b.js("window.__f6 = performance.now();"
                 "var v = document.getElementById('video'); v.src = '/s/%s/media/roto/no.mp4'; v.load(); true" % room)
            b.wait(f"{VIDEO}.currentSrc.indexOf('blob:') === 0 && {VIDEO}.readyState >= 2", timeout=25)
            elapsed = b.js("(performance.now() - window.__f6) / 1000")
            # el criterio fino (< 5 s) y la reproducción se miden en la VM; aquí basta con que la reserva exista
            # y el invitado no se quede con el src roto (server1 comparte CPU con el resto de la batería)
            assert elapsed < 10.0, f"tardó {elapsed:.2f} s en volver a la retransmisión"
        else:
            b.wait(f"{VIDEO}.currentSrc.indexOf('blob:') === 0 && {VIDEO}.readyState >= 2", timeout=10)
        assert b.js("document.getElementById('overlay').classList.contains('hidden')") is True
        assert b.js(f"{VIDEO}.error === null") is True
        assert b.js(f"{VIDEO}.currentSrc.indexOf('blob:') === 0") is True
        print(f"fallback directo → relay: {'ya caído solo' if elapsed is None else f'{elapsed:.2f} s'}")
        d.call("share.close")
    finally:
        b.close()
        httpd.shutdown()
        httpd.server_close()


def test_browser_key_error_shows_retry_and_recovers_when_the_key_comes_back(share_env, clip, tmp_path):  # noqa: F811
    """F6 · si la clave no se puede derivar, hls.js da KEY_LOAD_ERROR: antes solo quedaba un aviso y el reproductor
    destruido. Ahora se ve el aviso y «Reintentar», y al volver la clave el botón recupera sin recargar la página.
    (Un 404 literal de `mu-key` no llega a la red: la clave se deriva en la página y su URI nunca se pide.)"""
    h, d = share_env
    other = tmp_path / "otra.mkv"
    shutil.copy(clip, other)
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    h.command("set", "pause", "no")
    res = d.call("share.create")
    b = Browser()
    try:
        b.nav(res["url"])
        _join(b)
        b.wait(f"{VIDEO}.readyState >= 2 && {VIDEO}.currentTime > 1", timeout=40)
        # la clave deja de derivarse; al cargar otra película la página monta otro hls.js con ese fallo
        b.js("window.__ck = window.MuCrypt.hlsKey;"
             "window.MuCrypt.hlsKey = function () { throw new Error('sin clave'); }; true")
        h.command("loadfile", str(other))
        b.wait("!document.getElementById('overlay').classList.contains('hidden') && "
               "!document.getElementById('retry').classList.contains('hidden')", timeout=90)
        aviso = b.js("document.getElementById('overlay-text').textContent").lower()
        assert any(w in aviso for w in ("descifrar", "decrypt", "déchiffrer")), aviso
        # vuelve la clave y el botón rehace el reproductor, sin recargar
        b.js("window.MuCrypt.hlsKey = window.__ck; true")
        b.js("document.getElementById('retry').click(); true")
        b.wait(f"{VIDEO}.readyState >= 2 && !{VIDEO}.paused && {VIDEO}.currentTime > 1", timeout=60)
        assert b.js("document.getElementById('overlay').classList.contains('hidden')") is True
        assert b.js("document.getElementById('retry').classList.contains('hidden')") is True
        d.call("share.close")
    finally:
        b.close()
