"""«Entrar en una sala» (H44/C6): the guest side of *Ver juntos*, inside Atalaya Player instead of a browser.

An invitation is ``http(s)://<host>/s/<room>#k=<token>``. The token lives in the fragment, which never reaches the
server: the web page reads it and posts it to ``api/join``, and this module does exactly the same by hand. Then it
opens the room's SSE stream and follows the host.

Two things are deliberate here:

* **The guest plays the original file** (``/s/<room>/file?k=…``), not the relay the browser gets. That is the whole
  point of joining from the player: original quality, every codec, instant seeks and zero CPU on the host, instead
  of the H.264 the browser needs. The relay is only used when there is nothing else (a live stream).
* **The drift correction is the same one the page uses** (``www/sync.js``), ported constant by constant, with a test
  that runs both and compares. Two implementations that drift apart would be two different experiences in the same
  room.

mpv does not send cookies, so every URL we hand it carries the guest's signed credential in the query (``?k=``), the
same value the cookie holds: it belongs to one guest, it does not let anyone into the room and it dies with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

log = logging.getLogger(__name__)

# -- the drift correction, mirrored from www/sync.js (seconds here, milliseconds there) -------------------------
HARD = 1.5          # difference that is jumped over
SOFT = 0.15         # below this, left alone
MAX_ADJ = 0.08      # at most ±8 % of speed while catching up
GAIN = 0.5          # speed change per second of difference
PAUSED_TOL = 0.3

TICK = 0.5          # how often the guest compares itself with the host
SEEK_GRACE = 1.2    # after a jump, mpv's time-pos means nothing for a moment
LOAD_GRACE = 2.5    # ...and even less right after loading a file
ROOM_RE = re.compile(r"[A-Za-z0-9_-]{4,32}")
LINE_LIMIT = 1 << 20
CONNECT_TIMEOUT = 15.0
JOIN_TIMEOUT = 20.0
RETRY = (1.0, 2.0, 4.0, 8.0, 15.0)   # reconnection backoff, then every 15 s
COOKIE = "mu_share"


class GuestError(RuntimeError):
    """The room said no (bad link, full, closed) or it cannot be reached."""


def expected(state: dict[str, Any] | None, anchor: float, now: float) -> float | None:
    """Where the host is *now*: the last state plus the time since it arrived, by our own clock (the two machines
    never have to agree on the time of day)."""
    if not state:
        return None
    pos = float(state.get("pos") or 0)
    if not state.get("paused"):
        pos += max(0.0, now - anchor) * (float(state.get("speed") or 1) or 1.0)
    duration = state.get("duration")
    if duration and pos > float(duration):
        pos = float(duration)
    return pos


def correction(expected_pos: float, current: float, speed: Any, paused: bool) -> dict[str, Any]:
    """``{action: none|rate|seek, rate, to?, diff}``; ``diff > 0`` means the guest is behind."""
    speed = float(speed or 1) or 1.0
    diff = expected_pos - current
    far = abs(diff)
    if paused:
        if far > PAUSED_TOL:
            return {"action": "seek", "to": expected_pos, "rate": speed, "diff": diff}
        return {"action": "none", "rate": speed, "diff": diff}
    if far > HARD:
        return {"action": "seek", "to": expected_pos, "rate": speed, "diff": diff}
    if far > SOFT:
        adj = max(-MAX_ADJ, min(MAX_ADJ, diff * GAIN))
        return {"action": "rate", "rate": speed * (1 + adj), "diff": diff}
    return {"action": "none", "rate": speed, "diff": diff}


@dataclass(frozen=True)
class RoomLink:
    """An invitation, taken apart. ``token`` comes from the fragment, so it never travels in a request line."""

    scheme: str
    host: str
    port: int
    room: str
    token: str
    public: bool = False

    @property
    def tls(self) -> bool:
        return self.scheme == "https"

    @property
    def authority(self) -> str:
        default = 443 if self.tls else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"

    @property
    def base(self) -> str:
        return f"{self.scheme}://{self.authority}"

    def url(self, rest: str, k: str | None = None) -> str:
        rest = rest if rest.startswith("/") else f"/s/{self.room}/{rest}"
        sep = "&" if "?" in rest else "?"
        return self.base + rest + (f"{sep}k={quote(k, safe='')}" if k else "")


def parse_link(raw: str) -> RoomLink | None:
    """``http(s)://host[:port]/s/<room>#k=<token>[&v=1]`` → RoomLink, or None if it is not a room link."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    path = parts.path.rstrip("/").split("/")
    if len(path) < 3 or path[1] != "s" or not ROOM_RE.fullmatch(path[2]):
        return None
    frag = parse_qs(parts.fragment)
    token = (frag.get("k") or [""])[0]
    if not token:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return RoomLink(parts.scheme, parts.hostname, port, path[2], token, bool(frag.get("v")))


