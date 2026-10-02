"""H25 · «Compartir» end to end over HTTP (mpvd + headless mpv + mu-share + real ffmpeg, urllib/http.client as the
guests): joining with the link, the event stream following the host's pause/position, the HLS relay of the local
file and its WebVTT subtitles, asking for control and accepting it with mu-share's script message, a guest's pause
reaching mpv («Ana ha pausado» on the OSD), revoking, expelling, the per-address attempt limit, closing the room,
and the direct URL of a web video with the relay a guest asks for when its browser cannot play it."""

from __future__ import annotations

import contextlib
import functools
import http.client
import http.server
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from mpvd.rpc import RpcError
from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


class Guest:
    """A browser: one cookie, JSON calls and a background SSE reader."""

    def __init__(self, base: str, room: str):
        self.base = base
        self.room = room
        self.cookie = ""
        self.events: list[tuple[str, dict]] = []
        self._thread: threading.Thread | None = None
        self._conn: http.client.HTTPConnection | None = None
        self.stream_status = 0

    def req(self, path: str, body=None, headers: dict | None = None, method: str | None = None):  # type: ignore[no-untyped-def]
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        url = path if path.startswith("http") else self.base + (path if path.startswith("/") else f"/s/{self.room}/{path}")
        r = urllib.request.Request(url, data=data, method=method or ("POST" if body is not None else "GET"), headers=h)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                sc = resp.headers.get("Set-Cookie")
                if sc:
                    self.cookie = sc.split(";")[0]
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if ctype.startswith("application/json") else raw), dict(resp.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw), dict(e.headers)
            except ValueError:
                return e.code, raw, dict(e.headers)

    def listen(self) -> None:
        u = urlsplit(self.base)
        self._conn = http.client.HTTPConnection(u.hostname, u.port, timeout=60)
        self._conn.request("GET", f"/s/{self.room}/events", headers={"Cookie": self.cookie})
        resp = self._conn.getresponse()
        self.stream_status = resp.status

        def run() -> None:
            event, data = None, None
            try:
                while True:
                    line = resp.fp.readline()
                    if not line:
                        break
                    line = line.decode().rstrip("\n")
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                    elif line == "" and event is not None:
                        self.events.append((event, data))
                        event, data = None, None
            except (OSError, ValueError):
                pass
            self.events.append(("_eof", {}))

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def wait(self, pred, timeout: float = 30.0, start: int = 0):  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for ev, data in list(self.events[start:]):
                if pred(ev, data):
                    return data
            time.sleep(0.05)
        raise AssertionError(f"SSE: condición no alcanzada; últimos eventos: {self.events[-6:]}")

    def mark(self) -> int:
        return len(self.events)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("share-http")
    srt = d / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:05,000\nHola desde la sala\n", encoding="utf-8")
    out = d / "peli.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40", "-i", str(srt),
                    "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
                    "-c:a", "aac", "-c:s", "srt", "-metadata", "title=Peli de prueba", str(out)], check=True)
    return out


@pytest.fixture
def share_env(daemon_env):
    daemon_env.extra_env.update({"MPVD_SHARE_HOST": "127.0.0.1", "MPVD_SHARE_PUBLIC_HOST": "127.0.0.1",
                                 "MPVD_SHARE_PORT": "0", "MPV_UOS_VAAPI": "0", "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
                                 "MPV_UOS_CLOUDFLARED": "0"})
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def _share(h, pred, timeout: float = 20.0):  # type: ignore[no-untyped-def]
    return h.wait_property("user-data/mu/share", lambda v: bool(v) and pred(v), timeout=timeout)


