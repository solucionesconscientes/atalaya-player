"""H23 · the post-download chain: real two-pass loudnorm verified with ffmpeg's ebur128, renaming and moving to a
library folder (which is then rescanned) after an ordinary download, AI subtitles + translation with stand-in services,
and a failed step that does not stop the next ones."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mpvd.subscriptions.chain import ChainConfig, PostChain, loudnorm_file
from mpvd.ytdl.downloads import DownloadItem
from mpvd.ytdl.presets import DownloadSpec
from tests.test_feeds_service import env, run  # noqa: F401 - fixture reused

pytestmark = pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
                                reason="ffmpeg required")


def integrated_lufs(path: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True, check=True).stderr
    summary = err[err.rindex("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))


def streams(path: Path) -> list[dict]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,sample_rate,nb_frames",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


@pytest.fixture(scope="module")
def quiet(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("loud")
    out = d / "quiet.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=8,volume=-12dB",
                    "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "96k",
                    str(out)], check=True)
    return out


def test_loudnorm_two_passes_on_a_video(quiet, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(quiet.read_bytes())
    before = integrated_lufs(src)
    assert before < -30
    res = asyncio.run(loudnorm_file(src))
    assert abs(res["input_i"] - before) < 1.0 and res["target_i"] == -16
    assert abs(integrated_lufs(src) - (-16.0)) <= 1.0
    st = streams(src)
    orig = streams(quiet)
    assert [s["codec_name"] for s in st] == ["h264", "aac"]
    assert st[0]["nb_frames"] == orig[0]["nb_frames"]            # the video is copied, not re-encoded
    assert st[1]["sample_rate"] == "44100"                       # the track keeps its rate (loudnorm runs at 192k)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["v.mp4"]   # no leftovers


def test_loudnorm_audio_only_mp3_and_silence(tmp_path):
    mp3 = tmp_path / "p.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=300:sample_rate=48000:duration=6,volume=-20dB", "-c:a", "libmp3lame", "-b:a", "64k",
                    str(mp3)], check=True)
    asyncio.run(loudnorm_file(mp3))
    assert abs(integrated_lufs(mp3) + 16.0) <= 1.0 and streams(mp3)[0]["codec_name"] == "mp3"
    silent = tmp_path / "s.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3",
                    "-c:a", "aac", str(silent)], check=True)
    with pytest.raises(Exception, match="silencio"):
        asyncio.run(loudnorm_file(silent))
    assert silent.is_file()


def test_chain_after_an_ordinary_download_moves_to_the_library(env, tmp_path):  # noqa: F811
    lib = tmp_path / "Biblioteca"
    lib.mkdir()

    async def fn(server, c):
        await c.call("library.folders.add", {"path": str(lib), "scan": False})
        await c.call("feeds.settings.set", {"chain_downloads": True, "chain": {
            "loudnorm": True, "rename": "{title} [{id}]", "move_to": str(lib / "YouTube")}})
        item = await c.call("ytdl.download", {"url": "https://fake.test/clip", "preset": "video_360"})

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            st = await c.call("feeds.chain.status", {"id": item["id"]})
            if st["post"].get("status") in ("done", "failed"):
                break
            await asyncio.sleep(0.1)
        assert st["post"]["status"] == "done", st
        assert [(s["id"], s["status"]) for s in st["post"]["steps"]] == [
            ("loudnorm", "done"), ("rename", "done"), ("move", "done")]
        final = lib / "YouTube" / "Fake Test Video [fake-clip].mp4"
        assert st["post"]["file"] == str(final) and final.is_file() and st["outputs"][0] == str(final)
        assert not (tmp_path / "dl" / "Fake Test Video [fake-clip].mp4").exists()
        assert abs(integrated_lufs(final) + 16.0) <= 1.0
        assert st["message"] == "completado · pasos hechos"
        # the library folder is rescanned: the new file shows up
        job = server.library.scan_job
        assert job is not None
        await job.wait(60)
        items = server.library.store.items()
        assert any(i["path"] == str(final) for i in items)
        # the chain state survives in the history
        saved = json.loads((tmp_path / "data" / "downloads.json").read_text())
        assert saved[0]["post"]["status"] == "done"

    run(tmp_path, fn)


class _Downloads:
    def __init__(self):
        self.pushes = 0

    def changed(self, item, persist=True):
        self.pushes += 1


class _Task:
    def __init__(self):
        self.complete, self.status, self.detected, self.language, self.model, self.error = False, "running", "en", \
            "auto", "base", None


class _Asr:
    def __init__(self, available=True):
        self.engine = SimpleNamespace(available=available)
        self.started: list[tuple] = []

    async def start(self, path, language, model, purpose):
        self.started.append((path, language, purpose))
        t = _Task()

        async def finish():
            await asyncio.sleep(0.05)
            t.complete, t.status = True, "done"

        asyncio.get_running_loop().create_task(finish())
        return t


class _Subs:
    def __init__(self, folder: Path):
        self.folder = folder
        self.calls: list[tuple] = []

    async def save(self, path, kind, lang="", srt=None):
        self.calls.append(("save", kind, lang))
        out = self.folder / f"{Path(path).stem}.{lang}.{kind}.srt"
        out.write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")
        return {"path": str(out)}

    async def translate(self, srt, source, target, path=None, notify=""):
        self.calls.append(("translate", source, target))
        return {"status": "done", "srt": srt}


def _item(path: Path, **extra) -> DownloadItem:
    spec = DownloadSpec(url="https://fake.test/x", extra=dict(extra))
    item = DownloadItem(spec=spec, out_dir=str(path.parent), status="done", outputs=[str(path)])
    item.info = {"id": "x1", "title": "Episodio 1", "filepath": str(path), "upload_date": "20260930"}
    return item


def test_chain_subtitles_translation_and_failed_steps(tmp_path):
    async def go():
        noaudio = tmp_path / "mudo.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1",
                        "-c:v", "libx264", "-preset", "ultrafast", str(noaudio)], check=True)
        srt = tmp_path / "mudo.es.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n", encoding="utf-8")
        item = _item(noaudio, feed_title="Mi podcast")
        item.outputs.append(str(srt))
        subs = _Subs(tmp_path)
        asr = _Asr()
        server = SimpleNamespace(asr=asr, subs=subs, jobs=None, library=None)
        done = []
        chain = PostChain(server, _Downloads(), on_complete=lambda it: done.append(it.id))
        cfg = ChainConfig(loudnorm=True, rename="{feed} - {date} - {title}", subtitles=True, translate="es")
        chain.start(item, cfg)
        while not done:
            await asyncio.sleep(0.02)
        steps = {s["id"]: s for s in item.post["steps"]}
        assert steps["loudnorm"]["status"] == "failed" and "audio" in steps["loudnorm"]["message"]
        new = tmp_path / "Mi podcast - 2026-09-30 - Episodio 1.mkv"
        assert steps["rename"]["status"] == "done" and new.is_file() and not noaudio.exists()
        assert (tmp_path / "Mi podcast - 2026-09-30 - Episodio 1.es.srt").is_file()      # the sidecar followed
        assert steps["subtitles"]["status"] == "done" and steps["translate"]["status"] == "done"
        assert asr.started == [(str(new), "auto", "precompute")]
        assert subs.calls == [("save", "ai", "en"), ("translate", "en", "es"), ("save", "translation", "es")]
        assert item.post["status"] == "failed" and item.message.startswith("completado · igualar el volumen:")
        assert len(item.post["outputs"]) == 2

        # no Whisper: the step is skipped, not failed; a resumed chain does not redo what was done
        item2 = _item(new)
        item2.post = {"steps": [{"id": "rename", "label": "renombrar", "status": "done", "message": "x"}],
                      "file": str(new)}
        done.clear()
        server.asr = _Asr(available=False)
        chain.start(item2, ChainConfig(rename="{title}", subtitles=True, translate="fr"))
        while not done:
            await asyncio.sleep(0.02)
        st = {s["id"]: s["status"] for s in item2.post["steps"]}
        assert st == {"rename": "done", "subtitles": "skipped", "translate": "skipped"}
        assert new.is_file() and item2.post["status"] == "done"

    asyncio.run(go())
