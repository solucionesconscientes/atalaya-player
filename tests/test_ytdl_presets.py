"""Download specs → exact yt-dlp argv (every option verified against yt-dlp --help, docs/YTDLP.md §5/§9)."""

import subprocess
import sys
from pathlib import Path

import pytest

from mpvd.ytdl.presets import PRESETS, DownloadSpec, build_args, preset, spec_from_preset

URL = "https://archive.org/details/Countdow1960"
ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "ytdlp"
VENDORED = ROOT / "vendor" / "bin" / "yt-dlp"


def args_of(**kw):
    return build_args(DownloadSpec(url=URL, **kw), "/tmp/out", "%(id)s.%(ext)s")


def test_video_best_argv_exact():
    a = args_of(kind="video")
    assert a[:6] == ["--no-overwrites", "--continue", "--ignore-errors", "--retries", "5", "--socket-timeout"]
    assert a[a.index("-f") + 1] == "bv*+ba/b"
    assert a[a.index("--merge-output-format") + 1] == "mp4/mkv"  # mkv fallback when codecs do not fit mp4
    assert a[a.index("--remux-video") + 1] == "mov>mp4/m4v>mp4/flv>mp4/3gp>mp4"  # only safe remuxes
    assert "--no-playlist" in a and "--yes-playlist" not in a
    assert a[a.index("-P") + 1] == "/tmp/out" and a[a.index("-o") + 1] == "%(id)s.%(ext)s"
    assert "--newline" in a and "--no-simulate" in a
    i = a.index("--progress-template")
    assert a[i + 1] == "download:MU_PROGRESS %(progress)j"
    assert a[i + 3] == "postprocess:MU_PP %(progress)j"
    assert a[a.index("--print") + 1].startswith("after_move:MU_DONE %(.{id,title,ext,filepath,format_id,duration})j")
    assert a[-2:] == ["--", URL]
    assert "-x" not in a


def test_video_height_cap_and_container():
    a = args_of(kind="video", height=360, container="mkv")
    assert a[a.index("-f") + 1] == "bv*[height<=?360]+ba/b[height<=?360]/bv*+ba/b"
    assert a[a.index("--merge-output-format") + 1] == "mkv"


def test_exact_format():
    a = args_of(kind="exact", format="160+139", container="webm")
    assert a[a.index("-f") + 1] == "160+139"
    assert a[a.index("--merge-output-format") + 1] == "webm/mkv" and "--remux-video" not in a


def test_audio_original_keeps_codec():
    a = args_of(kind="audio_original")
    assert a[a.index("-f") + 1] == "ba/b" and "-x" in a
    assert "--audio-format" not in a and "--audio-quality" not in a
    assert "--merge-output-format" not in a


@pytest.mark.parametrize("fmt,bitrate,vbr,expected", [
    ("mp3", 128, 0, ["--audio-format", "mp3", "--audio-quality", "128K"]),
    ("opus", 96, 0, ["--audio-format", "opus", "--audio-quality", "96K"]),
    ("m4a", 320, 0, ["--audio-format", "m4a", "--audio-quality", "320K"]),
    ("mp3", None, 0, ["--audio-format", "mp3", "--audio-quality", "0"]),
    ("mp3", None, 4, ["--audio-format", "mp3", "--audio-quality", "4"]),
    ("flac", None, 0, ["--audio-format", "flac"]),
    ("wav", 192, 0, ["--audio-format", "wav"]),
])
def test_audio_convert(fmt, bitrate, vbr, expected):
    a = args_of(kind="audio_convert", audio_format=fmt, audio_bitrate=bitrate, audio_vbr=vbr)
    i = a.index("--audio-format")
    assert a[i:i + len(expected)] == expected
    if fmt in ("flac", "wav"):
        assert "--audio-quality" not in a
    assert "-x" in a


def test_extras_and_playlist():
    a = args_of(kind="video", subtitles=True, sub_langs="es,en", chapters=True, thumbnail=True, metadata=True,
                sponsorblock="mark", playlist=True, playlist_items="1:3")
    for flag in ("--write-subs", "--write-auto-subs", "--embed-subs", "--embed-chapters", "--embed-thumbnail",
                 "--embed-metadata", "--yes-playlist"):
        assert flag in a
    assert a[a.index("--sub-langs") + 1] == "es,en"
    assert a[a.index("--sponsorblock-mark") + 1] == "all"
    assert a[a.index("--playlist-items") + 1] == "1:3"
    b = args_of(kind="video", sponsorblock="remove")
    assert b[b.index("--sponsorblock-remove") + 1] == "default" and "--sponsorblock-mark" not in b
    c = args_of(kind="audio_convert", subtitles=True)
    assert "--embed-subs" not in c  # subtitles make no sense in audio files


