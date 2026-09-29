"""Saving subtitles as SRT (subs.save) and extracting embedded text tracks (subs.extract): naming rules, fallback
folder, ASS/VTT normalisation, atomic writes, bitmap tracks refused, AI tracks (partial / complete / already saved)
through a real mpvd, and "complete and save" with the vendored whisper.cpp."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mpvd.asr.srt import Segment, parse_srt, render_srt
from mpvd.rpc import UNAVAILABLE, RpcError
from mpvd.subs import save
from tests.asr_helpers import asr_model, whisper_available

ASS = """[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\i1}Hola{\\i0}, ¿qué tal?
Dialogue: 0,0:00:04.00,0:00:06.50,Default,,0,0,0,,- ¿Vienes?\\N- Sí.
"""
VTT = """WEBVTT

00:01.000 --> 00:03.000 align:start
<v Ana>Hello there</v>

00:04.000 --> 00:06.000
second line
split
"""
SRT_ITALIC = "1\n00:00:01,000 --> 00:00:02,000\n<i>Hola</i>\n\n2\n00:00:03,000 --> 00:00:04,000\nadiós\n"


def test_names_and_folders(tmp_path, monkeypatch):
    d = tmp_path / "v"
    d.mkdir()
    assert save.choose_name(d, "peli", "es", "ai") == d / "peli.es.srt"
    (d / "peli.es.srt").write_text("x")
    assert save.choose_name(d, "peli", "es", "ai") == d / "peli.es.ia.srt"
    assert save.choose_name(d, "peli", "es", "translation") == d / "peli.es.ia.srt"
    assert save.choose_name(d, "peli", "es", "resync") == d / "peli.es.resync.srt"
    assert save.choose_name(d, "peli", "es", "track") == d / "peli.es (2).srt"
    assert save.choose_name(d, "peli", "es", "track", overwrite=True) == d / "peli.es.srt"
    (d / "peli.es.ia.srt").write_text("x")
    assert save.choose_name(d, "peli", "es", "ai") == d / "peli.es.ia (2).srt"
    assert save.choose_name(d, "peli", "", "track") == d / "peli.srt"
    assert save.choose_name(d, "peli", "und", "track") == d / "peli.srt"
    assert save.choose_name(d, "peli", "en", "track", overwrite=True, avoid=(d / "peli.en.srt",)) == d / "peli.en (2).srt"

    assert save.video_stem("/a/b/Mi película.mkv") == "Mi película"
    assert save.video_stem("file:///a/b/c%20d.mp4") == "c d"
    assert save.video_stem("https://x.org/v/La%20peli.mp4") == "La peli"
    assert save.video_stem("https://youtu.be/abc", "Un vídeo: con / barras?") == "Un vídeo con barras"
    assert save.is_url("https://a/b") and not save.is_url("file:///a") and not save.is_url("/a")

    # XDG_VIDEOS_DIR from user-dirs.dirs, then ~/Vídeos; MPV_UOS_SUBS_SAVE_DIR overrides everything
    monkeypatch.delenv("MPV_UOS_SUBS_SAVE_DIR", raising=False)
    monkeypatch.delenv("XDG_VIDEOS_DIR", raising=False)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    videos = tmp_path / "MisVideos"
    videos.mkdir()
    (cfg / "user-dirs.dirs").write_text(f'XDG_MUSIC_DIR="$HOME/Música"\nXDG_VIDEOS_DIR="{videos}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    assert save.fallback_dir() == videos / "MPV-UOS" / "Subtítulos"
    videos.rmdir()
    assert save.fallback_dir() == Path.home() / "Vídeos" / "MPV-UOS" / "Subtítulos"
    monkeypatch.setenv("MPV_UOS_SUBS_SAVE_DIR", str(tmp_path / "fb"))
    assert save.target_dir(str(d / "peli.mkv")) == (d, False)
    assert save.target_dir("https://x.org/v.mp4") == (tmp_path / "fb", True)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("running as a user that can write anywhere")
        assert save.target_dir(str(ro / "peli.mkv")) == (tmp_path / "fb", True)
    finally:
        ro.chmod(0o755)


def test_srt_text_normalises_ass_vtt_and_keeps_srt(tmp_path):
    a = tmp_path / "a.ass"
    a.write_text(ASS, encoding="utf-8")
    text, n = save.srt_text(a)
    cues = parse_srt(text, keep_lines=True)
    assert n == 2 and cues[0].text == "Hola, ¿qué tal?" and cues[1].text == "- ¿Vienes?\n- Sí." and cues[1].end == 6.5
    v = tmp_path / "a.vtt"
    v.write_text(VTT, encoding="utf-8")
    text, n = save.srt_text(v)
    assert n == 2 and parse_srt(text, keep_lines=True)[1].text == "second line\nsplit" and "WEBVTT" not in text
    s = tmp_path / "a.srt"
    s.write_bytes(("﻿" + SRT_ITALIC).replace("\n", "\r\n").encode("utf-8"))
    text, n = save.srt_text(s)
    assert n == 2 and text == SRT_ITALIC              # SRT kept as written (italics), BOM and CRLF gone
    dest = tmp_path / "out.srt"
    save.write_atomic(dest, text)
    assert dest.read_text(encoding="utf-8") == SRT_ITALIC and [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []
    assert "hdmv_pgs_subtitle" in save.IMAGE_CODECS and "dvd_subtitle" in save.IMAGE_CODECS and "subrip" not in save.IMAGE_CODECS


def make_video(media_dir: Path, dest: Path) -> Path:
    """voz_es.flac + an embedded SRT (spa) + an embedded ASS (eng) in a Matroska file."""
    sub = dest.parent / "embedded.srt"
    sub.write_text(render_srt([Segment(0.5, 3.0, "Bienvenido a MPV-UOS,"), Segment(3.0, 5.5, "el reproductor del futuro."),
                               Segment(6.0, 8.0, "- ¿Vienes?\n- Sí.")]), encoding="utf-8")
    ass = dest.parent / "embedded.ass"
    ass.write_text(ASS, encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media_dir / "voz_es.flac"), "-i", str(sub), "-i", str(ass),
                    "-map", "0:a", "-map", "1:s", "-map", "2:s", "-c:a", "copy", "-c:s:0", "srt", "-c:s:1", "ass",
                    "-metadata:s:s:0", "language=spa", "-metadata:s:s:1", "language=eng", str(dest)], check=True)
    sub.unlink()
    ass.unlink()
    return dest


def wait_job(d, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next((j for j in d.call("jobs.list") if j["id"] == job_id), None)
        if job and job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_save_and_extract_through_mpvd(daemon_env, media_dir, tmp_path):
    d = daemon_env
    fb = tmp_path / "fallback"
    d.extra_env["MPV_UOS_SUBS_SAVE_DIR"] = str(fb)
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    vdir = tmp_path / "videos"
    vdir.mkdir()
    video = make_video(media_dir, vdir / "peli.mkv")

    # 1. embedded text track → SRT in the cache (job), then instant; the language tag comes from the container
    r = d.call("subs.extract", {"path": str(video), "ff_index": 1})
    assert r["codec"] == "subrip" and r["lang"] == "spa" and r["srt"].startswith(str(d.cache_dir))
    if r["status"] == "queued":
        assert wait_job(d, r["job"]["id"])["status"] == "done"
    cues = parse_srt(Path(r["srt"]).read_text(encoding="utf-8"), keep_lines=True)
    assert len(cues) == 3 and cues[2].text == "- ¿Vienes?\n- Sí."
    assert d.call("subs.extract", {"path": str(video), "ff_index": 1})["status"] == "done"
    with pytest.raises(RpcError) as exc:
        d.call("subs.extract", {"path": str(video), "ff_index": 0})          # the audio stream
    assert "no es un subtítulo" in exc.value.message
    with pytest.raises(RpcError) as exc:
        d.call("subs.save", {"path": str(video), "kind": "track", "ff_index": 3, "codec": "hdmv_pgs_subtitle"})
    assert exc.value.code == UNAVAILABLE and "OCR" in exc.value.message and exc.value.data["reason"] == "image"

    # 2. save the embedded track next to the video: peli.es.srt (spa → es), then peli.es (2).srt
    r = d.call("subs.save", {"path": str(video), "kind": "track", "ff_index": 1, "codec": "subrip", "lang": "spa"})
    assert r["status"] == "done" and r["path"] == str(vdir / "peli.es.srt") and r["lines"] == 3 and not r["fallback"]
    assert len(parse_srt((vdir / "peli.es.srt").read_text(encoding="utf-8"))) == 3
    r = d.call("subs.save", {"path": str(video), "kind": "track", "ff_index": 1, "lang": "es"})
    assert r["name"] == "peli.es (2).srt"
    # the ASS track (not extracted yet): queued, then saved by the watcher; FFmpeg turns ASS italics into <i>
    r = d.call("subs.save", {"path": str(video), "kind": "track", "ff_index": 2, "codec": "ass"})
    if r["status"] == "queued":
        d.wait(lambda: (vdir / "peli.en.srt").is_file(), timeout=30)
    else:
        assert r["name"] == "peli.en.srt"
    en = parse_srt((vdir / "peli.en.srt").read_text(encoding="utf-8"), keep_lines=True)
    assert [c.text for c in en] == ["<i>Hola</i>, ¿qué tal?", "- ¿Vienes?\n- Sí."]

    # 3. external VTT (as the selected track) → SRT; translation and resync names when the plain one exists
    vtt = tmp_path / "ext.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    r = d.call("subs.save", {"path": str(video), "kind": "track", "srt": str(vtt), "lang": "fr"})
    assert r["name"] == "peli.fr.srt" and "WEBVTT" not in (vdir / "peli.fr.srt").read_text(encoding="utf-8")
    assert d.call("subs.save", {"path": str(video), "kind": "translation", "srt": str(vtt), "lang": "fr"})["name"] == \
        "peli.fr.ia.srt"
    assert d.call("subs.save", {"path": str(video), "kind": "resync", "srt": str(vtt), "lang": "fr"})["name"] == \
        "peli.fr.resync.srt"
    # the selected track already is peli.fr.srt next to the video: nothing new is written
    r = d.call("subs.save", {"path": str(video), "kind": "track", "srt": str(vdir / "peli.fr.srt"), "lang": "fr"})
    assert r["already"] and r["path"] == str(vdir / "peli.fr.srt")

    # 4. URL or unwritable folder → fallback folder, named after the title
    r = d.call("subs.save", {"path": "https://example.com/v/x.mp4", "title": "Charla: IA local", "kind": "translation",
                             "srt": str(vtt), "lang": "en"})
    assert r["fallback"] and Path(r["path"]) == fb / "Charla IA local.en.srt" and Path(r["path"]).is_file()

    # 5. AI track: unfinished → partial + coverage; allow_partial saves it; a finished one is saved and remembered
    segs = [{"start": 0.5, "end": 3.0, "text": "Bienvenido a MPV-UOS,"},
            {"start": 3.0, "end": 5.5, "text": "el reproductor del futuro."}]
    part = d.call("asr.inject", {"path": str(video), "segments": segs, "duration": 70.0, "done_fraction": 0.34})
    assert not part["complete"]
    r = d.call("subs.save", {"path": str(video), "kind": "ai"})
    assert r["status"] == "partial" and 0.3 < r["coverage"] < 0.5 and r["lang"] == "es"
    r = d.call("subs.save", {"path": str(video), "kind": "ai", "allow_partial": True})
    assert r["status"] == "done" and r["partial"] and r["name"] == "peli.es.ia.srt"
    full = d.call("asr.inject", {"path": str(video), "segments": segs, "duration": 12.0})
    assert full["complete"]
    r = d.call("subs.save", {"path": str(video), "kind": "ai"})
    assert r["status"] == "done" and not r["partial"] and r["name"] == "peli.es.ia (2).srt" and r["lines"] == 2
    again = d.call("subs.save", {"path": str(video), "kind": "ai"})
    assert again["already"] and again["path"] == r["path"]
    saved = d.call("asr.status", {"id": full["id"]})["saved"]
    assert saved and saved[-1]["path"] == r["path"] and saved[-1]["complete"] is True
    assert sorted(p.name for p in vdir.iterdir()) == sorted([
        "peli.mkv", "peli.es.srt", "peli.es (2).srt", "peli.en.srt", "peli.fr.srt", "peli.fr.ia.srt",
        "peli.fr.resync.srt", "peli.es.ia.srt", "peli.es.ia (2).srt"])


@pytest.mark.skipif(not whisper_available(), reason="whisper.cpp not vendored (tools/vendor_whisper.sh)")
def test_complete_then_save_ai_track(daemon_env, media_dir, tmp_path):
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    video = tmp_path / "charla.flac"
    shutil.copy(media_dir / "voz_es.flac", video)
    t = d.call("asr.start", {"path": str(video), "language": "es", "model": asr_model(), "chunk_seconds": 6.0})
    d.call("asr.stop", {"id": t["id"]})
    t = d.call("asr.status", {"id": t["id"]})
    if t["complete"]:
        pytest.skip("transcribed before it could be stopped")
    r = d.call("subs.save", {"path": str(video), "kind": "ai"})
    assert r["status"] == "partial"
    r = d.call("subs.save", {"path": str(video), "kind": "ai", "complete": True})
    assert r["status"] == "waiting" and r["task"] == t["id"]
    d.wait(lambda: (tmp_path / "charla.es.srt").is_file(), timeout=240, interval=0.5)
    cues = parse_srt((tmp_path / "charla.es.srt").read_text(encoding="utf-8"))
    st = d.call("asr.status", {"id": t["id"]})
    assert st["complete"] and len(cues) == st["cues"] >= 1
    assert st["saved"][-1] == {**st["saved"][-1], "path": str(tmp_path / "charla.es.srt"), "complete": True}
