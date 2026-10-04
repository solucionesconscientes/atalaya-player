"""``remote.*``: a phone remote control. mpvd serves a small PWA over plain HTTP on the LAN; pairing uses a one-time
token (shown as a QR code by mu-remote inside mpv) that is exchanged for an HMAC-signed cookie. Paired phones
persist in ``<data_dir>/remote.json`` until "forget" (like a Bluetooth remote). The API is a whitelist of
player commands plus channel/dialogue/recents search; nothing arbitrary reaches mpv from the network."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd import __version__
from mpvd.brand import app_name
from mpvd.control import pick_session
from mpvd.mpvipc import MpvIpcError
from mpvd.remote import qr
from mpvd.remote.downloads import DownloadsPanel
from mpvd.remote.http import HttpError, HttpServer, Request, Response, sse_event
from mpvd.i18n import forget_visitor, page_script, t, visitor_language
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.sessions import Session

log = logging.getLogger("mpvd.remote")

COOKIE = "mu_remote"
TOKEN_TTL = 600.0
DEFAULT_PORT = 8790
STATE_PROPS = ("time-pos", "duration", "pause", "media-title", "path", "filename", "volume", "mute", "speed",
               "idle-active", "chapter", "playlist-pos", "playlist-count", "sid", "aid", "sub-visibility",
               "fullscreen", "ab-loop-a", "ab-loop-b", "eof-reached")
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css",
          "/sw.js": "sw.js", "/manifest.webmanifest": "manifest.webmanifest", "/icon.svg": "icon.svg",
          "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
          "/downloads": "downloads.html", "/downloads.js": "downloads.js"}  # H23: downloads panel
BRANDED = {"index.html", "manifest.webmanifest", "app.js", "downloads.html", "downloads.js"}
PAGES = ("/", "/downloads")  # where a pairing link may land  # the literal app name in them follows brand.json
MAX_PAIRED = 20


def lan_ip() -> str:
    """Best-effort LAN address (UDP connect to a non-routable address never sends anything)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# per (port, ip, label): the remote, the share rooms and the TV all ask, and a single-entry cache made them invalidate
# each other, so the blocking systemctl path ran on almost every call
_FIREWALL_CACHE: dict[tuple[int, str, str], tuple[float, dict[str, Any] | None]] = {}


def firewall_hint(port: int, ip: str, label: str = "mando") -> dict[str, Any] | None:
    """A host firewall that may drop the phone's connection (checked without root: unit state only), with the
    exact command to open the port for the local network. None when no known firewall is active. ``label`` names
    the rule (the remote and the share rooms use different ports)."""
    now = time.monotonic()
    hit = _FIREWALL_CACHE.get((port, ip, label))
    if hit is not None and now - hit[0] < 60:
        return hit[1]
    value = None
    subnet = ".".join(ip.split(".")[:3]) + ".0/24" if ip.count(".") == 3 and not ip.startswith("127.") else "192.168.1.0/24"
    if shutil.which("systemctl"):
        for unit, command in (
            ("ufw", f"sudo ufw allow from {subnet} to any port {port} proto tcp comment 'mpv-uos {label}'"),
            ("firewalld", f"sudo firewall-cmd --permanent --add-port={port}/tcp && sudo firewall-cmd --reload"),
        ):
            try:
                out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            if out.strip() == "active":
                value = {"tool": unit, "port": port, "subnet": subnet, "command": command}
                break
    _FIREWALL_CACHE[(port, ip, label)] = (now, value)
    return value


def _limit(query: Any, default: int, hi: int) -> int:
    """A ``limit=`` from the network: never an unhandled ValueError (which the 500 used to echo back), never unbounded."""
    raw = query.get("limit") if hasattr(query, "get") else None
    if raw in (None, ""):
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HttpError(400, t("limit debe ser un número")) from None
    return max(1, min(hi, n))


_FIREWALL_INFLIGHT: set[tuple[int, str, str]] = set()


