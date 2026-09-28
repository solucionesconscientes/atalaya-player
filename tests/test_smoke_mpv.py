"""Smoke test: headless mpv loads uosc, thumbfast and mu-core without Lua errors and plays the test media."""

import re
from pathlib import Path


def test_scripts_load_without_errors(mpv_headless):
    core = mpv_headless.wait_property("user-data/mu/core", lambda v: bool(v and v.get("version")))
    assert core["version"] == "0.1.0"
    assert core["ipc"] == str(mpv_headless.socket)
    assert core["platform"] == "linux"
    log = mpv_headless.log_text()
    for script in ("mu-core.lua", "thumbfast.lua", "uosc/main.lua"):
        assert re.search(rf"Loading lua script .*{re.escape(script)}", log), f"{script} not loaded"
    assert mpv_headless.script_errors() == []


def test_uosc_is_detected_and_registers_bindings(mpv_headless):
    core = mpv_headless.wait_property("user-data/mu/core", lambda v: bool(v and v.get("uosc")))
    assert core["uosc_version"] == "5.13.0"
    bindings = mpv_headless.get("input-bindings")
    uosc_cmds = {b["cmd"] for b in bindings if "uosc" in b.get("cmd", "")}
    assert "script-binding uosc/menu" in uosc_cmds
    assert len(uosc_cmds) >= 10


def test_plays_test_media_headless(mpv_headless, media_dir: Path, media_manifest):
    async def play(c):
        await c.command("loadfile", str(media_dir / "chapters.mkv"))
        await c.wait_event("file-loaded", 20)
        duration = await c.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=10)
        chapters = await c.get_property("chapter-list")
        await c.command("loadfile", str(media_dir / "voz_es_en.mkv"))
        await c.wait_event("file-loaded", 20)
        tracks = await c.get_property("track-list")
        return duration, chapters, tracks

    duration, chapters, tracks = mpv_headless.run(play)
    assert abs(duration - media_manifest["chapters.mkv"]["duration"]) < 0.5
    assert [ch["title"] for ch in chapters] == media_manifest["chapters.mkv"]["chapters"]
    langs = [t.get("lang") for t in tracks if t["type"] == "audio"]
    assert langs == media_manifest["voz_es_en.mkv"]["audio_langs"]
    assert mpv_headless.script_errors() == []
