"""Core JSON-RPC methods of mpvd (ping, version, capabilities, sessions.*, jobs.*, cache.*, file.*, guardian.*)."""

from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING, Any

from mpvd import __version__
from mpvd.config import PROTOCOL_VERSION
from mpvd.hashing import file_hash
from mpvd.jobs import Job
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext


def register(server: MpvdServer) -> None:  # noqa: C901 - flat list of small handlers
    d = server.dispatcher

    @d.method("ping")
    async def ping(ctx: RpcContext) -> dict[str, Any]:
        """Liveness check."""
        return {"pong": True, "time": time.time()}

    @d.method("version")
    async def version(ctx: RpcContext) -> dict[str, Any]:
        """Daemon and protocol versions."""
        return {"mpvd": __version__, "protocol": PROTOCOL_VERSION, "python": sys.version.split()[0]}

    @d.method("capabilities")
    async def capabilities(ctx: RpcContext) -> dict[str, Any]:
        """Methods, services, hardware and paths of this daemon."""
        return server.capabilities()

    @d.method("shutdown")
    async def shutdown(ctx: RpcContext) -> dict[str, Any]:
        """Stop the daemon (sessions are closed, queued jobs cancelled)."""
        asyncio.get_running_loop().call_later(0.05, server.request_shutdown)
        return {"ok": True}

    # -- sessions ---------------------------------------------------------------

    @d.method("sessions.register")
    async def sessions_register(ctx: RpcContext, ipc: str, pid: int | None = None) -> dict[str, Any]:
        """Attach to an mpv instance by its --input-ipc-server path (idempotent)."""
        try:
            session = await server.sessions.register(ipc, pid=pid)
        except (TimeoutError, OSError, ValueError) as exc:
            raise RpcError(UNAVAILABLE, f"cannot connect to mpv IPC {ipc}: {exc}") from exc
        return session.to_dict()

    @d.method("sessions.unregister")
    async def sessions_unregister(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Detach from an mpv instance."""
        return {"removed": await server.sessions.unregister(id)}

    @d.method("sessions.list")
    async def sessions_list(ctx: RpcContext) -> list[dict[str, Any]]:
        """Attached mpv instances."""
        return server.sessions.list()

    @d.method("sessions.current")
    async def sessions_current(ctx: RpcContext) -> dict[str, Any] | None:
        """The session the call came from (null over the Unix socket)."""
        return ctx.session.to_dict() if ctx.session is not None else None

    # -- jobs -------------------------------------------------------------------

    @d.method("jobs.list")
    async def jobs_list(ctx: RpcContext, include_finished: bool = True, session_id: str | None = None) -> list[dict[str, Any]]:
        """Queued, running and recently finished jobs."""
        return server.jobs.list(include_finished=include_finished, session_id=session_id)

    @d.method("jobs.get")
    async def jobs_get(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """One job by id."""
        job = server.jobs.get(id)
        if job is None:
            raise RpcError(NOT_FOUND, f"job not found: {id}")
        return job.to_dict()

    @d.method("jobs.cancel")
    async def jobs_cancel(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Cancel a queued or running job."""
        return {"cancelled": server.jobs.cancel(id)}

    @d.method("jobs.sleep")
    async def jobs_sleep(
        ctx: RpcContext, seconds: float = 1.0, priority: str = "interactive", heavy: bool = False, name: str = "sleep",
        notify: str | None = None,
    ) -> dict[str, Any]:
        """Diagnostic job that sleeps and reports progress (used by tests); ``notify`` = script to push events to."""

        async def body(job: Job) -> dict[str, Any]:
            steps = max(1, int(seconds * 10))
            for i in range(steps):
                await asyncio.sleep(seconds / steps)
                job.report((i + 1) / steps, f"{i + 1}/{steps}")
            return {"slept": seconds}

        sid = ctx.session.id if ctx.session is not None else None
        try:
            job = server.jobs.submit(name, body, priority=priority, heavy=heavy, session_id=sid,
                                     meta={"notify": notify} if notify else None)
        except (KeyError, ValueError) as exc:
            raise RpcError(INVALID_PARAMS, f"bad priority: {priority}") from exc
        return job.to_dict()

    # -- guardian ---------------------------------------------------------------

    @d.method("guardian.status")
    async def guardian_status(ctx: RpcContext) -> dict[str, Any]:
        """Throttle state of the performance guardian."""
        return server.guardian.status()

    @d.method("guardian.observe")
    async def guardian_observe(
        ctx: RpcContext, session_id: str, frame_drop_count: int, paused: bool = False
    ) -> dict[str, Any]:
        """Feed a frame-drop sample by hand (diagnostics/tests)."""
        server.guardian.observe(session_id, frame_drop_count, paused)
        return server.guardian.status()

    @d.method("guardian.release")
    async def guardian_release(ctx: RpcContext) -> dict[str, Any]:
        """Lift the throttle immediately."""
        server.guardian.release()
        return server.guardian.status()

    # -- files and cache ----------------------------------------------------------

    @d.method("file.hash")
    async def file_hash_method(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Path-independent identity of a local file (size + first/last 64 KiB)."""
        try:
            fh = await asyncio.to_thread(file_hash, path)
        except OSError as exc:
            raise RpcError(NOT_FOUND, f"cannot read {path}: {exc}") from exc
        return {"size": fh.size, "opensubtitles": fh.opensubtitles, "mu": fh.mu, "key": fh.key}

    @d.method("cache.get")
    async def cache_get(
        ctx: RpcContext, file_hash: str, artifact: str, model: str = "", version: str = "",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Look up a cached artifact (inline data and/or blob path)."""
        entry = await asyncio.to_thread(server.cache.get, file_hash, artifact, model, version, params)
        return entry.to_dict() if entry else None

    @d.method("cache.put")
    async def cache_put(
        ctx: RpcContext, file_hash: str, artifact: str, data: Any, model: str = "", version: str = "",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store JSON data for (file_hash, artifact, model, version, params)."""
        entry = await asyncio.to_thread(
            lambda: server.cache.put(file_hash, artifact, model=model, version=version, params=params, data=data)
        )
        return entry.to_dict()

    @d.method("cache.invalidate")
    async def cache_invalidate(
        ctx: RpcContext, file_hash: str | None = None, artifact: str | None = None, model: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Delete cached artifacts matching the given fields."""
        n = await asyncio.to_thread(server.cache.invalidate, file_hash, artifact, model, version)
        return {"removed": n}

    @d.method("cache.stats")
    async def cache_stats(ctx: RpcContext) -> dict[str, Any]:
        """Cache size and entry counts."""
        return await asyncio.to_thread(server.cache.stats)
