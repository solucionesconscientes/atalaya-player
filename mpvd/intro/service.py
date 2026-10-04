"""``intro.*``: find the intro and the credits of an episode by comparing its audio with other episodes of the same season
(Chromaprint runs, ``mpvd/intro/fingerprint.py``; neighbours from ``mpvd/intro/episodes.py``: same folder or one folder
per episode), check that the result makes sense, snap the boundaries to silence/black cuts (a few seconds around each
edge only) and cache per file. Manual marks (``intro.mark``) are user data and are carried to the rest of the season by
locating the marked audio in each episode. Nothing is written next to the videos unless asked (``intro.export``)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import AudioError, probe_duration
from mpvd.hashing import file_hash
from mpvd.intro.detect import cut_points, refine_edges, snap
from mpvd.intro.episodes import VIDEO_EXTS, episode_info, episode_number, is_video, next_episode, season_episodes
from mpvd.intro.fingerprint import Fingerprint, FingerprintError, Run, fingerprint_window, match
from mpvd.intro.marks import KINDS, MarkStore
from mpvd.jobs import Job, Priority
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.intro")

__all__ = ["VIDEO_EXTS", "IntroService", "register", "season_episodes", "siblings"]

ARTIFACT_FP = "chromaprint"
ARTIFACT_SEG = "segments"
FP_VERSION = "1"             # fingerprints (unchanged format: old caches stay valid)
SEG_VERSION = "2"            # results: season neighbours, sanity checks, reasons
HEAD_SECONDS = 600.0         # window searched for the intro (measured: 600 s of audio cost ~1-2 s more than 300 s)
TAIL_SECONDS = 300.0         # window searched for the credits
MIN_INTRO = 5.0
MIN_CREDITS = 3.0            # chromaprint needs ~1.5 s of context: short tails come out shorter
MAX_SIBLINGS = 3             # neighbours compared per analysis (the nearest valid ones)
MAX_TRIES = 6                # neighbours tried at most when some are broken / silent / other versions
MAX_SEASON = 60              # episodes handled by the season jobs
SNAP_TOLERANCE = 2.0
INTRO_MAX_START = 0.40       # an intro starts before 40 % of the file
CREDITS_MIN_END = 0.60       # credits end after 60 % of the file
SAME_VIDEO_SHARE = 0.50      # common audio over half of what was compared → another version of the same video
FRAGMENTS = 5                # ≥5 separate common snippets between unnamed files → a re-edit of the same material
MARK_MIN_SHARE = 0.30        # a marked span is "found" in another episode when ≥30 % of it matches
TICKS = 10_000_000           # Jellyfin ticks per second

NO_NEIGHBOURS = "no hay otros episodios para comparar"
SAME_VIDEO = "parecen versiones del mismo vídeo"
NOT_VIDEO = "no es un vídeo"


def siblings(path: Path, limit: int = MAX_SIBLINGS) -> list[Path]:
    """The ``limit`` nearest other episodes (same folder, else sibling folders of the same series and season)."""
    return season_episodes(path)[:limit]


def _short(exc: BaseException | str, n: int = 160) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _pieces(runs: list[Run]) -> int:
    """Distinct stretches of A among the runs (the same stretch found in the head and tail windows counts once)."""
    out: list[Run] = []
    for r in sorted(runs, key=lambda r: -r.length):
        if not any(min(r.a_end, o.a_end) - max(r.a_start, o.a_start) > 0.5 * min(r.length, o.length) for o in out):
            out.append(r)
    return len(out)


def _covered(runs: list[Run]) -> float:
    """Seconds of A covered by the union of the runs."""
    total, end = 0.0, -1.0
    for a, b in sorted((r.a_start, r.a_end) for r in runs):
        if b <= end:
            continue
        total += b - max(a, end)
        end = b
    return total


class IntroService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.tmp_dir = server.settings.cache_dir / "tmp" / "intro"
        self.jobs: dict[str, Job] = {}                        # path -> running analysis
        self.listeners: dict[str, set[tuple[str, str]]] = {}  # path -> {(session id, script)} waiting for it
        self.marks = MarkStore(server.settings.data_dir / "intro-marks.json")
        self._prefetched: set[str] = set()                     # seasons whose fingerprints were queued
        self._season_jobs: dict[str, Job] = {}
        self.failures: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}  # path -> (size, mtime), error result
        self._inflight: dict[str, asyncio.Future[Fingerprint]] = {}             # the same window asked twice at once

    # -- fingerprints (cached per file + window) ------------------------------------------------

    async def _fingerprint(self, path: Path, key: str, start: float, length: float) -> Fingerprint:
        params = {"start": round(start, 2), "length": round(length, 2)}
        entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT_FP, "fpcalc", FP_VERSION, params)
        if entry is not None and isinstance(entry.data, dict):
            return Fingerprint.from_dict(entry.data)
        # the analysis and the season prefetch often want the same window at the same time: compute it once
        flight = f"{key}|{params['start']}|{params['length']}"
        pending = self._inflight.get(flight)
        if pending is not None:
            return await asyncio.shield(pending)
        fut: asyncio.Future[Fingerprint] = asyncio.get_running_loop().create_future()
        self._inflight[flight] = fut
        try:
            fp = await fingerprint_window(str(path), start, length, self.tmp_dir)
            await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_FP, model="fpcalc", version=FP_VERSION,
                                    params=params, data=fp.to_dict())
            fut.set_result(fp)
            return fp
        except BaseException as exc:
            fut.set_exception(exc if isinstance(exc, Exception) else FingerprintError("interrumpido"))
            fut.exception()  # mark retrieved: nobody else may be waiting
            raise
        finally:
            self._inflight.pop(flight, None)

    async def _try_fp(self, path: Path, key: str, start: float, length: float) -> tuple[Fingerprint | None, str | None]:
        """A fingerprint, or None and a short Spanish reason (never raises for a broken / silent / missing file)."""
        try:
            return await self._fingerprint(path, key, start, length), None
        except FingerprintError as exc:
            if "not found" in str(exc):
                return None, "fpcalc no está instalado"
            if "too short" in str(exc) or "Not enough audio" in str(exc):
                return None, "huella vacía (silencio o sin audio)"
            return None, f"fpcalc falló ({_short(exc, 80)})"
        except AudioError as exc:
            log.info("intro: no audio from %s @%.0f s (%s)", path.name, start, _short(exc))
            return None, "no se pudo extraer el audio (sin pista de audio o archivo dañado)"
        except OSError as exc:
            return None, f"no se pudo leer ({_short(exc.strerror or exc, 80)})"

    async def _key_and_duration(self, path: Path) -> tuple[str, float]:
        key = (await asyncio.to_thread(file_hash, path)).key
        duration = await asyncio.to_thread(probe_duration, str(path))
        if not duration:
            raise RpcError(UNAVAILABLE, t("duración desconocida (¿archivo dañado?): %s") % (path.name,))
        return key, float(duration)

    # -- consensus and sanity checks ----------------------------------------------------------------

    @staticmethod
    def _consensus(runs_per_sibling: list[list[Run]], min_len: float, pick_last: bool) -> tuple[list[float] | None, int]:
        """(median interval, support) of the run most neighbours agree on; ties go to the earliest start (intro) or
        latest end (credits). Runs agree when they overlap by at least half of the shorter one."""
        cands = [(i, r) for i, runs in enumerate(runs_per_sibling) for r in runs if r.length >= min_len]
        if not cands:
            return None, 0

        def agrees(r: Run, seed: Run) -> bool:
            return min(r.a_end, seed.a_end) - max(r.a_start, seed.a_start) >= 0.5 * min(r.length, seed.length)

        def support(seed: Run) -> int:
            return len({i for i, r in cands if agrees(r, seed)})

        seed = max((r for _, r in cands),
                   key=lambda r: (support(r), r.a_end if pick_last else -r.a_start, r.length))
        group = [r for _, r in cands if agrees(r, seed)]
        return [statistics.median(r.a_start for r in group), statistics.median(r.a_end for r in group)], support(seed)

    @staticmethod
    def _sanity(intro: list[float] | None, n_intro: int, credits: list[float] | None, n_credits: int,
                duration: float, reasons: dict[str, str]) -> tuple[list[float] | None, list[float] | None]:
        if intro and intro[0] > INTRO_MAX_START * duration:
            reasons["intro"] = "el audio común empieza demasiado tarde para ser una intro"
            intro = None
        if credits and credits[1] < CREDITS_MIN_END * duration:
            reasons["credits"] = "el audio común acaba demasiado pronto para ser los créditos"
            credits = None
        if intro and credits and min(intro[1], credits[1]) > max(intro[0], credits[0]):
            keep_intro = n_intro > n_credits or (n_intro == n_credits and intro[0] / duration <= 1 - credits[1] / duration)
            if keep_intro:
                reasons["credits"] = "se solapaba con la intro"
                credits = None
            else:
                reasons["intro"] = "se solapaba con los créditos"
                intro = None
        return intro, credits

    # -- analysis -------------------------------------------------------------------------------

    def _progress(self, job: Job | None, skey: str, progress: float, message: str) -> None:
        if job is not None:
            job.report(progress, message)
        self._push(skey, "intro-progress", "running",
                   {"event": "intro-progress", "path": skey, "progress": round(progress, 3), "message": message})

    async def analyze(self, path: Path, job: Job | None = None) -> dict[str, Any]:
        """Detect, store and return the (manual-merged) segments of ``path``. Only an unreadable file raises."""
        skey = str(path)
        key, duration = await self._key_and_duration(path)
        eps = (await asyncio.to_thread(season_episodes, path))[:MAX_SEASON]
        result: dict[str, Any] = {"path": skey, "key": key, "duration": duration, "siblings": [], "episodes": len(eps),
                                  "intro": None, "credits": None, "matches": {"intro": 0, "credits": 0}, "reasons": {},
                                  "analyzed_at": time.time(), "version": SEG_VERSION}
        reasons: dict[str, str] = result["reasons"]
        if not eps:  # not cached: episodes added later must be picked up
            result["reason"] = NO_NEIGHBOURS if is_video(path) else NOT_VIDEO
            return self._merged(key, result)
        head_len = min(HEAD_SECONDS, duration)
        tail_start = max(0.0, duration - TAIL_SECONDS)
        tail_len = duration - tail_start
        self._progress(job, skey, 0.05, "huella del episodio")
        my_head, err_head = await self._try_fp(path, key, 0.0, head_len)
        my_tail, err_tail = (await self._try_fp(path, key, tail_start, tail_len)) if duration > 20 else (None, "demasiado corto")
        if my_head is None:
            reasons["intro"] = f"sin huella del principio: {err_head}"
        if my_tail is None:
            reasons["credits"] = f"sin huella del final: {err_tail}"
        if my_head is None and my_tail is None:
            result["reason"] = f"sin huella de audio: {err_head}"
            await self._store(key, result)
            return self._merged(key, result)
        analysed = head_len + tail_len - max(0.0, head_len - tail_start)
        named = episode_info(path).known  # S01E02-style names: they are episodes, not versions of one video
        head_runs: list[list[Run]] = []
        tail_runs: list[list[Run]] = []
        used: list[str] = []
        skipped: list[str] = []
        same_video: list[str] = []
        for sib in eps[:MAX_TRIES]:
            if len(used) >= MAX_SIBLINGS:
                break
            self._progress(job, skey, 0.1 + 0.65 * (len(used) + len(skipped) + len(same_video)) / MAX_TRIES,
                           f"comparando con {sib.name}")
            try:
                skey2, sdur = await self._key_and_duration(sib)
            except (RpcError, OSError):
                skipped.append(f"{sib.name}: archivo dañado o ilegible")
                continue
            s_head = s_tail = None
            err = None
            if my_head is not None:
                s_head, err = await self._try_fp(sib, skey2, 0.0, min(HEAD_SECONDS, sdur))
            if my_tail is not None and sdur > 20:
                s_start = max(0.0, sdur - TAIL_SECONDS)
                s_tail, err2 = await self._try_fp(sib, skey2, s_start, sdur - s_start)
                err = err or err2
            if s_head is None and s_tail is None:
                skipped.append(f"{sib.name}: {err}")
                continue
            hr = await asyncio.to_thread(match, my_head, s_head, MIN_INTRO) if my_head and s_head else []
            tr = await asyncio.to_thread(match, my_tail, s_tail, MIN_CREDITS) if my_tail and s_tail else []
            if (analysed > 0 and _covered(hr + tr) > SAME_VIDEO_SHARE * analysed) or (
                    not named and _pieces(hr + tr) >= FRAGMENTS):
                same_video.append(sib.name)
                continue
            used.append(sib.name)
            head_runs.append(hr)
            tail_runs.append(tr)
            # H39/E4: parar en cuanto la coincidencia es clara. Dos vecinos de acuerdo es lo que ya se exige para dar
            # un tramo por bueno (ver más abajo con `same_video`), así que un tercer vecino solo cuesta otra huella de
            # cabecera y cola. Si los dos tramos ya tienen dos apoyos, no se sigue.
            if len(used) >= 2:
                _, n_h = self._consensus(head_runs, MIN_INTRO, pick_last=False)
                _, n_t = self._consensus(tail_runs, MIN_CREDITS, pick_last=True)
                if n_h >= 2 and n_t >= 2:
                    result["early_stop"] = len(used)
                    break
        result["siblings"] = used
        if skipped:
            result["skipped"] = skipped
        if same_video:
            result["same_video"] = same_video
        intro, n_intro = self._consensus(head_runs, MIN_INTRO, pick_last=False)
        credits, n_credits = self._consensus(tail_runs, MIN_CREDITS, pick_last=True)
        intro, credits = self._sanity(intro, n_intro, credits, n_credits, duration, reasons)
        if same_video:
            # a folder with versions of the same video: one neighbour agreeing is not enough evidence
            if intro and n_intro < 2:
                intro, reasons["intro"] = None, SAME_VIDEO
            if credits and n_credits < 2:
                credits, reasons["credits"] = None, SAME_VIDEO
        self._progress(job, skey, 0.8, "ajustando bordes")
        # a segment START snaps to where a quiet/black region ENDS (content begins); a segment END to where one STARTS
        points = [t for seg in (intro, credits) if seg for t in seg]
        if points:
            starts, ends = cut_points(await refine_edges(str(path), points, duration=duration))
            if intro:
                intro = [snap(intro[0], ends, SNAP_TOLERANCE, starts), snap(intro[1], starts, SNAP_TOLERANCE, ends)]
                if intro[1] - intro[0] < MIN_INTRO:
                    intro = None
                    reasons["intro"] = "coincidencia demasiado corta"
            if credits:
                credits = [snap(credits[0], ends, SNAP_TOLERANCE, starts),
                           min(duration, snap(credits[1], starts, SNAP_TOLERANCE, ends))]
                if credits[1] - credits[0] < MIN_CREDITS:
                    credits = None
                    reasons["credits"] = "coincidencia demasiado corta"
        for kind, seg in (("intro", intro), ("credits", credits)):
            if seg is None and kind not in reasons:
                reasons[kind] = (f"sin audio común con {len(used)} episodio(s)" if used
                                 else SAME_VIDEO if same_video else "ningún episodio se pudo comparar")
        result["intro"] = [round(intro[0], 2), round(intro[1], 2)] if intro else None
        result["credits"] = [round(credits[0], 2), round(credits[1], 2)] if credits else None
        result["matches"] = {"intro": n_intro if intro else 0, "credits": n_credits if credits else 0}
        if not intro and not credits:
            if same_video and (not used or SAME_VIDEO in reasons.values()):
                result["reason"] = SAME_VIDEO
            elif not used:
                result["reason"] = "no se pudo comparar con ningún episodio (" + "; ".join(skipped[:2]) + ")"
            else:
                result["reason"] = f"no hay audio común con los otros episodios ({len(used)} comparados)"
        if used or same_video:  # nothing compared (broken neighbours): retry next time instead of caching
            await self._store(key, result)
        await self._apply_templates(path, key, duration, eps)
        self._progress(job, skey, 1.0, "listo")
        return self._merged(key, result)

    async def _store(self, key: str, result: dict[str, Any]) -> None:
        await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_SEG, model="chromaprint", version=SEG_VERSION,
                                params={}, data=result)

    def _merged(self, key: str, result: dict[str, Any]) -> dict[str, Any]:
        """The automatic result with the manual marks of the file on top (``sources``: auto / manual)."""
        out = dict(result)
        out["reasons"] = dict(result.get("reasons") or {})
        sources = {k: "auto" for k in KINDS if out.get(k)}
        entry = self.marks.get(key)
        manual_from: dict[str, str] = {}
        for kind in KINDS:
            m = entry.get(kind)
            if isinstance(m, dict):
                out[kind] = [m["start"], m["end"]]
                sources[kind] = "manual"
                out["reasons"].pop(kind, None)
                if m.get("propagated"):
                    manual_from[kind] = m.get("origin_name", "")
        out["sources"] = sources
        if manual_from:
            out["manual_from"] = manual_from
        if out.get("intro") or out.get("credits"):
            out.pop("reason", None)
        return out

    async def cached(self, path: Path, key: str | None = None) -> dict[str, Any] | None:
        key = key or (await asyncio.to_thread(file_hash, path)).key
        entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT_SEG, "chromaprint", SEG_VERSION, {})
        if entry is None or not isinstance(entry.data, dict):
            return None
        return self._merged(key, entry.data)

    def manual_only(self, path: Path, key: str, reason: str | None = None) -> dict[str, Any]:
        base: dict[str, Any] = {"path": str(path), "key": key, "intro": None, "credits": None, "siblings": []}
        if reason:
            base["reason"] = reason
        return self._merged(key, base)

    # -- notifications ------------------------------------------------------------------------------

    def _listen(self, skey: str, notify: str, session_id: str | None) -> None:
        if session_id:
            self.listeners.setdefault(skey, set()).add((session_id, notify))

    def _push(self, skey: str, kind: str, status: str, payload: dict[str, Any], final: bool = False,
              extra: set[tuple[str, str]] | None = None) -> None:
        targets = set(self.listeners.get(skey, ())) | (extra or set())
        for sid, target in targets:
            session = self.server.sessions.get(sid)
            if session is not None and session.connected:
                session.push_event(target, f"{kind}:{skey}", status, payload, final=final)

    def _finish(self, skey: str, result: dict[str, Any], extra: set[tuple[str, str]] | None = None) -> None:
        self._push(skey, "intro", result.get("status", "done"), {"event": "intro", "result": result}, final=True,
                   extra=extra)
        self.listeners.pop(skey, None)

    def extra_info(self, path: Path, eps: list[Path] | None = None) -> dict[str, Any]:
        eps = season_episodes(path) if eps is None else eps
        nxt = next_episode(path, eps)
        return {"next": str(nxt) if nxt else None, "episodes": len(eps)}

    # -- jobs -----------------------------------------------------------------------------------

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int]:
        try:
            st = path.stat()
            return st.st_size, int(st.st_mtime)
        except OSError:
            return -1, -1

    def failure(self, path: Path) -> dict[str, Any] | None:
        """The error of the last analysis of this file while it is unchanged (the watchdog must not retry forever)."""
        f = self.failures.get(str(path))
        return f[1] if f is not None and f[0] == self._stamp(path) else None

    def submit(self, path: Path, notify: str, session_id: str | None) -> Job:
        skey = str(path)
        self.failures.pop(skey, None)
        self._listen(skey, notify, session_id)
        job = self.jobs.get(skey)
        if job is not None and job.status.value in ("queued", "running"):
            return job

        async def body(j: Job) -> dict[str, Any]:
            try:
                res = {"status": "done", **(await self.analyze(path, j))}
                res.update(await asyncio.to_thread(self.extra_info, path))
            except asyncio.CancelledError:
                self.jobs.pop(skey, None)
                raise
            except Exception as exc:
                self.jobs.pop(skey, None)
                log.warning("intro: analysis of %s failed: %s", path, exc)
                reason = exc.message if isinstance(exc, RpcError) else f"error al analizar: {_short(exc)}"
                err = {"status": "error", "path": skey, "intro": None, "credits": None, "reason": reason}
                self.failures[skey] = (self._stamp(path), err)
                self._finish(skey, err)
                raise
            self.jobs.pop(skey, None)
            self._finish(skey, res)
            return res

        job = self.server.jobs.submit("intro.analyze", body, priority=Priority.PRECOMPUTE, heavy=True, session_id=None,
                                      meta={"notify": notify, "path": skey, "session": session_id})
        self.jobs[skey] = job
        return job

    def prefetch_season(self, path: Path, eps: list[Path]) -> Job | None:
        """Queue (lowest priority) the fingerprints of every episode of a recognised season, once per daemon run: the
        next episodes then compare instantly and ``intro.season`` has little left to do."""
        info = episode_info(path)
        if not info.known or not eps:
            return None
        season = f"{path.parent.parent}|{info.title}|{info.season}"
        if season in self._prefetched:
            return None
        self._prefetched.add(season)
        files = [path, *eps][:MAX_SEASON]

        async def body(j: Job) -> dict[str, Any]:
            done = 0
            for n, f in enumerate(files):
                j.report(n / len(files), f.name)
                try:
                    k, d = await self._key_and_duration(f)
                except (RpcError, OSError):
                    continue
                head, _ = await self._try_fp(f, k, 0.0, min(HEAD_SECONDS, d))
                tail = None
                if d > 20:
                    s = max(0.0, d - TAIL_SECONDS)
                    tail, _ = await self._try_fp(f, k, s, d - s)
                done += int(head is not None or tail is not None)
            return {"episodes": len(files), "fingerprinted": done}

        return self.server.jobs.submit("intro.prefetch", body, priority=Priority.INDEX, heavy=True,
                                       meta={"path": str(path), "episodes": len(files)})

    def submit_season(self, path: Path, eps: list[Path], notify: str, session_id: str | None) -> tuple[Job, int]:
        """Analyse every episode of the season (cached ones are skipped); progress as ``intro-season`` events."""
        files = sorted([path, *eps], key=lambda p: (episode_number(p), p.name))
        skey = "|".join(sorted(str(f) for f in files))
        job = self._season_jobs.get(skey)
        if job is not None and job.status.value in ("queued", "running"):
            return job, len(files)
        me = {(session_id, notify)} if session_id else set()

        async def body(j: Job) -> dict[str, Any]:
            found = analysed = 0
            for n, f in enumerate(files):
                j.report(n / len(files), f"{n + 1}/{len(files)}: {f.name}")
                self._push(str(path), "intro-season", "running", {"event": "intro-season", "path": str(path), "done": n,
                           "total": len(files), "current": f.name}, extra=me)
                try:
                    res = await self.cached(f)
                    if res is None:
                        res = {"status": "done", **(await self.analyze(f)), **(await asyncio.to_thread(self.extra_info, f))}
                        analysed += 1
                        self._finish(str(f), res, extra=me)
                except Exception as exc:  # noqa: BLE001 - one broken episode must not stop the season
                    log.info("intro: season analysis skipped %s (%s)", f.name, exc)
                    continue
                found += int(bool(res.get("intro") or res.get("credits")))
            summary = {"event": "intro-season", "path": str(path), "done": len(files), "total": len(files),
                       "found": found, "analysed": analysed, "final": True}
            self._push(str(path), "intro-season", "done", summary, final=True, extra=me)
            self._season_jobs.pop(skey, None)
            return summary

        job = self.server.jobs.submit("intro.season", body, priority=Priority.PRECOMPUTE, heavy=True,
                                      meta={"path": str(path), "episodes": len(files)})
        self._season_jobs[skey] = job
        return job, len(files)

    # -- manual marks -------------------------------------------------------------------------------

    async def _locate(self, tpl: Fingerprint, mark: dict[str, Any], origin_duration: float, kind: str, f: Path,
                      fkey: str, fdur: float) -> list[float] | None:
        """Where the marked span (fingerprint ``tpl``) is in episode ``f``: searched in its first minutes (intro) or its
        last minutes (credits), any offset (cold opens of different lengths). None when not found."""
        length = mark["end"] - mark["start"]
        if kind == "intro":
            win_start, win_len = 0.0, min(fdur, max(HEAD_SECONDS, mark["end"] + 120))
        else:
            win_len = min(fdur, max(TAIL_SECONDS, origin_duration - mark["start"] + 120))
            win_start = max(0.0, fdur - win_len)
            win_len = fdur - win_start
        fp, _ = await self._try_fp(f, fkey, win_start, win_len)
        if fp is None:
            return None
        runs = await asyncio.to_thread(match, tpl, fp, max(2.0, min(MIN_INTRO, 0.3 * length)))
        best = max(runs, key=lambda r: r.length, default=None)
        if best is None or best.length < max(2.0, MARK_MIN_SHARE * length):
            return None
        off = best.b_start - best.a_start
        s, e = max(0.0, mark["start"] + off), min(fdur, mark["end"] + off)
        return [round(s, 2), round(e, 2)] if e - s >= 1.0 else None

    async def _template(self, path: Path, key: str, mark: dict[str, Any]) -> Fingerprint | None:
        fp, err = await self._try_fp(path, key, float(mark["start"]), float(mark["end"]) - float(mark["start"]))
        if fp is None:
            log.info("intro: no fingerprint for the marked span of %s (%s)", path.name, err)
        return fp

    async def _apply_templates(self, path: Path, key: str, duration: float, eps: list[Path]) -> None:
        """Marks made by hand on other episodes of the season also apply to this one (episodes opened later)."""
        own = self.marks.get(key)
        missing = [k for k in KINDS if k not in own]
        if not missing:
            return
        templates = self.marks.originals({str(e) for e in eps})
        mine = episode_number(path)
        for kind in missing:
            cands = sorted((t for t in templates if t["kind"] == kind),
                           key=lambda t: abs(episode_number(Path(t["path"])) - mine))
            for t in cands[:2]:
                tpl = await self._template(Path(t["path"]), t["key"], t["mark"])
                seg = await self._locate(tpl, t["mark"], t["duration"], kind, path, key, duration) if tpl else None
                if seg:
                    self.marks.set(key, path, duration, kind, seg[0], seg[1], t["key"], Path(t["path"]).name)
                    break

    def submit_propagation(self, path: Path, key: str, duration: float, kind: str, mark: dict[str, Any], notify: str,
                           session_id: str | None) -> Job:
        me = {(session_id, notify)} if session_id else set()

        async def body(j: Job) -> dict[str, Any]:
            eps = (await asyncio.to_thread(season_episodes, path))[:MAX_SEASON]
            tpl = await self._template(path, key, mark)
            if tpl is None:
                return {"applied": [], "reason": t("el tramo marcado no tiene audio útil")}
            applied: list[str] = []
            for n, f in enumerate(eps):
                if not self.marks.current(key, kind, mark["at"]):
                    break  # superseded by a newer mark
                j.report(n / max(1, len(eps)), f.name)
                try:
                    fkey, fdur = await self._key_and_duration(f)
                except (RpcError, OSError):
                    continue
                own = self.marks.get(fkey).get(kind)
                if isinstance(own, dict) and own.get("origin") == fkey:
                    continue  # the user marked that episode too: theirs wins
                seg = await self._locate(tpl, mark, duration, kind, f, fkey, fdur)
                if seg is None:
                    continue
                self.marks.set(fkey, f, fdur, kind, seg[0], seg[1], key, path.name)
                applied.append(f.name)
                res = await self.cached(f, fkey) or self.manual_only(f, fkey)
                self._finish(str(f), {"status": "done", **res, **(await asyncio.to_thread(self.extra_info, f))}, extra=me)
            self._push(str(path), "intro-mark", "done", {"event": "intro-mark", "path": str(path), "kind": kind,
                       "applied": len(applied), "total": len(eps)}, final=True, extra=me)
            return {"applied": applied, "total": len(eps)}

        return self.server.jobs.submit("intro.mark", body, priority=Priority.INTERACTIVE, heavy=True,
                                       meta={"path": str(path), "kind": kind})

    # -- export (only when asked) ------------------------------------------------------------------

    async def export(self, path: Path) -> dict[str, Any]:
        """Write ``<folder>/segments.json`` with the segments of every video of the folder that has them: media-segments
        style (``type``/``start``/``end`` in seconds plus Jellyfin's ``Type``/``StartTicks``/``EndTicks``)."""
        folder = path.parent
        entries: dict[str, Any] = {}
        for f in sorted(p for p in folder.iterdir() if is_video(p) and p.is_file()):
            key = (await asyncio.to_thread(file_hash, f)).key
            res = await self.cached(f, key) or self.manual_only(f, key)
            segs = []
            for kind, jf in (("intro", "Intro"), ("credits", "Outro")):
                seg = res.get(kind)
                if seg:
                    segs.append({"type": kind, "start": seg[0], "end": seg[1], "source": res["sources"].get(kind, "auto"),
                                 "Type": jf, "StartTicks": int(seg[0] * TICKS), "EndTicks": int(seg[1] * TICKS)})
            if segs:
                entries[f.name] = {"segments": segs, "key": key, "analyzed_at": res.get("analyzed_at")}
        if not entries:
            raise RpcError(NOT_FOUND, t("todavía no hay segmentos que exportar en esa carpeta"))
        out = folder / "segments.json"
        data: dict[str, Any] = {}
        if out.exists():
            try:
                old = json.loads(out.read_text(encoding="utf-8"))
                data = old if isinstance(old, dict) else {}
            except (OSError, ValueError):
                data = {}
        data.update(entries)
        try:
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            raise RpcError(UNAVAILABLE, t("no se pudo escribir %s: %s") % (out, exc.strerror or exc)) from exc
        return {"file": str(out), "entries": entries}


def _local(path: str) -> Path:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not path.startswith("file://"):
        raise RpcError(UNAVAILABLE, t("solo archivos locales"))
    p = Path(path.removeprefix("file://"))
    if not p.is_file():
        raise RpcError(NOT_FOUND, t("no existe: %s") % (p,))
    return p


def register(server: MpvdServer, service: IntroService) -> None:
    d = server.dispatcher
    from shutil import which  # noqa: PLC0415

    server.services["intro"] = which("fpcalc") is not None

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    def _need_fpcalc() -> None:
        if not server.services["intro"]:
            raise RpcError(UNAVAILABLE, t("fpcalc (chromaprint) no está instalado"))

    @d.method("intro.segments")
    async def segments(ctx: RpcContext, path: str, analyze: bool = True, notify: str = "mu_intro") -> dict[str, Any]:
        """Segments of a file (cached result + manual marks); when missing (and ``analyze``) starts the analysis in the
        background and queues the fingerprints of the whole season at low priority. ``next`` = following episode."""
        p = _local(path)
        key = (await asyncio.to_thread(file_hash, p)).key
        eps = (await asyncio.to_thread(season_episodes, p))[:MAX_SEASON]
        extra = await asyncio.to_thread(service.extra_info, p, eps)
        res = await service.cached(p, key)
        if res is not None:
            if server.services["intro"]:
                service.prefetch_season(p, eps)
            return {"status": "done", **res, **extra}
        if not eps:
            return {"status": "done", **service.manual_only(p, key, NO_NEIGHBOURS if is_video(p) else NOT_VIDEO), **extra}
        if not analyze:
            return {"status": "unknown", **service.manual_only(p, key), **extra}
        _need_fpcalc()
        failed = service.failure(p)
        if failed is not None and str(p) not in service.jobs:
            return {**service.manual_only(p, key), **failed, **extra}
        job = service.submit(p, notify, _sid(ctx))
        service.prefetch_season(p, eps)
        return {"status": "analyzing", **service.manual_only(p, key), "job": job.to_dict(), "progress": job.progress,
                "message": job.message, "siblings": [s.name for s in eps[:MAX_SIBLINGS]], **extra}

    @d.method("intro.analyze")
    async def analyze(ctx: RpcContext, path: str, wait: bool = False, notify: str = "mu_intro") -> dict[str, Any]:
        """(Re)analyse a file now; ``wait`` returns the result instead of the job."""
        p = _local(path)
        _need_fpcalc()
        if wait:
            try:
                res = await service.analyze(p)
            except RpcError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RpcError(UNAVAILABLE, f"error al analizar: {_short(exc)}") from exc
            return {"status": "done", **res, **(await asyncio.to_thread(service.extra_info, p))}
        return {"status": "analyzing", "job": service.submit(p, notify, _sid(ctx)).to_dict()}

    @d.method("intro.season")
    async def season(ctx: RpcContext, path: str, notify: str = "mu_intro") -> dict[str, Any]:
        """Analyse every episode of the season in the background (``intro-season`` progress events)."""
        p = _local(path)
        _need_fpcalc()
        eps = (await asyncio.to_thread(season_episodes, p))[:MAX_SEASON]
        job, total = service.submit_season(p, eps, notify, _sid(ctx))
        return {"status": "analyzing", "episodes": total, "job": job.to_dict()}

    @d.method("intro.mark")
    async def mark(ctx: RpcContext, path: str, kind: str, start: float, end: float,
                   notify: str = "mu_intro") -> dict[str, Any]:
        """Manual intro/credits span (source "manual"); carried in the background to the rest of the season by finding
        the same audio at the start (intro) or the end (credits) of each episode."""
        if kind not in KINDS:
            raise RpcError(INVALID_PARAMS, "kind debe ser intro o credits")
        p = _local(path)
        key, duration = await service._key_and_duration(p)
        s, e = max(0.0, float(start)), min(duration, float(end))
        if e - s < 1.0:
            raise RpcError(INVALID_PARAMS, "el tramo marcado es demasiado corto")
        m = service.marks.set(key, p, duration, kind, s, e, key, p.name)
        job = service.submit_propagation(p, key, duration, kind, m, notify, _sid(ctx)) if server.services["intro"] else None
        res = await service.cached(p, key) or service.manual_only(p, key)
        return {"status": "done", **res, **(await asyncio.to_thread(service.extra_info, p)),
                "propagation": job.to_dict() if job else None}

    @d.method("intro.unmark")
    async def unmark(ctx: RpcContext, path: str, kind: str | None = None) -> dict[str, Any]:
        """Forget this file's manual marks (and the ones propagated from them)."""
        if kind is not None and kind not in KINDS:
            raise RpcError(INVALID_PARAMS, "kind debe ser intro o credits")
        p = _local(path)
        key = (await asyncio.to_thread(file_hash, p)).key
        removed = service.marks.clear(key, kind)
        res = await service.cached(p, key) or service.manual_only(p, key)
        return {"status": "done", **res, **(await asyncio.to_thread(service.extra_info, p)), "removed": removed}

    @d.method("intro.export")
    async def export(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Write ``segments.json`` next to the video (only on request) and return it."""
        return await service.export(_local(path))