def test_room_join_sync_relay_permissions_and_close(share_env, clip):
    h, d = share_env
    assert d.call("capabilities")["services"]["share"] is True
    st = d.call("share.status")
    assert st["open"] is False and st["tunnel"] is None and st["lan_only"] is True
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)

    # H55 · aquí se prueba el camino de «el anfitrión se queda los mandos», que sigue existiendo;
    # que por defecto se entre pudiendo controlar lo cubre test_share_rooms y el test del navegador
    res = d.call("share.create", {"ttl_hours": 1, "control": False})
    url = res["url"]
    assert url.startswith("http://127.0.0.1:") and "/s/" in url and "#k=" in url
    base, rest = url.split("/s/", 1)
    room, token = rest.split("#k=")
    assert token == res["token"] and len(token) >= 32 and res["qr"]["size"] >= 21
    assert 3500 < res["room"]["expires_in"] <= 3600
    assert d.call("share.create")["url"] == url  # one room at a time: the same one comes back
    assert d.call("share.status")["open"] is True

    ana = Guest(base, room)
    status, page, headers = ana.req(f"/s/{room}")
    assert status == 200 and b"Ver juntos" in page and b"/static/room.js" in page
    assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["Referrer-Policy"] == "no-referrer"
    for name in ("room.js", "sync.js", "room.css", "hls.light.min.js"):
        assert ana.req(f"/static/{name}")[0] == 200
    assert ana.req("/static/../service.py")[0] == 404
    assert ana.req("api/me")[0] == 401
    assert ana.req("events")[0] == 401
    assert ana.req("api/join", {"token": "x" * 32, "name": "Ana"})[0] == 403
    assert ana.req(f"/s/nosuchroom/api/join", {"token": token, "name": "Ana"})[0] == 410
    status, data, headers = ana.req("api/join", {"token": token, "name": "Ana"})
    assert status == 200 and data["guest"]["perm"] == "view", data
    assert ana.cookie.startswith("mu_share=") and f"Path=/s/{room}" in headers["Set-Cookie"]
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    _share(h, lambda v: v.get("last_notice") == "Ana se ha unido" and len(v.get("guests") or []) == 1)
    status, me, _ = ana.req("api/me")
    assert status == 200 and me["guest"]["name"] == "Ana" and me["room"]["id"] == room

    # the event stream: hello, the host's state and the relay of the local file
    ana.listen()
    assert ana.stream_status == 200
    ana.wait(lambda e, x: e == "hello" and x["guest"]["name"] == "Ana")
    st0 = ana.wait(lambda e, x: e == "state")
    assert st0["paused"] is True and st0["title"] == "Peli de prueba"
    # H44/C4 · un archivo del anfitrión se ofrece SIEMPRE tal cual («abrir en tu reproductor»); esta peli es un
    # Matroska con H.264 + AAC, que el navegador no abre, así que además va el relay de siempre
    media = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file" and x.get("relay_complete"), timeout=60)
    assert media["browser"] == "relay" and media["relay_mode"] == "copy"
    # H51 · la URL lleva un testigo POR PELÍCULA: sin él era la misma cadena para todas y quien la estaba
    # reproduciendo no se enteraba de que el anfitrión había cambiado de película
    assert media["url"] == f"/s/{room}/file?v={media['v']}" and len(media["v"]) == 12
    assert media["name"] == "peli.mkv" and media["size"] > 0
    # ni la ruta real del fichero ni nada que la delate salen en el aviso que se reparte
    assert "path" not in media and "local" not in media
    status, m3u8, headers = ana.req(media["relay_url"])
    assert status == 200 and headers["Content-Type"] == "application/vnd.apple.mpegurl"
    text = m3u8.decode()
    assert "#EXT-X-ENDLIST" in text and "#EXT-X-PLAYLIST-TYPE:EVENT" in text
    seg = next(ln for ln in text.splitlines() if ln.endswith(".ts"))
    seg_url = media["relay_url"].rsplit("/", 1)[0] + "/" + seg
    status, body, headers = ana.req(seg_url)
    assert status == 200 and headers["Content-Type"] == "video/mp2t" and body[:1] == b"G"  # TS sync byte
    status, part, headers = ana.req(seg_url, headers={"Range": "bytes=0-187"})
    assert status == 206 and len(part) == 188 and headers["Content-Range"].startswith("bytes 0-187/")
    assert ana.req(media["relay_url"].rsplit("/", 1)[0] + "/../../x")[0] == 404
    assert Guest(base, room).req(seg_url)[0] == 401  # no cookie, no media

    # H44/C2-C3 · el fichero original, con rangos y leído por trozos, y el enlace para el reproductor del invitado
    status, head, headers = ana.req("file", headers={"Range": "bytes=0-1023"})
    assert status == 206 and len(head) == 1024 and headers["Content-Range"].endswith("/" + str(media["size"]))
    assert headers["Accept-Ranges"] == "bytes" and headers["Content-Length"] == "1024"
    assert ana.req("file", headers={"Range": f"bytes={media['size'] + 10}-"})[0] == 416
    link = ana.req("api/filelink")[1]
    assert link["url"].endswith("/file?k=") is False and "/file?k=" in link["url"]
    # mpv o VLC no mandan la cookie: con la credencial en la query sí pueden, sin ella no
    plain = Guest(base, room)
    assert plain.req(link["url"].split("/s/" + room + "/", 1)[1])[0] == 200
    assert plain.req("file")[0] == 401
    status, m3u, headers = ana.req("file.m3u")
    assert status == 200 and headers["Content-Type"].startswith("audio/x-mpegurl")
    assert b"#EXTM3U" in m3u and b"/file?k=" in m3u

    # subtitles of the active track as WebVTT
    h.command("set", "sid", "1")
    media = ana.wait(lambda e, x: e == "media" and (x.get("subs") or {}).get("url"), timeout=30)
    status, vtt, headers = ana.req(media["subs"]["url"])
    assert status == 200 and headers["Content-Type"].startswith("text/vtt") and b"Hola desde la sala" in vtt

    # host playback → state in the stream (position moving), notices for the host's actions
    mark = ana.mark()
    h.command("set", "pause", "no")
    ana.wait(lambda e, x: e == "state" and x["paused"] is False, start=mark)
    ana.wait(lambda e, x: e == "notice" and x["text"] == "El anfitrión ha reanudado", start=mark)
    ana.wait(lambda e, x: e == "state" and x["reason"] == "tick" and x["pos"] > 1.0, start=mark, timeout=15)
    mark = ana.mark()
    h.command("seek", "20", "absolute")
    seek = ana.wait(lambda e, x: e == "state" and x["reason"] == "seek", start=mark)
    assert 19.5 < seek["pos"] < 23
    ana.wait(lambda e, x: e == "notice" and x["text"].startswith("El anfitrión ha saltado a 0:2"), start=mark)

    # a guest who only watches cannot control
    status, err, _ = ana.req("api/cmd", {"cmd": "pause"})
    assert status == 403 and "pide el control" in err["error"]
    # asking for control → dialog in mpv (mu-share) → accepted with its script message
    status, r, _ = ana.req("api/request", {})
    assert status == 200 and r["pending"] is True and r["perm"] == "view"
    v = _share(h, lambda v: v.get("asking") and v["last_request"]["name"] == "Ana")
    h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-share-request", timeout=10)
    mark = ana.mark()
    h.command("script-message-to", "mu_share", "mu-share-answer", v["asking"], "yes")
    ana.wait(lambda e, x: e == "perm" and x["perm"] == "control", start=mark)
    h.wait_property("user-data/uosc/menu/type", lambda t: t != "mu-share-request", timeout=10)
    assert d.call("share.status")["guests"][0]["perm"] == "control"
    # the guest pauses: it reaches mpv and the host sees «Ana ha pausado»
    mark = ana.mark()
    assert ana.req("api/cmd", {"cmd": "pause"})[0] == 200
    h.wait_property("pause", lambda p: p is True, timeout=10)
    _share(h, lambda v: v.get("last_notice") == "Ana ha pausado")
    ana.wait(lambda e, x: e == "state" and x["paused"] is True, start=mark)
    time.sleep(0.6)
    assert not any(e == "notice" and x["text"] == "El anfitrión ha pausado" for e, x in ana.events[mark:])
    assert ana.req("api/cmd", {"cmd": "seek", "seconds": 5})[0] == 200
    h.wait_property("time-pos", lambda p: isinstance(p, (int, float)) and 4.5 < p < 6, timeout=10)
    _share(h, lambda v: v.get("last_notice") == "Ana ha saltado a 0:05")
    assert ana.req("api/cmd", {"cmd": "toggle"})[0] == 200
    h.wait_property("pause", lambda p: p is False, timeout=10)
    _share(h, lambda v: v.get("last_notice") == "Ana ha reanudado")
    assert ana.req("api/cmd", {"cmd": "quit"})[0] == 400
    assert ana.req("api/cmd", {"cmd": "seek", "seconds": "x"})[0] == 400
    # CSRF guard
    assert ana.req("api/cmd", {"cmd": "pause"}, headers={"Origin": "http://evil.example"})[0] == 403
    # revoke
    gid = d.call("share.status")["guests"][0]["id"]
    mark = ana.mark()
    d.call("share.permission", {"guest": gid, "perm": "view"})
    ana.wait(lambda e, x: e == "perm" and x["perm"] == "view", start=mark)
    assert ana.req("api/cmd", {"cmd": "pause"})[0] == 403
    # denied request
    ana.req("api/request", {})
    mark = ana.mark()
    h.command("script-message-to", "mu_share", "mu-share-answer", gid, "no")
    ana.wait(lambda e, x: e == "perm" and x.get("denied") is True, start=mark)
    assert d.call("share.status")["pending"] == []

    # a second guest: presence, same name gets a suffix, expelled
    luis = Guest(base, room)
    assert luis.req("api/join", {"token": token, "name": "ana"})[1]["guest"]["name"] == "ana (2)"
    luis.listen()
    ana.wait(lambda e, x: e == "guests" and len(x) == 2 and all(g["connected"] for g in x))
    lid = [g["id"] for g in d.call("share.status")["guests"] if g["name"] == "ana (2)"][0]
    d.call("share.kick", {"guest": lid})
    luis.wait(lambda e, x: e == "kicked")
    assert luis.req("api/me")[0] == 401
    ana.wait(lambda e, x: e == "notice" and x["text"] == "ana (2) ha salido de la sala")
    _share(h, lambda v: len(v.get("guests") or []) == 1)

    # new link: the old token stops working, guests inside stay
    new = d.call("share.rotate")
    assert new["token"] != token and new["url"].endswith(new["token"])
    assert Guest(base, room).req("api/join", {"token": token, "name": "X"})[0] == 403
    assert ana.req("api/me")[0] == 200

    # too many wrong tokens from one address → blocked (429) even with the right one
    bad = Guest(base, room)
    codes = [bad.req("api/join", {"token": "nope", "name": "Eve"})[0] for _ in range(5)]
    # (the old token above was this address's 1st failure since Luis joined: joining well resets the count)
    assert codes == [403, 403, 403, 429, 429], codes
    assert bad.req("api/join", {"token": new["token"], "name": "Eve"})[0] == 429

    # close: guests are told, the relay files go away, the server stops
    share_dir = d.cache_dir / "share" / room
    assert share_dir.is_dir()
    d.call("share.close")
    ana.wait(lambda e, x: e == "closed" and x["reason"] == "host")
    assert not share_dir.exists()
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        urllib.request.urlopen(base + f"/s/{room}", timeout=3)
    assert d.call("share.status")["open"] is False
    ana.close()
    luis.close()
    assert not h.script_errors(), h.script_errors()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # type: ignore[no-untyped-def]
        pass


