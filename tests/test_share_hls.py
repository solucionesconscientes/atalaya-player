"""H25 · what the guests play: real HLS made by ffmpeg (``-c copy`` of H.264/AAC, CPU transcode of other codecs,
VA-API when this machine has it), the playlist and a segment checked with ffprobe, WebVTT from an SRT, the choice of
a web video's direct URL or relay inputs (yt-dlp fixtures), and the page's drift correction (sync.js under node)."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mpvd.convert import hw as hw_mod
from mpvd.convert.presets import HwPlan
from mpvd.share import hls

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "ytdlp"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="sin ffmpeg")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("share-src")
    srt = d / "s.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:02,500\nHola <i>mundo</i>\n\n2\n00:00:03,000 --> 00:00:04,000\n"
                   "Adiós\n", encoding="utf-8")
    h264 = d / "h264.mkv"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=9", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000:duration=9", "-f", "lavfi",
            "-i", "sine=frequency=880:sample_rate=48000:duration=9", "-i", str(srt),
            "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
            "-c:a", "aac", "-c:s", "srt", str(h264))
    other = d / "mpeg4.mkv"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=5", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000:duration=5", "-c:v", "mpeg4", "-c:a", "mp2", str(other))
    return {"h264": h264, "mpeg4": other, "srt": srt}


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _run_stream(inputs: list[hls.Input], out: Path, audio_index: int | None = None, hw: HwPlan | None = None
                ) -> tuple[hls.HlsStream, list[hls.HlsPlan]]:
    probes = [hls.probe(i) for i in inputs]
    plans = hls.plans_for(inputs, probes, out, audio_index=audio_index, hw=hw)

    async def go() -> hls.HlsStream:
        st = hls.HlsStream("t", out, plans, duration=hls.duration_of(probes[0]))
        st.start()
        assert st.task is not None
        await asyncio.wait_for(st.task, 120)
        return st

    return asyncio.run(go()), plans


def _check_hls(out: Path, duration: float, vcodec: str = "h264", acodec: str = "aac") -> dict:
    text = (out / hls.PLAYLIST).read_text()
    assert text.startswith("#EXTM3U") and "#EXT-X-PLAYLIST-TYPE:EVENT" in text and "#EXT-X-ENDLIST" in text
    assert "#EXT-X-INDEPENDENT-SEGMENTS" in text
    info = hls.playlist_info(text)
    assert info["complete"] and info["type"] == "EVENT" and info["segments"] >= 2
    assert abs(info["seconds"] - duration) < 1.0, info
    segs = sorted(out.glob("seg_*.ts"))
    assert len(segs) == info["segments"] and all(s.name in text for s in segs)
    assert not list(out.glob("*.tmp"))
    data = _probe(segs[0])
    codecs = {s["codec_type"]: s["codec_name"] for s in data["streams"]}
    assert codecs.get("video") == vcodec and codecs.get("audio") == acodec, codecs
    assert data["format"]["format_name"] == "mpegts"
    return data


def test_hls_copy_of_h264_aac_with_the_chosen_audio(clips, tmp_path):
    data = hls.probe(hls.Input(str(clips["h264"])))
    assert hls.video_copyable(hls.video_stream(data))
    second_audio = hls.audio_streams(data)[1]["index"]
    out = tmp_path / "copy"
    st, plans = _run_stream([hls.Input(str(clips["h264"]))], out, audio_index=second_audio)
    assert st.status == "done" and plans[0].mode == "copy" and len(plans) == 1, st.error
    cmd = plans[0].cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "copy"
    assert f"0:{second_audio}" in cmd and "-sn" in cmd
    _check_hls(out, 9.0)
    info = st.info()
    assert info["complete"] and info["ready"] > 8 and info["status"] == "done"


def test_hls_cpu_transcode_of_other_codecs(clips, tmp_path):
    out = tmp_path / "cpu"
    st, plans = _run_stream([hls.Input(str(clips["mpeg4"]))], out, hw=None)
    assert st.status == "done", st.error
    assert plans[0].video == "cpu" and plans[0].audio == "aac"
    cmd = plans[0].cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and cmd[cmd.index("-preset") + 1] == "veryfast"
    _check_hls(out, 5.0)


def test_hls_vaapi_when_available(clips, tmp_path):
    caps = hw_mod.detect()
    plan = hw_mod.plan_for(caps, "h264")
    if plan is None:
        pytest.skip(f"sin VA-API para H.264: {caps.get('reason')}")
    out = tmp_path / "vaapi"
    st, plans = _run_stream([hls.Input(str(clips["mpeg4"]))], out, hw=plan)
    assert plans[0].video == "vaapi" and len(plans) == 2 and plans[1].video == "cpu"
    assert "h264_vaapi" in plans[0].cmd and ("-low_power" in plans[0].cmd) == plan.low_power
    assert st.status == "done", st.error
    _check_hls(out, 5.0)


def test_build_command_options():
    v = {"index": 0, "codec_name": "hevc", "height": 2160, "avg_frame_rate": "24000/1001", "pix_fmt": "yuv420p10le"}
    a = {"index": 1, "codec_name": "eac3"}
    out = Path("/tmp/x")
    cpu = hls.build_command([hls.Input("/m.mkv")], out, (0, v), (0, a))
    assert cpu.video == "cpu" and "scale=-2:720" in cpu.cmd and cpu.cmd[cpu.cmd.index("-g") + 1] == "48"
    assert cpu.cmd[cpu.cmd.index("-hls_playlist_type") + 1] == "event"
    assert cpu.cmd[cpu.cmd.index("-hls_flags") + 1] == "independent_segments+temp_file"
    assert cpu.cmd[-1] == str(out / "index.m3u8") and str(out / "seg_%05d.ts") in cpu.cmd
    hw = hls.build_command([hls.Input("/m.mkv")], out, (0, v), (0, a), hw=HwPlan("/dev/dri/renderD128", "h264", True))
    i = hw.cmd.index("-vaapi_device")
    assert i < hw.cmd.index("-i") and hw.cmd[i + 1] == "/dev/dri/renderD128"
    assert "scale=-2:1080,format=nv12,hwupload" in hw.cmd and hw.cmd[hw.cmd.index("-low_power") + 1] == "1"
    audio_only = hls.build_command([hls.Input("/a.flac")], out, None, (0, {"index": 0, "codec_name": "flac"}))
    assert audio_only.video == "none" and "-c:v" not in audio_only.cmd and audio_only.mode == "audio"
    # a web relay: two inputs with their request headers
    two = hls.build_command([hls.Input("https://v", {"User-Agent": "UA", "Referer": "https://r/"}),
                             hls.Input("https://a")], out, (0, {"index": 0, "codec_name": "h264", "pix_fmt": "yuv420p"}),
                            (1, {"index": 0, "codec_name": "aac"}))
    assert two.mode == "copy" and two.cmd.count("-i") == 2 and "1:0" in two.cmd and "0:0" in two.cmd
    assert two.cmd[two.cmd.index("-user_agent") + 1] == "UA" and "Referer: https://r/\r\n" in two.cmd
    assert not hls.video_copyable({"codec_name": "h264", "profile": "High 10", "pix_fmt": "yuv420p"})
    assert not hls.video_copyable({"codec_name": "h264", "pix_fmt": "yuv422p"})


def test_webvtt_from_srt_file_and_from_stream(clips, tmp_path):
    out = tmp_path / "a.vtt"
    subprocess.run(hls.vtt_command(str(clips["srt"]), out), check=True, capture_output=True)
    text = out.read_text()
    assert text.startswith("WEBVTT") and "00:01.500 --> 00:02.500" in text and "Adiós" in text
    sub_index = next(s["index"] for s in _probe(clips["h264"])["streams"] if s["codec_type"] == "subtitle")
    out2 = tmp_path / "b.vtt"
    subprocess.run(hls.vtt_command(str(clips["h264"]), out2, sub_index), check=True, capture_output=True)
    # timeline rebased like mpv's time-pos (the AAC priming makes this file start at -0.021 s)
    starts = [float(m) for m in re.findall(r"^00:(\d\d\.\d{3}) -->", out2.read_text(), re.MULTILINE)]
    assert len(starts) == 2 and abs(starts[1] - 3.0) < 0.05, starts


def test_direct_or_relay_for_web_videos():
    yt = json.loads((FIX / "youtube_bbb.json").read_text())
    assert hls.pick_direct(yt) is None  # YouTube: no combined formats
    inputs = hls.pick_relay(yt)
    assert len(inputs) == 2
    fmt = {f["url"]: f for f in yt["formats"]}
    v, a = fmt[inputs[0].url], fmt[inputs[1].url]
    assert v["vcodec"].startswith("avc1") and v["height"] == 720 and v["protocol"] == "https"
    assert a["format_id"] == "140" and inputs[0].headers.get("User-Agent")
    archive = json.loads((FIX / "archive_bbb.json").read_text())
    d = hls.pick_direct(archive)
    assert d is not None and d["ext"] == "mp4" and d["url"].endswith(".mp4")
    # special request headers → not playable by a bare <video>
    fmt1 = dict(archive["formats"][1], http_headers={"Referer": "https://x/"})
    assert hls.pick_direct({"formats": [fmt1]}) is None
    combined = {"formats": [{"url": "https://c/1.mp4", "ext": "mp4", "protocol": "https", "vcodec": "avc1.64001f",
                             "acodec": "mp4a.40.2", "height": 720},
                            {"url": "https://c/2.webm", "ext": "webm", "protocol": "https", "vcodec": "vp9",
                             "acodec": "opus", "height": 1080}]}
    assert hls.pick_direct(combined)["url"] == "https://c/1.mp4"
    assert hls.plain_direct("https://x/y/video.MP4?sig=1") and not hls.plain_direct("https://x/live.m3u8")
    assert hls.is_url("https://x") and not hls.is_url("/home/a.mkv") and not hls.is_url("file:///a.mkv")


@pytest.mark.skipif(not shutil.which("node"), reason="sin node")
def test_sync_js_drift_correction():
    script = """
