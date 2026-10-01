"""H24: MPRIS on a private session bus (never the user's): name, metadata, PlayPause/Seek/Volume/LoopStatus from
D-Bus reach mpv, and mpv's changes come back as PropertiesChanged/Seeked."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mpvd import mpris
from tests.conftest import start_mpv

pytest.importorskip("jeepney")
if shutil.which("dbus-daemon") is None:
    pytest.skip("dbus-daemon not installed", allow_module_level=True)

from jeepney import DBusAddress, MatchRule, Properties, message_bus, new_method_call  # noqa: E402
from jeepney.io.blocking import open_dbus_connection  # noqa: E402

PLAYER = mpris.PLAYER_IFACE
ROOT = mpris.ROOT_IFACE


def test_metadata_and_status_helpers():
    props = {"path": "/tmp/a b.mkv", "media-title": "Título", "duration": 12.5, "playlist-pos": 0,
             "playlist-count": 2, "pause": True, "volume": 50, "metadata": {"ARTIST": "Ana"}, "loop-file": "inf"}
    meta = mpris.metadata(props)
    assert meta["xesam:title"] == ("s", "Título") and meta["mpris:length"] == ("x", 12_500_000)
    assert meta["xesam:artist"] == ("as", ["Ana"]) and meta["xesam:url"] == ("s", "file:///tmp/a%20b.mkv")
    assert meta["mpris:trackid"] == ("o", "/org/mpris/MediaPlayer2/mpv_uos/track/0")
    p = mpris.player_props(props)
    assert p["PlaybackStatus"] == ("s", "Paused") and p["Volume"] == ("d", 0.5) and p["LoopStatus"] == ("s", "Track")
    assert p["CanGoNext"] == ("b", True) and p["CanGoPrevious"] == ("b", False)
    assert mpris.player_props({"idle-active": True})["PlaybackStatus"] == ("s", "Stopped")
    assert mpris.metadata({"idle-active": True}) == {"mpris:trackid": ("o", mpris.NO_TRACK)}


@pytest.fixture
def private_bus(tmp_path):
    sock = Path(os.environ.get("MU_TEST_TMP", "/tmp")) / f"mu-dbus-{os.getpid()}-{time.monotonic_ns()}"
    proc = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--nopidfile", "--print-address=1",
                             f"--address=unix:path={sock}"], stdout=subprocess.PIPE, text=True)
    try:
        addr = proc.stdout.readline().strip()
        assert addr.startswith("unix:"), addr
        yield addr
    finally:
        proc.terminate()   # our own PID only
        proc.wait(timeout=10)
        sock.unlink(missing_ok=True)


def wait_for(fn, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"{what}: last={last!r}")


def test_mpris_controls_mpv_and_follows_it(daemon_env, media_dir, private_bus):
    daemon_env.extra_env.update({"MPV_UOS_MPRIS": "1", "DBUS_SESSION_BUS_ADDRESS": private_bus})
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1",
                                           "--pause=yes", "--keep-open=yes"], env=daemon_env.env)
    conn = open_dbus_connection(private_bus)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        pid = h.get("pid")
        name = f"org.mpris.MediaPlayer2.mpv_uos.instance{pid}"

        def owned():
            return conn.send_and_get_reply(message_bus.NameHasOwner(name)).body[0]
        wait_for(owned, what="MPRIS bus name")

        obj = DBusAddress(mpris.OBJECT_PATH, bus_name=name)
        props = Properties(obj.with_interface(PLAYER))

        def get(prop, iface=PLAYER):
            reply = conn.send_and_get_reply(Properties(obj.with_interface(iface)).get(prop))
            assert reply.header.message_type.name == "method_return", reply.body
            return reply.body[0][1]

        def call(method, sig=None, body=()):
            reply = conn.send_and_get_reply(new_method_call(obj.with_interface(PLAYER), method, sig, body))
            assert reply.header.message_type.name == "method_return", reply.body
            return reply

        assert get("Identity", ROOT) == "MPV-UOS" and get("DesktopEntry", ROOT) == "mpv-uos"
        assert get("PlaybackStatus") == "Stopped"
        intro = conn.send_and_get_reply(new_method_call(obj.with_interface("org.freedesktop.DBus.Introspectable"),
                                                        "Introspect")).body[0]
        assert "org.mpris.MediaPlayer2.Player" in intro

        # signals: subscribe before the file loads
        rule = MatchRule(type="signal", interface="org.freedesktop.DBus.Properties", member="PropertiesChanged",
                         path=mpris.OBJECT_PATH)
        conn.send_and_get_reply(message_bus.AddMatch(rule))
        seek_rule = MatchRule(type="signal", interface=PLAYER, member="Seeked", path=mpris.OBJECT_PATH)
        conn.send_and_get_reply(message_bus.AddMatch(seek_rule))
        # filters keep matching signals that arrive while send_and_get_reply waits (it drops the rest)
        changes = conn.filter(rule, bufsize=500).__enter__()
        seeks = conn.filter(seek_rule, bufsize=100).__enter__()

        def saw(queue, pred):
            while queue:
                if pred(queue.popleft()):
                    return True
            try:
                # recv_until_filtered POPS the message it waited for and returns it: ignoring the return value threw
                # away precisely the signal we were waiting for (visible with Seeked, of which there is one per seek)
                msg = conn.recv_until_filtered(queue, timeout=0.5)
            except TimeoutError:
                return False
            return pred(msg) or saw(queue, pred)

        video = media_dir / "video30.mkv"
        h.command("loadfile", str(video))
        wait_for(lambda: get("Metadata").get("mpris:length", ("x", 0))[1] > 25_000_000, what="metadata")
        meta = get("Metadata")
        assert meta["xesam:url"][1].startswith("file://") and meta["xesam:url"][1].endswith("video30.mkv")
        assert get("PlaybackStatus") == "Paused" and get("CanSeek") is True

        call("PlayPause")
        h.wait_property("pause", lambda v: v is False, timeout=10)
        wait_for(lambda: get("PlaybackStatus") == "Playing", what="Playing")
        call("Pause")
        h.wait_property("pause", lambda v: v is True, timeout=10)

        # a PropertiesChanged with PlaybackStatus reached us (the pause above)
        wait_for(lambda: saw(changes, lambda m: "PlaybackStatus" in m.body[1]), timeout=10, what="PropertiesChanged")

        call("SetPosition", "ox", (meta["mpris:trackid"][1], 10_000_000))
        h.wait_property("time-pos", lambda v: v is not None and abs(v - 10) < 0.5, timeout=10)
        call("Seek", "x", (5_000_000,))
        h.wait_property("time-pos", lambda v: v is not None and abs(v - 15) < 0.5, timeout=10)
        assert abs(get("Position") - 15_000_000) < 500_000

        wait_for(lambda: saw(seeks, lambda m: abs(m.body[0] - 15_000_000) < 500_000), timeout=10, what="Seeked")

        conn.send_and_get_reply(props.set("Volume", "d", 0.4))
        h.wait_property("volume", lambda v: v is not None and abs(v - 40) < 0.01, timeout=10)
        conn.send_and_get_reply(props.set("LoopStatus", "s", "Track"))
        h.wait_property("loop-file", lambda v: v == "inf" or v is True, timeout=10)
        conn.send_and_get_reply(props.set("Rate", "d", 1.5))
        h.wait_property("speed", lambda v: v == 1.5, timeout=10)
        wait_for(lambda: get("Volume") == 0.4 and get("Rate") == 1.5, what="volume/rate back")

        bad = conn.send_and_get_reply(props.set("CanSeek", "b", False))
        assert bad.header.message_type.name == "error"

        call("Stop")
        wait_for(lambda: get("PlaybackStatus") == "Stopped", what="Stopped")
        assert h.script_errors() == [], h.script_errors()
    finally:
        conn.close()
        h.stop()
    # the name goes away with the player
    conn = open_dbus_connection(private_bus)
    try:
        wait_for(lambda: not conn.send_and_get_reply(message_bus.NameHasOwner(name)).body[0], what="name released")
    finally:
        conn.close()


def test_mpris_off_without_bus_or_when_disabled(monkeypatch):
    monkeypatch.setenv("MPV_UOS_MPRIS", "0")
    assert mpris.available() == (False, "disabled (MPV_UOS_MPRIS=0)")
    monkeypatch.setenv("MPV_UOS_MPRIS", "1")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/nonexistent")
    assert mpris.available() == (False, "no session D-Bus")
