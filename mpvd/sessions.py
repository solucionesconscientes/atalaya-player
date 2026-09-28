"""mpv sessions: mpvd connects to each mpv instance's JSON IPC socket and serves scripts through it.

Scripts call the daemon with ``script-message mu-rpc <json-rpc> [reply-target]`` (mpvd sees it as a
``client-message`` event) and get ``script-message-to <reply-target> mu-reply <json-rpc-response>``.
Default reply target: ``mu_core``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mpvd import __version__
from mpvd.config import PROTOCOL_VERSION
from mpvd.mpvipc import DISCONNECTED_EVENT, MpvIpcClient, MpvIpcError
from mpvd.rpc import encode

if TYPE_CHECKING:
    from mpvd.server import MpvdServer

log = logging.getLogger("mpvd.sessions")

RPC_MESSAGE = "mu-rpc"
REPLY_MESSAGE = "mu-reply"
HELLO_MESSAGE = "mu-hello"
EVENT_MESSAGE = "mu-event"
DEFAULT_TARGET = "mu_core"

OBSERVED = {1: "frame-drop-count", 2: "pause", 3: "path", 4: "media-title"}


@dataclass
class Session:
    ipc_path: str
    client: MpvIpcClient
    pid: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    connected: bool = True
    props: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    _pending: set[asyncio.Task[None]] = field(default_factory=set, repr=False)
    _pushed: dict[str, tuple[str, float]] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ipc": self.ipc_path,
            "pid": self.pid,
            "connected": self.connected,
            "created_at": self.created_at,
            "path": self.props.get("path"),
            "title": self.props.get("media-title"),
            "paused": self.props.get("pause"),
            "frame_drop_count": self.props.get("frame-drop-count"),
        }

    async def script_message(self, target: str, name: str, *args: str) -> None:
        await self.client.command("script-message-to", target, name, *args)

    async def reply(self, target: str, payload: str) -> None:
        await self.script_message(target, REPLY_MESSAGE, payload)

    def push_event(self, target: str, key: str, status: str, payload: dict[str, Any], min_interval: float = 0.25,
                   final: bool = False) -> None:
        """Send ``script-message-to <target> mu-event <json>``, throttled per ``key``.

        Status changes are always delivered; same-status updates at most every ``min_interval`` seconds.
        ``final`` drops the throttle entry (the key will not be seen again).
        """
        now = time.monotonic()
        last = self._pushed.get(key)
        if last is not None and last[0] == status and now - last[1] < min_interval:
            return
        self._pushed[key] = (status, now)
        if final:
            self._pushed.pop(key, None)
        t = asyncio.create_task(self._push(target, encode(payload)))
        self._pending.add(t)
        t.add_done_callback(self._pending.discard)

    def push_job(self, target: str, job: Any, min_interval: float = 0.25) -> None:
        """Push a job's state as ``{"event":"job","job":{...}}`` (see ``push_event``)."""
        status = job.status.value if hasattr(job.status, "value") else str(job.status)
        self.push_event(target, "job:" + job.id, status, {"event": "job", "job": job.to_dict()}, min_interval,
                        final=status in ("done", "failed", "cancelled"))

    async def _push(self, target: str, payload: str) -> None:
        with contextlib.suppress(ConnectionError, MpvIpcError, asyncio.TimeoutError):
            await self.script_message(target, EVENT_MESSAGE, payload)


class SessionManager:
    def __init__(self, server: MpvdServer):
        self.server = server
        self._sessions: dict[str, Session] = {}

    def list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._sessions.values()]

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)

    def find_by_ipc(self, ipc_path: str) -> Session | None:
        for s in self._sessions.values():
            if s.ipc_path == ipc_path and s.connected:
                return s
        return None

    async def register(self, ipc_path: str, pid: int | None = None, timeout: float = 5.0) -> Session:
        existing = self.find_by_ipc(ipc_path)
        if existing is not None:
            return existing
        client = MpvIpcClient(ipc_path)
        await client.connect(timeout=timeout)
        session = Session(ipc_path=ipc_path, client=client, pid=pid)
        with contextlib.suppress(MpvIpcError, ConnectionError, TimeoutError):
            session.pid = int(await client.get_property("pid"))
        self._sessions[session.id] = session
        session.task = asyncio.create_task(self._run(session), name=f"mpvd-session-{session.id}")
        self.server.touch()
        log.info("session %s registered (ipc=%s pid=%s)", session.id, ipc_path, session.pid)
        return session

    async def unregister(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if session.task is not None and session.task is not asyncio.current_task():
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.task
        await self._drop(session)
        return True

    async def close_all(self) -> None:
        for sid in list(self._sessions):
            await self.unregister(sid)

    async def _drop(self, session: Session) -> None:
        if self._sessions.pop(session.id, None) is None:
            return
        session.connected = False
        for t in list(session._pending):
            t.cancel()
        self.server.guardian.forget(session.id)
        cancelled = self.server.jobs.cancel_session(session.id)
        await session.client.close()
        self.server.touch()
        log.info("session %s closed (%d jobs cancelled)", session.id, cancelled)

    async def _run(self, session: Session) -> None:
        client = session.client
        try:
            for pid, name in OBSERVED.items():
                await client.command("observe_property", pid, name)
            hello = {
                "session": session.id,
                "version": __version__,
                "protocol": PROTOCOL_VERSION,
                "socket": str(self.server.settings.socket_path),
            }
            await session.script_message(DEFAULT_TARGET, HELLO_MESSAGE, encode(hello))
            while client.connected:
                ev = await client.events.get()
                kind = ev.get("event")
                if kind == "property-change":
                    session.props[ev.get("name", "")] = ev.get("data")
                    if ev.get("name") == "frame-drop-count":
                        self.server.guardian.observe(session.id, ev.get("data"), bool(session.props.get("pause")))
                elif kind == "client-message":
                    args = ev.get("args") or []
                    if args and args[0] == RPC_MESSAGE and len(args) >= 2:
                        target = args[2] if len(args) >= 3 and args[2] else DEFAULT_TARGET
                        t = asyncio.create_task(self._serve_rpc(session, args[1], target))
                        session._pending.add(t)
                        t.add_done_callback(session._pending.discard)
                elif kind in ("shutdown", DISCONNECTED_EVENT):
                    break
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            log.info("session %s: connection ended (%s)", session.id, exc)
        except Exception:  # noqa: BLE001
            log.exception("session %s: event loop failed", session.id)
        finally:
            await self._drop(session)

    async def _serve_rpc(self, session: Session, raw: str, target: str) -> None:
        ctx = self.server.make_context(session=session)
        try:
            resp = await self.server.dispatcher.handle_text(raw, ctx)
            if resp is not None and session.connected:
                await session.reply(target, resp)
        except (ConnectionError, MpvIpcError) as exc:
            log.debug("session %s: could not deliver reply (%s)", session.id, exc)