def test_direct_url_and_relay_on_demand(share_env, clip, tmp_path):
    """A plain http video: guests get its URL; one whose browser cannot play it asks for the relay."""
    h, d = share_env
    mp4 = tmp_path / "web.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-t", "8", "-map", "0:v", "-map", "0:a",
                    "-c", "copy", str(mp4)], check=True)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        web = f"http://127.0.0.1:{httpd.server_address[1]}/web.mp4"
        h.command("loadfile", web)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 5, timeout=20)
        res = d.call("share.create")
        base, rest = res["url"].split("/s/", 1)
        room, token = rest.split("#k=")
        g = Guest(base, room)
        assert g.req("api/join", {"token": token, "name": "Bea"})[0] == 200
        g.listen()
        media = g.wait(lambda e, x: e == "media" and x.get("kind") == "direct")
        assert media["url"] == web and media["can_relay"] is True
        status, m, _ = g.req("api/relay", {})
        assert status == 200 and m["relay_url"].startswith(f"/s/{room}/media/")
        media = g.wait(lambda e, x: e == "media" and x.get("relay_complete"), timeout=60)
        status, m3u8, _ = g.req(media["relay_url"])
        assert status == 200 and b"#EXT-X-ENDLIST" in m3u8
        assert g.req("api/relay", {})[1]["relay_url"] == media["relay_url"]  # made once
        # the host closes the player: the room closes by itself
        h.command("quit")
        closed = g.wait(lambda e, x: e == "closed", timeout=20)
        assert closed["reason"] == "player" and closed["text"] == "El reproductor se ha cerrado"
        d.wait(lambda: d.call("share.status")["open"] is False, timeout=10)
        g.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _room_of(url: str) -> tuple[str, str, str]:
    base, rest = url.split("/s/", 1)
    room, token = rest.split("#k=")
    return base, room, token.split("&", 1)[0]


