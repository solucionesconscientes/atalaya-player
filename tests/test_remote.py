"""Remote control (H12): pure-Python QR encoder (decoded with libzbar when available), the minimal HTTP server,
the ``remote.*`` service against the test daemon + headless mpv (one-time token → cookie, whitelisted commands
applied in mpv, SSE state stream, forget), and the mu-remote overlay/menu."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pytest

from mpvd.remote import qr
from mpvd.remote.http import HttpError, HttpServer, Request, Response, sse_event
from tests.conftest import APP, start_mpv
from tests.qr_helpers import available as zbar_available
from tests.qr_helpers import decode_modules

# -- QR ---------------------------------------------------------------------------------------------------


def _finder_ok(m: list[list[bool]], r0: int, c0: int) -> bool:
    for r in range(7):
        for c in range(7):
            expected = r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
            if m[r0 + r][c0 + c] != expected:
                return False
    return True


def test_qr_structure_and_capacity():
    code = qr.encode("http://192.168.1.23:8790/#t=0123456789ab")
    assert code.version == 3 and code.size == 29 and code.level == "M"
    m = code.modules
    assert _finder_ok(m, 0, 0) and _finder_ok(m, 0, 22) and _finder_ok(m, 22, 0)
    assert all(m[6][i] == (i % 2 == 0) for i in range(8, 21))  # timing pattern
    assert m[code.size - 8][8] is True  # dark module
    assert sum(len(r) for r in code.runs()) < code.size * code.size / 2
    assert [qr.capacity(v, "M") for v in (1, 4, 10)] == [14, 62, 213]
    assert qr.choose_version(62, "M") == 4 and qr.choose_version(63, "M") == 5
    with pytest.raises(ValueError):
        qr.encode("x" * 300)
    assert code.to_pbm().startswith(b"P4\n")
    assert "██" in code.to_text()


@pytest.mark.skipif(not zbar_available(), reason="libzbar no está instalada")
@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
def test_qr_decodes_with_zbar(version):
    for level in ("L", "M"):
        n = qr.capacity(version, level)
        text = ("http://192.168.1.135:8790/#t=" + "abcdefghij" * 30)[:n]
        code = qr.encode(text, level=level, version=version)
        assert decode_modules(code.modules) == [text], (version, level)


@pytest.mark.skipif(not zbar_available(), reason="libzbar no está instalada")
def test_qr_every_mask_decodes():
    text = "http://10.0.0.5:8790/#t=abcdef"
    for mask in range(8):
        code = qr.encode(text, mask=mask)
        assert code.mask == mask and decode_modules(code.modules) == [text]


# -- HTTP server ----------------------------------------------------------------------------------------------


class _Loop:
    """Run an asyncio loop in a thread so urllib can talk to HttpServer synchronously."""

    def __init__(self):
        import asyncio

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro):  # type: ignore[no-untyped-def]
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def http_server():
    import asyncio

    seen: list[Request] = []

    async def handler(req: Request) -> Response:
        seen.append(req)
        if req.path == "/json":
            return Response.json({"q": req.query, "peer": req.peer})
        if req.path == "/echo" and req.method == "POST":
            return Response.json({"body": req.json(), "cookie": req.cookies})
        if req.path == "/boom":
            raise HttpError(418, "tetera")
        if req.path == "/crash":
            raise RuntimeError("kaputt")
        if req.path == "/sse":
            async def gen():
                for i in range(3):
                    yield sse_event("tick", {"i": i})
                    await asyncio.sleep(0.05)
            return Response.sse(gen())
        raise HttpError(404)

    lp = _Loop()
    server = HttpServer(handler, name="test")
    host, port = lp.run(server.start("127.0.0.1", 0))
    try:
        yield f"http://127.0.0.1:{port}", seen
    finally:
        lp.run(server.stop())
        lp.close()


def _get(url: str, **kw):  # type: ignore[no-untyped-def]
    req = urllib.request.Request(url, **kw)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_server_basics(http_server):
    base, seen = http_server
    status, headers, body = _get(base + "/json?a=1&b=x%20y")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["q"] == {"a": "1", "b": "x y"}
    status, _, body = _get(base + "/echo", data=json.dumps({"k": [1, 2]}).encode(), method="POST",
                           headers={"Content-Type": "application/json", "Cookie": "a=1; b=2"})
    assert status == 200 and json.loads(body) == {"body": {"k": [1, 2]}, "cookie": {"a": "1", "b": "2"}}
    status, _, _ = _get(base + "/echo", data=b"x=1", method="POST", headers={"Content-Type": "text/plain"})
    assert status == 415
    assert _get(base + "/boom")[0] == 418
    status, _, body = _get(base + "/crash")
    # H34 · the body must not carry the exception text (it leaked absolute paths to whoever is on the LAN)
    assert status == 500 and "kaputt" not in body.decode() and "error interno" in body.decode()
    assert _get(base + "/nope")[0] == 404
    status, _, body = _get(base + "/echo", data=b"x" * (1024 * 1024 + 1), method="POST",
                           headers={"Content-Type": "application/json"})
    assert status == 413
    # garbage request line
    s = socket.create_connection(urlsplit(base).netloc.split(":")[0:1] + [int(urlsplit(base).port)], timeout=5)
    s.sendall(b"GARBAGE\r\n\r\n")
    assert s.recv(100).startswith(b"HTTP/1.1 400")
    s.close()


def test_http_server_sse(http_server):
    base, _ = http_server
    with urllib.request.urlopen(base + "/sse", timeout=10) as r:
        assert r.headers["Content-Type"].startswith("text/event-stream")
        text = r.read().decode()
    assert text.count("event: tick") == 3 and 'data: {"i": 2}' in text


# -- service + mpv --------------------------------------------------------------------------------------------


class RemoteClient:
    """urllib with a cookie jar of one cookie."""

    def __init__(self, base: str):
        self.base = base
        self.cookie = ""

    def req(self, path: str, body=None, method: str | None = None):  # type: ignore[no-untyped-def]
        headers = {"Cookie": self.cookie} if self.cookie else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if body is not None else "GET"),
                                   headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                sc = resp.headers.get("Set-Cookie")
                if sc:
                    self.cookie = sc.split(";")[0]
                raw = resp.read()
                return resp.status, (json.loads(raw) if resp.headers.get("Content-Type", "").startswith("application/json") else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def sse_until(self, predicate, timeout: float = 20.0):  # type: ignore[no-untyped-def]
        u = urlsplit(self.base)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
        conn.request("GET", "/events", headers={"Cookie": self.cookie})
        resp = conn.getresponse()
        assert resp.status == 200, resp.status
        deadline = time.monotonic() + timeout
        events = []
        event, data = None, None
        while time.monotonic() < deadline:
            line = resp.fp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and event is not None:
                events.append((event, data))
                if predicate(event, data):
                    conn.close()
                    return events
                event, data = None, None
        conn.close()
        raise AssertionError(f"SSE: condición no alcanzada; eventos: {events[-3:]}")


@pytest.fixture
def remote_env(daemon_env, media_dir):
    daemon_env.extra_env.update({"MPVD_REMOTE_HOST": "127.0.0.1", "MPVD_REMOTE_PUBLIC_HOST": "127.0.0.1",
                                 "MPVD_REMOTE_PORT": "0"})
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1",
                                           "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def test_remote_pair_commands_and_events(remote_env, media_dir):
    h, d = remote_env
    caps = d.call("capabilities")
    assert caps["services"]["remote"] is True
    st = d.call("remote.status")
    assert st["running"] is False and st["paired"] == []

    pair = d.call("remote.pair")
    assert pair["url"].startswith("http://127.0.0.1:") and pair["url"].endswith("#t=" + pair["token"])
    assert pair["qr"]["size"] == pair["qr"]["version"] * 4 + 17 and len(pair["qr"]["runs"]) == pair["qr"]["size"]
    if zbar_available():
        modules = [[ch == "1" for ch in row] for row in pair["qr"]["rows"]]
        assert decode_modules(modules) == [pair["url"]]
    assert d.call("remote.status")["running"] is True and d.call("remote.status")["pending_tokens"] == 1
    base = pair["url"].split("/#")[0]
    c = RemoteClient(base)

    # static app without auth, API locked
    status, html = c.req("/")
    assert status == 200 and f"Mando {APP}".encode() in html and b"/app.js" in html
    assert c.req("/manifest.webmanifest")[0] == 200 and c.req("/sw.js")[0] == 200 and c.req("/icon.svg")[0] == 200
    assert c.req("/api/state")[0] == 401
    assert c.req("/api/cmd", {"cmd": "toggle"})[0] == 401
    assert c.req("/api/pair", {"token": "nope"})[0] == 403
    status, data = c.req("/api/pair", {"token": pair["token"]})
    assert status == 200 and data["ok"] and c.cookie.startswith("mu_remote=")
    assert c.req("/api/pair", {"token": pair["token"]})[0] == 403  # one-time
    st = d.call("remote.status")
    assert len(st["paired"]) == 1 and st["pending_tokens"] == 0

    # state and commands applied in mpv
    status, state = c.req("/api/state")
    assert status == 200 and state["idle-active"] is True
    assert c.req("/api/cmd", {"cmd": "play", "target": str(media_dir / "chapters.mkv")})[1]["ok"]
    h.wait_property("path", lambda v: bool(v) and v.endswith("chapters.mkv"), timeout=20)
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 1, timeout=20)
    assert c.req("/api/cmd", {"cmd": "seek", "seconds": 12, "mode": "absolute"})[0] == 200
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 11.5 < v < 13.5, timeout=10)
    r = c.req("/api/cmd", {"cmd": "volume", "value": 999})
    assert r[0] == 200, r
    h.wait_property("volume", lambda v: v == 150, timeout=5)  # clamped to volume-max (mpv.conf: 150)
    assert c.req("/api/cmd", {"cmd": "speed", "value": 1.5})[0] == 200
    h.wait_property("speed", lambda v: abs(v - 1.5) < 0.01, timeout=5)
    assert c.req("/api/cmd", {"cmd": "resume"})[0] == 200
    h.wait_property("pause", lambda v: v is False, timeout=5)
    events = c.sse_until(lambda e, dta: e == "state" and dta.get("pause") is False and (dta.get("time-pos") or 0) > 12)
    assert events[0][0] == "hello" and any(e == "state" for e, _ in events)
    assert c.req("/api/cmd", {"cmd": "pause"})[0] == 200
    h.wait_property("pause", lambda v: v is True, timeout=5)
    state = c.req("/api/state")[1]
    assert state["path"].endswith("chapters.mkv") and state["pause"] is True and state["session"]
    assert c.req("/api/cmd", {"cmd": "chapter_set", "index": 1})[0] == 200
    h.wait_property("chapter", lambda v: v == 1, timeout=5)
    # whitelist
    assert c.req("/api/cmd", {"cmd": "quit"})[0] == 400
    assert c.req("/api/cmd", {"cmd": "script_binding", "name": "console/enable"})[0] == 400
    assert c.req("/api/cmd", {"cmd": "seek", "seconds": "abc"})[0] == 400
    assert c.req("/api/cmd", {"cmd": "play"})[0] == 400
    assert c.req("/api/cmd", "not-an-object")[0] == 400
    # lists
    status, ch = c.req("/api/chapters")
    assert status == 200 and len(ch["chapters"]) >= 2 and ch["current"] == 1
    status, tracks = c.req("/api/tracks")
    assert status == 200 and set(tracks) == {"sub", "audio", "video"} and tracks["video"]
    status, pl = c.req("/api/playlist")
    assert status == 200 and len(pl["items"]) == 1 and pl["items"][0]["current"] is True
    assert c.req("/api/recents")[0] == 200
    status, chans = c.req("/api/channels")
    assert status == 200 and set(chans) == {"favorites", "recents"}
    status, search = c.req("/api/search?q=hola")
    assert status == 200 and search["q"] == "hola" and "semantic" in search and "text" in search
    assert c.req("/api/search")[0] == 400
    # CSRF guard: foreign Origin on POST
    r = urllib.request.Request(base + "/api/cmd", data=b'{"cmd":"toggle"}', method="POST",
                               headers={"Content-Type": "application/json", "Cookie": c.cookie, "Origin": "http://evil.example"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(r, timeout=10)
    assert exc.value.code == 403
    # forget every phone → cookie rejected; the pairing file has no phones left
    assert d.call("remote.forget")["forgotten"] == 1
    assert c.req("/api/state")[0] == 401
    saved = json.loads((d.data_dir / "remote.json").read_text())
    assert saved["paired"] == [] and saved["autostart"] is True and saved["port"] == int(urlsplit(base).port)
    # stop the server
    assert d.call("remote.stop")["running"] is False
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        urllib.request.urlopen(base + "/", timeout=3)
    assert not h.script_errors(), h.script_errors()


def test_remote_pair_persists_and_unpair(remote_env):
    h, d = remote_env
    pair = d.call("remote.pair")
    c = RemoteClient(pair["url"].split("/#")[0])
    assert c.req("/api/pair", {"token": pair["token"]})[0] == 200
    saved = json.loads((d.data_dir / "remote.json").read_text())
    assert len(saved["paired"]) == 1 and saved["paired"][0]["name"].endswith("(127.0.0.1)") and "session" not in saved["paired"][0]
    # a tampered signature for a known phone id is rejected
    forged = RemoteClient(c.base)
    forged.cookie = c.cookie[:-4] + ("0000" if not c.cookie.endswith("0000") else "1111")
    assert forged.req("/api/state")[0] == 401 and c.req("/api/state")[0] in (200, 503)
    assert c.req("/api/unpair", {})[0] == 200
    assert c.req("/api/state")[0] == 401
    assert json.loads((d.data_dir / "remote.json").read_text())["paired"] == []
    # expired token
    pair = d.call("remote.pair", {"ttl": 0.2})
    time.sleep(0.5)
    assert c.req("/api/pair", {"token": pair["token"]})[0] == 403


def test_mu_remote_overlay_and_menu(remote_env):
    h, d = remote_env
    h.command("script-binding", "mu_remote/remote-qr")
    st = h.wait_property("user-data/mu/remote", lambda v: bool(v) and v.get("visible") is True, timeout=20)
    assert st["url"].startswith("http://127.0.0.1:") and st["qr_size"] >= 21 and st["running"] is True
    assert st["token"] in st["url"]
    assert d.call("remote.status")["pending_tokens"] == 1
    h.command("script-binding", "mu_remote/remote-qr")
    h.wait_property("user-data/mu/remote", lambda v: bool(v) and v.get("visible") is False, timeout=10)
    # menu
    h.command("script-binding", "mu_remote/remote-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-remote", timeout=10)
    h.wait_property("user-data/mu/remote", lambda v: bool(v) and v.get("port", 0) > 0, timeout=10)
    h.command("script-message-to", "uosc", "close-menu", "mu-remote")
    assert not h.script_errors(), h.script_errors()


def test_a_bad_limit_is_a_clear_400_and_a_huge_one_is_capped():
    """H34 · `?limit=abc` provocaba un 500 con el texto de la excepción; `?limit=999999999` llegaba tal cual a mpvd."""
    from mpvd.remote.http import HttpError
    from mpvd.remote.service import _limit

    assert _limit({}, 30, 200) == 30
    assert _limit({"limit": ""}, 30, 200) == 30
    assert _limit({"limit": "50"}, 30, 200) == 50
    assert _limit({"limit": "999999999"}, 30, 200) == 200
    assert _limit({"limit": "0"}, 30, 200) == 1
    with pytest.raises(HttpError) as exc:
        _limit({"limit": "abc"}, 30, 200)
    assert exc.value.status == 400
