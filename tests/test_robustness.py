"""Robustness of the core: failed loads are explained and never close the player; two players on one socket
path get their own mpvd sessions; timed-out IPC commands leave nothing behind."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from mpvd.config import Settings
from mpvd.mpvipc import MpvIpcClient
from mpvd.server import MpvdServer
from tests.conftest import start_mpv


def test_failed_load_is_explained_and_mpv_stays_open(tmp_path: Path):
    h = start_mpv(tmp_path, ["--script-opts-append=mu-core-autostart=no"])
    try:
        h.command("loadfile", str(tmp_path / "no-existe.mkv"), "replace")
        core = h.wait_property("user-data/mu/core", lambda v: bool(v) and bool(v.get("last_load_error")), timeout=15)
        err = core["last_load_error"]
        assert err["path"].endswith("no-existe.mkv")
        assert err["reason"] == "el archivo no existe"
        assert h.proc.poll() is None  # still running (bin/mpv-uos always adds --idle=yes)
        assert h.get("idle-active") is True
        assert h.script_errors() == []
    finally:
        h.stop()


def test_launcher_without_idle_keeps_the_player_open_after_a_failed_file(tmp_path: Path):
    """The real-world case: `mpv-uos broken.mkv` (no --idle) used to exit silently."""
    import os
    import subprocess

    from tests.conftest import ROOT

    sock = tmp_path / f"mpv-{uuid.uuid4().hex[:6]}.sock"
    proc = subprocess.Popen(
        [str(ROOT / "bin" / "mpv-uos"), "--vo=null", "--ao=null", "--no-terminal", f"--input-ipc-server={sock}",
         "--script-opts-append=mu-core-autostart=no,mu-menu-start_screen=no", str(tmp_path / "roto.mkv")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "MPV_UOS_RUNTIME_DIR": str(tmp_path)})
    try:
        async def idle() -> bool:
            for _ in range(100):
                if sock.exists():
                    break
                await asyncio.sleep(0.1)
            async with MpvIpcClient(str(sock)) as c:
                return bool(await c.wait_property("idle-active", lambda v: v is True, timeout=15))

        assert asyncio.run(idle())
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _player_on(path: Path, run_dir: Path):
    """A headless player whose IPC socket is ``path`` (start_mpv's own socket is then unused)."""
    h = start_mpv(run_dir, [f"--input-ipc-server={path}", "--script-opts-append=mu-core-autostart=no"],
                  wait_socket=path)
    return h


def test_two_players_on_one_socket_path_get_their_own_sessions(tmp_path: Path):
    shared = tmp_path / "shared.sock"
    a = _player_on(shared, tmp_path / "a")
    b = None
    try:
        async def scenario():
            nonlocal b
            server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                         data_dir=tmp_path / "data", idle_timeout=0, workers=1))
            await server.start()
            try:
                s1 = await server.sessions.register(str(shared), a.proc.pid)
                # a second player takes over the same socket path (e.g. an inherited MPV_UOS_SOCKET)
                b = await asyncio.to_thread(_player_on, shared, tmp_path / "b")
                # the second player cannot bind a live socket: the path still answers for A, so B must NOT get
                # A's session (before: B silently shared it and its own calls timed out)
                with pytest.raises(ValueError, match="belongs to mpv pid"):
                    await server.sessions.register(str(shared), b.proc.pid)
                again = await server.sessions.register(str(shared), a.proc.pid)
                return s1.id, s1.pid, again.id, len(server.sessions)
            finally:
                await server.stop()

        s1_id, s1_pid, again_id, n = asyncio.run(scenario())
        assert s1_pid == a.proc.pid
        assert again_id == s1_id and n == 1
    finally:
        a.stop()
        if b is not None:
            b.stop()


def test_timed_out_command_leaves_no_pending_future(mpv_headless):
    async def go(c: MpvIpcClient):
        # `wait_event`-free way to make mpv not answer in time: a tiny timeout on a normal command
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await c.command("get_property", "mpv-version", timeout=0.000001)
        await asyncio.sleep(0.2)
        return len(c._pending)

    assert mpv_headless.run(go) == 0
