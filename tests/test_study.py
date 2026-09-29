"""Study tools: clip/GIF export (argv per format, real ffmpeg on the test video, job + history through mpvd), silence map
for smart speed, and mu-study in headless mpv (repeat line over a subtitle cue, smart speed, note, clip from A-B)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from mpvd.study.clips import FORMATS, ClipError, ffmpeg_args, hms_name, output_path
from mpvd.study.silence import summary


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_ffmpeg_args_and_names(tmp_path):
    src = Path("/x/Serie S01E02.mkv")
    out = output_path(src, 65.0, 70.5, "gif", tmp_path)
    assert out.name == "Serie S01E02 [00.01.05-00.01.10].gif" and hms_name(3661) == "01.01.01"
    out.touch()
    assert output_path(src, 65.0, 70.5, "gif", tmp_path).name.endswith("(2).gif")
    args = ffmpeg_args(src, 5, 8, "mp4-copy", out)
    assert args[args.index("-ss") + 1] == "5.000" and args[args.index("-t") + 1] == "3.000" and "copy" in args
    assert args.index("-ss") < args.index("-i")  # fast seek before the input
    gif = ffmpeg_args(src, 5, 8, "gif", out)
    fc = gif[gif.index("-filter_complex") + 1]
    assert "palettegen" in fc and "paletteuse" in fc and "fps=12" in fc and "-an" in gif
    assert "libmp3lame" in ffmpeg_args(src, 5, 8, "mp3", out) and "libopus" in ffmpeg_args(src, 5, 8, "opus", out)
    assert "0:a:1" in ffmpeg_args(src, 5, 8, "mp3", out, audio_track=1)
    with pytest.raises(ClipError):
        ffmpeg_args(src, 5, 8, "avi", out)
    assert summary([[1.0, 2.0], [5.0, 6.5]], 10.0) == {"spans": 2, "quiet_seconds": 2.5, "quiet_ratio": 0.25}


@pytest.mark.parametrize("fmt", ["mp4", "gif", "mp3", "opus", "mp4-copy"])
def test_export_formats_real_ffmpeg(media_dir, tmp_path, fmt):
    import asyncio

    from mpvd.study.clips import export_clip

    out = tmp_path / f"clip{FORMATS[fmt]['ext']}"
    seen = []
    res = asyncio.run(export_clip(media_dir / "video30.mkv", 5.0, 8.0, fmt, out, progress=lambda f, t: seen.append(f)))
    assert out.is_file() and res["bytes"] > 1000 and seen and seen[-1] == 1.0
    info = _probe(out)
    codecs = {s["codec_name"] for s in info["streams"]}
    dur = float(info["format"].get("duration") or 0)
    if fmt == "mp4":
        assert codecs == {"h264", "aac"} and abs(dur - 3.0) < 0.2
    elif fmt == "gif":
        assert codecs == {"gif"} and abs(dur - 3.0) < 0.3
    elif fmt == "mp3":
        assert codecs == {"mp3"} and abs(dur - 3.0) < 0.2
    elif fmt == "opus":
        assert codecs == {"opus"} and abs(dur - 3.0) < 0.2
    else:  # stream copy cuts on keyframes: never shorter than asked, may start earlier
        assert codecs == {"h264", "aac"} and 2.9 <= dur <= 12.0


def test_study_methods_via_daemon(daemon_env, media_dir, tmp_path):
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    assert d.call("capabilities")["services"]["study"] is True
    names = {f["name"] for f in d.call("study.formats")}
    assert {"mp4", "gif", "mp3"} <= names
    video = str(media_dir / "video30.mkv")
    item = d.call("study.clip", {"path": video, "start": 5, "end": 8, "format": "mp3", "dir": str(tmp_path), "title": "prueba"})
    assert item["status"] in ("queued", "running") and item["file"].endswith("video30 [00.00.05-00.00.08].mp3")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        rows = d.call("study.clips.list")
        me = next(r for r in rows if r["id"] == item["id"])
        if me["status"] in ("done", "failed"):
            break
        time.sleep(0.3)
    assert me["status"] == "done" and Path(me["file"]).is_file() and me["progress"] == 1.0, me
    assert json.loads((d.data_dir / "clips.json").read_text())[0]["id"] == item["id"]
    with pytest.raises(Exception, match="tramo"):
        d.call("study.clip", {"path": video, "start": 8, "end": 5})
    with pytest.raises(Exception, match="formato"):
        d.call("study.clip", {"path": video, "start": 1, "end": 2, "format": "avi"})
    # silence map on the Spanish voice fixture: several gaps between sentences, cached on the second call
    voice = str(media_dir / "voz_es.flac")
    s1 = d.call("study.silences", {"path": voice, "length": 120, "noise_db": -35, "min_seconds": 0.3}, timeout=60)
    assert s1["cached"] is False and s1["spans"] >= 1 and all(b > a for a, b in s1["silences"]), s1
    s2 = d.call("study.silences", {"path": voice, "length": 120, "noise_db": -35, "min_seconds": 0.3})
    assert s2["cached"] is True and s2["silences"] == s1["silences"]


def _srt(path: Path) -> None:
    path.write_text("1\n00:00:02,000 --> 00:00:04,000\nPrimera línea de prueba.\n\n"
                    "2\n00:00:06,000 --> 00:00:08,000\nSegunda línea de prueba.\n\n"
                    "3\n00:00:12,000 --> 00:00:14,000\nTercera línea.\n", encoding="utf-8")


def test_mu_study_repeat_note_clip_and_smart_speed(daemon_env, media_dir, tmp_path):
    from tests.conftest import start_mpv

    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    srt = tmp_path / "video30.srt"
    _srt(srt)
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,"
                                  f"mu-study-clip_dir={tmp_path},mu-study-silence_db=-30,mu-study-silence_min=0.3,"
                                  "mu-study-poll_seconds=0.1", "--pause=yes", "--keep-open=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 20, timeout=20)
        h.command("sub-add", str(srt), "select")
        h.command("seek", 3.0, "absolute+exact")
        h.wait_property("sub-start", lambda v: isinstance(v, (int, float)) and abs(v - 2.0) < 0.01, timeout=10)
        # repeat line → ab-loop over the cue; sub-delay is added; toggling off clears the loop
        h.command("set", "sub-delay", "0.5")
        h.command("script-binding", "mu_study/repeat-line")
        st = h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("repeat_line") is True, timeout=10)
        assert abs(st["line"]["start"] - 2.5) < 0.01 and abs(st["line"]["end"] - 4.5) < 0.01
        assert abs(h.get("ab-loop-a") - 2.5) < 0.01 and abs(h.get("ab-loop-b") - 4.5) < 0.01
        h.command("set", "sub-delay", "0")
        h.command("script-binding", "mu_study/repeat-line")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("repeat_line") is False, timeout=10)
        assert h.get("ab-loop-a") == "no"
        # clearing the loop with `ab-loop` while repeating also stops the repeat (paused mpv only refreshes sub-start
        # after a redraw: seek again so the cue is current)
        h.command("seek", 3.0, "absolute+exact")
        h.wait_property("sub-start", lambda v: isinstance(v, (int, float)) and abs(v - 2.0) < 0.01, timeout=10)
        h.command("script-binding", "mu_study/repeat-line")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("repeat_line") is True, timeout=10)
        h.command("set", "ab-loop-a", "no")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("repeat_line") is False, timeout=10)
        # note with the subtitle quote → Markdown file with a time link (cue must be on screen: seek inside it)
        h.command("seek", 3.0, "absolute+exact")
        h.wait_property("sub-start", lambda v: isinstance(v, (int, float)) and abs(v - 2.0) < 0.01, timeout=10)
        h.command("script-message-to", "mu_study", "mu-study-note", "Ojo a esta frase")
        st = h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("notes", 0) == 1, timeout=15)
        md = Path(st["last_note"]["file"]).read_text(encoding="utf-8")
        assert "Ojo a esta frase" in md and "Primera línea de prueba" in md and "mpv://seek?t=3." in md
        assert d.call("notes.list")[0]["notes"] == 1
        # clip of the A-B loop (5–8 s) as mp3 into the test folder; done event reaches the script
        h.command("set", "ab-loop-a", "5")
        h.command("set", "ab-loop-b", "8")
        h.command("script-message-to", "mu_study", "mu-study-clip", "mp3")
        st = h.wait_property("user-data/mu/study", lambda v: bool(v) and isinstance(v.get("last_clip"), dict)
                             and v["last_clip"].get("status") == "done",
                             timeout=60)
        clip = Path(st["last_clip"]["file"])
        assert clip.parent == tmp_path and clip.suffix == ".mp3" and clip.is_file()
        assert abs(float(_probe(clip)["format"]["duration"]) - 3.0) < 0.2
        h.command("set", "ab-loop-a", "no")
        h.command("set", "ab-loop-b", "no")
        # smart speed on the Spanish voice: speeds up inside a pause, comes back to the base speed on speech
        h.command("loadfile", str(media_dir / "voz_es.flac"))
        h.wait_property("path", lambda v: bool(v) and v.endswith("voz_es.flac"), timeout=20)
        h.command("set", "speed", "1.25")
        h.command("script-message-to", "mu_study", "mu-study-smart", "yes")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("smart") and v.get("silences", 0) >= 1, timeout=60)
        h.command("seek", 3.0, "absolute+exact")
        h.command("set", "pause", "no")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("in_silence") is True, timeout=15)
        assert abs(h.get("speed") - 2.5) < 0.01
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("in_silence") is False, timeout=15)
        assert abs(h.get("speed") - 1.25) < 0.01
        h.command("set", "pause", "yes")
        h.command("script-message-to", "mu_study", "mu-study-smart", "no")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("smart") is False, timeout=10)
        # the menu opens and lists the formats
        h.command("script-binding", "mu_study/study-menu")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-study", timeout=10)
        h.command("script-message-to", "uosc", "close-menu", "mu-study")
        assert h.script_errors() == []
    finally:
        h.stop()
