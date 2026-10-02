"""H20 · conversions for real (in-process mpvd + ffmpeg on clips of a few seconds): codecs checked with ffprobe, range,
resolution cap, subtitles kept, cancel, failures, retry/remove, a whole folder (one task per file, one at a time),
VA-API (real encode when this machine has it, and the automatic CPU retry when the GPU fails), the queue surviving a
restart and tasks.list mixing downloads and conversions."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.convert import hw as hw_mod
from mpvd.convert.presets import available_encoders
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from tests.conftest import APP_FOLDER

FAST = {"speed": "fast", "hw": "cpu"}


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """4 s, 1280x720, a keyframe every 10 frames, AAC and a Spanish SRT track."""
    d = tmp_path_factory.mktemp("convert-src")
    srt = d / "s.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nUno\n\n2\n00:00:02,000 --> 00:00:03,000\nDos\n\n"
                   "3\n00:00:03,200 --> 00:00:03,900\nTres\n", encoding="utf-8")
    out = d / "clip.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4", "-i", str(srt),
                    "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast", "-g", "10",
                    "-c:a", "aac", "-c:s", "srt", "-metadata:s:s:0", "language=spa", str(out)], check=True)
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    monkeypatch.setenv("MPV_UOS_YTDLP", str(tmp_path / "no-yt-dlp"))
    return tmp_path


def settings(tmp_path: Path) -> Settings:
    return Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                    idle_timeout=0, workers=3)


def with_server(tmp_path: Path, fn):
    async def go():
        server = MpvdServer(settings(tmp_path))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


async def wait_status(c, item_id, statuses=("done", "failed", "cancelled"), timeout=60.0):
    deadline = time.monotonic() + timeout
    d = {}
    while time.monotonic() < deadline:
        d = await c.call("convert.get", {"id": item_id})
        if d["status"] in statuses:
            return d
        await asyncio.sleep(0.05)
    raise TimeoutError(f"conversion {item_id} still {d.get('status')}: {d.get('message')}")


def probe(path: str | Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,codec_tag_string,width,height,duration:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def codecs(info: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in info["streams"]:
        out.setdefault(s["codec_type"], []).append(s["codec_name"])
    return out


def duration(info: dict) -> float:
    return float(info["format"]["duration"])


def subs_text(path: Path) -> str:
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"],
                          capture_output=True, text=True, check=True).stdout


def test_mp4_range_resolution_subtitles_retry_remove_and_tasks(env, clip):
    out_dir = env / "Convertidos"

    async def fn(server, c):
        presets = await c.call("convert.presets")
        # H55 · «copy» (sin recodificar) y «av1» son nuevos; av1 solo si este ffmpeg trae SVT-AV1, porque ofrecer
        # un formato que la máquina no puede hacer es peor que no ofrecerlo
        ids = [p["id"] for p in presets["presets"]]
        assert ids[:3] == ["mp4", "small", "web"] and "copy" in ids
        assert ids[-6:] == ["mp3", "m4a", "opus", "flac", "wav", "gif"]
        assert ("av1" in ids) == ("libsvtav1" in available_encoders())
        assert presets["heights"] == [0, 1080, 720, 480] and presets["default_dir"].endswith(f"{APP_FOLDER}/Convertidos")
        res = await c.call("convert.start", {"path": str(clip), "preset": "mp4", "out_dir": str(out_dir),
                                             "options": {**FAST, "height": 480, "start": 1.0, "end": 3.5}})
        assert res["count"] == 1 and res["out_dir"] == str(out_dir)
        item = await wait_status(c, res["items"][0]["id"])
        assert item["status"] == "done", item
        out = Path(item["output"])
        assert out == out_dir / "clip [00.00.01-00.00.03].mp4" and item["hw"] == "cpu"
        assert item["description"] == "MP4 compatible · 480p · calidad normal · 0:01–0:03"
        info = probe(out)
        assert codecs(info) == {"video": ["h264"], "audio": ["aac"], "subtitle": ["mov_text"]}
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (854, 480)
        # the video is the range; the container may last a bit longer when the last subtitle ends after it
        assert abs(float(v["duration"]) - 2.5) < 0.1 and duration(info) < 3.0
        # the subtitles moved with the range: «Dos» (2–3 s) now starts at 1 s
        text = subs_text(out)
        assert "Dos" in text and "00:00:01,0" in text and "Uno" not in text
        assert not list(out_dir.glob("*.part.*"))
        detail = await c.call("convert.get", {"id": item["id"]})
        assert detail["argv"][0][0].endswith("ffmpeg") and "-n" in detail["argv"][0]

        tasks = await c.call("tasks.list")
        row = next(t for t in tasks["tasks"] if t["id"] == item["id"])
        assert row["type"] == "convert" and row["actions"] == ["retry", "remove", "folder"] and tasks["active"] == 0
        # «repetir»: a new file next to the first one, never overwritten
        again = await c.call("convert.retry", {"id": item["id"]})
        assert again["status"] == "queued"
        again = await wait_status(c, item["id"])
        assert again["status"] == "done" and Path(again["output"]).name == "clip [00.00.01-00.00.03] (2).mp4"
        assert out.exists()
        assert (await c.call("convert.remove", {"id": item["id"]}))["removed"] is True
        assert all(t["id"] != item["id"] for t in (await c.call("tasks.list"))["tasks"])
        assert Path(again["output"]).exists()   # forgetting keeps the file

    with_server(env, fn)


@pytest.mark.parametrize(("preset", "options", "ext", "expect"), [
    ("small", {}, ".mp4", {"video": ["hevc"], "audio": ["aac"], "subtitle": ["mov_text"]}),
    ("small", {"container": "mkv"}, ".mkv", {"video": ["hevc"], "audio": ["opus"], "subtitle": ["subrip"]}),
    ("web", {}, ".webm", {"video": ["vp9"], "audio": ["opus"], "subtitle": ["webvtt"]}),
    ("mp4", {"subtitles": False}, ".mp4", {"video": ["h264"], "audio": ["aac"]}),
    ("mp3", {"audio_bitrate": 128}, ".mp3", {"audio": ["mp3"]}),
    ("m4a", {}, ".m4a", {"audio": ["aac"]}),
    ("opus", {"quality": "small"}, ".opus", {"audio": ["opus"]}),
    ("flac", {}, ".flac", {"audio": ["flac"]}),
    ("wav", {}, ".wav", {"audio": ["pcm_s16le"]}),
    ("gif", {"gif_width": 320, "gif_fps": 10}, ".gif", {"video": ["gif"]}),
])
def test_every_preset_for_real(env, clip, preset, options, ext, expect):
    async def fn(server, c):
        res = await c.call("convert.start", {"path": str(clip), "preset": preset, "out_dir": str(env / "o"),
                                             "options": {**FAST, "start": 0.5, "end": 2.5, **options}})
        return await wait_status(c, res["items"][0]["id"])

    item = with_server(env, fn)
    assert item["status"] == "done", item
    out = Path(item["output"])
    assert out.suffix == ext
    info = probe(out)
    assert codecs(info) == expect
    if preset != "gif":
        # a subtitle that ends after the range may stretch the container a little (see the mp4 test)
        assert abs(duration(info) - 2.0) < (0.6 if "subtitle" in expect else 0.35), duration(info)
    if preset == "small" and ext == ".mp4":
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert v["codec_tag_string"] == "hvc1"
    if preset == "gif":
        v = info["streams"][0]
        assert v["width"] == 320


def test_cancel_failures_and_folder(env, clip, media_dir):
    folder = env / "Serie"
    folder.mkdir()
    for name in ("ep1.mkv", "ep2.mkv", "ep10.mkv"):
        shutil.copyfile(clip, folder / name)
    (folder / "notas.txt").write_text("no es un vídeo")
    broken = env / "roto.mkv"
    broken.write_text("esto no es un vídeo")
    out_dir = env / "out"

    async def fn(server, c):
        # cancel a long conversion while it runs: no file is left behind
        res = await c.call("convert.start", {"path": str(media_dir / "video30.mkv"), "preset": "web",
                                             "out_dir": str(out_dir), "options": {"hw": "cpu", "quality": "high"}})
        cid = res["items"][0]["id"]
        await wait_status(c, cid, ("running",))
        await asyncio.sleep(0.5)
        assert (await c.call("convert.cancel", {"id": cid}))["cancelled"] is True
        d = await wait_status(c, cid)
        assert d["status"] == "cancelled" and d["output"] == ""
        assert not [p for p in out_dir.glob("*") if p.is_file()]

        # broken file / missing file / unknown preset
        res = await c.call("convert.start", {"path": str(broken), "preset": "mp4", "out_dir": str(out_dir),
                                             "options": FAST})
        d = await wait_status(c, res["items"][0]["id"])
        assert d["status"] == "failed" and "no se puede leer" in d["error"]
        with pytest.raises(RpcError, match="no existe"):
            await c.call("convert.start", {"path": str(env / "nada.mkv"), "preset": "mp4"})
        with pytest.raises(RpcError, match="preset"):
            await c.call("convert.start", {"path": str(clip), "preset": "avi"})
        # a whole folder: one task per video, in natural order, one at a time, in <out>/<folder>
        res = await c.call("convert.start", {"path": str(folder), "preset": "mp3", "out_dir": str(out_dir),
                                             "options": {**FAST, "start": 1, "end": 2}})
        assert res["count"] == 3 and res["out_dir"] == str(out_dir / "Serie") and res["group"]
        ids = [i["id"] for i in res["items"]]
        assert [i["title"] for i in res["items"]] == ["ep1.mkv", "ep2.mkv", "ep10.mkv"]
        max_running = 0
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            rows = [r for r in await c.call("convert.list") if r["id"] in ids]
            max_running = max(max_running, sum(r["status"] == "running" for r in rows))
            if all(r["status"] in ("done", "failed") for r in rows):
                break
            await asyncio.sleep(0.05)
        assert max_running <= 1
        assert all(r["status"] == "done" for r in rows), rows
        outs = sorted(p.name for p in (out_dir / "Serie").iterdir())
        assert outs == ["ep1.mp3", "ep10.mp3", "ep2.mp3"]   # a folder ignores the range: whole files
        assert abs(duration(probe(out_dir / "Serie" / "ep1.mp3")) - 4.0) < 0.3
        with pytest.raises(RpcError, match="no hay archivos"):
            await c.call("convert.start", {"path": str(out_dir / "Serie"), "preset": "mp4"})
        cleared = await c.call("tasks.clear")
        assert cleared["removed"] == 5 and (await c.call("tasks.list"))["tasks"] == []

    with_server(env, fn)


def test_vaapi_failure_falls_back_to_cpu(env, clip, monkeypatch):
    """A GPU that vainfo reports but ffmpeg cannot open: the file is converted again on the CPU."""
    fake = env / "vainfo"
    fake.write_text("#!/bin/sh\necho 'vainfo: Driver version: fake'\n"
                    "echo '      VAProfileH264High               :\tVAEntrypointEncSliceLP'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("MPV_UOS_VAINFO", str(fake))
    monkeypatch.setenv("MPV_UOS_VAAPI_DEVICE", str(env / "no-such-render-node"))
    monkeypatch.setattr(hw_mod, "ffmpeg_encoders", lambda *a, **k: {"libx264", "h264_vaapi"})

    async def fn(server, c):
        caps = await c.call("convert.hw", {"refresh": True})
        assert caps["available"] is True and caps["codecs"] == ["h264"] and caps["encode"]["h264"]["low_power"]
        res = await c.call("convert.start", {"path": str(clip), "preset": "mp4", "out_dir": str(env / "o"),
                                             "options": {"speed": "fast", "end": 1.5}})
        d = await wait_status(c, res["items"][0]["id"])
        detail = await c.call("convert.get", {"id": d["id"]})
        return d, detail

    d, detail = with_server(env, fn)
    assert d["status"] == "done" and d["hw"] == "cpu" and d["message"] == "completado por CPU"
    assert any("tarjeta gráfica falló" in w for w in d["warnings"])
    assert "libx264" in detail["argv"][0] and codecs(probe(d["output"]))["video"] == ["h264"]


@pytest.mark.skipif(not hw_mod.detect().get("available"), reason="this machine has no VA-API encoder")
def test_vaapi_real_encode(env, clip):
    """Real H.264 encode on the GPU (Intel iHD here: VAEntrypointEncSliceLP only → -low_power 1)."""
    async def fn(server, c):
        caps = await c.call("convert.hw")
        res = await c.call("convert.start", {"path": str(clip), "preset": "mp4", "out_dir": str(env / "o"),
                                             "options": {"height": 480, "end": 2}})
        d = await wait_status(c, res["items"][0]["id"])
        return caps, d, await c.call("convert.get", {"id": d["id"]})

    caps, d, detail = with_server(env, fn)
    assert d["status"] == "done" and d["hw"] == "vaapi" and d["message"] == "completado", d
    argv = detail["argv"][0]
    assert "h264_vaapi" in argv and argv[argv.index("-vaapi_device") + 1] == caps["device"]
    assert ("-low_power" in argv) == bool(caps["encode"]["h264"]["low_power"])
    info = probe(d["output"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["codec_name"] == "h264" and (v["width"], v["height"]) == (854, 480)
    assert abs(duration(info) - 2.0) < 0.3


def test_queue_survives_restart_and_tasks_mix_downloads(env, clip):
    data = env / "data"
    data.mkdir(parents=True)
    out_dir = env / "o"
    out_dir.mkdir()
    stale = out_dir / "clip.part.m4a"
    stale.write_bytes(b"half")
    (data / "conversions.json").write_text(json.dumps([
        {"id": "c1", "src": str(clip), "out_dir": str(out_dir), "status": "running", "partial": str(stale),
         "spec": {"preset": "m4a", "speed": "fast", "end": 1}},
    ]))
    (data / "downloads.json").write_text(json.dumps([
        {"id": "d1", "url": "https://example.test/v", "title": "Un vídeo", "status": "done", "out_dir": str(out_dir),
         "outputs": [str(out_dir / "v.mp4")], "created_at": 1.0, "finished_at": 2.0,
         "spec": {"url": "https://example.test/v"}},
    ]))

    async def fn(server, c):
        d = await wait_status(c, "c1")
        assert d["status"] == "done" and Path(d["output"]).name == "clip [00.00.00-00.00.01].m4a"
        tasks = (await c.call("tasks.list"))["tasks"]
        by_id = {t["id"]: t for t in tasks}
        assert by_id["d1"]["type"] == "download" and by_id["d1"]["title"] == "Un vídeo"
        assert by_id["d1"]["actions"] == ["retry", "remove", "folder"]
        assert by_id["c1"]["type"] == "convert"
        return d

    with_server(env, fn)
    assert not stale.exists()
