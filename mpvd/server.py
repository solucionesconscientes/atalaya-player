"""mpvd daemon: JSON-RPC 2.0 over a Unix socket + one mpv IPC connection per session."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Any

from mpvd import __version__
from mpvd.cache import ArtifactCache
from mpvd.config import PROTOCOL_VERSION, Settings, project_root
from mpvd.guardian import PerformanceGuardian
from mpvd.jobs import Job, JobQueue
from mpvd.rpc import STREAM_LIMIT, Dispatcher
from mpvd.sessions import Session, SessionManager

log = logging.getLogger("mpvd.server")


@dataclass
class RpcContext:
    server: MpvdServer
    session: Session | None = None
    peer: asyncio.StreamWriter | None = None


def socket_is_alive(path: os.PathLike[str] | str, timeout: float = 1.0) -> bool:
    """True if something accepts connections on the Unix socket at ``path``."""
    if not os.path.exists(path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


class MpvdServer:
    def __init__(self, settings: Settings | None = None, iptv_sources: list[Any] | None = None,
                 radio_base_url: str | None = None):
        self.settings = settings or Settings.from_env()
        self.root = project_root()
        self.dispatcher = Dispatcher()
        self.guardian = PerformanceGuardian()
        self.jobs = JobQueue(workers=self.settings.workers, guardian=self.guardian, on_change=self._job_changed)
        self.cache = ArtifactCache(self.settings.cache_dir / "artifacts")
        self.sessions = SessionManager(self)
        self.started_at = time.time()
        self.last_activity = time.time()
        self._server: asyncio.AbstractServer | None = None
        self._peers: set[asyncio.StreamWriter] = set()
        self._stop = asyncio.Event()
        self._idle_task: asyncio.Task[None] | None = None
        self.services: dict[str, bool] = {}
        from mpvd import methods  # noqa: PLC0415 - avoid import cycle
        from mpvd.asr.service import AsrService  # noqa: PLC0415
        from mpvd.asr.service import register as register_asr  # noqa: PLC0415
        from mpvd import control  # noqa: PLC0415
        from mpvd.av import AvService  # noqa: PLC0415
        from mpvd.av import register as register_av  # noqa: PLC0415
        from mpvd.iptv.service import IptvService  # noqa: PLC0415
        from mpvd.subs.service import SubsService  # noqa: PLC0415
        from mpvd.subs.service import register as register_subs  # noqa: PLC0415
        from mpvd.iptv.service import register as register_iptv  # noqa: PLC0415
        from mpvd.watch import WatchService  # noqa: PLC0415
        from mpvd.watch import register as register_watch  # noqa: PLC0415
        from mpvd.ytdl.service import YtdlService  # noqa: PLC0415
        from mpvd.ytdl.service import register as register_ytdl  # noqa: PLC0415

        methods.register(self)
        self.iptv = IptvService(self, sources=iptv_sources, radio_base_url=radio_base_url)
        register_iptv(self, self.iptv)
        self.ytdl = YtdlService(self)
        register_ytdl(self, self.ytdl)
        self.watch = WatchService(self)
        register_watch(self, self.watch)
        self.asr = AsrService(self)
        register_asr(self, self.asr)
        self.subs = SubsService(self)
        register_subs(self, self.subs)
        self.av = AvService(self)
        register_av(self, self.av)
        control.register(self)

    # -- lifecycle -------------------------------------------------------------

    def touch(self) -> None:
        self.last_activity = time.time()

    def make_context(self, session: Session | None = None, peer: asyncio.StreamWriter | None = None) -> RpcContext:
        return RpcContext(server=self, session=session, peer=peer)

    async def start(self) -> None:
        s = self.settings
        s.ensure_dirs()
        if s.socket_path.exists():
            if socket_is_alive(s.socket_path):
                raise RuntimeError(f"another mpvd is already listening on {s.socket_path}")
            s.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_peer, path=str(s.socket_path), limit=STREAM_LIMIT)
        with contextlib.suppress(OSError):
            os.chmod(s.socket_path, 0o600)
        s.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        await self.jobs.start()
        await self.ytdl.start()
        self._idle_task = asyncio.create_task(self._idle_watch(), name="mpvd-idle")
        log.info("mpvd %s listening on %s (cache %s, workers %d)", __version__, s.socket_path, s.cache_dir, s.workers)

    async def serve_forever(self) -> None:
        await self._stop.wait()

    def request_shutdown(self) -> None:
        self._stop.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
        await self.sessions.close_all()
        await self.ytdl.close()
        await self.asr.close()
        await self.jobs.stop()
        for w in list(self._peers):
            w.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self.cache.close()
        self.iptv.close()
        self.watch.close()
        with contextlib.suppress(OSError):
            if self.settings.pid_path.exists() and self.settings.pid_path.read_text().strip() == str(os.getpid()):
                self.settings.pid_path.unlink()
        with contextlib.suppress(OSError):
            self.settings.socket_path.unlink()
        log.info("mpvd stopped")

    async def run(self) -> None:
        await self.start()
        try:
            await self.serve_forever()
        finally:
            await self.stop()

    async def _idle_watch(self) -> None:
        while True:
            await asyncio.sleep(5)
            timeout = self.settings.idle_timeout
            if timeout <= 0:
                continue
            idle = not self.sessions and not self._peers and self.jobs.pending() == 0
            if idle and time.time() - self.last_activity > timeout:
                log.info("idle for %.0fs without sessions: exiting", timeout)
                self.request_shutdown()
                return

    # -- job events pushed to mpv scripts ------------------------------------------

    def _job_changed(self, job: Job) -> None:
        """Forward job progress to the mpv script that asked for it (``meta.notify`` = script name)."""
        target = job.meta.get("notify")
        if not target or job.session_id is None:
            return
        session = self.sessions.get(job.session_id)
        if session is None or not session.connected:
            return
        session.push_job(target, job)

    # -- Unix socket peers -------------------------------------------------------

    async def _handle_peer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._peers.add(writer)
        self.touch()
        ctx = self.make_context(peer=writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                resp = await self.dispatcher.handle_text(line, ctx)
                if resp is not None:
                    writer.write(resp.encode("utf-8") + b"\n")
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("peer handler failed")
        finally:
            self._peers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self.touch()

    # -- info ----------------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        from mpvd.hardware import hardware_info  # noqa: PLC0415

        return {
            "mpvd": __version__,
            "protocol": PROTOCOL_VERSION,
            "methods": [{"name": m.name, "params": m.params, "doc": m.doc} for m in self.dispatcher.methods()],
            "services": dict(self.services),
            "hardware": hardware_info(),
            "paths": {"socket": str(self.settings.socket_path), "cache": str(self.settings.cache_dir)},
            "sessions": len(self.sessions),
            "workers": self.settings.workers,
            "uptime": round(time.time() - self.started_at, 1),
        }
