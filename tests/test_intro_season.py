"""Intro/credits beyond "one folder with the whole season": episodes in sibling folders, broken / silent neighbours,
several versions of the same video, sanity checks, the cost-bounded edge refinement, manual marks carried through the
season, season analysis, and mu-intro's notices / countdown / next episode in headless mpv.

Media are built once from the fixture series (tests/fixtures/media/serie/ep01..03: 1.5 s black, the same 8 s intro,
a different body, the same 6 s credits)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mpvd.asr.audio import AudioError
from mpvd.cache import ArtifactCache as Cache
from mpvd.intro import detect
from mpvd.intro import service as svc
from mpvd.intro.episodes import episode_info, next_episode, parse_episode, season_episodes
from mpvd.intro.fingerprint import Run
from mpvd.intro.service import IntroService
from mpvd.jobs import JobQueue
from tests.conftest import start_mpv

pytestmark = pytest.mark.skipif(shutil.which("fpcalc") is None, reason="fpcalc (chromaprint) not installed")

FF = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-nostdin"]


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


@pytest.fixture(scope="module")
def intro_media(media_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("intro-media")
    serie = media_dir / "serie"
    named = base / "named"
    for n in (1, 2, 3):
        d = named / f"Serie Prueba 1x0{n}"
        d.mkdir(parents=True)
        shutil.copy(serie / f"ep0{n}.mkv", d / f"Serie Prueba 1x0{n}.mkv")
    # 1x04: a 5 s cold open (other picture and sound) before episode 2
    d = named / "Serie Prueba 1x04"
    d.mkdir()
    subprocess.run([*FF, "-i", str(media_dir / "video30.mkv"), "-i", str(serie / "ep02.mkv"), "-filter_complex",
                    "[0:v]trim=0:5,setpts=PTS-STARTPTS,scale=640:360,fps=25,format=yuv420p[v0];"
                    "[0:a]atrim=0:5,asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a0];"
                    "[1:v]fps=25,format=yuv420p,setpts=PTS-STARTPTS[v1];[1:a]aresample=48000,asetpts=PTS-STARTPTS[a1];"
                    "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "128k", str(d / "Serie Prueba 1x04.mkv")],
                   check=True)
    # other series / other season next to it: never neighbours
    (named / "Otra Serie 1x02").mkdir()
    shutil.copy(serie / "ep03.mkv", named / "Otra Serie 1x02" / "Otra Serie 1x02.mkv")
    (named / "Serie Prueba 2x01").mkdir()
    shutil.copy(serie / "ep03.mkv", named / "Serie Prueba 2x01" / "Serie Prueba 2x01.mkv")
    # broken neighbours: no audio track, garbage, then two good ones
    broken = base / "broken"
    broken.mkdir()
    shutil.copy(serie / "ep01.mkv", broken / "Serie Rota 1x01.mkv")
    subprocess.run([*FF, "-i", str(serie / "ep02.mkv"), "-an", "-c", "copy", str(broken / "Serie Rota 1x02.mkv")], check=True)
    (broken / "Serie Rota 1x03.mkv").write_bytes(os.urandom(200_000))
    shutil.copy(serie / "ep02.mkv", broken / "Serie Rota 1x04.mkv")
    shutil.copy(serie / "ep03.mkv", broken / "Serie Rota 1x05.mkv")
    # three versions of the same video
    versions = base / "versions"
    versions.mkdir()
    shutil.copy(serie / "ep01.mkv", versions / "master.mkv")
    for name, rate in (("short-final.mkv", "96k"), ("short-final-instagram.mkv", "64k")):
        subprocess.run([*FF, "-i", str(serie / "ep01.mkv"), "-c:v", "copy", "-c:a", "aac", "-b:a", rate,
                        str(versions / name)], check=True)
    # a fifth episode with its own bytes (another content key) for "episodes that appear later"
    extra = base / "extra"
    extra.mkdir()
    subprocess.run([*FF, "-i", str(serie / "ep03.mkv"), "-c:v", "copy", "-c:a", "aac", "-b:a", "112k",
                    str(extra / "Serie Prueba 1x05.mkv")], check=True)
    solo = base / "solo"
    solo.mkdir()
    shutil.copy(serie / "ep01.mkv", solo / "pelicula.mkv")
    return base


# -- A: names and neighbours -------------------------------------------------------------------------


def test_parse_episode_names():
    cases = {
        "Don Matteo 1x02 ; Aroma De Café": ("don matteo", 1, 2),
        "El Padre Brown [HDTV][Cap.1x02][Castellano][www.descargas2020.com]": ("el padre brown", 1, 2),
        "El Padre Brown - Temporada 1 [HDTV][Cap.101_110][Castellano]": ("el padre brown", 1, 1),
        "Show.Name.S02E10.1080p.x264": ("show name", 2, 10),
        "La Casa de Papel - Temporada 2 Capítulo 5": ("la casa de papel", 2, 5),
        "Friends (1994) - S03E14 - The One": ("friends", 3, 14),
        "Café Solo s1.e3": ("cafe solo", 1, 3),
        "ep02": ("", None, 2),
    }
    for name, want in cases.items():
        info = parse_episode(name)
        assert info is not None and (info.title, info.season, info.episode) == want, (name, info)
    for name in ("master", "short-final-instagram", "video30", "Vacaciones 1920x1080", "Pelicula (2019)"):
        assert parse_episode(name) is None, name
    # the folder completes a file without a marker
    assert episode_info(Path("/x/Serie 1x03 Titulo/video.mp4")) == episode_info(Path("/x/Serie 1x03.mp4"))


def test_season_episodes_in_sibling_folders(tmp_path):
    root = tmp_path / "Descargas"
    eps = [_touch(root / f"Don Matteo 1x0{n} Titulo {n}" / f"Don Matteo 1x0{n} Titulo {n}.mp4") for n in range(1, 7)]
    _touch(root / "Don Matteo 1x03 Titulo 3" / "sample.txt")
    _touch(root / "Don Matteo 2x01 Otra" / "Don Matteo 2x01 Otra.mp4")          # other season
    _touch(root / "El Padre Brown 1x02" / "El Padre Brown 1x02.avi")             # other series
    _touch(root / "Don Matteo 1x07 Suelto.mkv")                                  # loose in the parent
    _touch(root / ".oculto" / "Don Matteo 1x08.mp4")                             # hidden folders are skipped
    _touch(root / "Don Matteo 1x05 Titulo 5" / "Don Matteo 1x05 Titulo 5.srt")   # not a video
    got = season_episodes(eps[2])
    assert [p.name for p in got] == ["Don Matteo 1x04 Titulo 4.mp4", "Don Matteo 1x02 Titulo 2.mp4",
                                     "Don Matteo 1x05 Titulo 5.mp4", "Don Matteo 1x01 Titulo 1.mp4",
                                     "Don Matteo 1x06 Titulo 6.mp4", "Don Matteo 1x07 Suelto.mkv"]
    assert next_episode(eps[2]) == eps[3]
    assert next_episode(eps[5]) == root / "Don Matteo 1x07 Suelto.mkv"
    assert next_episode(root / "Don Matteo 1x07 Suelto.mkv") is None
    assert svc.siblings(eps[2]) == got[:3]


def test_season_episodes_same_folder_and_fallbacks(tmp_path):
    flat = tmp_path / "flat"
    a1, a2, a3 = (_touch(flat / f"Serie A S01E0{n}.mkv") for n in (1, 2, 3))
    _touch(flat / "Otra Cosa S01E01.mkv")
    _touch(flat / "Serie A S02E01.mkv")
    _touch(flat / "notas.mp3")
    assert season_episodes(a2) == [a3, a1]
    # names without a series title: every other video of the folder (legacy behaviour)
    legacy = tmp_path / "legacy"
    e1, e2, e3 = (_touch(legacy / f"ep0{n}.mkv") for n in (1, 2, 3))
    assert set(season_episodes(e2)) == {e1, e3} and next_episode(e2) == e3
    versions = tmp_path / "versions"
    m, s1, s2 = (_touch(versions / n) for n in ("master.mp4", "short-final.mp4", "short-final-instagram.mp4"))
    assert set(season_episodes(s1)) == {m, s2} and next_episode(s1) is None
    # a lonely film and a song have no neighbours
    film = _touch(tmp_path / "pelis" / "Pelicula (2019).mkv")
    _touch(tmp_path / "pelis-otra" / "Otra Pelicula.mkv")
    assert season_episodes(film) == []
    assert season_episodes(_touch(tmp_path / "musica" / "a.flac")) == []


# -- C: consensus and sanity checks ------------------------------------------------------------------


def _run(a: float, b: float) -> Run:
    return Run(a, b, a, b, 1.0)


def test_consensus_support_and_sanity_checks():
    # a "previously on" clip that only matches one neighbour loses against the intro all of them share
    runs = [[_run(0, 40), _run(60, 90)], [_run(60.5, 90.2)], [_run(59.8, 89.9)]]
    seg, support = IntroService._consensus(runs, 5.0, pick_last=False)
    assert support == 3 and abs(seg[0] - 60) < 0.6 and abs(seg[1] - 90) < 0.6
    reasons: dict[str, str] = {}
    # intro too late (starts after 40 %) and credits too early (end before 60 %)
    intro, credits = IntroService._sanity([55, 70], 2, [39, 58], 2, 102.8, reasons)
    assert intro is None and credits is None and set(reasons) == {"intro", "credits"}
    # overlapping: the one more neighbours agree on wins
    reasons = {}
    intro, credits = IntroService._sanity([10, 50], 3, [30, 90], 1, 100.0, reasons)
    assert intro == [10, 50] and credits is None and reasons["credits"] == "se solapaba con la intro"
    reasons = {}
    intro, credits = IntroService._sanity([10, 50], 1, [30, 99], 2, 100.0, reasons)
    assert intro is None and credits == [30, 99]
    assert svc.HEAD_SECONDS == 600.0
    assert svc._pieces([_run(0, 5), _run(0.2, 5.1), _run(10, 20)]) == 2
    assert svc._covered([_run(0, 10), _run(5, 15), _run(20, 25)]) == 20


# -- D: edge refinement decodes a few seconds around each boundary ----------------------------------------


def test_refine_edges_windows(monkeypatch, media_dir):
    calls: list[tuple[float, float]] = []

    async def fake(src: str, start: float, length: float, **kw: Any) -> dict[str, list[tuple[float, float]]]:
        calls.append((round(start, 2), round(length, 2)))
        return {"silence": [(start + 1, start + 2)], "black": []}

    monkeypatch.setattr(detect, "detect_edges", fake)
    det = asyncio.run(detect.refine_edges("x.mkv", [0.0, 80.0, 2700.0, 2712.0], duration=2715.0))
    # [0,6] alone; 80±6; the two credits edges share one run clamped to the duration
    assert calls == [(0.0, 6.0), (74.0, 12.0), (2694.0, 21.0)]
    assert len(det["silence"]) == 3
    total = sum(length for _, length in calls)
    assert total < 45  # a fixed cost, whatever the length of the segments
    monkeypatch.undo()
    # real run on the fixture: the black/silent lead-in ends at ~1.5 s
    det = asyncio.run(detect.refine_edges(str(media_dir / "serie" / "ep01.mkv"), [1.0], duration=38.0))
    starts, ends = detect.cut_points(det)
    assert any(abs(e - 1.5) < 0.3 for e in ends), det


# -- B: robustness (in-process service with a fake session) ------------------------------------------------


class FakeSession:
    id = "s1"
    connected = True

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def push_event(self, target: str, key: str, status: str, payload: dict[str, Any], min_interval: float = 0.25,
                   final: bool = False) -> None:
        self.events.append((target, status, payload))


def _service(tmp_path: Path) -> tuple[IntroService, Any]:
    session = FakeSession()
    server = SimpleNamespace(
        settings=SimpleNamespace(cache_dir=tmp_path / "cache", data_dir=tmp_path / "data"),
        cache=Cache(tmp_path / "cache"), jobs=JobQueue(workers=2),
        sessions=SimpleNamespace(get=lambda sid: session if sid == session.id else None))
    return IntroService(server), server


def test_broken_neighbours_and_independent_windows(intro_media, tmp_path):
    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        service, server = _service(tmp_path)
        await server.jobs.start()
        try:
            ep1 = intro_media / "broken" / "Serie Rota 1x01.mkv"
            res = await service.analyze(ep1)
            # a failing tail window must not throw away a good head (and vice versa)
            real = service._fingerprint
            mine: list[float] = []

            async def no_tail(path: Path, key: str, start: float, length: float) -> Any:
                if path == ep1:
                    mine.append(start)
                    if len(mine) == 2:  # head first, then tail (same window on a 38 s file)
                        raise AudioError("ffmpeg failed (1): simulated")
                return await real(path, key, start, length)

            service._fingerprint = no_tail  # type: ignore[method-assign]
            res2 = await service.analyze(ep1)
            return res, res2
        finally:
            await server.jobs.stop()

    res, res2 = asyncio.run(go())
    assert res["intro"] and abs(res["intro"][1] - 9.5) < 1.0 and res["credits"], res
    assert sorted(res["siblings"]) == ["Serie Rota 1x04.mkv", "Serie Rota 1x05.mkv"]
    skipped = " | ".join(res["skipped"])
    assert "Serie Rota 1x02.mkv" in skipped and "Serie Rota 1x03.mkv" in skipped
    assert res2["intro"] and res2["credits"] is None
    assert res2["reasons"]["credits"].startswith("sin huella del final")


def test_failed_job_notifies_mpv(intro_media, tmp_path):
    folder = tmp_path / "rota"
    folder.mkdir()
    bad = folder / "Serie Mala 1x01.mkv"
    bad.write_bytes(os.urandom(100_000))
    shutil.copy(intro_media / "broken" / "Serie Rota 1x04.mkv", folder / "Serie Mala 1x02.mkv")

    async def go() -> tuple[FakeSession, Any]:
        service, server = _service(tmp_path)
        await server.jobs.start()
        try:
            job = service.submit(bad, "mu_intro", "s1")
            await job.wait(60)
            return server.sessions.get("s1"), (job, service.failure(bad))
        finally:
            await server.jobs.stop()

    session, (job, failure) = asyncio.run(go())
    assert job.status.value == "failed"
    finals = [p for t, s, p in session.events if p.get("event") == "intro"]
    assert finals and finals[-1]["result"]["status"] == "error" and finals[-1]["result"]["reason"]
    assert "duración desconocida" in finals[-1]["result"]["reason"]
    assert failure is not None and failure["status"] == "error"


def test_versions_of_the_same_video(intro_media, tmp_path):
    async def go() -> dict[str, Any]:
        service, server = _service(tmp_path)
        await server.jobs.start()
        try:
            return await service.analyze(intro_media / "versions" / "short-final.mkv")
        finally:
            await server.jobs.stop()

    res = asyncio.run(go())
    assert res["intro"] is None and res["credits"] is None
    assert res["reason"] == "parecen versiones del mismo vídeo"
    assert set(res["same_video"]) == {"master.mkv", "short-final-instagram.mkv"}


# -- A + G + F(4): through mpvd: sibling folders, prefetch, season, marks ---------------------------------


def _wait(pred, timeout: float = 120.0, interval: float = 0.3) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError("condition not met")


def test_season_folders_prefetch_season_and_marks(daemon_env, intro_media, tmp_path):
    d = daemon_env
    named = tmp_path / "named"
    shutil.copytree(intro_media / "named", named)
    ep = {n: named / f"Serie Prueba 1x0{n}" / f"Serie Prueba 1x0{n}.mkv" for n in (1, 2, 3, 4)}
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    r = d.call("intro.segments", {"path": str(ep[1])})
    assert r["status"] == "analyzing" and r["episodes"] == 3 and r["next"] == str(ep[2])
    assert sorted(r["siblings"]) == ["Serie Prueba 1x02.mkv", "Serie Prueba 1x03.mkv", "Serie Prueba 1x04.mkv"]
    jobs = d.call("jobs.list")
    assert any(j["name"] == "intro.prefetch" and j["meta"]["episodes"] == 4 for j in jobs)
    r = _wait(lambda: (x := d.call("intro.segments", {"path": str(ep[1])}))["status"] == "done" and x)
    assert r["intro"] and abs(r["intro"][0] - 1.5) < 1.0 and abs(r["intro"][1] - 9.5) < 1.0, r
    # season analysis: every episode ends up cached; the cold-open one finds its intro 5 s later
    s = d.call("intro.season", {"path": str(ep[1])})
    assert s["episodes"] == 4
    _wait(lambda: all(d.call("intro.segments", {"path": str(ep[n]), "analyze": False})["status"] == "done"
                      for n in (2, 3, 4)))
    r4 = d.call("intro.segments", {"path": str(ep[4]), "analyze": False})
    assert r4["intro"] and abs(r4["intro"][0] - 6.5) < 1.0, r4
    # a manual mark on 1x01 is carried to the other episodes, shifted where the intro starts later
    m = d.call("intro.mark", {"path": str(ep[1]), "kind": "intro", "start": 1.0, "end": 9.0})
    assert m["intro"] == [1.0, 9.0] and m["sources"]["intro"] == "manual" and m["propagation"]

    def carried(n: int, want: float) -> bool:
        x = d.call("intro.segments", {"path": str(ep[n]), "analyze": False})
        return x["sources"].get("intro") == "manual" and abs(x["intro"][0] - want) < 0.4 and x

    x2 = _wait(lambda: carried(2, 1.0), timeout=60)
    x4 = _wait(lambda: carried(4, 6.0), timeout=60)
    assert abs(x2["intro"][1] - 9.0) < 0.4 and abs(x4["intro"][1] - 14.0) < 0.4
    assert x4["manual_from"]["intro"] == "Serie Prueba 1x01.mkv"
    # an episode that appears later takes the mark too (template applied during its analysis)
    (named / "Serie Prueba 1x05").mkdir()
    ep5 = named / "Serie Prueba 1x05" / "Serie Prueba 1x05.mkv"
    shutil.copy(intro_media / "extra" / "Serie Prueba 1x05.mkv", ep5)
    r5 = d.call("intro.analyze", {"path": str(ep5), "wait": True}, timeout=120)
    assert r5["sources"]["intro"] == "manual" and abs(r5["intro"][0] - 1.0) < 0.4
    # forgetting the mark removes the propagated ones too
    u = d.call("intro.unmark", {"path": str(ep[1])})
    assert u["removed"] >= 3 and u["sources"]["intro"] == "auto"
    assert d.call("intro.segments", {"path": str(ep[4]), "analyze": False})["sources"]["intro"] == "auto"
    # nothing was written next to the videos
    assert not list(named.rglob("segments.json")) and not list(named.rglob(".mpv-uos"))


# -- F: mu-intro in headless mpv -----------------------------------------------------------------------------


def _intro(h: Any, pred, timeout: float = 30.0) -> dict[str, Any]:
    return h.wait_property("user-data/mu/intro", lambda v: bool(v) and pred(v), timeout=timeout)


def test_mu_intro_lonely_notice_progress_and_manual_mark(daemon_env, intro_media, tmp_path):
    d = daemon_env
    solo = tmp_path / "solo"
    shutil.copytree(intro_media / "solo", solo)
    named = tmp_path / "named"
    shutil.copytree(intro_media / "named", named)
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-intro-poll_seconds=0.2",
                                  "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(solo / "pelicula.mkv"))
        st = _intro(h, lambda v: v.get("status") == "done" and v.get("path", "").endswith("pelicula.mkv"))
        assert st["local_video"] is True and st["segments"] == [] and st["reason"] == "no hay otros episodios para comparar"
        want = "Saltar intro: no hay otros episodios para comparar · alt+j"
        h.wait_property("user-data/mu/intro-osd", lambda v: v == want, timeout=10)
        h.command("set_property", "user-data/mu/intro-osd", "")
        h.command("script-binding", "mu_intro/skip")
        h.wait_property("user-data/mu/intro-osd", lambda v: v == want, timeout=10)
        # a film has no detectable intro, but it can be marked by hand
        h.command("seek", 2.0, "absolute")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 2.0) < 0.3, timeout=10)
        h.command("script-binding", "mu_intro/intro-mark-start")
        _intro(h, lambda v: abs(v.get("pending_intro", -1) - 2.0) < 0.3, timeout=10)
        h.command("seek", 9.0, "absolute")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 9.0) < 0.3, timeout=10)
        h.command("script-binding", "mu_intro/intro-mark-end")
        st = _intro(h, lambda v: bool(v.get("segments")), timeout=20)
        seg = st["segments"][0]
        assert seg["type"] == "intro" and seg["source"] == "manual" and abs(seg["start"] - 2.0) < 0.3
        h.command("seek", 4.0, "absolute")
        _intro(h, lambda v: v.get("current") == "intro" and v.get("indicator") == "intro", timeout=10)
        # while an analysis waits in the queue, alt+k tells how far it is
        for _ in range(8):
            d.call("jobs.sleep", {"seconds": 4, "priority": "interactive"})
        h.command("loadfile", str(named / "Serie Prueba 1x02" / "Serie Prueba 1x02.mkv"))
        _intro(h, lambda v: v.get("status") == "analyzing" and v.get("path", "").endswith("1x02.mkv"))
        h.command("script-binding", "mu_intro/skip")
        h.wait_property("user-data/mu/intro-osd", lambda v: isinstance(v, str) and v.startswith("Analizando… ")
                        and v.endswith(" %"), timeout=10)
        _intro(h, lambda v: v.get("status") == "done" and bool(v.get("segments")), timeout=180)
        assert h.script_errors() == []
    finally:
        h.stop()


def test_mu_intro_countdown_escape_and_next_episode_in_other_folder(daemon_env, intro_media, tmp_path):
    d = daemon_env
    named = tmp_path / "named"
    shutil.copytree(intro_media / "named", named)
    ep1 = named / "Serie Prueba 1x01" / "Serie Prueba 1x01.mkv"
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-intro-poll_seconds=0.2,"
                                  "mu-intro-auto_skip_credits=yes", "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", str(ep1))
        st = _intro(h, lambda v: v.get("status") == "done" and bool(v.get("segments")), timeout=180)
        assert st["auto_skip_credits"] is True and st["auto_skip_intro"] is False
        assert st["next"].endswith("Serie Prueba 1x02.mkv")
        kinds = {s["type"]: s for s in st["segments"]}
        # automatic skip of the credits: a countdown (paused: it waits) that Esc cancels
        h.command("seek", kinds["credits"]["start"] + 0.5, "absolute")
        _intro(h, lambda v: v.get("countdown_kind") == "credits" and v.get("countdown") == 3, timeout=10)
        bindings = h.get("input-bindings")
        assert any(b.get("key") == "ESC" and "mu-intro-cancel" in str(b.get("cmd")) for b in bindings)
        h.command("keypress", "ESC")
        _intro(h, lambda v: v.get("countdown_kind") == "", timeout=10)
        h.wait_property("user-data/mu/intro-osd", lambda v: v == "Salto automático cancelado", timeout=10)
        bindings = h.get("input-bindings")
        assert not any("mu-intro-cancel" in str(b.get("cmd")) for b in bindings)
        # automatic skip of the intro with a 1 s countdown while playing
        h.command("script-message-to", "mu_intro", "mu-intro-set", "countdown_seconds", "1")
        h.command("script-message-to", "mu_intro", "mu-intro-set", "auto_skip_intro", "yes")
        h.command("seek", kinds["intro"]["start"] + 0.3, "absolute")
        h.command("set_property", "pause", False)
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= kinds["intro"]["end"] - 0.6, timeout=10)
        h.command("set_property", "pause", True)
        # no playlist: skipping the credits opens the next episode found in the sibling folder
        assert h.get("playlist-count") == 1
        h.command("script-message-to", "mu_intro", "mu-intro-skip", "credits")
        h.wait_property("path", lambda v: bool(v) and v.endswith("Serie Prueba 1x02.mkv"), timeout=20)
        assert h.get("playlist-count") == 2
        assert h.script_errors() == []
    finally:
        h.stop()


def test_mu_intro_error_is_reported_not_stuck(daemon_env, intro_media, tmp_path):
    """A job that fails (here: the file vanishes while queued) ends in status "error" with a notice, never "analyzing"."""
    d = daemon_env
    named = tmp_path / "named"
    shutil.copytree(intro_media / "named", named)
    ep3 = named / "Serie Prueba 1x03" / "Serie Prueba 1x03.mkv"
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1",
                                  "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        for _ in range(6):
            d.call("jobs.sleep", {"seconds": 3, "priority": "interactive"})
        h.command("loadfile", str(ep3))
        _intro(h, lambda v: v.get("status") == "analyzing" and v.get("path", "").endswith("1x03.mkv"))
        ep3.rename(ep3.with_name("renombrado.mkv"))  # mpv keeps its open file; mpvd no longer finds it
        st = _intro(h, lambda v: v.get("status") == "error", timeout=60)
        assert st["reason"] and st["segments"] == []
        h.wait_property("user-data/mu/intro-osd", lambda v: isinstance(v, str)
                        and v.startswith("Saltar intro: error al analizar") and v.endswith("· alt+j"), timeout=10)
        assert h.script_errors() == []
    finally:
        h.stop()