def test_public_room_view_only(share_env, clip):
    """«Sala pública (solo ver)»: anyone with the link, anonymous, up to the maximum; no control, no chat."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    res = d.call("share.create", {"mode": "public", "max_viewers": 2, "ttl_hours": 1})
    assert res["url"].endswith("&v=1") and res["status"]["mode"] == "public" and res["status"]["max_viewers"] == 2
    with pytest.raises(RpcError, match="ya hay una sala pública"):
        d.call("share.create", {"mode": "private"})
    assert d.call("share.create", {"mode": "public"})["url"] == res["url"]
    base, room, token = _room_of(res["url"])

    a = Guest(base, room)
    status, data, _ = a.req("api/join", {"token": token, "name": "<b>Ana</b>"})
    assert status == 200 and data["guest"]["name"] == "Espectador 1" and data["room"]["mode"] == "public"
    assert data["room"]["guests"] == [] and data["room"]["chat"] is False
    a.listen()
    hello = a.wait(lambda e, x: e == "hello")
    assert hello["room"]["mode"] == "public"
    a.wait(lambda e, x: e == "viewers" and x == {"count": 1, "max": 2})
    a.wait(lambda e, x: e == "media" and x.get("kind") == "file", timeout=60)
    b = Guest(base, room)
    assert b.req("api/join", {"token": token})[1]["guest"]["name"] == "Espectador 2"
    b.listen()
    a.wait(lambda e, x: e == "viewers" and x["count"] == 2)
    status, err, _ = Guest(base, room).req("api/join", {"token": token, "name": ""})
    assert status == 403 and "llena" in err["error"]
    # watching only: no control, no request, no chat, no reactions
    for path, body in (("api/cmd", {"cmd": "pause"}), ("api/request", {}), ("api/chat", {"text": "hola"}),
                       ("api/react", {"reaction": "like"})):
        status, err, _ = a.req(path, body)
        assert status == 403 and "pública" in err["error"], (path, err)
    gid = d.call("share.status")["guests"][0]["id"]
    with pytest.raises(RpcError, match="pública"):
        d.call("share.permission", {"guest": gid, "perm": "control"})
    with pytest.raises(RpcError, match="no hay chat"):
        d.call("share.chat", {"text": "hola"})
    # the host sees how many are watching, without a notice per viewer
    v = _share(h, lambda v: v.get("mode") == "public" and v.get("viewers") == 2)
    assert "se ha unido" not in v.get("last_notice", "")
    assert not any(e == "notice" and "Espectador" in x.get("text", "") for e, x in a.events)
    # the page itself: same room page, the public link joins by itself
    assert a.req(f"/s/{room}")[0] == 200
    # same attempt limit per address as private rooms
    bad = Guest(base, room)
    codes = [bad.req("api/join", {"token": "nope"})[0] for _ in range(5)]
    assert codes[-1] == 429, codes
    d.call("share.close")
    a.wait(lambda e, x: e == "closed")
    a.close()
    b.close()
    assert not h.script_errors(), h.script_errors()


def test_chat_and_reactions(share_env, clip):
    """Private room: chat and reactions over the SSE stream, length and rate limits, markup kept as text, the host's
    own messages, the history for late guests, and the lines in mpv's overlay (mu-share)."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    base, room, token = _room_of(d.call("share.create")["url"])
    ana, luis = Guest(base, room), Guest(base, room)
    assert ana.req("api/join", {"token": token, "name": "Ana"})[0] == 200
    assert luis.req("api/join", {"token": token, "name": "Luis"})[0] == 200
    me = ana.req("api/me")[1]
    assert me["room"]["chat"] is True and me["reactions"]["laugh"] == "😂" and me["chat"] == []
    ana.listen()
    luis.listen()
    luis.wait(lambda e, x: e == "hello")

    xss = "<img src=x onerror=alert(1)> hola {\\b1}${path}"
    status, r, _ = ana.req("api/chat", {"text": xss + "\n"})
    assert status == 200 and r["chat"]["text"] == xss
    row = luis.wait(lambda e, x: e == "chat" and x["kind"] == "chat")
    assert row["who"] == "Ana" and row["text"] == xss and row["host"] is False and row["guest"]
    v = _share(h, lambda v: v["last_chat"]["text"] == xss and v["chat_visible"] >= 1)
    assert v["last_chat"]["who"] == "Ana"

    assert ana.req("api/chat", {"text": "x" * 201})[0] == 400
    assert ana.req("api/chat", {"text": "   "})[0] == 400
    assert ana.req("api/chat", ["no"])[0] == 400
    codes = [ana.req("api/chat", {"text": f"m{i}"})[0] for i in range(5)]
    assert codes == [200, 200, 200, 200, 429], codes       # 5 per 10 s and guest (the first one counted)
    assert luis.req("api/chat", {"text": "yo sí puedo"})[0] == 200   # the limit is per guest

    status, r, _ = luis.req("api/react", {"reaction": "laugh"})
    assert status == 200 and r["chat"]["emoji"] == "😂"
    row = ana.wait(lambda e, x: e == "chat" and x["kind"] == "reaction")
    assert row["who"] == "Luis" and row["reaction"] == "laugh" and row["words"] == "se ríe"
    _share(h, lambda v: v["last_chat"]["kind"] == "reaction" and v["last_chat"]["reaction"] == "laugh")
    assert luis.req("api/react", {"reaction": "<script>"})[0] == 400
    codes = [luis.req("api/react", {"reaction": "clap"})[0] for _ in range(8)]
    assert codes[-1] == 429 and codes[:7] == [200] * 7, codes

    d.call("share.chat", {"text": "Hola a todos"})
    row = ana.wait(lambda e, x: e == "chat" and x.get("host"))
    assert row["who"] == "Anfitrión" and row["text"] == "Hola a todos"
    with pytest.raises(RpcError, match="vacío"):
        d.call("share.chat", {"text": " "})
    # history: a late guest gets the last messages (not the reactions)
    eva = Guest(base, room)
    eva.req("api/join", {"token": token, "name": "Eva"})
    hist = eva.req("api/me")[1]["chat"]
    assert [x["text"] for x in hist][0] == xss and hist[-1]["text"] == "Hola a todos"
    assert all(x["kind"] == "chat" for x in hist)
    assert d.call("share.status")["chat"][-1]["text"] == "Hola a todos"
    d.call("share.close")
    ana.close()
    luis.close()
    assert not h.script_errors(), h.script_errors()


