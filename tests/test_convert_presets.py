"""H20 · «Convertir»: every preset and option → the exact ffmpeg argv (no ffmpeg run here), vainfo parsing and the
file helpers (free names, .part files, folder listing)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mpvd.convert import hw
from mpvd.convert.presets import (
    ConvertError,
    ConvertSpec,
    HwPlan,
    SourceInfo,
    build_plan,
    media_files,
    output_path,
    partial_path,
    scale_filter,
)

SRC = Path("/v/peli.mkv")
OUT = Path("/o/peli.part.mp4")
FF = "ffmpeg"
HEAD = [FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-n"]
INFO = SourceInfo(duration=120.0, video_index=0, width=1920, height=1080, audio=[1, 2],
                  subs=[(3, "subrip"), (4, "hdmv_pgs_subtitle"), (5, "ass")])
SCALE_720 = "scale='if(gte(iw,ih),-2,trunc(min(iw,720)/2)*2)':'if(gte(iw,ih),trunc(min(ih,720)/2)*2,-2)'"


def plan(preset: str, out: Path = OUT, info: SourceInfo = INFO, hw_plan: HwPlan | None = None, **opts):
    return build_plan(ConvertSpec.from_dict({"preset": preset, **opts}), SRC, out, info, hw_plan, FF,
                      Path("/tmp/pal.png"))


def test_mp4_compatible_default():
    p = plan("mp4")
    assert p.commands == [[*HEAD, "-i", str(SRC), "-map", "0:0", "-map", "0:a?", "-map", "0:3", "-map", "0:5",
                           "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", "-c:s:0", "mov_text", "-c:s:1", "mov_text",
                           "-movflags", "+faststart", str(OUT)]]
    assert p.hw == "cpu" and p.weights == [1.0] and p.duration == 120.0
    # the picture subtitle (PGS) does not fit in mp4: dropped with a warning that suggests MKV
    assert len(p.warnings) == 1 and "MKV" in p.warnings[0]


def test_mp4_options_resolution_quality_range_no_subs():
    p = plan("mp4", height=720, quality="high", start=10, end=25.5, subtitles=False, speed="fast")
    assert p.commands[0] == [*HEAD, "-ss", "10.000", "-t", "15.500", "-i", str(SRC), "-map", "0:0", "-map", "0:a?",
                             "-vf", SCALE_720, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(OUT)]
    assert p.duration == pytest.approx(15.5) and p.warnings == []
    # only a start: to the end of the file
    p = plan("mp4", start=100, quality="small")
    assert p.commands[0][9:13] == ["-ss", "100.000", "-i", str(SRC)] and "28" in p.commands[0]
    assert p.duration == pytest.approx(20.0)
    # an end past the file = to the end
    assert "-t" not in plan("mp4", start=5, end=500).commands[0]
    # with subtitles the range is also an output limit (the input -t lets later cues through)
    cmd = plan("mp4", start=1, end=3).commands[0]
    assert cmd[-6:] == ["mov_text", "-t", "2.000", "-movflags", "+faststart", str(OUT)]


def test_scale_keeps_orientation_and_even_sizes():
    assert scale_filter(0) is None
    assert scale_filter(720) == SCALE_720


def test_mp4_vaapi_low_power():
    p = plan("mp4", hw_plan=HwPlan("/dev/dri/renderD128", "h264", low_power=True), height=720, subtitles=False)
    assert p.hw == "vaapi"
    assert p.commands[0] == [*HEAD, "-vaapi_device", "/dev/dri/renderD128", "-i", str(SRC), "-map", "0:0",
                             "-map", "0:a?", "-vf", SCALE_720 + ",format=nv12,hwupload", "-c:v", "h264_vaapi",
                             "-low_power", "1", "-rc_mode", "CQP", "-qp", "24", "-c:a", "aac", "-b:a", "160k",
                             "-movflags", "+faststart", str(OUT)]
    # a driver with the normal entry point: no -low_power; no scaling: only upload
    p = plan("mp4", hw_plan=HwPlan("/dev/dri/renderD129", "h264"), subtitles=False, quality="high")
    vf = p.commands[0][p.commands[0].index("-vf") + 1]
    assert vf == "format=nv12,hwupload" and "-low_power" not in p.commands[0] and "20" in p.commands[0]
    # VA-API for H.264 does not apply to H.265 / VP9
    assert plan("small", hw_plan=HwPlan("/dev/dri/renderD128", "h264")).hw == "cpu"


def test_small_h265_mp4_and_mkv():
    p = plan("small", subtitles=False)
    assert p.commands[0] == [*HEAD, "-i", str(SRC), "-map", "0:0", "-map", "0:a?", "-c:v", "libx265", "-preset",
                             "fast", "-crf", "28", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p",
                             "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT)]
    out = Path("/o/peli.part.mkv")
    p = plan("small", out=out, container="mkv", quality="small")
    assert p.commands[0] == [*HEAD, "-i", str(SRC), "-map", "0:0", "-map", "0:a?", "-map", "0:3", "-map", "0:4",
                             "-map", "0:5", "-c:v", "libx265", "-preset", "fast", "-crf", "32", "-x265-params",
                             "log-level=error", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "96k",
                             "-c:s:0", "copy", "-c:s:1", "copy", "-c:s:2", "copy", str(out)]
    assert p.warnings == []   # mkv keeps every subtitle, pictures included
    assert ConvertSpec.from_dict({"preset": "small", "container": "mkv"}).ext == ".mkv"
    hevc = plan("small", hw_plan=HwPlan("/dev/dri/renderD128", "hevc"), subtitles=False)
    assert hevc.hw == "vaapi" and "hevc_vaapi" in hevc.commands[0] and "hvc1" in hevc.commands[0]


def test_web_vp9_opus_webvtt():
    out = Path("/o/peli.part.webm")
    p = plan("web", out=out)
    assert p.commands[0] == [*HEAD, "-i", str(SRC), "-map", "0:0", "-map", "0:a?", "-map", "0:3", "-map", "0:5",
                             "-c:v", "libvpx-vp9", "-crf", "35", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
                             "-cpu-used", "4", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "128k",
                             "-c:s:0", "webvtt", "-c:s:1", "webvtt", str(out)]
    assert "WEBM" in p.warnings[0]
    fast = plan("web", out=out, speed="fast", subtitles=False)
    assert fast.commands[0][fast.commands[0].index("-deadline"):][:4] == ["-deadline", "realtime", "-cpu-used", "8"]


@pytest.mark.parametrize(("preset", "codec"), [
    ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3"]),
    ("m4a", ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
    ("opus", ["-c:a", "libopus", "-b:a", "128k"]),
    ("flac", ["-c:a", "flac"]),
    ("wav", ["-c:a", "pcm_s16le"]),
])
def test_audio_presets(preset, codec):
    out = Path(f"/o/peli.part.{preset}")
    p = plan(preset, out=out)
    assert p.commands == [[*HEAD, "-i", str(SRC), "-map", "0:1", "-vn", *codec, str(out)]]


def test_audio_bitrate_track_and_quality():
    out = Path("/o/a.mp3")
    p = plan("mp3", out=out, audio_bitrate=320, audio_track=1, start=1, end=3)
    assert p.commands[0] == [*HEAD, "-ss", "1.000", "-t", "2.000", "-i", str(SRC), "-map", "0:2", "-vn",
                             "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3", str(out)]
    assert "128k" in plan("mp3", out=out, quality="small").commands[0]
    assert "64k" in plan("opus", out=out, quality="small").commands[0]
    # a track that does not exist falls back to the first one
    assert plan("mp3", out=out, audio_track=7).commands[0][11:13] == ["-map", "0:1"]
    # audio from a file without video works; video presets need video
    audio_only = SourceInfo(duration=10, audio=[0])
    assert plan("flac", out=out, info=audio_only).commands[0][11:13] == ["-map", "0:0"]
    with pytest.raises(ConvertError, match="no tiene vídeo"):
        plan("mp4", info=audio_only)
    with pytest.raises(ConvertError, match="no tiene audio"):
        plan("mp3", out=out, info=SourceInfo(duration=10, video_index=0))


def test_gif_two_passes():
    out = Path("/o/peli.part.gif")
    p = plan("gif", out=out, start=5, end=9, gif_width=320, gif_fps=10)
    chain = "fps=10,scale='min(320,iw)':-1:flags=lanczos"
    inp = ["-ss", "5.000", "-t", "4.000", "-i", str(SRC)]
    assert p.commands == [
        [*HEAD[:-1], "-y", *inp, "-map", "0:0", "-vf", chain + ",palettegen=stats_mode=diff", "-frames:v", "1",
         "-update", "1", "/tmp/pal.png"],
        [*HEAD, *inp, "-i", "/tmp/pal.png", "-lavfi",
         f"[0:0]{chain}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", "-loop", "0", "-an",
         str(out)],
    ]
    assert p.weights == [0.4, 0.6] and p.duration == pytest.approx(4.0)
    with pytest.raises(ConvertError, match="60 s"):
        plan("gif", out=out)   # the whole 120 s file


def test_spec_validation():
    for bad in ({"preset": "avi"}, {"preset": "mp4", "height": 900}, {"preset": "mp4", "quality": "max"},
                {"preset": "mp4", "hw": "cuda"}, {"preset": "mp4", "container": "mkv"},
                {"preset": "mp3", "audio_bitrate": 1000}, {"preset": "gif", "gif_width": 1000},
                {"preset": "mp4", "start": 10, "end": 5}, {"preset": "mp4", "start": -1}):
        with pytest.raises(ConvertError):
            ConvertSpec.from_dict(bad)
    with pytest.raises(ConvertError, match="después del final"):
        plan("mp4", start=500)
    s = ConvertSpec.from_dict({"preset": "mp4", "height": "720", "start": "10", "end": 70, "quality": "high"})
    assert s.describe() == "MP4 compatible · 720p · calidad alta · 0:10–1:10"
    assert ConvertSpec.from_dict({"preset": "mp3", "audio_bitrate": 256}).describe() == "Solo audio · MP3 · 256 kbps"
    assert ConvertSpec.from_dict({"preset": "gif"}).describe() == "GIF animado · 480 px · 12 fps"


def test_output_names_never_overwrite(tmp_path):
    src = tmp_path / "clip.mkv"
    src.write_bytes(b"x")
    spec = ConvertSpec.from_dict({"preset": "mp4"})
    out = output_path(src, tmp_path, spec)
    assert out == tmp_path / "clip.mp4" and partial_path(out) == tmp_path / "clip.part.mp4"
    out.write_bytes(b"x")
    assert output_path(src, tmp_path, spec) == tmp_path / "clip (2).mp4"
    (tmp_path / "clip (2).part.mp4").write_bytes(b"x")   # a conversion in progress
    assert output_path(src, tmp_path, spec, {str(tmp_path / "clip (3).mp4")}) == tmp_path / "clip (4).mp4"
    ranged = ConvertSpec.from_dict({"preset": "mp3", "start": 65, "end": 3725})
    assert output_path(src, tmp_path, ranged).name == "clip [00.01.05-01.02.05].mp3"
    # converting an mkv to mkv in its own folder never targets the source itself
    same = ConvertSpec.from_dict({"preset": "small", "container": "mkv"})
    assert output_path(src, tmp_path, same) == tmp_path / "clip (2).mkv"


def test_media_files_natural_order_and_skip(tmp_path):
    for name in ("ep10.mkv", "ep2.mp4", "ep1.avi", "tema.mp3", "notas.txt", ".oculto.mkv", "x.part.mp4"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "Convertidos").mkdir()
    (tmp_path / "Convertidos" / "ep1.mp4").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ep3.mkv").write_bytes(b"x")
    names = [p.name for p in media_files(tmp_path, "video")]
    assert names == ["ep1.avi", "ep2.mp4", "ep10.mkv"]
    assert [p.name for p in media_files(tmp_path, "audio")] == ["ep1.avi", "ep2.mp4", "ep10.mkv", "tema.mp3"]
    rec = [p.relative_to(tmp_path).as_posix() for p in media_files(tmp_path, "video", True, skip=tmp_path / "Convertidos")]
    assert rec == ["ep1.avi", "ep2.mp4", "ep10.mkv", "sub/ep3.mkv"]


VAINFO_IHD = """libva info: VA-API version 1.23.0
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 26.1.2 ()
vainfo: Supported profile and entrypoints
      VAProfileMPEG2Simple            :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointEncSliceLP
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointEncSliceLP
      VAProfileJPEGBaseline           :	VAEntrypointEncPicture
      VAProfileH264ConstrainedBaseline:	VAEntrypointEncSliceLP
      VAProfileHEVCMain               :	VAEntrypointVLD
