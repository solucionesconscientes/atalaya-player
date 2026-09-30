"""H25 · «Compartir» end to end over HTTP (mpvd + headless mpv + mu-share + real ffmpeg, urllib/http.client as the
guests): joining with the link, the event stream following the host's pause/position, the HLS relay of the local
file and its WebVTT subtitles, asking for control and accepting it with mu-share's script message, a guest's pause
reaching mpv («Ana ha pausado» on the OSD), revoking, expelling, the per-address attempt limit, closing the room,
and the direct URL of a web video with the relay a guest asks for when its browser cannot play it."""

from __future__ import annotations

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
                                 "MPVD_SHARE_PORT": "0", "MPV_UOS_VAAPI": "0", "MPV_UOS_YTDLP_AUTO_UPDATE": "0"})
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

    res = d.call("share.create", {"ttl_hours": 1})
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
    media = ana.wait(lambda e, x: e == "media" and x.get("kind") == "hls" and x.get("complete"), timeout=60)
    assert media["mode"] == "copy" and media["url"].startswith(f"/s/{room}/media/") and "path" not in media
    status, m3u8, headers = ana.req(media["url"])
    assert status == 200 and headers["Content-Type"] == "application/vnd.apple.mpegurl"
    text = m3u8.decode()
    assert "#EXT-X-ENDLIST" in text and "#EXT-X-PLAYLIST-TYPE:EVENT" in text
    seg = next(ln for ln in text.splitlines() if ln.endswith(".ts"))
    seg_url = media["url"].rsplit("/", 1)[0] + "/" + seg
    status, body, headers = ana.req(seg_url)
    assert status == 200 and headers["Content-Type"] == "video/mp2t" and body[:1] == b"G"  # TS sync byte
    status, part, headers = ana.req(seg_url, headers={"Range": "bytes=0-187"})
    assert status == 206 and len(part) == 188 and headers["Content-Range"].startswith("bytes 0-187/")
    assert ana.req(media["url"].rsplit("/", 1)[0] + "/../../x")[0] == 404
    assert Guest(base, room).req(seg_url)[0] == 401  # no cookie, no media

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