def test_no_relay_is_started_for_a_room_that_is_already_closed(tmp_path):
    """H34 · el sondeo de una URL tarda segundos; si la sala se cerró en ese hueco, arrancar ffmpeg transcodificaría la
    película entera sin nadie mirando y recrearía la carpeta que el cierre acababa de borrar."""
    import asyncio

    from mpvd.config import Settings
    from mpvd.server import MpvdServer
    from mpvd.share import hls as hls_mod
    from mpvd.share.rooms import Room
    from mpvd.share.service import RoomRuntime

    server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", idle_timeout=0, workers=1))
    room = Room.new(ttl=3600.0)
    room.closed = True
    rt = RoomRuntime(room=room, session_id="s1", dir=tmp_path / "sala")

    with pytest.raises(RuntimeError, match="cerrado"):
        asyncio.run(server.share._start_stream(rt, [hls_mod.Input(str(tmp_path / "x.mkv"))], None))
    assert not (tmp_path / "sala").exists()


def test_al_cambiar_de_pelicula_el_invitado_recibe_otra_direccion(share_env, clip, media_dir):
    """H51 · el fallo que encontró Ser: «si en el reproductor principal cambio de peli, en el invitado no cambia,
    sigue la misma». La dirección del archivo original era `/s/<sala>/file` para TODAS las películas, así que la
    cadena no cambiaba y ni el navegador ni VLC ni el modo invitado tenían forma de saber que había otra cosa."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    res = d.call("share.create", {"ttl_hours": 1})
    base, rest = res["url"].split("/s/", 1)
    room = rest.split("#")[0]

    ana = Guest(base, room)
    ana.req("api/join", {"token": res["token"], "name": "Ana"})
    ana.listen()
    primera = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file", timeout=60)
    assert primera["name"] == "peli.mkv"
    # el enlace para su reproductor lleva el mismo testigo
    enlace1 = ana.req("api/filelink")[1]["url"]
    assert f"v={primera['v']}" in enlace1

    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=20)
    segunda = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file" and x.get("name") == "video30.mkv",
                       timeout=60)
    assert segunda["v"] != primera["v"], "la misma dirección para dos películas distintas"
    assert segunda["url"] != primera["url"]
    assert f"v={segunda['v']}" in ana.req("api/filelink")[1]["url"]

    # y volver a la primera devuelve su testigo: se deriva de la película, no de un contador
    h.command("loadfile", str(clip))
    tercera = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file" and x.get("name") == "peli.mkv",
                       timeout=60)
    assert tercera["v"] == primera["v"]
    ana.close()


def test_cualquier_cosa_se_puede_abrir_en_el_reproductor_del_invitado(share_env, clip):
    """H51 · la otra queja de Ser: «sólo se puede ver desde el navegador, no desde vlc o mpv». El bloque «Abrir en
    mi reproductor» solo existía para un archivo del anfitrión; con la TV o un vídeo de internet el invitado se
    quedaba encerrado en el navegador. Y para una retransmisión hace falta además que la credencial llegue a cada
    trozo: la lista los nombra en relativo y el reproductor los pediría a pelo."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    res = d.call("share.create", {"ttl_hours": 1})
    base, rest = res["url"].split("/s/", 1)
    room = rest.split("#")[0]
    ana = Guest(base, room)
    ana.req("api/join", {"token": res["token"], "name": "Ana"})
    ana.listen()
    media = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file" and x.get("relay_complete"), timeout=60)

    # 1. un archivo del anfitrión: el original
    status, link, _ = ana.req("api/filelink")
    assert status == 200 and link["kind"] == "file" and "original" in link["quality"]
    assert link["url"].endswith(f"&v={media['v']}") or f"v={media['v']}" in link["url"]
    status, m3u, headers = ana.req("file.m3u")
    assert status == 200 and headers["Content-Type"].startswith("audio/x-mpegurl")
    assert link["url"] in m3u.decode()

    # 2. la retransmisión, que es lo que hay cuando el anfitrión ve la TV o un vídeo de internet: la lista le llega
    #    al reproductor con la credencial puesta EN CADA TROZO, y esos trozos se sirven de verdad
    playlist = media["relay_url"] + "?k=" + ana.cookie.split("=", 1)[1]
    status, body, headers = ana.req(playlist)
    assert status == 200 and headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
    texto = body.decode()
    trozos = [ln for ln in texto.splitlines() if ln and not ln.startswith("#")]
    assert trozos and all("?k=" in ln for ln in trozos), texto[:300]
    suelto = media["relay_url"].rsplit("/", 1)[0] + "/" + trozos[0]
    sin_credencial = media["relay_url"].rsplit("/", 1)[0] + "/" + trozos[0].split("?")[0]
    assert ana.req(suelto)[0] == 200
    # y sin ella sigue sin servir a cualquiera: la credencial no es decorado
    sola = Guest(base, room)
    assert sola.req(sin_credencial)[0] == 401
    ana.close()


