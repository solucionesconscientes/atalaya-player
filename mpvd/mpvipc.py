"""Minimal asyncio client for mpv's JSON IPC protocol (``--input-ipc-server``).

Protocol (mpv manual, "JSON IPC"): newline-delimited JSON. Requests are
``{"command": [...], "request_id": N}``; replies carry the same ``request_id``
plus ``"error"`` ("success" or a message) and optional ``"data"``. Unsolicited
messages with an ``"event"`` key are events.

Unix sockets only for now; a Windows named-pipe transport is planned (see
docs/PLATAFORMAS.md).
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Callable
from typing import Any


DISCONNECTED_EVENT = "mpvd:disconnected"  # synthesized locally when the connection ends


class MpvIpcError(RuntimeError):
    """mpv answered a command with an error string."""

    def __init__(self, error: str, command: list[Any]):
        super().__init__(f"{error} (command: {command!r})")
        self.error = error
        self.command = command


class MpvIpcClient:
    """One connection to an mpv instance."""

    def __init__(self, path: str, *, event_queue_size: int = 2000):
        self.path = path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=event_queue_size)

    # -- lifecycle -----------------------------------------------------------

    async def connect(self, timeout: float = 15.0) -> MpvIpcClient:
        """Connect, waiting up to ``timeout`` seconds for the socket to appear."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(self.path)
                break
            except (FileNotFoundError, ConnectionRefusedError, PermissionError):
                if loop.time() >= deadline:
                    raise TimeoutError(f"mpv IPC socket not available: {self.path}") from None
                await asyncio.sleep(0.05)
        self._reader_task = asyncio.create_task(self._read_loop())
        return self

    async def __aenter__(self) -> MpvIpcClient:
        return await self.connect()

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._closed.is_set()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._closed.set()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("request_id")
                if rid is not None and rid in self._pending:
                    fut = self._pending.pop(rid)
                    if not fut.done():
                        fut.set_result(msg)
                elif "event" in msg:
                    if self.events.full():
                        self.events.get_nowait()  # drop the oldest event rather than block mpv
                    self.events.put_nowait(msg)
        finally:
            self._closed.set()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("mpv IPC connection closed"))
            self._pending.clear()
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait({"event": DISCONNECTED_EVENT})

    # -- commands ------------------------------------------------------------

    async def command(self, *args: Any, timeout: float = 10.0) -> Any:
        """Run an mpv command (list form) and return its ``data``."""
        if self._writer is None or self._closed.is_set():
            raise ConnectionError("not connected to mpv")
        rid = next(self._ids)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"command": list(args), "request_id": rid}).encode("utf-8") + b"\n"
        self._writer.write(payload)
        await self._writer.drain()
        msg = await asyncio.wait_for(fut, timeout)
        if msg.get("error") != "success":
            raise MpvIpcError(str(msg.get("error")), list(args))
        return msg.get("data")

    async def get_property(self, name: str, timeout: float = 10.0) -> Any:
        return await self.command("get_property", name, timeout=timeout)

    async def set_property(self, name: str, value: Any, timeout: float = 10.0) -> None:
        await self.command("set_property", name, value, timeout=timeout)

    async def wait_event(
        self,
        name: str,
        timeout: float = 10.0,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Return the next event called ``name`` (optionally matching ``predicate``)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"mpv event {name!r} not received in {timeout}s")
            ev = await asyncio.wait_for(self.events.get(), remaining)
            if ev.get("event") == DISCONNECTED_EVENT:
                raise ConnectionError("mpv IPC connection closed while waiting for " + name)
            if ev.get("event") == name and (predicate is None or predicate(ev)):
                return ev

    async def wait_property(
        self,
        name: str,
        predicate: Callable[[Any], bool],
        timeout: float = 10.0,
        interval: float = 0.1,
    ) -> Any:
        """Poll ``name`` until ``predicate(value)`` is true; return the value."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                value = await self.get_property(name)
            except MpvIpcError as exc:
                if exc.error != "property unavailable":
                    raise
                value = None
            if predicate(value):
                return value
            if loop.time() >= deadline:
                raise TimeoutError(f"mpv property {name!r} did not satisfy the predicate in {timeout}s (last={value!r})")
            await asyncio.sleep(interval)