def looks_like_room(raw: str) -> bool:
    """Cheap check for the launcher and the menu: is this an invitation and not something to play?"""
    return parse_link(raw) is not None


# -- a minimal HTTP/SSE client ---------------------------------------------------------------------------------
# Not urllib: the event stream has to be read without blocking the loop, and it is a long GET that never ends.
# The server at the other end is ours, so one request per connection and no chunked bodies is enough.

async def _open(link: RoomLink) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    ctx = ssl.create_default_context() if link.tls else None
    return await asyncio.wait_for(
        asyncio.open_connection(link.host, link.port, ssl=ctx, limit=LINE_LIMIT,
                                server_hostname=link.host if ctx else None),
        CONNECT_TIMEOUT)


def _request_bytes(method: str, path: str, link: RoomLink, *, body: bytes = b"", cookie: str = "",
                   accept: str = "application/json") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {link.authority}", f"Accept: {accept}",
             "User-Agent: atalaya-guest", "Connection: close"]
    if body:
        lines += ["Content-Type: application/json; charset=utf-8", f"Content-Length: {len(body)}",
                  f"Origin: {link.base}"]   # the server checks that a POST comes from its own origin
    if cookie:
        lines.append(f"Cookie: {COOKIE}={cookie}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


async def _read_head(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
    status_line = await reader.readline()
    if not status_line:
        raise GuestError("la sala no ha contestado")
    try:
        status = int(status_line.split(b" ")[1])
    except (IndexError, ValueError):
        raise GuestError("respuesta ininteligible de la sala") from None
    headers: dict[str, str] = {}
    while True:
        raw = await reader.readline()
        if raw in (b"\r\n", b"\n", b""):
            break
        name, _, value = raw.decode("utf-8", "replace").partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers


def _error_text(status: int, body: bytes) -> str:
    with contextlib.suppress(Exception):
        data = json.loads(body.decode("utf-8", "replace"))
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    text = body.decode("utf-8", "replace").strip()
    if text and len(text) < 200:
        return text
    return {401: "el enlace no vale", 403: "el enlace no vale o ha caducado", 404: "esa sala no existe",
            410: "la sala está cerrada", 429: "demasiados intentos: espera un poco"}.get(status, f"error {status}")


async def _json_request(link: RoomLink, method: str, path: str, *, body: dict[str, Any] | None = None,
                        cookie: str = "") -> tuple[dict[str, Any], dict[str, str]]:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
    reader, writer = await _open(link)
    try:
        writer.write(_request_bytes(method, path, link, body=raw, cookie=cookie))
        await writer.drain()
        status, headers = await asyncio.wait_for(_read_head(reader), JOIN_TIMEOUT)
        payload = await asyncio.wait_for(reader.read(), JOIN_TIMEOUT)
        if status != 200:
            raise GuestError(_error_text(status, payload))
        try:
            data = json.loads(payload.decode("utf-8", "replace") or "{}")
        except ValueError:
            raise GuestError("respuesta ininteligible de la sala") from None
        return (data if isinstance(data, dict) else {}), headers
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


@dataclass
class GuestState:
    """What the menu and the OSD show about the room we are in."""

    link: RoomLink
    name: str
    room: dict[str, Any] = field(default_factory=dict)
    guest: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    host: dict[str, Any] | None = None     # last state of the host
    url: str = ""                          # what our mpv is playing
    connected: bool = False
    error: str = ""
    seeks: int = 0
    diff: float = 0.0

    def public(self) -> dict[str, Any]:
        return {"joined": True, "link": self.link.base + f"/s/{self.link.room}", "room": self.room,
                "guest": self.guest, "name": self.name, "connected": self.connected, "error": self.error or None,
                "title": self.media.get("title") or (self.host or {}).get("title") or "",
                "kind": self.media.get("kind") or "", "paused": bool((self.host or {}).get("paused")),
                "pos": (self.host or {}).get("pos"), "duration": (self.host or {}).get("duration"),
                "diff": round(self.diff, 2), "seeks": self.seeks, "public_room": self.link.public,
                "url": self.url}


class GuestSession:
    """One player inside somebody else's room: joins, follows the host and gives up cleanly."""

    def __init__(self, service: Any, session: Any, link: RoomLink, name: str) -> None:
        self.service = service
        self.session = session
        self.link = link
        self.state = GuestState(link=link, name=name)
        self.cookie = ""
        self.host_at = 0.0          # monotonic clock when the last host state arrived
        self.quiet_until = 0.0      # ...and until when our own time-pos means nothing
        self.stopped = False
        self.task: asyncio.Task[None] | None = None
        self._speed = 1.0

    # -- joining ---------------------------------------------------------------------------------------------
    async def join(self) -> dict[str, Any]:
        body = {"token": self.link.token, "name": self.state.name}
        data, headers = await _json_request(self.link, "POST", f"/s/{self.link.room}/api/join", body=body)
        raw = headers.get("set-cookie", "")
        value = raw.split(";", 1)[0].partition("=")[2]
        if not value:
            raise GuestError("la sala no ha dado credencial")
        self.cookie = value
        self.state.room = data.get("room") or {}
        self.state.guest = data.get("guest") or {}
        self.task = asyncio.create_task(self._run(), name=f"share-guest-{self.link.room}")
        return self.state.public()

    async def leave(self) -> None:
        self.stopped = True
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
            self.task = None
        if self.cookie:
            with contextlib.suppress(Exception):
                await _json_request(self.link, "POST", f"/s/{self.link.room}/api/leave", body={},
                                    cookie=self.cookie)
        with contextlib.suppress(Exception):
            await self.session.client.set_property("speed", 1.0)

    # -- the loop --------------------------------------------------------------------------------------------
    async def _run(self) -> None:
        tries = 0
        while not self.stopped:
            try:
                await self._stream()
                tries = 0
            except asyncio.CancelledError:
                raise
            except GuestError as exc:
                self.state.error = str(exc)
                self._push("error", str(exc))
                if "cerrada" in str(exc) or "no vale" in str(exc) or "no existe" in str(exc):
                    self.state.connected = False
                    self._push("left", f"Fuera de la sala: {exc}")
                    return
            except Exception as exc:  # noqa: BLE001
                log.warning("share guest %s: %s", self.link.room, exc)
                self.state.error = str(exc) or type(exc).__name__
            self.state.connected = False
            if self.stopped:
                return
            wait = RETRY[min(tries, len(RETRY) - 1)]
            tries += 1
            await asyncio.sleep(wait)

    async def _stream(self) -> None:
        reader, writer = await _open(self.link)
        follow: asyncio.Task[None] | None = None
        try:
            writer.write(_request_bytes("GET", f"/s/{self.link.room}/events", self.link, cookie=self.cookie,
                                        accept="text/event-stream"))
            await writer.drain()
            status, _headers = await asyncio.wait_for(_read_head(reader), CONNECT_TIMEOUT)
            if status != 200:
                raise GuestError(_error_text(status, await reader.read()))
            self.state.connected = True
            self.state.error = ""
            follow = asyncio.create_task(self._follow(), name=f"share-guest-follow-{self.link.room}")
            event, data = "", []
            while not self.stopped:
                raw = await reader.readline()
                if not raw:
                    raise GuestError("se ha cortado la conexión con la sala")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "":
                    if event:
                        await self._on_event(event, "".join(data))
                    event, data = "", []
        finally:
            if follow is not None:
                follow.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await follow
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _on_event(self, event: str, raw: str) -> None:
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if event == "hello":
            self.state.room = data.get("room") or self.state.room
            self.state.guest = data.get("guest") or self.state.guest
            self._push("joined", f"En la sala de {self.state.room.get('host') or 'tu anfitrión'}")
        elif event == "state":
            self.state.host = data
            self.host_at = time.monotonic()
        elif event == "media":
            await self._media(data)
        elif event in ("notice", "chat"):
            text = data.get("text") or ""
            if text:
                self._push(event, text)
        elif event in ("closed", "kicked"):
            self.stopped = True
            self._push("left", "La sala se ha cerrado" if event == "closed" else "El anfitrión te ha sacado")
            await self._unload()

    # -- what we play ----------------------------------------------------------------------------------------
    def _source(self, media: dict[str, Any]) -> str:
        """The guest is a real player: the original file first, the relay only when there is nothing else.

        H51 · the URL comes from the room's own `url` field and is never rebuilt here. `/s/<room>/file` is the same
        string for every film, so building it here meant the guest could not tell that the host had changed film
        and stayed on the previous one; the server now puts a per-film token in it."""
        kind = media.get("kind")
        url = str(media.get("url") or "")
        if kind in ("file", "hls"):
            return self.link.url(url, self.cookie) if url.startswith("/") else url
        if kind == "direct":
            return url
        return ""

    async def _media(self, media: dict[str, Any]) -> None:
        self.state.media = media
        url = self._source(media)
        if not url:
            kind = media.get("kind")
            if kind == "preparing":
                self._push("media", "Preparando lo que está viendo el anfitrión…")
            elif kind == "none":
                self.state.url = ""
                self._push("media", "El anfitrión no está viendo nada")
            return
        if url == self.state.url:
            return
        self.state.url = url
        self.quiet_until = time.monotonic() + LOAD_GRACE
        title = media.get("title") or media.get("name") or ""
        self._push("media", f"Poniendo: {title}" if title else "Poniendo lo del anfitrión")
        await self.session.client.command("loadfile", url, "replace")

    async def _unload(self) -> None:
        with contextlib.suppress(Exception):
            await self.session.client.set_property("speed", 1.0)

    # -- following the host ----------------------------------------------------------------------------------
    async def _follow(self) -> None:
        while not self.stopped:
            await asyncio.sleep(TICK)
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.debug("share guest tick: %s", exc)

    async def _tick(self) -> None:
        host = self.state.host
        if host is None or not self.state.url:
            return
        now = time.monotonic()
        want = expected(host, self.host_at, now)
        if want is None:
            return
        client = self.session.client
        paused = bool(host.get("paused"))
        if bool(await client.get_property("pause")) != paused:
            await client.set_property("pause", paused)
        if now < self.quiet_until:
            return
        current = await client.get_property("time-pos")
        if not isinstance(current, (int, float)):
            return
        plan = correction(want, float(current), host.get("speed") or 1, paused)
        self.state.diff = plan["diff"]
        if plan["action"] == "seek":
            self.state.seeks += 1
            self.quiet_until = now + SEEK_GRACE
            await client.command("seek", plan["to"], "absolute+exact")
        if abs(plan["rate"] - self._speed) > 0.001:
            self._speed = plan["rate"]
            await client.set_property("speed", plan["rate"])

    # -- telling the player ----------------------------------------------------------------------------------
    def _push(self, kind: str, text: str) -> None:
        self.service.notify_guest(self, kind, text)
