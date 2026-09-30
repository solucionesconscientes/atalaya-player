"""``share.*``: «Compartir» (H25). A private room with a long invitation link: guests pick a name, get a cookie of
that room and watch in their browser in sync with the host's mpv (state over SSE: position, pause, speed, file;
the page corrects its own drift). Guests start as «solo ver»; one can ask for control, the host accepts or rejects
it in mpv (mu-share) and can take it back or expel them at any time. Joins, departures and guest actions show up
in mpv («Ana ha pausado») and in every page.

What the guests play: a web video's direct URL when a browser can play it (yt-dlp format), otherwise an HLS relay
made by ffmpeg in the cache (mpvd/share/hls.py); the active text subtitle goes along as WebVTT.

Network: its own HTTP server (port 8791, not the remote's): the remote's API must stay LAN-only, while this one is
the only thing a future tunnel will publish. ``ShareService.tunnel`` (None today) is where the Cloudflare quick tunnel
plugs in: started when a room opens, stopped when it closes (Ser's decision: internet only while a room is open)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from mpvd import __version__
from mpvd.brand import app_name
from mpvd.control import pick_session
from mpvd.convert import hw as hw_mod
from mpvd.mpvipc import MpvIpcError
from mpvd.remote import qr
from mpvd.remote.http import HttpError, HttpServer, Request, Response, sse_event
from mpvd.remote.service import firewall_hint, lan_ip
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.share import hls
from mpvd.share.rooms import PERM_CONTROL, PERM_VIEW, PERMS, ROOM_TTL, AttemptLimiter, JoinError, Room

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.sessions import Session

log = logging.getLogger("mpvd.share")

DEFAULT_PORT = 8791
COOKIE = "mu_share"
TARGET = "mu_share"              # mpv script that gets the host notices
POLL_SECONDS = 0.25
HEARTBEAT = 2.0                  # a state message at least this often while playing (guests re-anchor their clock)
SEEK_JUMP = 1.5                  # a position this far from the prediction is a seek
GUEST_ECHO = 1.5                 # a change this soon after a guest's command is that guest's, not the host's
LEAVE_GRACE = 8.0                # a guest without open streams this long has left (reloads do not count)
PING = 5.0
MAX_STREAMS = 3                  # relays kept per room (current file + fallback + previous)
STATIC = {"room.js": "text/javascript; charset=utf-8", "sync.js": "text/javascript; charset=utf-8",
          "room.css": "text/css; charset=utf-8", "hls.light.min.js": "text/javascript; charset=utf-8",
          "icon.svg": "image/svg+xml"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "media-src 'self' blob: https: http:; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
SEG_RE = re.compile(r"^(index\.m3u8|seg_\d{5}\.ts)$")
SUB_RE = re.compile(r"^[a-f0-9]{8}\.vtt$")
STATE_PROPS = ("path", "time-pos", "pause", "speed", "duration", "media-title", "aid", "sid", "idle-active",
               "eof-reached")


class Tunnel(Protocol):
    """A way to reach the room from the internet (H25 point 3: Cloudflare quick tunnel, not implemented yet).

    ``start`` gets the local port of the share server and returns the public base URL (``https://….trycloudflare.com``);
    ``stop`` tears it down. The service starts it when a room opens and stops it when the room closes or expires."""

    name: str

    async def start(self, local_port: int) -> str: ...

    async def stop(self) -> None: ...


def clock(seconds: float | None) -> str:
    s = int(max(0.0, float(seconds or 0)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


@dataclass
class RoomRuntime:
    room: Room
    session_id: str | None
    queues: dict[asyncio.Queue[bytes | None], str] = field(default_factory=dict)  # event queue → guest id
    state: dict[str, Any] = field(default_factory=dict)       # last host state sent
    raw: dict[str, Any] = field(default_factory=dict)         # last host properties read
    sent_at: float = 0.0                                        # monotonic time of the last state message
    media: dict[str, Any] = field(default_factory=lambda: {"kind": "none", "reason": "nada en reproducción"})
    media_key: tuple[Any, ...] | None = None
    media_sent: float = 0.0
    subs_key: tuple[Any, ...] | None = None
    info: dict[str, Any] | None = None                          # yt-dlp -J of the current web video
    streams: dict[str, hls.HlsStream] = field(default_factory=dict)
    guest_action: dict[str, float] = field(default_factory=dict)
    notices: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))
    task: asyncio.Task[None] | None = None
    jobs: set[asyncio.Task[Any]] = field(default_factory=set)
    dir: Path = Path()


class ShareService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.www = Path(__file__).resolve().parent / "www"
        self.http = HttpServer(self.handle, name=f"mpvd/{__version__}")
        self.host = os.environ.get("MPVD_SHARE_HOST", "0.0.0.0")
        self.public_host = os.environ.get("MPVD_SHARE_PUBLIC_HOST", "")
        env_port = os.environ.get("MPVD_SHARE_PORT")
        self.port_pref = int(env_port) if env_port not in (None, "") else DEFAULT_PORT
        self.limiter = AttemptLimiter()
        self.rt: RoomRuntime | None = None
        self.tunnel: Tunnel | None = None       # H25 point 3 plugs the Cloudflare tunnel in here
        self.public_url: str | None = None      # base URL given by the tunnel while a room is open
        self.root = server.settings.cache_dir / "share"
        self.firewall: dict[str, Any] | None = None
        self._seq = 0
        self._closing = False
        server.session_listeners.append(self._session_event)

    # -- life of the room --------------------------------------------------------------------------------

    def base_url(self) -> str:
        if self.public_url:
            return self.public_url.rstrip("/")
        host = self.public_host or (lan_ip() if self.host in ("0.0.0.0", "") else self.host)
        return f"http://{host}:{self.http.port}"

    def _room(self) -> RoomRuntime:
        if self.rt is None or not self.rt.room.alive():
            raise RpcError(NOT_FOUND, "no hay ninguna sala abierta")
        return self.rt

    async def create(self, session_id: str | None, ttl: float = ROOM_TTL) -> dict[str, Any]:
        if self.rt is not None and self.rt.room.alive():
            return await self.link()
        if self.rt is not None:
            await self.close("expired")
        try:
            session = pick_session(self.server, session_id)
        except RpcError as exc:
            raise RpcError(UNAVAILABLE, "abre el reproductor antes de crear la sala") from exc
        if not self.http.running:
            try:
                await self.http.start(self.host, self.port_pref)
            except OSError as exc:
                if self.port_pref == 0:
                    raise RpcError(UNAVAILABLE, f"no se pudo abrir el puerto: {exc}") from exc
                log.warning("port %d busy (%s): using a free one", self.port_pref, exc)
                await self.http.start(self.host, 0)
        room = Room.new(session.id, ttl)
        rt = RoomRuntime(room=room, session_id=session.id, dir=self.root / room.id)
        self.rt = rt
        if self.tunnel is not None:
            try:
                self.public_url = await self.tunnel.start(self.http.port)
            except Exception as exc:  # noqa: BLE001 - the room still works in the LAN
                log.warning("tunnel %s failed: %s", getattr(self.tunnel, "name", "?"), exc)
                self.public_url = None
        if self.host in ("0.0.0.0", "") and not self.public_url:
            self.firewall = await asyncio.to_thread(firewall_hint, self.http.port, lan_ip(), "compartir")
        rt.task = asyncio.create_task(self._poll(rt), name=f"share-room-{room.id}")
        log.info("room %s open on %s (session %s)", room.id, self.base_url(), session.id)
        return await self.link()

    async def link(self) -> dict[str, Any]:
        rt = self._room()
        url = self.base_url() + rt.room.link_path()
        code = qr.encode(url, level="M")
        return {"url": url, "token": rt.room.token, "room": rt.room.public(), "status": self.status(),
                "qr": {"version": code.version, "size": code.size, "runs": code.runs(), "rows": code.rows()}}

    async def rotate(self) -> dict[str, Any]:
        self._room().room.rotate()
        return await self.link()

    async def close(self, reason: str = "host") -> dict[str, Any]:
        rt = self.rt
        if rt is None or self._closing:
            return self.status()
        self._closing = True
        try:
            rt.room.closed = True
            text = {"host": "El anfitrión ha cerrado la sala", "expired": "La sala ha caducado",
                    "player": "El reproductor se ha cerrado"}.get(reason, "La sala se ha cerrado")
            self._broadcast("closed", {"reason": reason, "text": text})
            for q in list(rt.queues):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)
            if reason != "host":
                self._host_push(rt, "closed", text)
            if rt.task is not None and rt.task is not asyncio.current_task():
                rt.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await rt.task
            for t in list(rt.jobs):
                if t is not asyncio.current_task():
                    t.cancel()
            for st in list(rt.streams.values()):
                await st.stop()
            await asyncio.to_thread(self._remove_dir, rt.dir)
            self.rt = None
            await asyncio.sleep(0.3)  # let the event streams deliver «closed»
            if self.tunnel is not None and self.public_url:
                with contextlib.suppress(Exception):
                    await self.tunnel.stop()
            self.public_url = None
            await self.http.stop()
            log.info("room %s closed (%s)", rt.room.id, reason)
        finally:
            self._closing = False
        return self.status()

    @staticmethod
    def _remove_dir(path: Path) -> None:
        import shutil  # noqa: PLC0415

        if path.name and path.parent.name == "share":
            shutil.rmtree(path, ignore_errors=True)

    def status(self) -> dict[str, Any]:
        rt = self.rt
        open_ = rt is not None and rt.room.alive()
        out: dict[str, Any] = {
            "open": open_, "running": self.http.running, "port": self.http.port or self.port_pref,
            "url": None, "room": None, "guests": [], "pending": [], "media": None, "notices": [],
            "firewall": self.firewall if open_ else None, "tunnel": getattr(self.tunnel, "name", None),
            "public_url": self.public_url, "lan_only": self.public_url is None,
        }
        if open_ and rt is not None:
            out.update({"url": self.base_url() + rt.room.link_path(), "room": rt.room.public(),
                        "guests": [g.public() for g in rt.room.active_guests()],
                        "pending": [g.public() for g in rt.room.pending()], "media": self._media_public(rt),
                        "notices": list(rt.notices)[-5:], "title": rt.raw.get("media-title") or ""})
        return out

    def _session(self, rt: RoomRuntime) -> Session | None:
        s = self.server.sessions.get(rt.session_id) if rt.session_id else None
        return s if s is not None and s.connected else None

    def _session_event(self, kind: str, session: Session) -> None:
        rt = self.rt
        if kind == "closed" and rt is not None and rt.session_id == session.id:
            self._spawn(rt, self.close("player"))

    def _spawn(self, rt: RoomRuntime, coro: Any) -> asyncio.Task[Any]:
        t = asyncio.create_task(coro)
        rt.jobs.add(t)
        t.add_done_callback(rt.jobs.discard)
        return t

    # -- permissions (host side) ---------------------------------------------------------------------------

    def _guest(self, rt: RoomRuntime, guest_id: str) -> Any:
        g = rt.room.guests.get(guest_id)
        if g is None or g.kicked:
            raise RpcError(NOT_FOUND, "ese invitado ya no está en la sala")
        return g

    def set_permission(self, guest_id: str, perm: str) -> dict[str, Any]:
        rt = self._room()
        if perm not in PERMS:
            raise RpcError(INVALID_PARAMS, f"perm debe ser {' o '.join(PERMS)}")
        g = self._guest(rt, guest_id)
        before = g.perm
        rt.room.set_perm(guest_id, perm)
        if perm == PERM_CONTROL:
            text = f"{g.name} puede controlar la reproducción"
        else:
            text = f"{g.name} ya no controla la reproducción" if before == PERM_CONTROL else ""
        self._send_to(rt, g.id, "perm", {"perm": g.perm, "pending": False})
        if text:
            self._notice(rt, text, kind="perm", who=g.name, host_osd=False)
        self._guests_changed(rt)
        return self.status()

    def deny(self, guest_id: str) -> dict[str, Any]:
        rt = self._room()
        g = self._guest(rt, guest_id)
        rt.room.deny(guest_id)
        self._send_to(rt, g.id, "perm", {"perm": g.perm, "pending": False, "denied": True})
        self._guests_changed(rt)
        return self.status()

    def kick(self, guest_id: str) -> dict[str, Any]:
        rt = self._room()
        g = self._guest(rt, guest_id)
        rt.room.kick(guest_id)
        self._send_to(rt, g.id, "kicked", {"text": "El anfitrión te ha sacado de la sala"})
        for q, gid in list(rt.queues.items()):
            if gid == g.id:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)
        self._notice(rt, f"{g.name} ha salido de la sala", kind="kick", who=g.name, host_osd=False)
        self._guests_changed(rt)
        return self.status()

    # -- events to guests and host ---------------------------------------------------------------------------

    def _put(self, q: asyncio.Queue[bytes | None], data: bytes) -> None:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:  # a stuck client: drop its oldest message
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(data)

    def _broadcast(self, event: str, data: Any) -> None:
        rt = self.rt
        if rt is None:
            return
        raw = sse_event(event, data)
        for q in list(rt.queues):
            self._put(q, raw)

    def _send_to(self, rt: RoomRuntime, guest_id: str, event: str, data: Any) -> None:
        raw = sse_event(event, data)
        for q, gid in list(rt.queues.items()):
            if gid == guest_id:
                self._put(q, raw)

    def _host_push(self, rt: RoomRuntime, kind: str, text: str = "", **extra: Any) -> None:
        s = self._session(rt)
        if s is None:
            return
        self._seq += 1
        payload = {"event": "share", "kind": kind, "text": text, **extra, "status": self.status()}
        s.push_event(TARGET, f"share:{self._seq}", kind, payload, final=True)

    def _notice(self, rt: RoomRuntime, text: str, kind: str = "info", who: str = "", host_osd: bool = True) -> None:
        row = {"text": text, "kind": kind, "who": who, "at": round(time.time(), 2)}
        rt.notices.append(row)
        self._broadcast("notice", row)
        if host_osd:
            self._host_push(rt, "notice", text, notice=row)

    def _guests_changed(self, rt: RoomRuntime) -> None:
        self._broadcast("guests", [g.public() for g in rt.room.active_guests()])
        self._host_push(rt, "guests")

    # -- host state ------------------------------------------------------------------------------------------

    async def _prop(self, s: Session, name: str) -> Any:
        try:
            return await s.client.get_property(name, timeout=5)
        except (MpvIpcError, ConnectionError, asyncio.TimeoutError):
            return None

    async def _read_host(self, s: Session) -> dict[str, Any]:
        values = await asyncio.gather(*(self._prop(s, p) for p in STATE_PROPS))
        return dict(zip(STATE_PROPS, values, strict=True))

    def _state_msg(self, raw: dict[str, Any], reason: str) -> dict[str, Any]:
        return {"title": raw.get("media-title") or "", "pos": round(float(raw.get("time-pos") or 0), 3),
                "paused": bool(raw.get("pause")) or bool(raw.get("idle-active")),
                "speed": float(raw.get("speed") or 1.0), "duration": raw.get("duration"),
                "idle": bool(raw.get("idle-active")), "eof": bool(raw.get("eof-reached")), "reason": reason,
                "at": round(time.time(), 3)}

    def _recent_guest(self, rt: RoomRuntime, what: str) -> bool:
        return time.monotonic() - rt.guest_action.get(what, -1e9) < GUEST_ECHO

    async def _poll(self, rt: RoomRuntime) -> None:
        last_mono = time.monotonic()
        try:
            while True:
                room = rt.room
                if room.closed:
                    return
                if room.expired():
                    self._spawn(rt, self.close("expired"))
                    return
                s = self._session(rt)
                if s is None:
                    self._spawn(rt, self.close("player"))
                    return
                raw = await self._read_host(s)
                now = time.monotonic()
                old = rt.raw
                reason = ""
                if raw.get("path") != old.get("path") or raw.get("aid") != old.get("aid"):
                    reason = "file" if raw.get("path") != old.get("path") else "track"
                    if raw.get("path") and reason == "file" and old:
                        self._notice(rt, f"Ahora: {raw.get('media-title') or ''}", kind="file", host_osd=False)
                    self._schedule_media(rt, raw)
                if raw.get("sid") != old.get("sid") or reason:
                    self._schedule_subs(rt, raw)
                if not reason and bool(raw.get("pause")) != bool(old.get("pause")):
                    reason = "pause"
                    if not self._recent_guest(rt, "pause") and not raw.get("idle-active") and old:
                        self._notice(rt, "El anfitrión ha pausado" if raw.get("pause") else "El anfitrión ha reanudado",
                                     kind="pause", who="", host_osd=False)
                if not reason and raw.get("speed") != old.get("speed"):
                    reason = "speed"
                if not reason and old and isinstance(raw.get("time-pos"), (int, float)):
                    predicted = float(old.get("time-pos") or 0)
                    if not old.get("pause"):
                        predicted += (now - last_mono) * float(old.get("speed") or 1.0)
                    if abs(float(raw["time-pos"]) - predicted) > SEEK_JUMP:
                        reason = "seek"
                        if not self._recent_guest(rt, "seek"):
                            self._notice(rt, f"El anfitrión ha saltado a {clock(raw['time-pos'])}", kind="seek",
                                         host_osd=False)
                if not reason and (not rt.state or (not raw.get("pause") and now - rt.sent_at >= HEARTBEAT)):
                    reason = "tick"
                rt.raw = raw
                last_mono = now
                if reason:
                    rt.state = self._state_msg(raw, reason)
                    rt.sent_at = now
                    self._broadcast("state", rt.state)
                self._media_progress(rt)
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("share poller failed")

    # -- media -----------------------------------------------------------------------------------------------

    def _media_public(self, rt: RoomRuntime) -> dict[str, Any]:
        m = dict(rt.media)
        m.pop("path", None)
        return m

    def _media_progress(self, rt: RoomRuntime) -> None:
        changed = False
        for key in ("stream", "relay_stream"):
            sid = rt.media.get(key)
            st = rt.streams.get(sid) if sid else None
            if st is None:
                continue
            info = st.info()
            prefix = "" if key == "stream" else "relay_"
            new = {prefix + "status": info["status"], prefix + "mode": info["mode"], prefix + "ready": info["ready"],
                   prefix + "complete": info["complete"], prefix + "error": info["error"]}
            if any(rt.media.get(k) != v for k, v in new.items()):
                status_changed = rt.media.get(prefix + "status") != new[prefix + "status"] or \
                    rt.media.get(prefix + "complete") != new[prefix + "complete"] or \
                    (not rt.media.get(prefix + "ready") and new[prefix + "ready"])
                rt.media.update(new)
                if status_changed or time.monotonic() - rt.media_sent >= 1.0:
                    changed = True
        if changed:
            self._send_media(rt)

    def _send_media(self, rt: RoomRuntime) -> None:
        rt.media_sent = time.monotonic()
        self._broadcast("media", self._media_public(rt))

    def _schedule_media(self, rt: RoomRuntime, raw: dict[str, Any]) -> None:
        path = raw.get("path")
        key = (path, raw.get("aid"))
        rt.media_key = key
        rt.info = None
        if not path:
            rt.media = {"kind": "none", "reason": "nada en reproducción"}
            self._send_media(rt)
            return
        rt.media = {"kind": "preparing", "title": raw.get("media-title") or ""}
        self._send_media(rt)
        self._spawn(rt, self._prepare_media(rt, key, str(path)))

    async def _audio_ff_index(self, s: Session) -> int | None:
        tracks = await self._prop(s, "track-list") or []
        for t in tracks:
            if t.get("type") == "audio" and t.get("selected") and not t.get("external"):
                idx = t.get("ff-index")
                return int(idx) if isinstance(idx, int) else None
        return None

    async def _prepare_media(self, rt: RoomRuntime, key: tuple[Any, ...], path: str) -> None:
        s = self._session(rt)
        media: dict[str, Any]
        try:
            if not hls.is_url(path):
                local = Path(path.removeprefix("file://"))
                if not local.is_file():
                    raise ValueError("solo se retransmiten archivos de este equipo o vídeos de internet")
                audio_index = await self._audio_ff_index(s) if s is not None else None
                st = await self._start_stream(rt, [hls.Input(str(local))], audio_index)
                media = {"kind": "hls", "url": self._stream_url(rt, st), "stream": st.id, "source": "local"}
            elif hls.plain_direct(path):
                media = {"kind": "direct", "url": path, "source": "url", "can_relay": True, "path": path}
            else:
                seed = None
                if s is not None:
                    res = await self._prop(s, "user-data/mpv/ytdl/json-subprocess-result")
                    if isinstance(res, dict) and res.get("status") == 0 and isinstance(res.get("stdout"), str):
                        seed = res["stdout"]
                info: dict[str, Any] | None = None
                with contextlib.suppress(RpcError, Exception):
                    info = await self.server.ytdl.raw_info(path, seed=seed)
                rt.info = info
                direct = hls.pick_direct(info) if info else None
                if direct:
                    media = {"kind": "direct", "url": direct["url"], "source": "web", "can_relay": True,
                             "format": direct.get("format_id"), "path": path}
                else:
                    inputs = hls.pick_relay(info) if info else [hls.Input(path)]
                    st = await self._start_stream(rt, inputs, None)
                    media = {"kind": "hls", "url": self._stream_url(rt, st), "stream": st.id, "source": "web"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("share media for %s: %s", path, exc)
            media = {"kind": "none", "reason": str(exc) or type(exc).__name__}
        if rt.media_key != key or rt.room.closed:
            return
        media["title"] = rt.raw.get("media-title") or ""
        subs = rt.media.get("subs")
        rt.media = {**media, "subs": subs}
        self._media_progress(rt)
        self._send_media(rt)

    def _stream_url(self, rt: RoomRuntime, st: hls.HlsStream) -> str:
        return f"/s/{rt.room.id}/media/{st.id}/{hls.PLAYLIST}"

    async def _start_stream(self, rt: RoomRuntime, inputs: list[hls.Input], audio_index: int | None) -> hls.HlsStream:
        if not hls.ffmpeg_bin():
            raise RuntimeError("ffmpeg no está instalado")
        probes = [await asyncio.to_thread(hls.probe, i) for i in inputs]
        needs_video = any(hls.video_stream(p) is not None and not hls.video_copyable(hls.video_stream(p))
                          for p in probes)
        hw_plan = None
        if needs_video:
            caps = await self.server.convert.hw_caps()
            hw_plan = hw_mod.plan_for(caps, "h264")
        sid = secrets.token_hex(4)
        out = rt.dir / sid
        plans = hls.plans_for(inputs, probes, out, audio_index=audio_index, hw=hw_plan)
        st = hls.HlsStream(sid, out, plans, duration=hls.duration_of(probes[0]))
        st.start()
        rt.streams[sid] = st
        while len(rt.streams) > MAX_STREAMS:
            old_id = next(iter(rt.streams))
            old = rt.streams.pop(old_id)
            await old.stop()
            await asyncio.to_thread(old.remove_files)
        log.info("share relay %s: %s (%s)", sid, plans[0].mode, inputs[0].url[:80])
        return st

    async def relay(self) -> dict[str, Any]:
        """A guest cannot play the direct URL: make (once) an HLS relay of the same video."""
        rt = self._room()
        m = rt.media
        if m.get("kind") != "direct":
            return self._media_public(rt)
        if m.get("relay_url") or m.get("relay_pending"):
            return self._media_public(rt)
        key = rt.media_key
        m["relay_pending"] = True
        try:
            info = rt.info
            inputs = hls.pick_relay(info) if info else [hls.Input(str(m.get("path") or m.get("url")))]
            st = await self._start_stream(rt, inputs, None)
        except Exception as exc:  # noqa: BLE001
            m.pop("relay_pending", None)
            raise HttpError(503, f"no se pudo retransmitir: {exc}") from exc
        if rt.media_key == key:
            m.pop("relay_pending", None)
            m.update({"relay_url": self._stream_url(rt, st), "relay_stream": st.id})
            self._media_progress(rt)
            self._send_media(rt)
        return self._media_public(rt)

    def _schedule_subs(self, rt: RoomRuntime, raw: dict[str, Any]) -> None:
        key = (raw.get("path"), raw.get("sid"))
        if key == rt.subs_key:
            return
        rt.subs_key = key
        if not raw.get("sid") or raw.get("sid") == "no" or not raw.get("path"):
            if rt.media.get("subs"):
                rt.media["subs"] = None
                self._send_media(rt)
            return
        self._spawn(rt, self._prepare_subs(rt, key, str(raw["path"])))

    async def _prepare_subs(self, rt: RoomRuntime, key: tuple[Any, ...], path: str) -> None:
        s = self._session(rt)
        if s is None:
            return
        tracks = await self._prop(s, "track-list") or []
        track = next((t for t in tracks if t.get("type") == "sub" and t.get("selected")), None)
        subs: dict[str, Any] | None = None
        if track is not None and str(track.get("codec") or "") in hls.TEXT_SUB_CODECS:
            if track.get("external") and track.get("external-filename"):
                source, index = str(track["external-filename"]), None
            elif not track.get("external") and isinstance(track.get("ff-index"), int):
                source, index = path.removeprefix("file://"), int(track["ff-index"])
            else:
                source, index = "", None
            if source and not source.startswith(("memory://", "edl://")):
                name = secrets.token_hex(4) + ".vtt"
                out = rt.dir / "subs" / name
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix(".tmp.vtt")
                cmd = hls.vtt_command(source, tmp, index)
                proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                                            stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.PIPE)
                try:
                    _, err = await asyncio.wait_for(proc.communicate(), 180)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    err = b"timeout"
                if proc.returncode == 0 and tmp.is_file():
                    os.replace(tmp, out)
                    subs = {"url": f"/s/{rt.room.id}/subs/{name}", "lang": track.get("lang") or "",
                            "label": track.get("title") or track.get("lang") or "Subtítulos"}
                else:
                    log.warning("share subs: %s", err.decode("utf-8", "replace").strip()[-300:])
        if rt.subs_key != key or rt.room.closed:
            return
        rt.media["subs"] = subs
        self._send_media(rt)

    # -- HTTP -------------------------------------------------------------------------------------------------

    def _page(self) -> Response:
        resp = Response.file(self.www / "room.html", cache="no-cache")
        if app_name() != "MPV-UOS":
            resp.body = resp.body.replace(b"MPV-UOS", app_name().encode("utf-8"))
        resp.headers.update({"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer",
                             "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
        return resp

    @staticmethod
    def _file(path: Path, ctype: str, cache: str, req: Request) -> Response:
        if not path.is_file():
            raise HttpError(404)
        data = path.read_bytes()
        headers = {"Content-Type": ctype, "Cache-Control": cache, "Accept-Ranges": "bytes",
                   "X-Content-Type-Options": "nosniff"}
        rng = req.headers.get("range", "")
        m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
        if m and (m.group(1) or m.group(2)):
            size = len(data)
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            if start > end or start >= size:
                return Response(416, {"Content-Range": f"bytes */{size}"}, b"")
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            return Response(206, headers, data[start:end + 1])
        return Response(200, headers, data)

    def _guest_from(self, rt: RoomRuntime, req: Request) -> Any:
        guest = rt.room.guest_from_cookie(req.cookies.get(COOKIE))
        if guest is None:
            raise HttpError(401, "no estás en la sala: abre el enlace de invitación")
        return guest

    async def handle(self, req: Request) -> Response:
        path = req.path
        if req.method in ("GET", "HEAD") and path.startswith("/static/"):
            name = path[len("/static/"):]
            if name not in STATIC:
                raise HttpError(404)
            return self._file(self.www / name, STATIC[name], "no-cache", req)
        if req.method in ("GET", "HEAD") and path in ("/", "/favicon.ico"):
            if path == "/favicon.ico":
                return self._file(self.www / "icon.svg", STATIC["icon.svg"], "max-age=86400", req)
            return Response.text("Pide el enlace de la sala a quien la ha creado.", 404)
        parts = path.split("/")
        if len(parts) < 3 or parts[1] != "s" or not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", parts[2]):
            raise HttpError(404)
        room_id, rest = parts[2], "/".join(parts[3:])
        if rest == "" and req.method in ("GET", "HEAD"):
            return self._page()
        if req.method == "POST":
            origin = req.headers.get("origin")
            host = req.headers.get("host", "")
            if origin and origin.split("://", 1)[-1] != host:
                raise HttpError(403, "origen no permitido")
        rt = self.rt
        if rt is None or rt.room.id != room_id or not rt.room.alive():
            raise HttpError(410, "la sala está cerrada o ha caducado")
        room = rt.room
        if rest == "api/join" and req.method == "POST":
            body = req.json() or {}
            if not isinstance(body, dict):
                raise HttpError(400, "cuerpo inválido")
            ip = req.peer.rsplit(":", 1)[0]
            try:
                guest = room.join(body.get("token"), body.get("name"), ip, self.limiter)
            except JoinError as exc:
                raise HttpError(exc.status, exc.message) from None
            max_age = max(60, int(room.expires_at - time.time()))
            cookie = (f"{COOKIE}={room.cookie_value(guest.id)}; Path=/s/{room.id}; HttpOnly; SameSite=Strict; "
                      f"Max-Age={max_age}")
            self._notice(rt, f"{guest.name} se ha unido", kind="join", who=guest.name)
            self._guests_changed(rt)
            return Response.json({"ok": True, "guest": guest.public(), "room": room.public()},
                                 **{"Set-Cookie": cookie})
        guest = self._guest_from(rt, req)
        if rest == "api/me":
            return Response.json({"guest": guest.public(), "room": room.public(), "state": rt.state or None,
                                  "media": self._media_public(rt), "notices": list(rt.notices)[-5:],
                                  "tunnel": self.public_url is not None})
        if rest == "events" and req.method in ("GET", "HEAD"):
            return Response.sse(self._events(rt, guest))
        if rest == "api/cmd" and req.method == "POST":
            body = req.json()
            if not isinstance(body, dict) or not body.get("cmd"):
                raise HttpError(400, "falta cmd")
            return Response.json(await self._guest_command(rt, guest, str(body["cmd"]), body))
        if rest == "api/request" and req.method == "POST":
            if room.request_control(guest.id):
                self._host_push(rt, "request", f"{guest.name} pide el control", guest=guest.public())
                self._guests_changed(rt)
            return Response.json({"perm": guest.perm, "pending": guest.pending})
        if rest == "api/leave" and req.method == "POST":
            room.guests.pop(guest.id, None)
            self._notice(rt, f"{guest.name} se ha ido", kind="leave", who=guest.name)
            self._guests_changed(rt)
            return Response.json({"ok": True}, **{"Set-Cookie": f"{COOKIE}=; Path=/s/{room.id}; Max-Age=0"})
        if rest == "api/relay" and req.method == "POST":
            return Response.json(await self.relay())
        if rest.startswith("media/") and req.method in ("GET", "HEAD"):
            _, sid, name = (rest.split("/") + ["", ""])[:3]
            st = rt.streams.get(sid)
            if st is None or not SEG_RE.match(name):
                raise HttpError(404)
            if name == hls.PLAYLIST:
                return self._file(st.dir / name, "application/vnd.apple.mpegurl", "no-cache", req)
            return self._file(st.dir / name, "video/mp2t", "private, max-age=3600", req)
        if rest.startswith("subs/") and req.method in ("GET", "HEAD"):
            name = rest[len("subs/"):]
            if not SUB_RE.match(name):
                raise HttpError(404)
            return self._file(rt.dir / "subs" / name, "text/vtt; charset=utf-8", "no-cache", req)
        raise HttpError(404 if req.method == "GET" else 405)

    async def _events(self, rt: RoomRuntime, guest: Any) -> AsyncIterator[bytes]:
        q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)
        rt.queues[q] = guest.id
        guest.streams += 1
        if guest.streams == 1:
            self._guests_changed(rt)
        try:
            yield sse_event("hello", {"guest": guest.public(), "room": rt.room.public(), "version": __version__})
            if rt.state:
                yield sse_event("state", {**rt.state, "reason": "hello"})
            yield sse_event("media", self._media_public(rt))
            while not rt.room.closed and not guest.kicked:
                try:
                    item = await asyncio.wait_for(q.get(), PING)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if item is None:
                    break
                yield item
                if item.startswith((b"event: closed", b"event: kicked")):
                    break
        finally:
            rt.queues.pop(q, None)
            guest.streams = max(0, guest.streams - 1)
            if guest.streams == 0 and not rt.room.closed:
                self._spawn(rt, self._maybe_left(rt, guest))

    async def _maybe_left(self, rt: RoomRuntime, guest: Any) -> None:
        await asyncio.sleep(LEAVE_GRACE)
        if guest.streams == 0 and not guest.kicked and guest.id in rt.room.guests and not rt.room.closed:
            self._notice(rt, f"{guest.name} se ha desconectado", kind="leave", who=guest.name)
            self._guests_changed(rt)

    async def _guest_command(self, rt: RoomRuntime, guest: Any, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        if not guest.can_control:
            raise HttpError(403, "solo puedes ver: pide el control al anfitrión")
        s = self._session(rt)
        if s is None:
            raise HttpError(503, "el reproductor no está disponible")
        c = s.client
        try:
            if cmd in ("pause", "resume", "toggle"):
                paused = bool(await self._prop(s, "pause"))
                want = {"pause": True, "resume": False}.get(cmd, not paused)
                rt.guest_action["pause"] = time.monotonic()
                await c.set_property("pause", want, timeout=10)
                if want != paused:
                    self._notice(rt, f"{guest.name} ha {'pausado' if want else 'reanudado'}", kind="pause",
                                 who=guest.name)
            elif cmd in ("seek", "seek_rel"):
                try:
                    value = float(args.get("seconds", 0))
                except (TypeError, ValueError):
                    raise HttpError(400, "seconds debe ser numérico") from None
                rt.guest_action["seek"] = time.monotonic()
                await c.command("seek", value, "absolute" if cmd == "seek" else "relative", timeout=10)
                pos = await self._prop(s, "time-pos")
                self._notice(rt, f"{guest.name} ha saltado a {clock(pos if pos is not None else value)}",
                             kind="seek", who=guest.name)
            else:
                raise HttpError(400, f"orden desconocida: {cmd}")
        except MpvIpcError as exc:
            raise HttpError(400, str(exc)) from exc
        return {"ok": True, "cmd": cmd}


def register(server: MpvdServer, service: ShareService) -> None:
    d = server.dispatcher
    server.services["share"] = True

    @d.method("share.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """The open room (link, guests with permissions, what the guests play), or open=false."""
        return service.status()

    @d.method("share.create")
    async def create(ctx: RpcContext, ttl_hours: float = ROOM_TTL / 3600, session: str | None = None) -> dict[str, Any]:
        """Open a private room for the caller's player (or return the one already open): link + QR modules."""
        sid = session or (ctx.session.id if ctx.session is not None else None)
        return await service.create(sid, float(ttl_hours) * 3600)

    @d.method("share.link")
    async def link(ctx: RpcContext) -> dict[str, Any]:
        """Link and QR of the open room."""
        return await service.link()

    @d.method("share.rotate")
    async def rotate(ctx: RpcContext) -> dict[str, Any]:
        """New invitation link (the old one stops working; guests inside stay)."""
        return await service.rotate()

    @d.method("share.close")
    async def close(ctx: RpcContext) -> dict[str, Any]:
        """Close the room: guests are told, relays stopped and removed, the server stops."""
        return await service.close("host")

    @d.method("share.permission")
    async def permission(ctx: RpcContext, guest: str, perm: str) -> dict[str, Any]:
        """Give (perm=control) or take back (perm=view) control of the playback; answers a pending request."""
        return service.set_permission(guest, perm)

    @d.method("share.deny")
    async def deny(ctx: RpcContext, guest: str) -> dict[str, Any]:
        """Reject a guest's request for control."""
        return service.deny(guest)

    @d.method("share.kick")
    async def kick(ctx: RpcContext, guest: str) -> dict[str, Any]:
        """Expel a guest (their cookie stops working; they would need the link again)."""
        return service.kick(guest)


__all__ = ["PERM_CONTROL", "PERM_VIEW", "ShareService", "Tunnel", "register"]
