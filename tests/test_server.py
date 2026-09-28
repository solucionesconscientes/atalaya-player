"""mpvd server in-process: Unix socket JSON-RPC, core methods, jobs, cache, stale sockets."""

import asyncio
import json
import os
import socket as socketmod
from pathlib import Path

import pytest

from mpvd import __version__
from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import METHOD_NOT_FOUND, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.server import MpvdServer, socket_is_alive


def make_settings(tmp_path: Path, **kw) -> Settings:
    return Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", idle_timeout=0, workers=2, **kw)


def with_server(tmp_path: Path, fn, **kw):
    async def go():
        server = MpvdServer(make_settings(tmp_path, **kw))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_core_methods(tmp_path):
    async def fn(server, c):
        ping = await c.call("ping")
        version = await c.call("version")
        caps = await c.call("capabilities")
        sessions = await c.call("sessions.list")
        current = await c.call("sessions.current")
        return ping, version, caps, sessions, current

    ping, version, caps, sessions, current = with_server(tmp_path, fn)
    assert ping["pong"] is True
    assert version == {"mpvd": __version__, "protocol": 1, "python": version["python"]}
    names = {m["name"] for m in caps["methods"]}
    assert {"ping", "version", "capabilities", "jobs.list", "jobs.cancel", "sessions.register", "sessions.list",
            "file.hash", "cache.get", "cache.put", "cache.invalidate", "guardian.status", "shutdown"} <= names
    assert caps["hardware"]["cpu_count"] >= 1 and caps["protocol"] == 1
    assert caps["paths"]["socket"].endswith("mpvd.sock")
    assert sessions == [] and current is None


def test_pid_and_socket_files_and_stale_socket_cleanup(tmp_path):
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    settings.socket_path.write_text("stale")  # a dead leftover file, not a socket
    assert socket_is_alive(settings.socket_path) is False

    async def go():
        server = MpvdServer(settings)
        await server.start()
        assert settings.pid_path.read_text() == str(os.getpid())
        assert socket_is_alive(settings.socket_path) is True
        assert oct(settings.socket_path.stat().st_mode & 0o777) == "0o600"
        with pytest.raises(RuntimeError):  # a second daemon on the same socket refuses to start
            await MpvdServer(settings).start()
        await server.stop()
        assert not settings.socket_path.exists() and not settings.pid_path.exists()

    asyncio.run(go())


def test_errors_and_raw_protocol(tmp_path):
    async def fn(server, c):
        with pytest.raises(RpcError) as e1:
            await c.call("nope")
        with pytest.raises(RpcError) as e2:
            await c.call("jobs.get", {"id": "missing"})
        with pytest.raises(RpcError) as e3:
            await c.call("sessions.register", {"ipc": str(tmp_path / "no-such.sock")})
        with pytest.raises(RpcError) as e4:
            await c.call("file.hash", {"path": str(tmp_path / "missing.bin")})
        # raw framing: notification (no reply) followed by a request on the same connection
        assert c._writer is not None
        c._writer.write(b'{"jsonrpc":"2.0","method":"ping"}\n{"jsonrpc":"2.0","id":"z","method":"ping"}\n')
        await c._writer.drain()
        line = json.loads(await asyncio.wait_for(c._reader.readline(), 5))
        return e1.value.code, e2.value.code, e3.value.code, e4.value.code, line

    c1, c2, c3, c4, line = with_server(tmp_path, fn)
    assert (c1, c2, c3, c4) == (METHOD_NOT_FOUND, NOT_FOUND, UNAVAILABLE, NOT_FOUND)
    assert line["id"] == "z" and line["result"]["pong"] is True


def test_jobs_over_rpc(tmp_path):
    async def fn(server, c):
        j = await c.call("jobs.sleep", {"seconds": 0.3, "priority": "precompute"})
        assert j["status"] in ("queued", "running")
        for _ in range(50):
            got = await c.call("jobs.get", {"id": j["id"]})
            if got["status"] == "done":
                break
            await asyncio.sleep(0.05)
        slow = await c.call("jobs.sleep", {"seconds": 30})
        await asyncio.sleep(0.1)
        cancelled = await c.call("jobs.cancel", {"id": slow["id"]})
        await asyncio.sleep(0.1)
        listing = await c.call("jobs.list")
        with pytest.raises(RpcError):
            await c.call("jobs.sleep", {"priority": "bogus"})
        return got, cancelled, {x["id"]: x["status"] for x in listing}

    got, cancelled, statuses = with_server(tmp_path, fn)
    assert got["status"] == "done" and got["progress"] == 1.0 and got["message"].endswith("/3")
    assert cancelled == {"cancelled": True}
    assert "cancelled" in statuses.values()


def test_guardian_over_rpc_holds_heavy_jobs(tmp_path):
    async def fn(server, c):
        await c.call("guardian.observe", {"session_id": "s", "frame_drop_count": 0})
        await asyncio.sleep(0.2)
        st = await c.call("guardian.observe", {"session_id": "s", "frame_drop_count": 50})
        heavy = await c.call("jobs.sleep", {"seconds": 0.1, "heavy": True, "name": "heavy"})
        light = await c.call("jobs.sleep", {"seconds": 0.1, "heavy": False, "name": "light"})
        await asyncio.sleep(0.4)
        h1 = await c.call("jobs.get", {"id": heavy["id"]})
        l1 = await c.call("jobs.get", {"id": light["id"]})
        await c.call("guardian.release")
        await asyncio.sleep(0.4)
        h2 = await c.call("jobs.get", {"id": heavy["id"]})
        return st, h1["status"], l1["status"], h2["status"]

    st, h1, l1, h2 = with_server(tmp_path, fn)
    assert st["throttled"] is True and st["triggers"] == 1
    assert (h1, l1, h2) == ("queued", "done", "done")


def test_file_hash_and_cache_over_rpc(tmp_path, media_dir):
    async def fn(server, c):
        fh = await c.call("file.hash", {"path": str(media_dir / "video30.mkv")})
        put = await c.call("cache.put", {"file_hash": fh["key"], "artifact": "probe", "data": {"ok": 1},
                                         "params": {"x": 1}})
        get = await c.call("cache.get", {"file_hash": fh["key"], "artifact": "probe", "params": {"x": 1}})
        miss = await c.call("cache.get", {"file_hash": fh["key"], "artifact": "probe"})
        stats = await c.call("cache.stats")
        inv = await c.call("cache.invalidate", {"file_hash": fh["key"]})
        return fh, put, get, miss, stats, inv

    fh, put, get, miss, stats, inv = with_server(tmp_path, fn)
    assert fh["key"] == f"mu:{fh['mu']}" and len(fh["opensubtitles"]) == 16 and fh["size"] > 0
    assert get["id"] == put["id"] and get["data"] == {"ok": 1} and miss is None
    assert stats["entries"] == 1 and inv == {"removed": 1}


def test_shutdown_method_stops_server(tmp_path):
    async def go():
        server = MpvdServer(make_settings(tmp_path))
        await server.start()
        async with MpvdClient(str(server.settings.socket_path)) as c:
            assert await c.call("shutdown") == {"ok": True}
        await asyncio.wait_for(server.serve_forever(), 5)
        await server.stop()
        s = socketmod.socket(socketmod.AF_UNIX)
        with pytest.raises(OSError):
            s.connect(str(server.settings.socket_path))
        s.close()

    asyncio.run(go())
