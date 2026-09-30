"""H28 · mpvd/transport.py: the Unix socket path and the Windows named-pipe path (driven by a loop that provides
``start_serving_pipe`` / ``create_pipe_connection`` over Unix sockets, as the Proactor loop does over pipes), and a
real mpvd started on a Unix socket through it."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mpvd import transport
from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.server import MpvdServer


def test_names(monkeypatch):
    monkeypatch.setenv("USERNAME", "Ana López")
    assert transport.pipe_name("mpvd") == "\\\\.\\pipe\\mpv-uos-mpvd-Ana_L_pez" or \
        transport.pipe_name("mpvd").startswith("\\\\.\\pipe\\mpv-uos-mpvd-Ana")
    assert transport.is_pipe("\\\\.\\pipe\\x") and not transport.is_pipe("/run/user/1000/mpv-uos/mpvd.sock")
    monkeypatch.setattr(transport.sys, "platform", "win32")
    assert transport.is_pipe(transport.default_endpoint("/tmp/x.sock"))
    monkeypatch.setattr(transport.sys, "platform", "linux")
    assert transport.default_endpoint("/tmp/x.sock") == "/tmp/x.sock"
    assert transport.endpoint_alive("\\\\.\\pipe\\nobody-here") is False


class PipeLoop:
    """Proactor-style pipe methods mapped onto Unix sockets in ``base``."""

    def __init__(self, loop: asyncio.AbstractEventLoop, base: Path):
        self.loop, self.base = loop, base

    def _path(self, name: str) -> str:
        return str(self.base / name.replace("\\", "_").strip("_"))

    async def start_serving_pipe(self, factory, address):
        server = await self.loop.create_unix_server(factory, self._path(address))
        return [server]

    async def create_pipe_connection(self, factory, address):
        return await self.loop.create_unix_connection(factory, self._path(address))


def test_pipe_branch_roundtrip(tmp_path):
    async def go():
        loop = asyncio.get_running_loop()
        fake = PipeLoop(loop, tmp_path)
        name = transport.PIPE_PREFIX + "mpv-uos-test"
        got = []

        async def handle(reader, writer):
            line = await reader.readline()
            got.append(line)
            writer.write(b"pong\n")
            await writer.drain()
            writer.close()

        server = await transport.start_server(handle, name, 1 << 16, api=fake)
        reader, writer = await transport.open_connection(name, 1 << 16, api=fake)
        writer.write(b"ping\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 5) == b"pong\n"
        writer.close()
        server.close()
        await server.wait_closed()
        assert got == [b"ping\n"]
        with pytest.raises(OSError):
            await transport.open_connection(name, 1024, api=object())   # no Proactor: a clear error
        with pytest.raises(OSError):
            await transport.start_server(handle, name, 1024, api=object())

    asyncio.run(go())


def test_mpvd_over_transport(tmp_path):
    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=1)
        assert settings.endpoint == str(settings.socket_path)
        server = MpvdServer(settings)
        await server.start()
        try:
            assert transport.endpoint_alive(settings.endpoint)
            async with MpvdClient(settings.endpoint) as c:
                assert (await c.call("ping"))["pong"]
                caps = await c.call("capabilities")
                assert caps["paths"]["socket"] == settings.endpoint
        finally:
            await server.stop()
        assert not os.path.exists(settings.socket_path)

    asyncio.run(go())