def firewall_cached(port: int, ip: str, label: str = "mando") -> dict[str, Any] | None:
    """The firewall hint without ever blocking the event loop.

    ``firewall_hint`` runs ``systemctl is-active`` twice (up to 6 s with a slow systemd), and it used to run inside
    ``status()``, i.e. in the loop: every alt+z froze the daemon, every mpv session and every SSE with it. Here a stale
    (or missing) answer is returned at once and refreshed in a thread for the next call.
    """
    key = (port, ip, label)
    hit = _FIREWALL_CACHE.get(key)
    if hit is not None and time.monotonic() - hit[0] < 60:
        return hit[1]
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return firewall_hint(port, ip, label)     # no loop (CLI, tests): answering right away is fine
    if key not in _FIREWALL_INFLIGHT:
        _FIREWALL_INFLIGHT.add(key)

        async def refresh() -> None:
            try:
                await asyncio.to_thread(firewall_hint, port, ip, label)
            finally:
                _FIREWALL_INFLIGHT.discard(key)

        loop.create_task(refresh())
    return hit[1] if hit is not None else None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class RemoteService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.www = Path(__file__).resolve().parent / "www"
        self.http = HttpServer(self.handle, name=f"mpvd/{__version__}")
        self.state_path = server.settings.data_dir / "remote.json"
        self.host = os.environ.get("MPVD_REMOTE_HOST", "0.0.0.0")
        self.public_host = os.environ.get("MPVD_REMOTE_PUBLIC_HOST", "")  # address written in the QR URL
        env_port = os.environ.get("MPVD_REMOTE_PORT")
        self.port_pref = int(env_port) if env_port not in (None, "") else -1  # -1: from remote.json or DEFAULT_PORT
        self.secret = b""
        self.autostart = False
        self.tokens: dict[str, dict[str, Any]] = {}
        self.paired: dict[str, dict[str, Any]] = {}
        self.clients = 0  # open SSE streams
        self._load()
        self.downloads = DownloadsPanel(self)
        server.session_listeners.append(self._session_event)

    # -- persistence -------------------------------------------------------------------------------------

    def _load(self) -> None:
        data: dict[str, Any] = {}
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        secret = data.get("secret")
        self.secret = bytes.fromhex(secret) if isinstance(secret, str) and len(secret) == 64 else secrets.token_bytes(32)
        if self.port_pref < 0:
            self.port_pref = int(data.get("port") or DEFAULT_PORT)
        self.autostart = bool(data.get("autostart"))
        for row in data.get("paired") or []:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                self.paired[row["id"]] = {"id": row["id"], "name": str(row.get("name") or ""), "created": row.get("created"),
                                          "session": None, "last_seen": row.get("last_seen")}
        if not isinstance(secret, str):
            self._save()

    def _save(self) -> None:
        data = {"secret": self.secret.hex(), "port": self.http.port or self.port_pref, "autostart": self.autostart,
                "paired": [{k: v for k, v in p.items() if k != "session"} for p in self.paired.values()]}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.warning("cannot save %s: %s", self.state_path, exc)

    # -- lifecycle ---------------------------------------------------------------------------------------

    async def start(self, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        if self.http.running:
            return self.status()
        host = host or self.host
        wanted = self.port_pref if port is None else int(port)
        try:
            await self.http.start(host, wanted)
        except OSError as exc:
            if wanted == 0:
                raise RpcError(UNAVAILABLE, t("no se pudo abrir el puerto: %s") % (exc,)) from exc
            log.warning("port %d busy (%s): using a free one", wanted, exc)
            await self.http.start(host, 0)
        self.host = host
        self.autostart = True
        self._save()
        return self.status()

    async def stop(self) -> None:
        await self.http.stop()
        self.tokens.clear()

    async def close(self) -> None:
        await self.http.stop()

    async def maybe_autostart(self) -> None:
        if self.autostart and not self.http.running:
            with contextlib.suppress(Exception):
                await self.start()

    def base_url(self, local: bool = False) -> str:
        """URL of the server for the phone (LAN address), or for a browser on this computer (``local``: loopback,
        where browsers also allow notifications over plain HTTP)."""
        if local and self.host in ("0.0.0.0", "", "127.0.0.1", "localhost"):
            return f"http://127.0.0.1:{self.http.port}"
        host = self.public_host or (lan_ip() if self.host in ("0.0.0.0", "") else self.host)
        return f"http://{host}:{self.http.port}"

    def status(self) -> dict[str, Any]:
        now = time.time()
        self.tokens = {t: v for t, v in self.tokens.items() if v["expires"] > now}
        ip = lan_ip()
        port = self.http.port or self.port_pref
        return {"running": self.http.running, "host": self.host, "port": port,
                "url": self.base_url() if self.http.running else None, "lan_ip": ip, "autostart": self.autostart,
                "firewall": firewall_cached(port, ip) if self.host in ("0.0.0.0", "") else None,
                "paired": [{"id": p["id"][:8], "name": p["name"], "created": p["created"], "last_seen": p.get("last_seen")}
                           for p in self.paired.values()],
                "pending_tokens": len(self.tokens), "clients": self.clients}

    # -- pairing -------------------------------------------------------------------------------------------

    async def pair_token(self, session_id: str | None, ttl: float = TOKEN_TTL, path: str = "/",
                         local: bool = False) -> dict[str, Any]:
        if path not in PAGES:
            raise RpcError(INVALID_PARAMS, f"path must be one of {PAGES}")
        await self.start()
        now = time.time()
        self.tokens = {t: v for t, v in self.tokens.items() if v["expires"] > now}
        token = secrets.token_urlsafe(9)  # 12 chars, 72 bits: keeps the QR at version 3
        self.tokens[token] = {"expires": now + ttl, "session": session_id, "created": now}
        url = f"{self.base_url(local)}{path}#t={token}"
        code = qr.encode(url, level="M")
        return {"token": token, "url": url, "expires_in": ttl, "session": session_id, "status": self.status(),
                "qr": {"version": code.version, "size": code.size, "runs": code.runs(), "rows": code.rows()}}

    def _cookie_value(self, pid: str) -> str:
        return pid + "." + hmac.new(self.secret, pid.encode(), sha256).hexdigest()[:32]

    def _pair(self, token: str, req: Request) -> tuple[str, dict[str, Any]]:
        info = self.tokens.pop(token, None)
        if info is None or info["expires"] < time.time():
            raise HttpError(403, t("código caducado o ya usado: vuelve a mostrar el QR"))
        pid = secrets.token_hex(16)
        ua = req.headers.get("user-agent", "")
        name = "móvil"
        for tag in ("iPhone", "iPad", "Android", "Windows", "Macintosh", "Linux"):
            if tag in ua:
                name = tag
                break
        row = {"id": pid, "name": f"{name} ({req.peer.split(':')[0]})", "created": time.time(), "session": info["session"],
               "last_seen": time.time()}
        self.paired[pid] = row
        while len(self.paired) > MAX_PAIRED:
            oldest = min(self.paired.values(), key=lambda p: p.get("created") or 0)
            self.paired.pop(oldest["id"], None)
        self._save()
        return self._cookie_value(pid), row

    def _authenticated(self, req: Request) -> dict[str, Any] | None:
        raw = req.cookies.get(COOKIE, "")
        pid, _, sig = raw.partition(".")
        if not pid or not sig or pid not in self.paired:
            return None
        if not hmac.compare_digest(self._cookie_value(pid), raw):
            return None
        row = self.paired[pid]
        row["last_seen"] = time.time()
        return row

    def forget_all(self) -> int:
        n = len(self.paired)
        self.paired.clear()
        self.secret = secrets.token_bytes(32)
        self._save()
        return n

    def _session_event(self, kind: str, session: Session) -> None:
        if kind != "closed":
            return
        for row in self.paired.values():
            if row.get("session") == session.id:
                row["session"] = None  # the mpv that showed the QR is gone: follow the most recent one from now on
        for t in [t for t, v in self.tokens.items() if v.get("session") == session.id]:
            self.tokens.pop(t, None)

    def _session_for(self, row: dict[str, Any]) -> Session:
        sid = row.get("session")
        try:
            return pick_session(self.server, sid if sid and self.server.sessions.get(sid) else None)
        except RpcError as exc:
            raise HttpError(503, t("no hay ningún reproductor abierto")) from exc

    # -- HTTP ----------------------------------------------------------------------------------------------

    async def handle(self, req: Request) -> Response:
        # H49/G8 · todo lo que se diga dentro de esta petición va en el idioma de QUIEN la hace (su navegador),
        # no en el de este equipo: los errores de la sala y del mando los lee el invitado, no el anfitrión.
        token = visitor_language(req.headers.get("accept-language"))
        try:
            return await self._handle(req)
        finally:
            forget_visitor(token)

    async def _handle(self, req: Request) -> Response:
        path = req.path
        if req.method in ("GET", "HEAD") and path == "/i18n.js":
            # H49/G6 · el mando lo abre otra persona en su móvil: las cadenas, en el idioma de SU navegador
            return Response(200, {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-cache"},
                            page_script(self.www, req.headers.get("accept-language")))
        if req.method in ("GET", "HEAD") and path in STATIC:
            resp = Response.file(self.www / STATIC[path], cache="no-cache")
            if STATIC[path] in BRANDED and app_name() != "MPV-UOS":
                resp.body = resp.body.replace(b"MPV-UOS", app_name().encode("utf-8"))
            return resp
        if not path.startswith("/api/") and path not in ("/events", "/events/tasks"):
            raise HttpError(404)
        if req.method == "POST":
            origin = req.headers.get("origin")
            host = req.headers.get("host", "")
            if origin and origin.split("://", 1)[-1] != host:
                raise HttpError(403, t("origen no permitido"))
        if path == "/api/pair" and req.method == "POST":
            body = req.json() or {}
            token = str(body.get("token") or "").strip()
            value, row = self._pair(token, req)
            cookie = f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={365 * 86400}"
            return Response.json({"ok": True, "name": row["name"], "version": __version__}, **{"Set-Cookie": cookie})
        row = self._authenticated(req)
        if row is None:
            raise HttpError(401, t("sin emparejar: escanea el QR del reproductor (alt+z)"))
        if path == "/api/unpair" and req.method == "POST":
            self.paired.pop(row["id"], None)
            self._save()
            return Response.json({"ok": True}, **{"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
        if path == "/events":
            return Response.sse(self._events(row))
        panel = await self.downloads.handle(req, row)
        if panel is not None:
            return panel
        if path == "/api/state":
            return Response.json(await self.state(self._session_for(row)))
        if path == "/api/cmd" and req.method == "POST":
            body = req.json()
            if not isinstance(body, dict) or not body.get("cmd"):
                raise HttpError(400, t("falta cmd"))
            return Response.json(await self.command(self._session_for(row), str(body["cmd"]), body))
        if path == "/api/channels":
            return Response.json(await self._channels(req.query))
        if path == "/api/search":
            return Response.json(await self._search(self._session_for(row), req.query.get("q", "")))
        if path == "/api/recents":
            rows = await self._rpc("watch.recents", {"limit": _limit(req.query, 30, 200)})
            return Response.json(rows)
        if path == "/api/tracks":
            return Response.json(await self._tracks(self._session_for(row)))
        if path == "/api/playlist":
            return Response.json(await self._playlist(self._session_for(row)))
        if path == "/api/chapters":
            s = self._session_for(row)
            return Response.json({"chapters": await self._prop(s, "chapter-list") or [], "current": await self._prop(s, "chapter")})
        raise HttpError(404 if req.method == "GET" else 405)

    async def _rpc(self, method: str, params: dict[str, Any] | None = None, session: Session | None = None) -> Any:
        ctx = self.server.make_context(session=session)
        try:
            return await self.server.dispatcher.call(method, params or {}, ctx)
        except RpcError as exc:
            raise HttpError(400 if exc.code == INVALID_PARAMS else 503, exc.message) from exc

    async def _prop(self, s: Session, name: str) -> Any:
        try:
            return await s.client.get_property(name, timeout=5)
        except (MpvIpcError, ConnectionError, asyncio.TimeoutError):
            return None

    async def state(self, s: Session) -> dict[str, Any]:
        values = await asyncio.gather(*(self._prop(s, p) for p in STATE_PROPS))
        st = dict(zip(STATE_PROPS, values, strict=True))
        iptv = await self._prop(s, "user-data/mu/iptv")
        if isinstance(iptv, dict):
            st["channel"] = iptv.get("current") or None
            st["icy_title"] = iptv.get("icy_title") or None
        st["session"] = s.id
        st["ts"] = round(time.time(), 2)
        return st

    async def _events(self, row: dict[str, Any]) -> AsyncIterator[bytes]:
        self.clients += 1
        last: dict[str, Any] | None = None
        beat = time.monotonic()
        try:
            yield sse_event("hello", {"version": __version__, "name": row["name"]})
            while True:
                try:
                    s = self._session_for(row)
                    st = await self.state(s)
                except HttpError:
                    st = {"idle-active": True, "no_player": True, "ts": round(time.time(), 2)}
                cmp_new = {k: v for k, v in st.items() if k != "ts"}
                cmp_old = {k: v for k, v in (last or {}).items() if k != "ts"}
                if cmp_new != cmp_old:
                    yield sse_event("state", st)
                    last = st
                    beat = time.monotonic()
                elif time.monotonic() - beat > 15:
                    yield b": ping\n\n"
                    beat = time.monotonic()
                await asyncio.sleep(0.5)
        finally:
            self.clients -= 1

    async def command(self, s: Session, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        c = s.client

        async def run(*parts: Any) -> Any:
            try:
                return await c.command(*parts, timeout=10)
            except MpvIpcError as exc:
                raise HttpError(400, str(exc)) from exc

        def num(key: str, default: float = 0.0) -> float:
            try:
                return float(args.get(key, default))
            except (TypeError, ValueError) as exc:
                raise HttpError(400, t("%s debe ser numérico") % (key,)) from exc

        async def setp(name: str, value: Any) -> None:
            try:
                await c.set_property(name, value, timeout=10)
            except MpvIpcError as exc:
                raise HttpError(400, str(exc)) from exc

        async def addp(name: str, delta: float, lo: float, hi: float, as_int: bool = False) -> None:
            cur = await self._prop(s, name)
            if cur is None:
                raise HttpError(400, t("%s no disponible") % (name,))
            value = _clamp(float(cur) + delta, lo, hi)
            await setp(name, int(value) if as_int else value)

        if cmd == "toggle":
            await run("cycle", "pause")
        elif cmd == "pause":
            await setp("pause", True)
        elif cmd == "resume":
            await setp("pause", False)
        elif cmd == "seek":
            mode = str(args.get("mode") or "relative")
            if mode not in ("relative", "absolute", "absolute-percent", "relative-percent"):
                raise HttpError(400, t("mode inválido"))
            await run("seek", num("seconds"), mode)
        elif cmd == "volume":
            vmax = await self._prop(s, "volume-max") or 100
            await setp("volume", _clamp(num("value", 100), 0, float(vmax)))
        elif cmd == "volume_add":
            vmax = await self._prop(s, "volume-max") or 100
            await addp("volume", _clamp(num("delta", 5), -50, 50), 0, float(vmax))
        elif cmd == "mute":
            await run("cycle", "mute")
        elif cmd == "speed":
            await setp("speed", _clamp(num("value", 1.0), 0.25, 4.0))
        elif cmd == "next":
            await run("playlist-next")
        elif cmd == "prev":
            await run("playlist-prev")
        elif cmd == "playlist_play":
            await run("playlist-play-index", int(num("index")))
        elif cmd == "chapter":
            await addp("chapter", int(num("delta", 1)), 0, 10_000, as_int=True)
        elif cmd == "chapter_set":
            await setp("chapter", int(num("index")))
        elif cmd == "stop":
            await run("stop")
        elif cmd in ("sub", "audio"):
            raw = args.get("id")
            value: Any = "no" if raw in (None, "no", "", False) else int(raw)
            await setp("sid" if cmd == "sub" else "aid", value)
        elif cmd == "sub_cycle":
            await run("cycle", "sub")
        elif cmd == "audio_cycle":
            await run("cycle", "audio")
        elif cmd == "sub_toggle":
            await run("cycle", "sub-visibility")
        elif cmd == "fullscreen":
            await run("cycle", "fullscreen")
        elif cmd == "ab_loop":
            await run("ab-loop")
        elif cmd == "frame_step":
            await run("frame-step")
        elif cmd == "osd":
            await run("show-text", str(args.get("text") or "")[:200], 3000)
        elif cmd == "play":
            target = str(args.get("target") or "").strip()
            mode = str(args.get("mode") or "replace")
            if not target:
                raise HttpError(400, t("falta target"))
            if mode not in ("replace", "append-play", "append"):
                raise HttpError(400, t("mode inválido"))
            await run("loadfile", target, mode, -1)
        elif cmd == "channel":
            cid = str(args.get("id") or "").strip()
            if not cid:
                raise HttpError(400, t("falta id"))
            await self._rpc("iptv.channel", {"id": cid})
            await run("script-message-to", "mu_iptv", "mu-iptv-play", cid)
        elif cmd == "zap":
            await run("script-binding", "mu_iptv/zap-next" if num("delta", 1) >= 0 else "mu_iptv/zap-prev")
        elif cmd == "script_binding":
            name = str(args.get("name") or "")
            if not name.startswith("mu_") or "/" not in name:
                raise HttpError(400, t("solo bindings mu_*/…"))
            await run("script-binding", name)
        else:
            raise HttpError(400, f"comando desconocido: {cmd}")
        return {"ok": True, "cmd": cmd}

    async def _channels(self, query: dict[str, str]) -> Any:
        q = query.get("q", "").strip()
        kind = query.get("kind") or None
        limit = _limit(query, 40, 500)
        if q:
            return await self._rpc("iptv.search", {"q": q, "limit": limit, "kind": kind, "compact": True})
        try:
            rows = await self._rpc("iptv.recents.list", {"limit": limit})
        except HttpError:
            rows = []
        favs: list[Any] = []
        with contextlib.suppress(HttpError):
            favs = await self._rpc("iptv.favorites.list", {})
        return {"favorites": favs, "recents": rows}

    async def _search(self, s: Session, q: str) -> dict[str, Any]:
        q = q.strip()
        if not q:
            raise HttpError(400, t("falta q"))
        path = await self._prop(s, "path")
        if not path:
            raise HttpError(400, t("no hay archivo abierto"))
        out: dict[str, Any] = {"q": q, "path": path, "semantic": [], "text": [], "mode": "text"}
        with contextlib.suppress(HttpError):
            res = await self._rpc("semantic.search", {"q": q, "path": path, "k": 10, "index": True}, session=s)
            out["semantic"] = res.get("hits") or []
            out["mode"] = res.get("mode", "text")
            out["status"] = res.get("status")
        with contextlib.suppress(HttpError):
            res = await self._rpc("asr.search", {"q": q, "path": path, "limit": 20}, session=s)
            out["text"] = res.get("hits") or res.get("results") or []
        return out

    async def _tracks(self, s: Session) -> dict[str, Any]:
        tracks = await self._prop(s, "track-list") or []
        out: dict[str, list[dict[str, Any]]] = {"sub": [], "audio": [], "video": []}
        for t in tracks:
            if t.get("type") in out:
                out[t["type"]].append({"id": t.get("id"), "title": t.get("title"), "lang": t.get("lang"),
                                       "selected": bool(t.get("selected")), "external": bool(t.get("external")),
                                       "codec": t.get("codec")})
        return out

    async def _playlist(self, s: Session) -> dict[str, Any]:
        rows = await self._prop(s, "playlist") or []
        return {"items": [{"index": i, "filename": r.get("filename"), "title": r.get("title"), "current": bool(r.get("current"))}
                          for i, r in enumerate(rows)][:500], "pos": await self._prop(s, "playlist-pos")}


def register(server: MpvdServer, service: RemoteService) -> None:
    d = server.dispatcher
    server.services["remote"] = True

    @d.method("remote.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """Remote control server: running?, URL, paired phones, pending tokens."""
        return service.status()

    @d.method("remote.start")
    async def start(ctx: RpcContext, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        """Start the HTTP server of the remote (LAN by default; port from remote.json or 8790)."""
        return await service.start(host, port)

    @d.method("remote.stop")
    async def stop(ctx: RpcContext) -> dict[str, Any]:
        """Stop the HTTP server (paired phones are kept)."""
        await service.stop()
        service.autostart = False
        service._save()
        return service.status()

    @d.method("remote.pair")
    async def pair(ctx: RpcContext, session: str | None = None, ttl: float = TOKEN_TTL, path: str = "/",
                   local: bool = False) -> dict[str, Any]:
        """New one-time pairing token: URL for the phone plus the QR modules to draw (starts the server if needed).

        ``path``: page the link opens (``/`` the remote, ``/downloads`` the downloads panel); ``local``: a loopback
        URL for a browser on this computer."""
        sid = session or (ctx.session.id if ctx.session is not None else None)
        return await service.pair_token(sid, ttl, path, bool(local))

    @d.method("remote.forget")
    async def forget(ctx: RpcContext) -> dict[str, Any]:
        """Forget every paired phone (they must scan a new QR)."""
        return {"forgotten": service.forget_all()}
