"""Download queue: progress parsing from real yt-dlp lines, and the ytdl.* methods end to end against the fake
yt-dlp (tests/fixtures/ytdlp/fake_ytdlp.py) through an in-process mpvd server."""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.ytdl.downloads import DownloadSettings, ProgressState, fmt_eta

FIX = Path(__file__).parent / "fixtures" / "ytdlp"
FAKE = FIX / "fake_ytdlp.py"


def test_progress_state_from_real_lines_two_files_and_merge():
    ps = ProgressState()
    lines = (FIX / "progress_lines_youtube_160+139.txt").read_text(encoding="utf-8").splitlines()
    seen_download = False
    for line in lines:
        assert ps.feed(line) or not line.startswith("MU_")
        if ps.stage == "download":
            seen_download = True
            assert 0.0 <= ps.progress <= 0.97
    assert seen_download and len(ps.files) == 2  # 160 (video) + 139 (audio)
    assert ps.stage == "postprocess" and ps.postprocessor == "MoveFiles" and ps.progress == 0.98
    assert ps.total == sum(v[1] for v in ps.files.values()) and ps.downloaded == ps.total
    ps.feed('MU_DONE {"id": "x", "title": "T", "ext": "mp4", "filepath": "/tmp/x.mp4", "format_id": "160+139"}')
    assert ps.stage == "done" and ps.progress == 1.0 and ps.outputs == ["/tmp/x.mp4"] and ps.message() == "completado"


def test_progress_state_archive_single_file_and_unknowns():
    ps = ProgressState()
    lines = (FIX / "progress_lines_archive_countdown.txt").read_text(encoding="utf-8").splitlines()
    ps.feed(lines[0])
    assert ps.speed is None and ps.eta is None and ps.message().startswith("0%")
    for line in lines[1:]:
        ps.feed(line)
    assert len(ps.files) == 1 and ps.stage == "postprocess"
    assert not ps.feed("[download] Destination: x.mp4") and not ps.feed("MU_PROGRESS not json")
    assert fmt_eta(65) == "01:05" and fmt_eta(3725) == "1:02:05"


def test_download_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("MPV_UOS_DOWNLOAD_DIR", raising=False)
    s = DownloadSettings()
    assert s.resolved_dir("video").name == "MPV-UOS" and s.resolved_dir("audio").name == "MPV-UOS"
    s.update({"video_dir": str(tmp_path / "v"), "chapters": 0, "concurrent": "3", "bogus": 1})
    s.save(tmp_path / "ytdl.json")
    t = DownloadSettings.load(tmp_path / "ytdl.json")
    assert t.video_dir == str(tmp_path / "v") and t.chapters is False and t.concurrent == 3
    assert t.resolved_dir("video") == tmp_path / "v"


# -- in-process server with the fake binary -------------------------------------------------------


@pytest.fixture
def ytdl_env(tmp_path, media_dir, monkeypatch):
    arglog = tmp_path / "argv.log"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_ARGLOG", str(arglog))
    monkeypatch.setenv("FAKE_YTDLP_MEDIA", str(media_dir))
    monkeypatch.setenv("FAKE_YTDLP_DELAY", "0.02")
    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    return tmp_path, arglog


def make_settings(tmp_path: Path) -> Settings:
    return Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                    idle_timeout=0, workers=3)


def with_server(tmp_path: Path, fn):
    async def go():
        server = MpvdServer(make_settings(tmp_path))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def argv_lines(arglog: Path) -> list[list[str]]:
    if not arglog.exists():
        return []
    return [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()]


