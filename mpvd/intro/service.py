"""``intro.*``: find the intro and the credits of an episode by comparing its audio with the other episodes of the same
folder (Chromaprint runs, ``mpvd/intro/fingerprint.py``), snap the boundaries to silence/black cuts, cache per file and
export a ``segments.json`` next to the videos (``<folder>/.mpv-uos/segments.json``)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import probe_duration
from mpvd.hashing import file_hash
from mpvd.intro.detect import cut_points, detect_edges, snap
from mpvd.intro.fingerprint import Fingerprint, FingerprintError, fingerprint_window, match
from mpvd.jobs import Job, Priority
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.intro")

ARTIFACT_FP = "chromaprint"
ARTIFACT_SEG = "segments"
VERSION = "1"
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv"}
HEAD_SECONDS = 300.0        # window searched for the intro
TAIL_SECONDS = 300.0        # window searched for the credits
MIN_INTRO = 5.0
MIN_CREDITS = 3.0           # chromaprint needs ~1.5 s of context: short tails come out shorter
MAX_SIBLINGS = 3
SNAP_TOLERANCE = 2.0
EP_RE = re.compile(r"(?i)(?:s(\d{1,2})\s*e(\d{1,3})|(\d{1,2})x(\d{1,3})|\bep?\.?\s*(\d{1,3})\b|\b(\d{1,3})\b)")


def siblings(path: Path, limit: int = MAX_SIBLINGS) -> list[Path]:
    """Other video files in the same folder, nearest episode numbers first (audio files are never episodes)."""
    if path.suffix.lower() not in VIDEO_EXTS or not path.parent.is_dir():
        return []
    cands = [p for p in path.parent.iterdir()
             if p.is_file() and p != path and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")]

    def epnum(p: Path) -> int:
        m = EP_RE.search(p.stem)
        if not m:
            return 10**6
        groups = [g for g in m.groups() if g]
        return int(groups[-1])

    mine = epnum(path)
    cands.sort(key=lambda p: (abs(epnum(p) - mine), p.name))
    return cands[:limit]


class IntroService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.tmp_dir = server.settings.cache_dir / "tmp" / "intro"
        self.jobs: dict[str, Job] = {}      # key -> running analysis

    # -- fingerprints (cached per file + window) ------------------------------------------------

    async def _fingerprint(self, path: Path, key: str, start: float, length: float) -> Fingerprint:
        params = {"start": round(start, 2), "length": round(length, 2)}
        entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT_FP, "fpcalc", VERSION, params)
        if entry is not None and isinstance(entry.data, dict):
            return Fingerprint.from_dict(entry.data)
        fp = await fingerprint_window(str(path), start, length, self.tmp_dir)
        await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_FP, model="fpcalc", version=VERSION, params=params,
                                data=fp.to_dict())
        return fp

    async def _key_and_duration(self, path: Path) -> tuple[str, float]:
        key = (await asyncio.to_thread(file_hash, path)).key
        duration = await asyncio.to_thread(probe_duration, str(path))
        if not duration:
            raise RpcError(UNAVAILABLE, f"duración desconocida: {path.name}")
        return key, float(duration)

    # -- analysis -------------------------------------------------------------------------------

    @staticmethod
    def _consensus(runs_per_sibling: list[list[Any]], min_len: float, pick_last: bool) -> list[float] | None:
        """Median interval of the runs that agree with the seed run: the one closest to the end (credits) or to the
        start (intro). Runs from other siblings only count when they overlap the seed by at least half."""
        cands = [r for runs in runs_per_sibling for r in runs if r.length >= min_len]
        if not cands:
            return None
        seed = max(cands, key=lambda r: (r.a_end, r.length)) if pick_last else min(cands, key=lambda r: (r.a_start, -r.length))
        group = []
        for r in cands:
            overlap = min(r.a_end, seed.a_end) - max(r.a_start, seed.a_start)
            if overlap >= 0.5 * min(r.length, seed.length):
                group.append(r)
        return [statistics.median(r.a_start for r in group), statistics.median(r.a_end for r in group)]

    async def analyze(self, path: Path, job: Job | None = None) -> dict[str, Any]:
        key, duration = await self._key_and_duration(path)
        sibs = siblings(path)
        result: dict[str, Any] = {"path": str(path), "key": key, "duration": duration, "siblings": [s.name for s in sibs],
                                  "intro": None, "credits": None, "analyzed_at": time.time(), "version": VERSION}
        if not sibs:
            result["reason"] = "no hay otros episodios en la carpeta"
            await self._store(key, path, result)
            return result
        head_len = min(HEAD_SECONDS, duration)
        tail_start = max(0.0, duration - TAIL_SECONDS)
        tail_len = duration - tail_start
        if job:
            job.report(0.05, "huella del episodio")
        try:
            my_head = await self._fingerprint(path, key, 0.0, head_len)
            my_tail = await self._fingerprint(path, key, tail_start, tail_len) if duration > 20 else None
        except FingerprintError as exc:
            result["reason"] = f"sin huella: {exc}"
            await self._store(key, path, result)
            return result
        head_runs, tail_runs = [], []
        for n, sib in enumerate(sibs, 1):
            if job:
                job.report(0.1 + 0.6 * n / len(sibs), f"comparando con {sib.name}")
            try:
                skey, sdur = await self._key_and_duration(sib)
                s_head = await self._fingerprint(sib, skey, 0.0, min(HEAD_SECONDS, sdur))
                head_runs.append(match(my_head, s_head, min_seconds=MIN_INTRO))
                if my_tail is not None and sdur > 20:
                    s_tail_start = max(0.0, sdur - TAIL_SECONDS)
                    s_tail = await self._fingerprint(sib, skey, s_tail_start, sdur - s_tail_start)
                    tail_runs.append(match(my_tail, s_tail, min_seconds=MIN_CREDITS))
            except (FingerprintError, RpcError, OSError) as exc:
                log.info("intro: skipping %s (%s)", sib.name, exc)
        intro = self._consensus(head_runs, MIN_INTRO, pick_last=False)
        credits = self._consensus(tail_runs, MIN_CREDITS, pick_last=True)
        if job:
            job.report(0.8, "ajustando bordes")
        # snap to silence / black cuts around each boundary
        # a segment START snaps to where a quiet/black region ENDS (content begins); a segment END to where one STARTS
        starts: list[float] = []
        ends: list[float] = []
        for seg in (intro, credits):
            if seg:
                det = await detect_edges(str(path), max(0.0, seg[0] - 4), (seg[1] - seg[0]) + 8)
                s_, e_ = cut_points(det)
                starts.extend(s_)
                ends.extend(e_)
        if intro:
            intro = [snap(intro[0], ends, SNAP_TOLERANCE, starts), snap(intro[1], starts, SNAP_TOLERANCE, ends)]
            if intro[1] - intro[0] < MIN_INTRO:
                intro = None
        if credits:
            credits = [snap(credits[0], ends, SNAP_TOLERANCE, starts),
                       min(duration, snap(credits[1], starts, SNAP_TOLERANCE, ends))]
            if credits[1] - credits[0] < MIN_CREDITS:
                credits = None
        result["intro"] = [round(intro[0], 2), round(intro[1], 2)] if intro else None
        result["credits"] = [round(credits[0], 2), round(credits[1], 2)] if credits else None
        result["matches"] = {"intro": sum(1 for r in head_runs if r), "credits": sum(1 for r in tail_runs if r)}
        await self._store(key, path, result)
        if job:
            job.report(1.0, "listo")
        return result

    async def _store(self, key: str, path: Path, result: dict[str, Any]) -> None:
        await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_SEG, model="chromaprint", version=VERSION, params={},
                                data=result)
        await asyncio.to_thread(self._export, path, result)

    def _export(self, path: Path, result: dict[str, Any]) -> None:
        """Merge into <folder>/.mpv-uos/segments.json (media-segments style: type/start/end per file)."""
        folder = path.parent / ".mpv-uos"
        try:
            folder.mkdir(exist_ok=True)
            out = folder / "segments.json"
            data: dict[str, Any] = {}
            if out.exists():
                try:
                    data = json.loads(out.read_text(encoding="utf-8"))
                except ValueError:
                    data = {}
            segs = []
            for kind in ("intro", "credits"):
                if result.get(kind):
                    segs.append({"type": kind, "start": result[kind][0], "end": result[kind][1]})
            data[path.name] = {"segments": segs, "analyzed_at": result.get("analyzed_at"), "key": result.get("key")}
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            log.info("intro: cannot export segments next to %s (%s)", path, exc)

    async def cached(self, path: Path) -> dict[str, Any] | None:
        key = (await asyncio.to_thread(file_hash, path)).key
        entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT_SEG, "chromaprint", VERSION, {})
        return entry.data if entry is not None and isinstance(entry.data, dict) else None

    def submit(self, path: Path, notify: str, session_id: str | None) -> Job:
        skey = str(path)
        job = self.jobs.get(skey)
        if job is not None and job.status.value in ("queued", "running"):
            return job

        async def body(j: Job) -> dict[str, Any]:
            try:
                res = await self.analyze(path, j)
            finally:
                self.jobs.pop(skey, None)
            if session_id:
                session = self.server.sessions.get(session_id)
                if session is not None and session.connected:
                    session.push_event(notify, "intro:" + skey, "done", {"event": "intro", "result": res}, final=True)
            return res

        job = self.server.jobs.submit(f"intro.analyze", body, priority=Priority.PRECOMPUTE, heavy=True, session_id=None,
                                      meta={"notify": notify, "path": skey, "session": session_id})
        self.jobs[skey] = job
        return job


def _local(path: str) -> Path:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not path.startswith("file://"):
        raise RpcError(UNAVAILABLE, "solo archivos locales")
    p = Path(path.removeprefix("file://"))
    if not p.is_file():
        raise RpcError(NOT_FOUND, f"no existe: {p}")
    return p


def register(server: MpvdServer, service: IntroService) -> None:
    d = server.dispatcher
    from shutil import which  # noqa: PLC0415

    server.services["intro"] = which("fpcalc") is not None

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    @d.method("intro.segments")
    async def segments(ctx: RpcContext, path: str, analyze: bool = True, notify: str = "mu_intro") -> dict[str, Any]:
        """Cached intro/credits segments of a file; when missing (and ``analyze``) starts the analysis in the background."""
        p = _local(path)
        res = await service.cached(p)
        if res is not None:
            return {"status": "done", **res}
        if not analyze:
            return {"status": "unknown", "path": str(p)}
        if not server.services["intro"]:
            raise RpcError(UNAVAILABLE, "fpcalc (chromaprint) no está instalado")
        sibs = siblings(p)
        if not sibs:
            return {"status": "done", "path": str(p), "intro": None, "credits": None, "siblings": [],
                    "reason": "no hay otros episodios en la carpeta"}
        job = service.submit(p, notify, _sid(ctx))
        return {"status": "analyzing", "path": str(p), "job": job.to_dict(), "siblings": [s.name for s in sibs]}

    @d.method("intro.analyze")
    async def analyze(ctx: RpcContext, path: str, wait: bool = False, notify: str = "mu_intro") -> dict[str, Any]:
        """(Re)analyse a file now; ``wait`` returns the result instead of the job."""
        p = _local(path)
        if not server.services["intro"]:
            raise RpcError(UNAVAILABLE, "fpcalc (chromaprint) no está instalado")
        if wait:
            return {"status": "done", **(await service.analyze(p))}
        return {"status": "analyzing", "job": service.submit(p, notify, _sid(ctx)).to_dict()}

    @d.method("intro.export")
    async def export(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Path of the folder's segments.json (written on every analysis)."""
        p = _local(path)
        out = p.parent / ".mpv-uos" / "segments.json"
        if not out.exists():
            raise RpcError(NOT_FOUND, "todavía no hay segmentos exportados para esa carpeta")
        return {"file": str(out), "entries": json.loads(out.read_text(encoding="utf-8"))}
