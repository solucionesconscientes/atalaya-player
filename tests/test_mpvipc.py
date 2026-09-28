"""Unit tests for the mpv JSON IPC client against a real headless mpv."""

import pytest

from mpvd.mpvipc import MpvIpcError


def test_command_roundtrip_and_errors(mpv_headless):
    async def go(c):
        version = await c.get_property("mpv-version")
        await c.set_property("volume", 42)
        vol = await c.get_property("volume")
        with pytest.raises(MpvIpcError) as exc:
            await c.get_property("this-property-does-not-exist")
        assert "property not found" in str(exc.value)
        with pytest.raises(MpvIpcError):
            await c.command("no-such-command-xyz")
        return version, vol

    version, vol = mpv_headless.run(go)
    assert version.startswith("mpv v0.")
    assert vol == 42


def test_events_are_delivered(mpv_headless, media_dir):
    async def go(c):
        await c.command("loadfile", str(media_dir / "video30.mkv"))
        ev = await c.wait_event("file-loaded", 20)
        await c.command("stop")
        end = await c.wait_event("end-file", 20)
        return ev, end

    ev, end = mpv_headless.run(go)
    assert ev["event"] == "file-loaded"
    assert end["reason"] == "stop"