"""
VAINFO_FULL = """vainfo: Driver version: Mesa Gallium driver 25.0 for AMD Radeon
      VAProfileH264High               :	VAEntrypointEncSlice
      VAProfileHEVCMain               :	VAEntrypointEncSlice
      VAProfileHEVCMain10             :	VAEntrypointEncSlice
      VAProfileHEVCMain               :	VAEntrypointVLD
"""


def test_parse_vainfo():
    caps = hw.parse_vainfo(VAINFO_IHD)
    assert caps["driver"].startswith("Intel iHD driver")
    assert set(caps["encode"]) == {"h264"}              # JPEG encoding and HEVC decoding do not count
    assert caps["encode"]["h264"]["low_power"] is True  # only VAEntrypointEncSliceLP → -low_power 1
    caps = hw.parse_vainfo(VAINFO_FULL)
    assert set(caps["encode"]) == {"h264", "hevc"} and caps["encode"]["hevc"]["low_power"] is False
    assert caps["encode"]["hevc"]["profiles"] == ["VAProfileHEVCMain"]   # Main10 (10-bit) is not used
    assert hw.parse_vainfo("")["encode"] == {}


def test_detect_with_fake_vainfo(tmp_path, monkeypatch):
    fake = tmp_path / "vainfo"
    fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + VAINFO_IHD + "EOF\n")
    fake.chmod(0o755)
    monkeypatch.setenv("MPV_UOS_VAINFO", str(fake))
    monkeypatch.setenv("MPV_UOS_VAAPI_DEVICE", "/dev/dri/renderD128")
    monkeypatch.setattr(hw, "ffmpeg_encoders", lambda *a, **k: {"libx264", "h264_vaapi", "hevc_vaapi"})
    caps = hw.detect()
    assert caps["available"] and caps["codecs"] == ["h264"] and caps["device"] == "/dev/dri/renderD128"
    assert hw.plan_for(caps, "h264") == HwPlan("/dev/dri/renderD128", "h264", True)
    assert hw.plan_for(caps, "hevc") is None and hw.plan_for(caps, "vp9") is None
    # ffmpeg without h264_vaapi: nothing usable
    monkeypatch.setattr(hw, "ffmpeg_encoders", lambda *a, **k: {"libx264"})
    assert hw.detect()["available"] is False
    # a vainfo that hangs: timeout → CPU
    fake.write_text("#!/bin/sh\nsleep 5\n")
    caps = hw.detect(timeout=0.3)
    assert caps["available"] is False and "no respondió" in caps["reason"]
    monkeypatch.setenv("MPV_UOS_VAAPI", "0")
    assert hw.detect()["reason"].startswith("desactivado")
    monkeypatch.setenv("MPV_UOS_VAAPI", "1")
    monkeypatch.setenv("MPV_UOS_VAINFO", "")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert hw.detect()["reason"] == "vainfo no está instalado"
