"""Cloudflare quick tunnel for the share rooms (H25, ADR-068): entering the room from outside the house.

``cloudflared tunnel --url http://127.0.0.1:<port>`` needs no account and no configuration: it prints an address like
``https://<random-words>.trycloudflare.com`` that lives exactly as long as the process. So the tunnel is opened when the
room opens and the process is killed when the room closes, and nothing of the player is reachable from the internet one
second longer than the room is. It is off unless the viewer asks for it in the «Compartir» menu.

The binary is not downloaded with the rest: ``MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh`` installs it in ``vendor/bin``
(pinned and verified by SHA-256 in vendor.lock), or it is taken from ``$MPV_UOS_CLOUDFLARED`` / the PATH.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import sys
from pathlib import Path

log = logging.getLogger("mpvd.share.tunnel")

URL_RE = re.compile(rb"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
START_TIMEOUT = 40.0      # cloudflared needs a few seconds to register the quick tunnel
STOP_TIMEOUT = 5.0
TAIL_LINES = 8


def find_cloudflared(root: Path | None) -> Path | None:
    """``cloudflared`` from $MPV_UOS_CLOUDFLARED, vendor/bin or the PATH (``.exe`` on Windows)."""
    name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
    env = os.environ.get("MPV_UOS_CLOUDFLARED")
    candidates = []
    if env:
        p = Path(env)
        candidates.append(p if p.is_file() else p / name)
    if root is not None:
        candidates.append(root / "vendor" / "bin" / name)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which(name)
    return Path(found) if found else None


class TunnelError(RuntimeError):
    pass


class CloudflaredTunnel:
    """A quick tunnel in front of the local share server. One at a time: ``start`` twice reuses the running one."""

    name = "cloudflared"

    def __init__(self, binary: Path, start_timeout: float = START_TIMEOUT):
        self.binary = binary
        self.start_timeout = start_timeout
        self.url: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._drain: asyncio.Task[None] | None = None

    def args(self, local_port: int) -> list[str]:
        return [str(self.binary), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{int(local_port)}"]

    async def start(self, local_port: int) -> str:
        if self._proc is not None and self._proc.returncode is None and self.url:
            return self.url
        await self.stop()
        proc = await asyncio.create_subprocess_exec(
            *self.args(local_port), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        self._proc = proc
        assert proc.stderr is not None
        tail: list[bytes] = []
        try:
            url = await asyncio.wait_for(self._read_url(proc.stderr, tail), self.start_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            await self.stop()
            raise TunnelError("cloudflared no dio una dirección a tiempo: " + _tail(tail)) from None
        except asyncio.CancelledError:
            await self.stop()
            raise
        if url is None:
            code = proc.returncode
            await self.stop()
            raise TunnelError(f"cloudflared terminó ({code}) sin dar una dirección: " + _tail(tail))
        self.url = url
        # keep reading, or cloudflared blocks on a full stderr pipe once it has been chatting for a while
        self._drain = asyncio.create_task(self._drain_stderr(proc), name="share-tunnel-drain")
        log.info("tunnel %s open: %s -> 127.0.0.1:%d", self.name, url, local_port)
        return url

    async def _read_url(self, stderr: asyncio.StreamReader, tail: list[bytes]) -> str | None:
        while True:
            line = await stderr.readline()
            if not line:
                return None            # cloudflared closed its output: it died
            tail.append(line.strip())
            del tail[:-TAIL_LINES]
            m = URL_RE.search(line)
            if m:
                return m.group(0).decode("ascii")

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        with contextlib.suppress(Exception):
            while await proc.stderr.readline():
                pass

    async def stop(self) -> None:
        proc, self._proc, self.url = self._proc, None, None
        if self._drain is not None:
            self._drain.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._drain
            self._drain = None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
        log.info("tunnel %s closed", self.name)


def _tail(lines: list[bytes]) -> str:
    text = " · ".join(ln.decode("utf-8", "replace") for ln in lines if ln)
    return text[-400:] if text else "sin salida"


__all__ = ["CloudflaredTunnel", "TunnelError", "find_cloudflared"]
