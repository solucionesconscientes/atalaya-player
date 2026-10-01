"""``library.*``: a library without a server (H22). Folders chosen by the user are scanned in the background at INDEX
priority (incremental, ``mpvd/library/store.py``), files are classified by name (``parse.py``) into movies and series →
seasons → episodes, progress comes from "continue watching" (``mpvd/watch.py``, same content key) and the start screen
gets "continue watching" + "next episode" rows (``library.continue``). Posters: local images first, then (optional,
off by default, user's key) TMDB, then a frame extracted with ffmpeg into the cache. Subtitles from OpenSubtitles
(optional, off by default, user's Api-Key/account) by file hash, then by name; downloaded once into the cache and
resynchronised against the Whisper transcription (``subs.resync`` machinery of H6) when that makes sense (ADR-050).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import file_hash
from mpvd.jobs import Job, Priority, Status
from mpvd.library import tmdb as tmdb_mod
from mpvd.library.opensubtitles import API_URL as OSUB_URL
from mpvd.library.opensubtitles import MIN_HASH_SIZE, OpenSubtitles, OpenSubtitlesError, rank

# C4 · dónde se saca la clave y qué campos se pueden pegar del portapapeles sin que pasen por el script.
OSUB_KEY_URL = "https://www.opensubtitles.com/es/consumers"
CLIPBOARD_PROP = "clipboard/text"
PASTEABLE = ("osub_api_key", "osub_password", "tmdb_key")
from mpvd.library.parse import norm, parse_path
from mpvd.library.settings import LibrarySettings, normalize_languages
from mpvd.library.store import LibraryStore
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.library")

RESCAN_AGE = 1800.0          # library.list / continue start a background rescan when the last one is older
MAX_FRAMES_PER_SCAN = 200
MAX_ONLINE_PER_SCAN = 100
SEARCH_TTL = 3600.0
SUBS_ARTIFACT_VERSION = "1"


def _local(path: str) -> str:
    return path[7:] if path.startswith("file://") else path


def _is_url(path: str) -> bool:
    return "://" in path and not path.startswith("file://")


def extract_frame(video: str, dest: Path, timeout: float = 40.0) -> bool:
    """A representative frame (10 % into the video, at most 5 min) scaled to 342 px wide, as JPEG. Low CPU priority."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    from mpvd.asr.audio import probe_duration  # noqa: PLC0415 - optional import (ffprobe)

    duration = probe_duration(video, timeout=15) or 0.0
    at = min(max(duration * 0.1, 0.0), 300.0) if duration > 0 else 0.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.jpg")
    nice = [shutil.which("nice"), "-n", "10"] if shutil.which("nice") else []
    for t in (at, 0.0) if at > 0 else (0.0,):
        args = [*nice, ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                "-vf", "scale=342:-2", "-q:v", "4", str(tmp)]
        try:
            proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False)  # noqa: S603
        except (subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, dest)
            return True
    with contextlib.suppress(OSError):
        tmp.unlink()
    return False


def season_label(season: int | None) -> str:
    if season is None:
        return "Sin temporada"
    return "Especiales" if season == 0 else f"Temporada {season}"


def episode_label(it: dict[str, Any]) -> str:
    s, e = it.get("season"), it.get("episode")
    if e is None:
        num = ""
    else:
        num = f"{s if s is not None else 1}x{e:02d}"
        if it.get("episode_end"):
            num += f"-{it['episode_end']:02d}"
    title = it.get("episode_title") or ("" if num else Path(it["path"]).stem)
    return " · ".join(x for x in (num, title) if x)


