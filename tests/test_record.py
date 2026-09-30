"""H18 · «Grabar» backend: audio ranges copied without re-encoding (container follows the codec), readable names after
the title, and keeping only the audio of a live recording (record.audio)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mpvd.record import extract_audio
from mpvd.study.clips import audio_codec, audio_copy_ext, export_clip, ffmpeg_args, output_path


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_audio_copy_containers_and_args(tmp_path, media_dir):
    assert [audio_copy_ext(c) for c in ("aac", "opus", "mp3", "vorbis", "flac", "dts", None)] == [
        ".m4a", ".opus", ".mp3", ".ogg", ".flac", ".mka", ".mka"]
    src = media_dir / "video30.mkv"
    assert audio_codec(src) == "aac"
    args = ffmpeg_args(src, 5.0, 11.0, "audio-copy", tmp_path / "x.m4a")
    assert args[args.index("-c:a") + 1] == "copy" and "-vn" in args
    out = output_path(src, 5, 11, "audio-copy", tmp_path, ext=".m4a", stem="La 1 Telediario")
    assert out.name == "La 1 Telediario [00.00.05-00.00.11].m4a"


def test_audio_copy_clip_is_not_reencoded(tmp_path, media_dir):
    src = media_dir / "video30.mkv"
    out = tmp_path / "a.m4a"
    res = asyncio.run(export_clip(src, 5.0, 11.0, "audio-copy", out))
    info = probe(Path(res["file"]))
    assert [s["codec_type"] for s in info["streams"]] == ["audio"] and info["streams"][0]["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.3


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe missing")
def test_extract_audio_of_a_recording(tmp_path, media_dir):
    rec = tmp_path / "Canal 24h 2026-09-30 10.00.00.mkv"
    shutil.copyfile(media_dir / "video30.mkv", rec)
    out = asyncio.run(extract_audio(rec, remove=True))
    assert out == rec.with_suffix(".m4a") and not rec.exists()
    info = probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["audio"] and abs(float(info["format"]["duration"]) - 30) < 0.5