async def wait_status(c, item_id, statuses, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = await c.call("ytdl.downloads.get", {"id": item_id})
        if d["status"] in statuses:
            return d
        await asyncio.sleep(0.05)
    raise TimeoutError(f"download {item_id} still {d['status']}")


def test_status_info_cache_and_presets(ytdl_env):
    tmp_path, arglog = ytdl_env

    async def fn(server, c):
        st = await c.call("ytdl.status")
        assert st["available"] and st["binary"]["source"] == "env" and st["version"] == "2026.08.19"
        assert st["hook"]["ytdl_path"] == str(FAKE) and st["update"]["auto"] is False
        caps = await c.call("capabilities")
        assert caps["services"]["ytdl"] is True and any(m["name"] == "ytdl.download" for m in caps["methods"])

        info = await c.call("ytdl.info", {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"})
        assert info["id"] == "aqz-KE-bpKQ" and info["counts"]["audio"] >= 10 and info["formats"]["video"]
        n = len(argv_lines(arglog))
        j = [a for a in argv_lines(arglog) if "-J" in a][-1]
        assert "--no-playlist" in j and j[-1] == "https://www.youtube.com/watch?v=aqz-KE-bpKQ" and "--no-update" in j
        again = await c.call("ytdl.info", {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ#frag"})
        assert again["id"] == info["id"] and len(argv_lines(arglog)) == n  # served from the cache (fragment ignored)
        forced = await c.call("ytdl.info", {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "force": True})
        assert forced["id"] == info["id"] and len(argv_lines(arglog)) == n + 1
        raw = await c.call("ytdl.info", {"url": "https://archive.org/details/Countdow1960", "raw": True})
        assert raw["extractor"] == "archive.org" and isinstance(raw["formats"], list)

        pl = await c.call("ytdl.playlist", {"url": "https://www.youtube.com/@BlenderOfficial/videos"})
        assert len(pl["entries"]) == 5 and pl["type"] == "playlist"
        flat = [a for a in argv_lines(arglog) if "--flat-playlist" in a][-1]
        assert "--yes-playlist" in flat

        with pytest.raises(RpcError) as exc:
            await c.call("ytdl.info", {"url": "https://example.com/fail"})
        assert "Unsupported URL" in str(exc.value)

        presets = await c.call("ytdl.presets")
        assert [p["id"] for p in presets["presets"]][:2] == ["video_best", "video_1080"]
        assert presets["containers"] == ["mp4", "mkv", "webm"] and 128 in presets["audio_bitrates"]

    with_server(tmp_path, fn)


def test_download_done_cancel_fail_retry_and_history(ytdl_env):
    tmp_path, arglog = ytdl_env

    async def fn(server, c):
        url = "https://fake.test/clip"
        item = await c.call("ytdl.download", {"url": url, "preset": "video_360", "title": "Clip"})
        assert item["status"] in ("queued", "running") and item["preset"] == "video_360" and item["title"] == "Clip"
        assert item["out_dir"] == str(tmp_path / "dl")
        done = await wait_status(c, item["id"], ("done", "failed"))
        assert done["status"] == "done", done
        assert done["progress"] == 1.0 and done["stage"] == "done" and len(done["outputs"]) == 1
        out = Path(done["outputs"][0])
        assert out.is_file() and out.parent == tmp_path / "dl" and out.suffix == ".mp4"
        assert "Fake Test Video" in out.name and "[fake-clip]" in out.name  # default template
        assert done["total"] > 0 and done["downloaded"] == done["total"] and done["attempts"] == 1
        argv = [a for a in argv_lines(arglog) if a[-1] == url][-1]
        assert argv[argv.index("-f") + 1].startswith("bv*[height<=?360]+ba")
        assert argv[argv.index("-S") + 1] == "vcodec:h264,res,acodec:aac"  # mp4 (settings default): H.264 + AAC
        assert "--embed-chapters" in argv and "--embed-metadata" in argv  # settings defaults
        assert "--embed-thumbnail" not in argv
        jobs = await c.call("jobs.list")
        assert any(j["name"] == f"download:{item['id']}" and j["status"] == "done" for j in jobs)

        # audio preset goes to the audio folder with -x and the requested bitrate
        os.environ["FAKE_YTDLP_DELAY"] = "0"
        a = await c.call("ytdl.download", {"url": "https://fake.test/song", "preset": "audio_mp3_128",
                                            "options": {"thumbnail": True}})
        a = await wait_status(c, a["id"], ("done", "failed"))
        assert a["status"] == "done" and Path(a["outputs"][0]).suffix == ".mp3"
        argv = [x for x in argv_lines(arglog) if x[-1] == "https://fake.test/song"][-1]
        assert "-x" in argv and argv[argv.index("--audio-quality") + 1] == "128K" and "--embed-thumbnail" in argv

        # explicit spec instead of preset
        e = await c.call("ytdl.download", {"url": "https://fake.test/exact", "kind": "exact", "format": "137+140",
                                            "container": "mkv"})
        e = await wait_status(c, e["id"], ("done", "failed"))
        assert e["status"] == "done" and Path(e["outputs"][0]).suffix == ".mkv"

        # cancel a slow one: the subprocess dies and the item ends as cancelled
        os.environ["FAKE_YTDLP_DELAY"] = "0.5"
        os.environ["FAKE_YTDLP_STEPS"] = "40"
        slow = await c.call("ytdl.download", {"url": "https://fake.test/slow"})
        await wait_status(c, slow["id"], ("running",))
        t0 = time.monotonic()
        assert (await c.call("ytdl.downloads.cancel", {"id": slow["id"]}))["cancelled"] is True
        cancelled = await wait_status(c, slow["id"], ("cancelled",), timeout=10)
        assert time.monotonic() - t0 < 8 and cancelled["message"] == "cancelada"
        assert (await c.call("ytdl.downloads.cancel", {"id": slow["id"]}))["cancelled"] is False
        os.environ["FAKE_YTDLP_DELAY"] = "0"
        os.environ["FAKE_YTDLP_STEPS"] = "3"

        # failure surfaces yt-dlp's ERROR line; retry re-queues with attempts=2
        f = await c.call("ytdl.download", {"url": "https://fake.test/fail-please"})
        f = await wait_status(c, f["id"], ("failed",))
        assert f["error"].startswith("ERROR:") and "Unable to download" in f["error"]
        detail = await c.call("ytdl.downloads.get", {"id": f["id"]})
        assert detail["stderr"] and detail["argv"][-1] == "https://fake.test/fail-please"
        r = await c.call("ytdl.downloads.retry", {"id": f["id"]})
        assert r["id"] == f["id"] and r["attempts"] == 2 and r["status"] in ("queued", "running")
        r = await wait_status(c, f["id"], ("failed",))
        assert r["attempts"] == 2
        with pytest.raises(RpcError):
            await c.call("ytdl.downloads.retry", {"id": "nope"})
        with pytest.raises(RpcError):
            await c.call("ytdl.downloads.get", {"id": "nope"})

        rows = await c.call("ytdl.downloads.list")
        assert [r["id"] for r in rows if r["status"] == "done"] and all(r["status"] != "running" for r in rows)
        assert (await c.call("ytdl.downloads.remove", {"id": f["id"]}))["removed"] is True
        n_done = sum(1 for r in rows if r["status"] in ("done", "cancelled"))
        assert (await c.call("ytdl.downloads.clear"))["removed"] == n_done
        assert await c.call("ytdl.downloads.list") == []

        # settings persist
        s = await c.call("ytdl.settings.set", {"container": "mkv", "subtitles": True, "audio_dir": str(tmp_path / "m")})
        assert s["container"] == "mkv" and s["subtitles"] is True and s["audio_dir_resolved"] == str(tmp_path / "m")
        with pytest.raises(RpcError):
            await c.call("ytdl.settings.set", {"container": "avi"})
        return item["id"]

    with_server(tmp_path, fn)
    saved = json.loads((tmp_path / "data" / "ytdl.json").read_text())
    assert saved["container"] == "mkv" and saved["subtitles"] is True

    # history + settings survive a restart; an interrupted download is marked failed
    hist = tmp_path / "data" / "downloads.json"
    rows = json.loads(hist.read_text())
    rows.append({"id": "zz", "url": "https://fake.test/x", "status": "running", "out_dir": str(tmp_path),
                 "spec": {"url": "https://fake.test/x"}})
    hist.write_text(json.dumps(rows))

    async def fn2(server, c):
        rows = await c.call("ytdl.downloads.list")
        z = next(r for r in rows if r["id"] == "zz")
        assert z["status"] == "failed" and "reinici" in z["error"]
        assert (await c.call("ytdl.settings.get"))["container"] == "mkv"
        argv = [x for x in argv_lines(arglog)]
        d = await c.call("ytdl.download", {"url": "https://fake.test/again", "preset": "video_best"})
        d = await wait_status(c, d["id"], ("done", "failed"))
        assert d["status"] == "done" and Path(d["outputs"][0]).suffix == ".mkv"  # container setting applied
        new = [x for x in argv_lines(arglog)][len(argv):]
        assert any("--embed-subs" in x for x in new)

    with_server(tmp_path, fn2)