@pytest.mark.parametrize("bad", [
    {"kind": "nope"}, {"kind": "exact"}, {"container": "avi"}, {"kind": "audio_convert", "audio_format": "ogg"},
    {"kind": "audio_convert", "audio_bitrate": 9999}, {"audio_vbr": 11}, {"sponsorblock": "maybe"}, {"height": 0},
])
def test_validation(bad):
    with pytest.raises(ValueError):
        DownloadSpec(url=URL, **bad).validate()
    with pytest.raises(ValueError):
        DownloadSpec.from_dict({"url": "", "kind": "video"})


def test_presets_build_and_describe():
    ids = [p["id"] for p in PRESETS]
    assert len(ids) == len(set(ids)) and "video_360" in ids and "audio_mp3_128" in ids
    for p in PRESETS:
        spec = spec_from_preset(p["id"], URL, {"container": "mkv", "subtitles": True})
        argv = build_args(spec, "/tmp/out")
        assert argv[-1] == URL and spec.describe()
        assert spec.extra["preset"] == p["id"]
        if p["group"] == "video":
            assert argv[argv.index("--merge-output-format") + 1] == "mkv"  # option applied
            assert argv[argv.index("--remux-video") + 1] == "mkv"
    s = spec_from_preset("audio_mp3_128", URL, {"audio_format": "flac", "audio_bitrate": 320})
    assert s.audio_format == "mp3" and s.audio_bitrate == 128  # preset fields win over options
    with pytest.raises(KeyError):
        preset("nope")
    assert spec_from_preset("video_360", URL).describe() == "vídeo 360p (mp4)"
    assert spec_from_preset("audio_mp3_vbr", URL).describe() == "audio mp3 VBR q0"
    assert spec_from_preset("audio_flac", URL).describe() == "audio flac sin pérdida"


def test_video_presets_sort_codecs_for_the_container():
    """Bug: «Vídeo · 360p» in mp4 produced an .mkv with AV1+Opus (yt-dlp's default order ranks them first)."""
    mp4 = args_of(kind="video", height=360)
    assert mp4[mp4.index("-S") + 1] == "vcodec:h264,res,acodec:aac"
    assert mp4.index("-f") < mp4.index("-S") < mp4.index("--merge-output-format") and mp4[-2:] == ["--", URL]
    webm = args_of(kind="video", container="webm")
    assert webm[webm.index("-S") + 1] == "vcodec:vp9,res,acodec:opus"
    assert "-S" not in args_of(kind="video", container="mkv")  # mkv takes any codec
    assert "-S" not in args_of(kind="exact", format="137+140")  # an explicit format is the user's choice
    assert "-S" not in args_of(kind="audio_convert") and "-S" not in args_of(kind="audio_original")
    for p in PRESETS:
        argv = build_args(spec_from_preset(p["id"], URL, {"container": "mp4"}), "/tmp/out")
        assert ("-S" in argv) == (p["group"] == "video"), p["id"]


@pytest.mark.skipif(not VENDORED.is_file(), reason="vendored yt-dlp missing (tools/vendor.sh)")
@pytest.mark.parametrize("container,height,expected", [
    ("mp4", 360, ("avc1", "mp4a", "mp4", 360)),
    ("mp4", None, ("avc1", "mp4a", "mp4", 1080)),   # H.264 tops out at 1080p on YouTube
    ("webm", 360, ("vp9", "opus", "webm", 360)),
    ("mkv", 360, ("av01", "opus", "mkv", 360)),     # unchanged: best codecs, any container
])
def test_real_ytdlp_selection_on_recorded_youtube_info(container, height, expected):
    """The vendored yt-dlp picks formats from the recorded -J (offline: --load-info-json + simulate)."""
    a = args_of(kind="video", height=height, container=container)
    cmd = [sys.executable, str(VENDORED), "--no-update", "--no-remote-components", "--no-warnings",
           "--load-info-json", str(FIX / "youtube_bbb.json"), "--simulate",
           "--print", "%(vcodec)s|%(acodec)s|%(ext)s|%(height)s",
           "-f", a[a.index("-f") + 1], "--merge-output-format", a[a.index("--merge-output-format") + 1]]
    if "-S" in a:
        cmd += ["-S", a[a.index("-S") + 1]]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True).stdout.strip().splitlines()
    vcodec, acodec, ext, h = out[-1].split("|")
    assert (vcodec.split(".")[0], acodec.split(".")[0], ext, int(h)) == expected, out
