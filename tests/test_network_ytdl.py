"""@network: the vendored yt-dlp against real sites — ``-J`` analysis, the daily update check against GitHub and two real
downloads (video 360p and mp3 128k) of a short public-domain film verified with ffprobe."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.server import MpvdServer

pytestmark = pytest.mark.network

URL = "https://archive.org/details/Countdow1960"  # 14 s, public domain (Prelinger); docs/YTDLP.md §6


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


async def wait_done(c, item_id, timeout=240.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = await c.call("ytdl.downloads.get", {"id": item_id})
        if d["status"] in ("done", "failed", "cancelled"):
            return d
        await asyncio.sleep(0.5)
    raise TimeoutError(item_id)


def test_real_info_update_check_and_downloads(tmp_path, monkeypatch):
    monkeypatch.delenv("MPV_UOS_YTDLP", raising=False)
    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=3)

    async def go():
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                st = await c.call("ytdl.status")
                assert st["available"] and st["binary"]["source"] in ("vendor", "system"), st
                assert st["version"] and st["version"][:4].isdigit()

                upd = await c.call("ytdl.update.check", {"force": True})
                assert not upd["error"], upd
                assert upd["latest"][:4].isdigit() and upd["checked_at"] > 0

                info = await c.call("ytdl.info", {"url": URL})
                assert info["extractor"] == "archive.org" and info["counts"]["combined"] >= 2
                assert any(r["ext"] == "mp4" for r in info["formats"]["combined"])

                v = await c.call("ytdl.download", {"url": URL, "preset": "video_360", "title": "Countdown"})
                a = await c.call("ytdl.download", {"url": URL, "preset": "audio_mp3_128"})
                m = await c.call("ytdl.download", {"url": URL, "kind": "exact", "format": "3", "container": "mkv"})
                v, a, m = await wait_done(c, v["id"]), await wait_done(c, a["id"]), await wait_done(c, m["id"])
                for item in (v, a, m):
                    assert item["status"] == "done", await c.call("ytdl.downloads.get", {"id": item["id"]})
                return v, a, m
        finally:
            await server.stop()

    v, a, m = asyncio.run(go())
    # 360p preset: the best ≤360p file on archive.org is a Theora .ogv, which must NOT be remuxed to mp4 (it would fail)
    vf = Path(v["outputs"][0])
    assert vf.is_file() and vf.parent == tmp_path / "dl" and vf.suffix in (".ogv", ".mp4", ".mkv")
    pv = ffprobe(vf)
    video = next(s for s in pv["streams"] if s["codec_type"] == "video")
    assert int(video["height"]) <= 360, video
    assert any(s["codec_type"] == "audio" for s in pv["streams"])
    # exact mp4 format remuxed into the chosen mkv container (safe: mkv accepts anything)
    mf = Path(m["outputs"][0])
    assert mf.suffix == ".mkv"
    pm = ffprobe(mf)
    assert pm["format"]["format_name"].startswith("matroska")
    assert next(s for s in pm["streams"] if s["codec_type"] == "video")["codec_name"] == "h264"

    af = Path(a["outputs"][0])
    assert af.is_file() and af.suffix == ".mp3"
    pa = ffprobe(af)
    audio = next(s for s in pa["streams"] if s["codec_type"] == "audio")
    assert audio["codec_name"] == "mp3"
    bitrate = int(pa["format"].get("bit_rate") or audio.get("bit_rate") or 0)
    assert 110_000 <= bitrate <= 150_000, bitrate  # 128k CBR (container overhead / short file tolerance)
    assert not any(s["codec_type"] == "video" for s in pa["streams"] if s.get("disposition", {}).get("attached_pic") != 1)