def test_un_salto_atras_rehace_la_retransmision(share_env, clip):
    """H54 · el fallo que encontró Ser: con el control dado, al tirar el vídeo hacia atrás «en el dispositivo de la
    otra persona se queda en el mismo minuto, y tampoco tiene play/pause».

    La causa es C5: la retransmisión arranca DONDE ESTÁ el anfitrión y solo contiene desde ahí. Al saltar a un
    minuto anterior, ese minuto no existe en lo que el invitado está viendo, y la corrección de deriva lo empujaba
    una y otra vez al segundo 0 de la retransmisión: un vídeo congelado que no responde. Ahora se rehace allí."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    h.command("seek", "30", "absolute+exact")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 28, timeout=20)

    res = d.call("share.create", {"ttl_hours": 1})
    base, rest = res["url"].split("/s/", 1)
    room = rest.split("#")[0]
    ana = Guest(base, room)
    ana.req("api/join", {"token": res["token"], "name": "Ana"})
    ana.listen()
    media = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file" and x.get("relay_url"), timeout=60)
    # la retransmisión empieza donde va el anfitrión (C5), no en el segundo 0
    assert media["relay_offset"] > 25, media["relay_offset"]
    primera = media["relay_stream"]

    # el invitado con el control tira hacia atrás, a un minuto que la retransmisión NO tiene
    d.call("share.permission", {"guest": ana.req("api/me")[1]["guest"]["id"], "perm": "control"})
    status, _, _ = ana.req("api/cmd", {"cmd": "seek", "seconds": 3})
    assert status == 200
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v < 8, timeout=20)

    nueva = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file"
                     and x.get("relay_stream") not in (None, primera), timeout=90)
    assert nueva["relay_offset"] < 8, f"la retransmisión sigue empezando en {nueva['relay_offset']}"
    # y lo que se sirve de verdad es la nueva
    assert ana.req(nueva["relay_url"])[0] == 200
    ana.close()


def test_el_enlace_para_otro_reproductor_lo_abre_mpv_de_verdad(share_env, clip, tmp_path):
    """H54 · Ser dice que «el enlace no se puede reproducir en mpv o vlc, solo en el navegador». Aquí se comprueba
    con un mpv DE VERDAD: el enlace de «Abrir en mi reproductor» tiene que cargar y dar la duración del original."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    res = d.call("share.create", {"ttl_hours": 1})
    base, rest = res["url"].split("/s/", 1)
    room = rest.split("#")[0]
    ana = Guest(base, room)
    ana.req("api/join", {"token": res["token"], "name": "Ana"})
    ana.listen()
    ana.wait(lambda e, x: e == "media" and x.get("kind") == "file", timeout=60)
    enlace = ana.req("api/filelink")[1]["url"]
    assert enlace.startswith("http") and "k=" in enlace

    out = subprocess.run(["mpv", "--no-config", "--vo=null", "--ao=null", "--frames=1",
                          "--msg-level=all=error", enlace], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"mpv no pudo con el enlace: {out.stderr[-400:]}"

    # y el .m3u que se baja de la página lleva ese mismo enlace, que es lo que abre VLC con doble clic
    m3u = ana.req("file.m3u")[1].decode()
    assert enlace.split("?")[0] in m3u
    ana.close()


def test_el_anfitrion_tiene_un_enlace_que_abre_vlc_o_mpv(share_env, clip):
    """H54 · lo que le faltaba a Ser: él copia el enlace de la SALA, y ese no puede abrirse en un reproductor —su
    token va en el fragmento y el navegador no lo manda nunca—. `share.player_link` da el que sí, y se reutiliza."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
    res = d.call("share.create", {"ttl_hours": 1})
    base, rest = res["url"].split("/s/", 1)
    room = rest.split("#")[0]
    sonda = Guest(base, room)
    sonda.req("api/join", {"token": res["token"], "name": "Sonda"})
    sonda.listen()
    sonda.wait(lambda e, x: e == "media" and x.get("kind") == "file", timeout=60)

    uno = d.call("share.player_link")
    assert uno["kind"] == "file" and "k=" in uno["url"] and uno["url"].startswith("http")
    assert d.call("share.player_link")["url"] == uno["url"], "cada vez crea un invitado nuevo"

    out = subprocess.run(["mpv", "--no-config", "--vo=null", "--ao=null", "--frames=1",
                          "--msg-level=all=error", uno["url"]], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"mpv no pudo con el enlace del anfitrión: {out.stderr[-400:]}"
    # el .m3u también lleva su credencial, que es lo que abre VLC con doble clic
    status, m3u, _ = sonda.req(uno["m3u"])
    assert status == 200 and b"#EXTM3U" in m3u
    sonda.close()