const S = require(process.argv[1]);
const out = {};
const st = {pos: 10, paused: false, speed: 1, duration: 100};
out.exp = S.expected(st, 1000, 3000);
out.expPaused = S.expected({...st, paused: true}, 1000, 3000);
out.expFast = S.expected({...st, speed: 2}, 1000, 2000);
out.expEnd = S.expected({...st, pos: 99.5}, 0, 5000);
out.seek = S.correction(12, 9, 1, false);
out.faster = S.correction(12, 11.7, 1, false);
out.slower = S.correction(12, 12.4, 1.5, false);
out.none = S.correction(12, 11.95, 1, false);
out.capped = S.correction(12, 10.6, 1, false);
out.pausedSeek = S.correction(12, 11, 1, true);
out.pausedOk = S.correction(12, 11.9, 1, true);
out.clock = [S.clock(5), S.clock(754), S.clock(3725)];
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", script, str(ROOT / "mpvd" / "share" / "www" / "sync.js")],
                         capture_output=True, text=True, check=True, timeout=30)
    o = json.loads(res.stdout)
    assert o["exp"] == 12 and o["expPaused"] == 10 and o["expFast"] == 12 and o["expEnd"] == 100
    assert o["seek"]["action"] == "seek" and o["seek"]["to"] == 12
    assert o["faster"]["action"] == "rate" and 1.0 < o["faster"]["rate"] <= 1.08
    assert o["slower"]["action"] == "rate" and 1.5 * 0.92 <= o["slower"]["rate"] < 1.5
    assert o["none"]["action"] == "none" and o["none"]["rate"] == 1
    assert o["capped"]["action"] == "rate" and abs(o["capped"]["rate"] - 1.08) < 1e-9
    assert o["pausedSeek"]["action"] == "seek" and o["pausedOk"]["action"] == "none"
    assert o["clock"] == ["0:05", "12:34", "1:02:05"]
