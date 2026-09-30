"""Intro/credits detection on the synthetic series (three episodes sharing an 8 s intro and a 6 s credits tail):
fingerprint matching, edge snapping, the intro.* service through mpvd (analysis → cache; segments.json only on request),
and mu-intro in headless mpv (segments picked up, skip key, credits → next episode). More cases in test_intro_season.py."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

from mpvd.intro.detect import cut_points, edges, snap
from mpvd.intro.fingerprint import Fingerprint, fingerprint_window, match
from mpvd.intro.service import siblings
from tests.conftest import start_mpv

pytestmark = pytest.mark.skipif(shutil.which("fpcalc") is None, reason="fpcalc (chromaprint) not installed")


def test_siblings_and_snap(media_dir, tmp_path):
    sibs = siblings(media_dir / "serie" / "ep02.mkv")
    assert sorted(s.name for s in sibs) == ["ep01.mkv", "ep03.mkv"]
    assert siblings(media_dir / "video30.mkv")  # other videos in the fixtures root
    assert siblings(media_dir / "voz_es.flac") == []  # audio files are never analysed as episodes
    det = {"silence": [(31.59, 32.17)], "black": [(0.06, 1.58), (31.62, 38.02)]}
    assert edges(det) == [0.06, 1.58, 31.59, 31.62, 32.17, 38.02]
    starts, ends = cut_points(det)
    assert starts == [0.06, 31.59, 31.62] and ends == [1.58, 32.17, 38.02]
    # an intro start lands where black ends; a credits end where black starts (or, failing that, any cut)
    assert snap(0.13, ends, 2.0, starts) == 1.58 and snap(9.86, starts, 2.0, ends) == 9.86
    assert snap(33.3, ends, 2.0, starts) == 32.17 and snap(37.7, starts, 2.0, ends) == 38.02


def test_fingerprint_match_between_episodes(media_dir, tmp_path):
    serie = media_dir / "serie"

    async def go():
        a = await fingerprint_window(str(serie / "ep01.mkv"), 0, 38, tmp_path)
        b = await fingerprint_window(str(serie / "ep02.mkv"), 0, 38, tmp_path)
        return a, b

    a, b = asyncio.run(go())
    assert 7 < a.rate < 9 and len(a.values) > 250
    runs = match(a, b, min_seconds=4)
    assert runs, "the shared intro must be found"
    intro = runs[0]
    assert intro.a_start < 2.0 and 9.0 < intro.a_end < 10.5 and abs(intro.a_start - intro.b_start) < 0.5
    credits = [r for r in runs if r.a_start > 25]
    assert credits and credits[0].a_end > 37
    # different bodies never match; a fingerprint against itself matches everywhere
    body_a = Fingerprint(a.values[90:220], 130 / a.rate, 12.0)
    body_b = Fingerprint(b.values[90:220], 130 / b.rate, 12.0)
    assert match(body_a, body_b, min_seconds=4) == []
    assert match(a, a, min_seconds=4)[0].length > 30
    assert Fingerprint.from_dict(a.to_dict()).values == a.values


def test_intro_service_analysis_cache_and_export(daemon_env, media_dir, tmp_path):
    d = daemon_env
    # work on a copy: the on-demand export writes segments.json next to the videos
    serie = tmp_path / "serie"
    shutil.copytree(media_dir / "serie", serie, ignore=shutil.ignore_patterns(".mpv-uos", "segments.json"))
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    assert d.call("capabilities")["services"]["intro"] is True
    r = d.call("intro.segments", {"path": str(serie / "ep01.mkv")})
    assert r["status"] == "analyzing" and sorted(r["siblings"]) == ["ep02.mkv", "ep03.mkv"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        r = d.call("intro.segments", {"path": str(serie / "ep01.mkv")})
        if r["status"] == "done":
            break
        time.sleep(0.5)
    assert r["status"] == "done", r
    assert r["intro"] and abs(r["intro"][0] - 1.5) < 1.0 and abs(r["intro"][1] - 9.5) < 1.0, r["intro"]
    assert r["credits"] and abs(r["credits"][0] - 32.0) < 1.5 and r["credits"][1] > 37.5, r["credits"]
    assert r["matches"]["intro"] == 2 and r["sources"] == {"intro": "auto", "credits": "auto"}
    assert r["next"] == str(serie / "ep02.mkv")
    # nothing is written next to the videos until the user asks for it
    assert not (serie / ".mpv-uos").exists() and not (serie / "segments.json").exists()
    ex = d.call("intro.export", {"path": str(serie / "ep01.mkv")})
    assert ex["file"] == str(serie / "segments.json") and "ep01.mkv" in ex["entries"]
    exported = json.loads((serie / "segments.json").read_text(encoding="utf-8"))
    types = {s["type"]: s for s in exported["ep01.mkv"]["segments"]}
    assert set(types) == {"intro", "credits"} and types["intro"]["end"] == r["intro"][1]
    assert types["intro"]["Type"] == "Intro" and types["credits"]["Type"] == "Outro"
    assert types["intro"]["EndTicks"] == int(r["intro"][1] * 10_000_000)
    # the second episode reuses the cached fingerprints and finishes quickly, synchronously
    t0 = time.monotonic()
    r2 = d.call("intro.analyze", {"path": str(serie / "ep02.mkv"), "wait": True}, timeout=120)
    assert r2["intro"] and abs(r2["intro"][1] - 9.5) < 1.0 and time.monotonic() - t0 < 60
    # a lonely file has nothing to compare with
    alone = d.call("intro.segments", {"path": str(media_dir / "video30.mkv")})
    assert alone["status"] in ("done", "analyzing")


def test_mu_intro_skip_and_next_episode(daemon_env, media_dir, tmp_path):
    d = daemon_env
    serie = tmp_path / "serie"
    shutil.copytree(media_dir / "serie", serie, ignore=shutil.ignore_patterns(".mpv-uos", "segments.json"))
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-intro-poll_seconds=0.2,"
                                  "mu-intro-countdown_seconds=0",
                                  "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(serie / "ep01.mkv"))
        h.command("loadfile", str(serie / "ep02.mkv"), "append")
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=20)
        st = h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("status") in ("analyzing", "done"), timeout=30)
        st = h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("status") == "done" and v.get("segments"),
                             timeout=180)
        kinds = {s["type"]: s for s in st["segments"]}
        assert set(kinds) == {"intro", "credits"}
        # the menu lists both segments and stays open while toggling automatic skipping
        h.command("script-binding", "mu_intro/intro-menu")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-intro", timeout=10)
        h.command("script-message-to", "mu_intro", "mu-intro-set", "auto_skip_credits", "yes")
        h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("auto_skip_credits") is True, timeout=10)
        h.command("script-message-to", "mu_intro", "mu-intro-set", "auto_skip_credits", "no")
        h.command("script-message-to", "uosc", "close-menu", "mu-intro")
        h.wait_property("user-data/uosc/menu/type", lambda v: v != "mu-intro", timeout=10)
        # inside the intro → current = intro; alt+k jumps to its end
        h.command("seek", kinds["intro"]["start"] + 1.0, "absolute")
        h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("current") == "intro", timeout=10)
        h.command("script-binding", "mu_intro/skip")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - kinds["intro"]["end"]) < 0.6, timeout=10)
        h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("current") == "", timeout=10)
        # inside the credits → skip goes to the next playlist item
        h.command("seek", kinds["credits"]["start"] + 1.0, "absolute")
        h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("current") == "credits", timeout=10)
        h.command("script-message-to", "mu_intro", "mu-intro-skip", "credits")
        h.wait_property("path", lambda v: bool(v) and v.endswith("ep02.mkv"), timeout=20)
        # ep02 gets its segments too (cached fingerprints of ep01/ep03 → fast) and auto-skip works when enabled
        h.command("script-message-to", "mu_intro", "mu-intro-set", "auto_skip_intro", "yes")
        st = h.wait_property("user-data/mu/intro", lambda v: bool(v) and v.get("status") == "done" and v.get("segments")
                             and v.get("path", "").endswith("ep02.mkv"), timeout=180)
        intro = next(s for s in st["segments"] if s["type"] == "intro")
        h.command("seek", intro["start"] + 0.5, "absolute")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= intro["end"] - 0.6, timeout=10)
        assert h.script_errors() == []
    finally:
        h.stop()
