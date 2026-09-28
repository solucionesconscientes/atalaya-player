"""Integration: headless mpv (bin/mpv-uos) + mu-core + a real mpvd daemon spawned by mu-core."""

import json
import time

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


@pytest.fixture
def mpv_with_daemon(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS], env=daemon_env.env)
    try:
        yield h, daemon_env
    finally:
        h.stop()


def wait_core(h, predicate, timeout=40.0):
    return h.wait_property("user-data/mu/core", lambda v: bool(v) and predicate(v), timeout=timeout)


def mu_call(h, method, params=None, timeout=20.0):
    args = ["script-message-to", "mu_core", "mu-call", method]
    if params is not None:
        args.append(json.dumps(params))
    h.command(*args)
    reply = h.wait_property("user-data/mu/last_reply",
                            lambda v: bool(v) and v.get("method") == method and not v.get("pending"), timeout=timeout)
    return reply


def test_mu_core_starts_daemon_registers_and_round_trips(mpv_with_daemon, media_dir):
    h, d = mpv_with_daemon
    core = wait_core(h, lambda v: v.get("mpvd") == "connected")
    assert core["session"] and core["mpvd_socket"] == str(d.socket)
    assert d.alive()

    sessions = d.call("sessions.list")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == core["session"] and s["ipc"] == str(h.socket) and s["pid"] == h.proc.pid and s["connected"]

    # RPC from inside mpv (script-message mu-rpc -> mpvd -> script-message-to mu_core mu-reply)
    reply = mu_call(h, "ping")
    assert reply["ok"] is True and reply["result"]["pong"] is True
    reply = mu_call(h, "file.hash", {"path": str(media_dir / "video30.mkv")})
    assert reply["ok"] is True and reply["result"]["key"].startswith("mu:")
    reply = mu_call(h, "sessions.current")
    assert reply["result"]["id"] == core["session"]
    reply = mu_call(h, "no.such.method")
    assert reply["ok"] is False and reply["error"]["code"] == -32601

    # The daemon observes properties of the session through mpv's IPC.
    h.command("loadfile", str(media_dir / "video30.mkv"))
    d.wait(lambda: (d.call("sessions.list")[0].get("path") or "").endswith("video30.mkv"), timeout=15)

    # Jobs submitted from a session are tagged with it and cancelled when it goes away.
    reply = mu_call(h, "jobs.sleep", {"seconds": 60, "name": "from-mpv"})
    job_id = reply["result"]["id"]
    assert d.call("jobs.get", {"id": job_id})["session_id"] == core["session"]
    assert h.script_errors() == []

    h.stop()
    d.wait(lambda: d.call("sessions.list") == [], timeout=10)
    d.wait(lambda: d.call("jobs.get", {"id": job_id})["status"] == "cancelled", timeout=10)


def test_watchdog_reconnects_after_daemon_dies(mpv_with_daemon):
    h, d = mpv_with_daemon
    first = wait_core(h, lambda v: v.get("mpvd") == "connected")
    d.call("shutdown")
    d.wait(lambda: not d.alive(), timeout=10)
    lost = wait_core(h, lambda v: v.get("mpvd") != "connected", timeout=20)
    assert lost["mpvd"] in ("disconnected", "starting", "error")
    # "connected" arrives either from the daemon's hello or from the ensure subprocess result (which
    # also resets the attempt counter); wait for both to settle.
    again = wait_core(h, lambda v: v.get("mpvd") == "connected" and v.get("session") != first["session"]
                      and v.get("attempts") == 0, timeout=40)
    assert d.alive() and again["error"] == ""
    assert d.call("sessions.list")[0]["ipc"] == str(h.socket)
    assert h.script_errors() == []


def test_ensure_cli_attaches_an_existing_mpv(daemon_env, media_dir):
    d = daemon_env
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-autostart=no"], env=d.env)
    try:
        core = wait_core(h, lambda v: v.get("version"))
        assert core["mpvd"] == "disconnected"
        t0 = time.monotonic()
        out = d.cli("ensure", "--attach", str(h.socket), "--pid", str(h.proc.pid))
        assert out.returncode == 0, out.stderr
        info = json.loads(out.stdout.strip().splitlines()[-1])
        assert info["ok"] and info["started"] is True and info["session"]["ipc"] == str(h.socket)
        # Second call is idempotent: nothing started, same session.
        out2 = d.cli("ensure", "--attach", str(h.socket))
        info2 = json.loads(out2.stdout.strip().splitlines()[-1])
        assert info2["started"] is False and info2["session"]["id"] == info["session"]["id"]
        # mu-core learned about the daemon from its hello message.
        core = wait_core(h, lambda v: v.get("mpvd") == "connected", timeout=10)
        assert core["session"] == info["session"]["id"]
        assert time.monotonic() - t0 < 30
        status = d.cli("status")
        assert status.returncode == 0 and json.loads(status.stdout)["running"] is True
        assert d.cli("stop").returncode == 0
        d.wait(lambda: not d.alive(), timeout=10)
    finally:
        h.stop()