class LibraryService:
    def __init__(self, server: MpvdServer):
        self.server = server
        data = server.settings.data_dir
        self.store = LibraryStore(data / "library.sqlite3")
        self.settings = LibrarySettings(data)
        self.art_dir = server.settings.cache_dir / "library"
        self.subs_dir = self.art_dir / "subs"
        self.osub_url = os.environ.get("MPV_UOS_OSUB_URL") or OSUB_URL
        self.tmdb_url = os.environ.get("MPV_UOS_TMDB_URL") or tmdb_mod.API_URL
        self.tmdb_image_url = os.environ.get("MPV_UOS_TMDB_IMAGE_URL") or tmdb_mod.IMAGE_URL
        self.scan_job: Job | None = None
        self.last_scan: dict[str, Any] = {}
        self._retry_frames = False   # a forced scan looks again at the groups whose frame could not be extracted
        self._osub: OpenSubtitles | None = None
        self._osub_sig: tuple[str, ...] = ()
        self._osub_lock = asyncio.Lock()
        self._search_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self.waiting: set[asyncio.Task[Any]] = set()
        self.resync_fn: Any = None        # test hook: async (path, srt, language, model, notify, session_id) -> dict

    def close(self) -> None:
        for t in list(self.waiting):
            t.cancel()
        if self._osub is not None and self._osub.token:
            self._osub.timeout = 3.0     # the API asks for a logout; never hold the shutdown for long
            with contextlib.suppress(Exception):
                self._osub.logout()
        self.store.close()

    # -- scanning ----------------------------------------------------------------------------------------------

    @property
    def scanning(self) -> bool:
        return self.scan_job is not None and self.scan_job.status in (Status.QUEUED, Status.RUNNING)

    def submit_scan(self, folders: list[str] | None = None, force: bool = False, notify: str | None = None,
                    session_id: str | None = None) -> Job:
        if self.scanning and not folders and self.scan_job is not None:
            return self.scan_job
        if force:
            self._retry_frames = True   # «Actualizar la biblioteca» does try the failed frames again

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            stop = threading.Event()

            def progress(frac: float, message: str) -> None:
                loop.call_soon_threadsafe(job.report, frac * 0.8, message)

            try:
                stats = await asyncio.to_thread(self.store.scan, folders, progress, stop.is_set, force)
            except asyncio.CancelledError:
                stop.set()
                raise
            job.report(0.8, "carátulas")
            art = await self._artwork(job)
            self.last_scan = {**stats.to_dict(), **art, "at": time.time()}
            return self.last_scan

        # heavy: waits while the performance guardian sees dropped frames (hashing, ffmpeg frames, TMDB)
        job = self.server.jobs.submit("library.scan", body, priority=Priority.INDEX, heavy=True, session_id=session_id,
                                      meta={"notify": notify} if notify else {})
        self.scan_job = job
        return job

    def maybe_rescan(self) -> None:
        """Background refresh when the last scan of some folder is older than RESCAN_AGE (cheap when unchanged)."""
        if self.scanning:
            return
        now = time.time()
        if any(f["exists"] and now - f["last_scan"] > RESCAN_AGE for f in self.store.folders()):
            self.submit_scan()

    async def _artwork(self, job: Job) -> dict[str, Any]:
        online = 0
        if self.settings.tmdb_active:
            online = await self._online_metadata()
        frames = 0
        groups = await asyncio.to_thread(self.store.groups_without_poster, self._retry_frames)
        self._retry_frames = False
        for g in groups[:MAX_FRAMES_PER_SCAN]:
            item = await asyncio.to_thread(self.store.first_item, g["kind"], g["group_key"])
            if item is None:
                continue
            dest = self.art_dir / "frames" / f"{item['key'].split(':', 1)[-1]}.jpg"
            ok = dest.exists() or await asyncio.to_thread(extract_frame, item["path"], dest)
            if ok:
                await asyncio.to_thread(self.store.set_group_poster, g["kind"], g["group_key"], str(dest), "frame")
                frames += 1
            else:
                # remember the failure (as the online metadata already does): otherwise every scan pays for its timeouts
                await asyncio.to_thread(self.store.set_group_poster, g["kind"], g["group_key"], "", "frame-failed")
            job.report(None, f"carátula: {g['title']}")
        return {"frames": frames, "online": online}

    def _tmdb(self) -> tmdb_mod.Tmdb:
        return tmdb_mod.Tmdb(self.settings.secret("tmdb_key"), self.tmdb_url, self.tmdb_image_url,
                             self.settings.get("tmdb_language"))

    async def _online_metadata(self) -> int:
        client = self._tmdb()
        n = 0
        for g in (await asyncio.to_thread(self.store.groups_without_online))[:MAX_ONLINE_PER_SCAN]:
            try:
                meta = await asyncio.to_thread(client.search, g["kind"], g["title"], g["year"])
                if meta and meta.get("poster_path") and g["poster_source"] != "local":
                    dest = self.art_dir / "tmdb" / f"{meta['type']}-{meta['id']}.jpg"
                    if not dest.exists():
                        await asyncio.to_thread(client.download_poster, meta["poster_path"], dest)
                    await asyncio.to_thread(self.store.set_group_poster, g["kind"], g["group_key"], str(dest), "tmdb")
            except tmdb_mod.TmdbError as exc:
                log.warning("tmdb: %s", exc)
                break
            await asyncio.to_thread(self.store.set_group_online, g["kind"], g["group_key"], meta or {"none": True})
            n += 1
        return n

    # -- views -------------------------------------------------------------------------------------------------

    def _decorate(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        watch = self.server.watch.store.get_many([i["key"] for i in items])
        groups: dict[tuple[str, str], dict[str, Any] | None] = {}
        out = []
        for it in items:
            e = watch.get(it["key"])
            gk = (it["kind"], it["group_key"])
            if gk not in groups:
                groups[gk] = self.store.group(*gk)
            g = groups[gk] or {}
            pos = float(e["position"]) if e else 0.0
            dur = float(e["duration"]) if e else 0.0
            fin = bool(e["finished"]) if e else False
            prog = 1.0 if fin else (min(1.0, pos / dur) if dur > 0 else 0.0)
            d = {
                "path": it["path"], "key": it["key"], "kind": it["kind"], "group": it["group_key"],
                "title": it["title"], "year": it["year"], "season": it["season"], "episode": it["episode"],
                "episode_end": it["episode_end"], "episode_title": it["episode_title"],
                "poster": it["poster"] or g.get("poster", ""), "group_poster": g.get("poster", ""),
                "position": pos, "duration": dur, "finished": fin, "progress": round(prog, 4),
                "resume": bool(e["resume"]) if e else False, "last_played": float(e["updated_at"]) if e else 0.0,
                "exists": os.path.exists(it["path"]),
            }
            if it["kind"] == "episode":
                d["label"] = episode_label(it)
                d["show"] = g.get("title") or it["title"]
                d["full_title"] = f"{d['show']} · {d['label']}"
            else:
                d["label"] = it["title"] + (f" ({it['year']})" if it["year"] else "")
                d["full_title"] = d["label"]
                online = g.get("online") or {}
                if online.get("overview"):
                    d["overview"] = online["overview"]
            out.append(d)
        return out

    @staticmethod
    def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda i: (i["season"] if i["season"] is not None else 10**4, i["episode"] is None,
                                            i["episode"] or 0, i["path"]))

    def summary(self) -> dict[str, Any]:
        return {**self.store.counts(), "scanning": self.scanning,
                "progress": round(self.scan_job.progress, 3) if self.scanning and self.scan_job else 0.0,
                "last_scan": self.last_scan, "settings": self.settings.public()}

    def movies(self) -> list[dict[str, Any]]:
        items = self.store.items("movie")
        first: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        for it in items:
            first.setdefault(it["group_key"], it)
            counts[it["group_key"]] = counts.get(it["group_key"], 0) + 1
        rows = self._decorate(list(first.values()))
        for r in rows:
            r["files"] = counts.get(r["group"], 1)
        rows.sort(key=lambda r: norm(r["title"]))
        return rows

    def shows(self) -> list[dict[str, Any]]:
        out = []
        by_group: dict[str, list[dict[str, Any]]] = {}
        for it in self._decorate(self.store.items("episode")):
            by_group.setdefault(it["group"], []).append(it)
        for gkey, eps in by_group.items():
            eps = self._ordered(eps)
            g = self.store.group("episode", gkey) or {}
            watched = sum(1 for e in eps if e["finished"])
            last = max((e["last_played"] for e in eps), default=0.0)
            nxt = self._next_unwatched_in(eps)
            out.append({"key": gkey, "title": g.get("title") or eps[0]["title"], "year": g.get("year"),
                        "poster": g.get("poster", ""), "seasons": len({e["season"] for e in eps}),
                        "episodes": len(eps), "watched": watched, "last_played": last,
                        "next": nxt["label"] if nxt else "", "overview": (g.get("online") or {}).get("overview", "")})
        out.sort(key=lambda s: norm(s["title"]))
        return out

    def show(self, gkey: str, season: int | None = None) -> dict[str, Any]:
        g = self.store.group("episode", gkey)
        if g is None:
            raise RpcError(NOT_FOUND, f"serie desconocida: {gkey}")
        eps = self._ordered(self._decorate(self.store.items("episode", gkey)))
        seasons: dict[Any, list[dict[str, Any]]] = {}
        for e in eps:
            seasons.setdefault(e["season"], []).append(e)
        info = {"key": gkey, "title": g["title"], "year": g["year"], "poster": g["poster"],
                "overview": (g.get("online") or {}).get("overview", "")}
        if season is not None:
            return {"show": info, "season": season, "label": season_label(season), "episodes": seasons.get(season, [])}
        return {"show": info, "seasons": [
            {"season": s, "label": season_label(s), "episodes": len(v), "watched": sum(1 for e in v if e["finished"]),
             "started": any(e["progress"] > 0 for e in v)}
            for s, v in sorted(seasons.items(), key=lambda kv: kv[0] if kv[0] is not None else 10**4)]}

    def search(self, q: str, limit: int = 50) -> dict[str, Any]:
        items = self._decorate(self.store.search(q, limit * 2))
        words = norm(q).split()
        shows: dict[str, dict[str, Any]] = {}
        for it in items:
            if it["kind"] == "episode" and all(w in norm(it["show"]) for w in words) and it["group"] not in shows:
                shows[it["group"]] = {"key": it["group"], "title": it["show"], "poster": it["group_poster"]}
        movies = [i for i in items if i["kind"] == "movie"]
        episodes = [i for i in items if i["kind"] == "episode"]
        return {"query": q, "shows": list(shows.values())[:limit], "movies": movies[:limit], "episodes": episodes[:limit]}

    # -- continue watching / next episode ----------------------------------------------------------------------

    @staticmethod
    def _next_unwatched_in(eps: list[dict[str, Any]], after: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """First unfinished episode after ``after`` (or after the last finished one), skipping duplicate numbers."""
        if after is None:
            idx = max((i for i, e in enumerate(eps) if e["finished"]), default=-1)
            if idx < 0:
                return None
        else:
            idx = next((i for i, e in enumerate(eps) if e["path"] == after["path"]), -1)
        cur = eps[idx] if idx >= 0 else None
        for e in eps[idx + 1:]:
            if cur is not None and e["episode"] is not None and (e["season"], e["episode"]) == (cur["season"], cur["episode"]):
                continue
            if not e["finished"] and e["exists"]:
                return e
        return None

    def continue_rows(self, limit: int = 10) -> list[dict[str, Any]]:
        recents = self.server.watch.store.recents(limit=300)
        by_key = self.store.by_keys([r["key"] for r in recents])
        rows: list[dict[str, Any]] = []
        done: set[tuple[str, str]] = set()
        for r in recents:
            it = by_key.get(r["key"])
            if it is None:
                continue
            g = (it["kind"], it["group_key"])
            if g in done:
                continue
            if r["resume"]:
                done.add(g)
                d = self._decorate([it])[0]
                if d["exists"]:
                    rows.append({**d, "row": "continue"})
            elif r["finished"]:
                done.add(g)
                if it["kind"] == "episode":
                    eps = self._ordered(self._decorate(self.store.items("episode", it["group_key"])))
                    nxt = self._next_unwatched_in(eps, after=it)
                    if nxt is not None:
                        rows.append({**nxt, "row": "next"})
            if len(rows) >= limit:
                break
        return rows

    async def item_for(self, path: str) -> dict[str, Any] | None:
        local = _local(path)
        it = await asyncio.to_thread(self.store.item, local)
        if it is None and not _is_url(path) and os.path.isfile(local):
            key = await self.server.watch.key_for(local)
            it = (await asyncio.to_thread(self.store.by_keys, [key])).get(key)
        return it

    async def next_of(self, path: str) -> dict[str, Any]:
        it = await self.item_for(path)
        if it is not None and it["kind"] == "episode":
            eps = await asyncio.to_thread(lambda: self._ordered(self._decorate(self.store.items("episode", it["group_key"]))))
            idx = next((i for i, e in enumerate(eps) if e["key"] == it["key"]), -1)
            cur = eps[idx] if idx >= 0 else None
            for e in eps[idx + 1:] if idx >= 0 else []:
                if cur is not None and e["episode"] is not None and (e["season"], e["episode"]) == (cur["season"], cur["episode"]):
                    continue
                if e["exists"]:
                    return {"current": cur, "next": {**e, "source": "library"}}
            return {"current": cur, "next": None}
        if it is not None:
            return {"current": (await asyncio.to_thread(self._decorate, [it]))[0], "next": None}
        if _is_url(path):
            return {"current": None, "next": None}
        # not in the library: the following episode found next to it (same rules as mu-intro)
        from mpvd.intro.episodes import next_episode  # noqa: PLC0415

        nxt = await asyncio.to_thread(next_episode, Path(_local(path)))
        if nxt is None:
            return {"current": None, "next": None}
        return {"current": None, "next": {"path": str(nxt), "source": "folder", "full_title": nxt.stem,
                                          "label": nxt.stem, "kind": "episode"}}

    # -- subtitles from OpenSubtitles --------------------------------------------------------------------------

    def _osub_client(self) -> OpenSubtitles:
        s = self.settings
        if not s.get("osub_enabled"):
            raise RpcError(UNAVAILABLE, "Subtítulos de internet desactivados (Biblioteca › Ajustes)")
        key = s.secret("osub_api_key")
        if not key:
            raise RpcError(UNAVAILABLE, "Falta la Api-Key de OpenSubtitles (Biblioteca › Ajustes)")
        sig = (key, s.secret("osub_username"), s.secret("osub_password"), self.osub_url)
        if self._osub is None or sig != self._osub_sig:
            self._osub = OpenSubtitles(key, sig[1], sig[2], self.osub_url)
            self._osub_sig = sig
        return self._osub

    async def _osub_call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._osub_lock:   # the client keeps a token and 5 req/s is the API limit: one call at a time
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except OpenSubtitlesError as exc:
                raise RpcError(UNAVAILABLE, f"OpenSubtitles: {exc.message}") from None

    async def subs_search(self, path: str, languages: str | None = None, title: str | None = None) -> dict[str, Any]:
        client = self._osub_client()
        try:
            langs = normalize_languages(languages or self.settings.get("osub_languages"))
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        local = _local(path)
        key, oshash, size = "", "", 0
        if not _is_url(path):
            if not os.path.isfile(local):
                raise RpcError(NOT_FOUND, f"no existe: {local}")
            h = await asyncio.to_thread(file_hash, local)
            key, oshash, size = h.key, h.opensubtitles, h.size
        else:
            key = "url:" + norm(title or path)
        cached = self._search_cache.get((key, langs))
        if cached and time.time() - cached[0] < SEARCH_TTL:
            return cached[1]
        results: list[dict[str, Any]] = []
        if oshash and size >= MIN_HASH_SIZE:
            results = await self._osub_call(client.search, moviehash=oshash, languages=langs)
        query = ""
        if not any(r["hash_match"] for r in results):
            item = await self.item_for(path)
            if item is not None:
                name = {"kind": item["kind"], "title": item["title"], "season": item["season"],
                        "episode": item["episode"], "year": item["year"]}
            else:
                pn = parse_path(title or local)
                name = {"kind": pn.kind, "title": pn.title, "season": pn.season, "episode": pn.episode, "year": pn.year}
            query = name["title"]
            if query:
                more = await self._osub_call(
                    client.search, query=query, languages=langs, kind=name["kind"],
                    season=name["season"] if name["kind"] == "episode" else None,
                    episode=name["episode"] if name["kind"] == "episode" else None,
                    year=name["year"] if name["kind"] == "movie" else None)
                have = {r["file_id"] for r in results}
                results += [r for r in more if r["file_id"] not in have]
        ranked = rank(results, langs.split(","))
        out = {"path": path, "key": key, "hash": oshash, "query": query, "languages": langs, "results": ranked[:40],
               "hash_matches": sum(1 for r in ranked if r["hash_match"])}
        self._search_cache[(key, langs)] = (time.time(), out)
        return out

    def _subs_dest(self, key: str, file_id: int, lang: str) -> Path:
        d = self.subs_dir / key.replace(":", "_")
        return d / f"{int(file_id)}.{lang or 'und'}.srt"

    async def subs_download(self, path: str, file_id: int | None = None, languages: str | None = None,
                            resync: bool | None = None, audio_lang: str | None = None, title: str | None = None,
                            notify: str = "mu_library", session_id: str | None = None) -> dict[str, Any]:
        found = await self.subs_search(path, languages, title)
        results = found["results"]
        if file_id is None:
            if not results:
                raise RpcError(NOT_FOUND, "No hay subtítulos para este vídeo en OpenSubtitles")
            choice = results[0]
        else:
            choice = next((r for r in results if r["file_id"] == int(file_id)), None) or {
                "file_id": int(file_id), "language": "", "hash_match": False, "release": ""}
        lang = choice["language"]
        dest = self._subs_dest(found["key"], choice["file_id"], lang)
        cached = dest.exists() and dest.stat().st_size > 0
        remaining = None
        if not cached:
            client = self._osub_client()
            dl = await self._osub_call(client.download, choice["file_id"])
            text = dl.content.decode("utf-8", "replace").lstrip("﻿")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            await asyncio.to_thread(tmp.write_text, text, "utf-8")
            os.replace(tmp, dest)
            remaining = dl.remaining
        out: dict[str, Any] = {"status": "done", "srt": str(dest), "original": str(dest), "language": lang,
                               "file_id": choice["file_id"], "hash_match": bool(choice.get("hash_match")),
                               "release": choice.get("release", ""), "cached": cached, "remaining": remaining}
        out.update(await self._maybe_resync(path, dest, lang, bool(choice.get("hash_match")), resync, audio_lang,
                                            notify, session_id))
        return out

    async def subs_quota(self) -> dict[str, Any]:
        """Cuánto cupo queda hoy (C4). Con cuenta, se lo pregunta a OpenSubtitles (``/infos/user``); sin cuenta solo se
        sabe lo que dijo la última descarga, y se dice de dónde sale el número para no dar por bueno un dato viejo."""
        client = self._osub_client()
        if self.settings.secret("osub_username") and self.settings.secret("osub_password"):
            try:
                info = await self._osub_call(client.user_info)
            except RpcError:
                info = None
            if isinstance(info, dict):
                return {"source": "cuenta", "remaining": info.get("remaining_downloads"),
                        "allowed": info.get("allowed_downloads"), "used": info.get("downloads_count"),
                        "level": info.get("level") or "", "vip": bool(info.get("vip")),
                        "reset_time": info.get("reset_time") or ""}
        last = dict(client.last_quota or {})
        return {"source": "última descarga" if last.get("remaining") is not None else "sin datos",
                "remaining": last.get("remaining"), "allowed": None, "used": last.get("requests"),
                "level": "", "vip": False, "reset_time": last.get("reset_time") or ""}

    async def paste_secret(self, session: Any, field: str) -> dict[str, Any]:
        """Guarda en ``field`` lo que haya en el portapapeles del reproductor, leyéndolo mpvd por su propia conexión IPC
        (ADR-061 hace lo mismo con la clave de emisión): la clave no pasa por el script Lua ni por el OSD, así que no
        acaba en un log ni en la pantalla. Devuelve solo la longitud y los ajustes públicos."""
        if field not in PASTEABLE:
            raise RpcError(INVALID_PARAMS, f"no se puede pegar en {field!r}")
        if session is None:
            raise RpcError(UNAVAILABLE, "hace falta un reproductor conectado para leer su portapapeles")
        # la propiedad se lee en cada llamada (no al importar): los tests apuntan a un user-data propio
        prop = os.environ.get("MPVD_LIBRARY_CLIPBOARD_PROP") or CLIPBOARD_PROP
        try:
            value = await session.client.get_property(prop, timeout=5)
        except Exception:  # noqa: BLE001 - nunca repetir lo que hubiera en el portapapeles
            raise RpcError(UNAVAILABLE, "no se pudo leer el portapapeles del reproductor") from None
        value = str(value or "").strip()
        if not value:
            raise RpcError(INVALID_PARAMS, "el portapapeles está vacío")
        if len(value) > 500 or "\n" in value:
            raise RpcError(INVALID_PARAMS, "eso no parece una clave: copia solo la clave")
        changes: dict[str, Any] = {field: value}
        if field == "osub_api_key" and not self.settings.get("osub_enabled"):
            changes["osub_enabled"] = True       # pegar la clave es decir «quiero esto»
        out = await asyncio.to_thread(self.settings.update, changes)
        self._osub = None
        return {"length": len(value), "settings": out}

    async def _maybe_resync(self, path: str, srt: Path, lang: str, hash_match: bool, resync: bool | None,
                            audio_lang: str | None, notify: str, session_id: str | None) -> dict[str, Any]:
        """ADR-050: a hash match is timed for this very file → no resync unless asked; a subtitle found by name is
        resynchronised when Whisper is available and the subtitle is in the language of the audio (the aligner
        matches words); ``osub_resync`` = always/never overrides, and so does ``resync`` in the call."""
        from mpvd.subs.translate import normalize_lang  # noqa: PLC0415

        mode = "always" if resync is True else "never" if resync is False else self.settings.get("osub_resync")
        if _is_url(path):
            return {"resync": "skipped", "resync_reason": "solo archivos locales"}
        if mode == "never" or (mode == "auto" and hash_match):
            return {"resync": "skipped", "resync_reason": "sincronizado para este archivo" if hash_match else "desactivada"}
        sub_l, audio_l = normalize_lang(lang), normalize_lang(audio_lang)
        if sub_l and audio_l and audio_l != "auto" and sub_l != audio_l:
            return {"resync": "skipped", "resync_reason": "el subtítulo no está en el idioma del audio"}
        fn = self.resync_fn
        if fn is None:
            if not self.server.asr.engine.available:
                return {"resync": "unavailable", "resync_reason": "sin Whisper"}
            fn = self.server.subs.resync_file
        language = sub_l or "auto"
        try:
            res = await fn(_local(path), str(srt), language, None, notify, session_id)
        except RpcError as exc:
            return {"resync": "failed", "resync_reason": exc.message}
        if res.get("status") == "done":
            return self._resync_result(res, srt)
        task_id = (res.get("task") or {}).get("id", "")
        self._spawn(self._resync_later(fn, path, srt, language, task_id, notify, session_id))
        return {"resync": "pending", "task": task_id}

    @staticmethod
    def _resync_result(res: dict[str, Any], srt: Path) -> dict[str, Any]:
        stats = res.get("stats") or {}
        if stats.get("ok") is False:
            return {"resync": "failed", "resync_reason": "pocas coincidencias con la voz", "stats": stats}
        return {"resync": "done", "srt": res["srt"], "original": str(srt), "stats": stats}

    async def _resync_later(self, fn: Any, path: str, srt: Path, language: str, task_id: str, notify: str,
                            session_id: str | None) -> None:
        """Wait for the transcription (a low-priority ASR task), then resync and tell the script to swap the track."""
        key = f"library-subs:{srt}"
        payload: dict[str, Any] = {"event": "library-subs", "path": path, "original": str(srt)}
        try:
            deadline = time.monotonic() + 6 * 3600
            while time.monotonic() < deadline:
                await asyncio.sleep(2.0)
                task = self.server.asr.tasks.get(task_id) if task_id else None
                if task is None or task.complete:
                    break
                if task.status in ("failed", "cancelled"):
                    raise RpcError(UNAVAILABLE, task.error or "la transcripción se detuvo")
            res = await fn(_local(path), str(srt), language, None, notify, session_id)
            if res.get("status") != "done":
                raise RpcError(UNAVAILABLE, "la transcripción no terminó")
            payload.update(self._resync_result(res, srt))
        except RpcError as exc:
            payload.update({"resync": "failed", "resync_reason": exc.message})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("library resync failed")
            payload.update({"resync": "failed", "resync_reason": type(exc).__name__})
        self._push(session_id, notify, key, payload)

    def _push(self, session_id: str | None, target: str, key: str, payload: dict[str, Any]) -> None:
        if not session_id:
            return
        session = self.server.sessions.get(session_id)
        if session is not None and session.connected:
            session.push_event(target, key, str(payload.get("resync", "")), payload, final=True)

    def _spawn(self, coro: Any) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self.waiting.add(t)
        t.add_done_callback(self.waiting.discard)


def register(server: MpvdServer, service: LibraryService) -> None:  # noqa: C901 - flat list of handlers
    d = server.dispatcher
    server.services["library"] = True

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    def _folder(path: str) -> str:
        p = os.path.expanduser(_local(str(path or "")).strip())
        if not p:
            raise RpcError(INVALID_PARAMS, "path required")
        return os.path.abspath(p)

    @d.method("library.folders.list")
    async def folders_list(ctx: RpcContext) -> list[dict[str, Any]]:
        """Folders of the library with their file count and last scan."""
        return await asyncio.to_thread(service.store.folders)

    @d.method("library.folders.add")
    async def folders_add(ctx: RpcContext, path: str, scan: bool = True, notify: str | None = None) -> dict[str, Any]:
        """Add a folder (a file path adds its folder) and scan it in the background."""
        p = _folder(path)
        if os.path.isfile(p):
            p = os.path.dirname(p)
        if not os.path.isdir(p):
            raise RpcError(NOT_FOUND, f"no existe la carpeta: {p}")
        added = await asyncio.to_thread(service.store.add_folder, p)
        job = service.submit_scan([p], notify=notify, session_id=_sid(ctx)) if scan else None
        return {"path": p, "added": added, "job": job.id if job else None,
                "folders": await asyncio.to_thread(service.store.folders)}

    @d.method("library.folders.remove")
    async def folders_remove(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Forget a folder and its files (the files themselves are not touched)."""
        p = _folder(path)
        n = await asyncio.to_thread(service.store.remove_folder, p)
        if n < 0:
            raise RpcError(NOT_FOUND, f"no está en la biblioteca: {p}")
        return {"path": p, "removed": n, "folders": await asyncio.to_thread(service.store.folders)}

    @d.method("library.scan")
    async def scan(ctx: RpcContext, path: str | None = None, force: bool = False, wait: bool = False,
                   notify: str | None = None) -> dict[str, Any]:
        """Rescan every folder (or one) in the background at INDEX priority; ``wait`` returns the result."""
        folders = [_folder(path)] if path else None
        if folders and folders[0] not in {f["path"] for f in await asyncio.to_thread(service.store.folders)}:
            raise RpcError(NOT_FOUND, f"no está en la biblioteca: {folders[0]}")
        job = service.submit_scan(folders, bool(force), notify, _sid(ctx))
        if wait:
            await job.wait()
            if job.status != Status.DONE:
                raise RpcError(UNAVAILABLE, job.error or job.status.value)
            return {"job": job.id, "status": job.status.value, "result": job.result}
        return {"job": job.id, "status": job.status.value}

    @d.method("library.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """Counts, scan state and settings (never the secrets)."""
        return await asyncio.to_thread(service.summary)

    @d.method("library.list")
    async def list_(ctx: RpcContext, kind: str | None = None, show: str | None = None,
                    season: int | None = None) -> Any:
        """``kind``: movies | shows; ``show`` (+ ``season``): its seasons (or episodes); nothing: counts."""
        service.maybe_rescan()
        if show is not None:
            return await asyncio.to_thread(service.show, show, None if season is None else int(season))
        if kind in ("movies", "movie"):
            return await asyncio.to_thread(service.movies)
        if kind in ("shows", "series", "tv"):
            return await asyncio.to_thread(service.shows)
        if kind:
            raise RpcError(INVALID_PARAMS, "kind: movies | shows")
        return await asyncio.to_thread(service.summary)

    @d.method("library.search")
    async def search(ctx: RpcContext, q: str, limit: int = 50) -> dict[str, Any]:
        """Accent-insensitive search in titles, episode titles and file names."""
        return await asyncio.to_thread(service.search, str(q or ""), max(1, min(200, int(limit))))

    @d.method("library.continue")
    async def continue_(ctx: RpcContext, limit: int = 10) -> list[dict[str, Any]]:
        """Start screen rows: library files in progress (``row: continue``) and the next unwatched episode of the
        series last watched (``row: next``), most recent first, one row per movie/series."""
        service.maybe_rescan()
        return await asyncio.to_thread(service.continue_rows, max(1, min(50, int(limit))))

    @d.method("library.next")
    async def next_(ctx: RpcContext, path: str) -> dict[str, Any]:
        """The episode after ``path`` in the library (``source: library``) or, outside it, next to it (``folder``)."""
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        return await service.next_of(path)

    @d.method("library.poster")
    async def poster(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Poster of a file: its own image, its movie/series poster or a frame extracted now (cached)."""
        it = await service.item_for(path)
        if it is not None:
            d = (await asyncio.to_thread(service._decorate, [it]))[0]
            if d["poster"]:
                return {"poster": d["poster"]}
            key = it["key"]
        else:
            key = await server.watch.key_for(_local(path))
        dest = service.art_dir / "frames" / f"{key.split(':', 1)[-1]}.jpg"
        if dest.exists() or await asyncio.to_thread(extract_frame, _local(path), dest):
            return {"poster": str(dest)}
        return {"poster": ""}

    @d.method("library.settings.get")
    async def settings_get(ctx: RpcContext) -> dict[str, Any]:
        """Switches and languages; secrets only as has_* flags."""
        return service.settings.public()

    @d.method("library.settings.set")
    async def settings_set(ctx: RpcContext, **changes: Any) -> dict[str, Any]:
        """tmdb_enabled, tmdb_key, tmdb_language, osub_enabled, osub_api_key, osub_username, osub_password,
        osub_languages, osub_resync (auto|always|never). Secrets go to a 0600 file in the data dir."""
        try:
            out = await asyncio.to_thread(service.settings.update, changes)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        service._search_cache.clear()
        if any(k.startswith("tmdb") for k in changes) and out["tmdb_active"]:
            service.submit_scan()   # fetch what is missing now
        return out

    @d.method("library.subs.search")
    async def subs_search(ctx: RpcContext, path: str, languages: str | None = None,
                          title: str | None = None) -> dict[str, Any]:
        """OpenSubtitles results for a file: by hash first, then by name (needs the switch and the Api-Key)."""
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        return await service.subs_search(path, languages, title)

    @d.method("library.subs.quota")
    async def subs_quota(ctx: RpcContext) -> dict[str, Any]:
        """Cupo de descargas que queda hoy en OpenSubtitles, y de dónde sale el dato (cuenta o última descarga)."""
        return await service.subs_quota()

    @d.method("library.settings.paste")
    async def settings_paste(ctx: RpcContext, field: str) -> dict[str, Any]:
        """Guarda en ``field`` (osub_api_key, osub_password, tmdb_key) lo que haya en el portapapeles del reproductor,
        leído por mpvd: la clave no pasa por el script ni por la pantalla. Devuelve su longitud, no su valor."""
        return await service.paste_secret(ctx.session, field)

    @d.method("library.subs.help")
    async def subs_help(ctx: RpcContext) -> dict[str, Any]:
        """Lo que hace falta para buscar subtítulos en internet y la página donde se saca la clave (gratis)."""
        s = service.settings
        return {"key_url": OSUB_KEY_URL, "has_key": bool(s.secret("osub_api_key")),
                "enabled": bool(s.get("osub_enabled")), "active": s.osub_active,
                "has_account": bool(s.secret("osub_username") and s.secret("osub_password")),
                "languages": s.get("osub_languages")}

    @d.method("library.subs.download")
    async def subs_download(ctx: RpcContext, path: str, file_id: int | None = None, languages: str | None = None,
                            resync: bool | None = None, audio_lang: str | None = None, title: str | None = None,
                            notify: str = "mu_library") -> dict[str, Any]:
        """Download one subtitle (``file_id``, else the best) into the cache and resync it when it makes sense
        (``resync: pending`` → a ``library-subs`` event brings the resynced file later)."""
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        return await service.subs_download(path, None if file_id is None else int(file_id), languages, resync,
                                           audio_lang, title, notify, _sid(ctx))
