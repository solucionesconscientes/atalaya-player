"""Small JSON-RPC client for the mpvd Unix socket (used by the CLI, mu-core's `ensure` and tests)."""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

from mpvd.rpc import RpcError, encode, make_request


class MpvdClient:
    def __init__(self, socket_path: str):
        self.socket_path = str(socket_path)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)

    async def __aenter__(self) -> MpvdClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def connect(self, timeout: float = 5.0) -> None:
        self._reader, self._writer = await asyncio.wait_for(asyncio.open_unix_connection(self.socket_path), timeout)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None

    async def call(self, method: str, params: Any = None, timeout: float = 30.0) -> Any:
        assert self._reader is not None and self._writer is not None, "not connected"
        rid = next(self._ids)
        self._writer.write(encode(make_request(method, params, rid)).encode("utf-8") + b"\n")
        await self._writer.drain()
        line = await asyncio.wait_for(self._reader.readline(), timeout)
        if not line:
            raise ConnectionError("mpvd closed the connection")
        msg = json.loads(line)
        if "error" in msg:
            err = msg["error"]
            raise RpcError(err.get("code", -32603), err.get("message", "error"), err.get("data"))
        return msg.get("result")


async def rpc_call_async(socket_path: str, method: str, params: Any = None, timeout: float = 30.0) -> Any:
    async with MpvdClient(socket_path) as c:
        return await c.call(method, params, timeout=timeout)


def rpc_call(socket_path: str, method: str, params: Any = None, timeout: float = 30.0) -> Any:
    return asyncio.run(rpc_call_async(socket_path, method, params, timeout=timeout))
