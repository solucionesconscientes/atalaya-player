"""``music.*``: the music library (H32, ADR-064). Folders (by default the user's XDG Music folder, added once) are
scanned in the background at INDEX priority (incremental, ``store.py``; ffprobe at the lowest CPU priority), albums get
their cover from the folder or the embedded picture (extracted once into the cache), and tracks without ReplayGain tags
are measured afterwards with ffmpeg ``ebur128`` (track and album gain stored in the index, never in the files). The
script applies those gains through mpv's ``replaygain-fallback`` (mu-music).

Also: playlists saved as M3U8 in the user's data dir (``playlists.py``), smart lists (recently added, most played, never
played, by genre, by year or decade) and a local listening history (no scrobbling).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import file_hash
from mpvd.jobs import Job, Priority, Status
from mpvd.library.parse import norm
from mpvd.music import tags as tagmod
from mpvd.music.playlists import PlaylistError, Playlists
from mpvd.music.store import NO_ALBUM, MusicStore
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.music")

RESCAN_AGE = 1800.0
RG_ARTIFACT = "replaygain"
RG_MODEL = "ebur128"
RG_VERSION = "1"
SMART = {
    "recent": "Añadidas recientemente",
    "top": "Más escuchadas",
    "unplayed": "Sin escuchar",
}


def _local(path: str) -> str:
    return path[7:] if path.startswith("file://") else path


def _is_url(path: str) -> bool:
    return "://" in path and not path.startswith("file://")


def track_label(t: dict[str, Any]) -> str:
    n = t.get("track_no")
    return f"{n}. {t['title']}" if n else t["title"]


class MusicService:
    def __init__(self, server: MpvdServer, probe: Any = None, measure: Any = None):
        self.server = server
        data = server.settings.data_dir
        self.store = MusicStore(data / "music.sqlite3")
        self.playlists = Playlists(data / "music" / "playlists")
        self.art_dir = server.settings.cache_dir / "music"
        self.probe = probe or tagmod.probe          # test hooks
        self.measure = measure or tagmod.measure
        self.scan_job: Job | None = None
        self.rg_job: Job | None = None
        self.last_scan: dict[str, Any] = {}
        self._measure_lock = asyncio.Lock()        # one ffmpeg measuring at a time (4-core laptops)
        self._notify: tuple[str | None, str | None] = (None, None)

    def close(self) -> None:
        self.store.close()

    # -- settings ----------------------------------------------------------------------------------------------

    @property
    def auto_replaygain(self) -> bool:
        return self.store.get_meta("auto_replaygain", "1") == "1"

    def settings(self) -> dict[str, Any]:
        return {"auto_replaygain": self.auto_replaygain}

    def ensure_default_folder(self) -> str | None:
        """The user's Music folder is added (and scanned) the first time the library is opened, once: removing it
        later is respected."""
        if self.store.get_meta("default_checked") == "1":
            return None
        self.store.set_meta("default_checked", "1")
        d = tagmod.xdg_music_dir()
        if d is None or not d.is_dir() or self.store.folders():
            return None
        p = os.path.abspath(str(d))
        self.store.add_folder(p)
        self.submit_scan([p])
        return p

    # -- background work ---------------------------------------------------------------------------------------

    @property
    def scanning(self) -> bool:
        return self.scan_job is not None and self.scan_job.status in (Status.QUEUED, Status.RUNNING)

    @property
    def measuring(self) -> bool:
        return self.rg_job is not None and self.rg_job.status in (Status.QUEUED, Status.RUNNING)

    def submit_scan(self, folders: list[str] | None = None, force: bool = False, notify: str | None = None,
                    session_id: str | None = None) -> Job:
        if self.scanning and not folders and self.scan_job is not None:
            return self.scan_job
        if notify:
            self._notify = (notify, session_id)

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            import threading  # noqa: PLC0415

            stop = threading.Event()

            def progress(frac: float, message: str) -> None:
                loop.call_soon_threadsafe(job.report, frac * 0.9, message)

            try:
                stats = await asyncio.to_thread(self.store.scan, self.probe, folders, progress, stop.is_set, force)
            except asyncio.CancelledError:
                stop.set()
                raise
            job.report(0.9, "carátulas")
            covers = await self._covers()
            self.last_scan = {**stats.to_dict(), "covers": covers, "at": time.time()}
            if self.auto_replaygain and self.store.rg_counts()["pending"]:
                self.submit_replaygain(*self._notify)
            return self.last_scan

        job = self.server.jobs.submit("music.scan", body, priority=Priority.INDEX, heavy=True, session_id=session_id,
                                      meta={"notify": notify} if notify else {})
        self.scan_job = job
        return job

    def maybe_rescan(self) -> None:
        if self.scanning:
            return
        now = time.time()
        if any(f["exists"] and now - f["last_scan"] > RESCAN_AGE for f in self.store.folders()):
            self.submit_scan()

    async def _covers(self) -> int:
        n = 0
        for a in await asyncio.to_thread(self.store.albums_needing_cover):
            dest = self.art_dir / "covers" / (file_hash_text(a["album_key"]) + ".jpg")
            ok = dest.exists() or await asyncio.to_thread(tagmod.extract_cover, a["cover_track"], dest)
            if ok:
                await asyncio.to_thread(self.store.set_album_cover, a["album_key"], str(dest), "embedded")
                n += 1
        return n

    async def _wait_guardian(self) -> None:
        """Measuring is the heaviest part: pause between files while mpv drops frames."""
        g = self.server.guardian
        while g.throttled:
            await asyncio.sleep(max(0.2, min(5.0, g.throttle_remaining)))

    async def _measure_cached(self, path: str, key: str, locked: bool = False) -> dict[str, float] | None:
        """Loudness of a file, remembered by content key in the artifact cache: a moved, renamed or re-added file
        is never decoded twice."""
        entry = await asyncio.to_thread(self.server.cache.get, key, RG_ARTIFACT, RG_MODEL, RG_VERSION)
        if entry is not None and isinstance(entry.data, dict) and entry.data.get("gain") is not None:
            return {k: entry.data[k] for k in ("loudness", "peak", "gain")}
        if not locked:
            async with self._measure_lock:
                return await self._measure_cached(path, key, locked=True)
        try:
            res = await asyncio.to_thread(self.measure, path)
        except Exception as exc:  # noqa: BLE001 - unreadable or silent file
            log.info("replaygain %s: %s", path, exc)
            return None
        await asyncio.to_thread(self.server.cache.put, key, RG_ARTIFACT, model=RG_MODEL, version=RG_VERSION,
                                data={"has_tags": False, **res})
        return res

    def submit_replaygain(self, notify: str | None = None, session_id: str | None = None) -> Job:
        if self.measuring and self.rg_job is not None:
            return self.rg_job

        async def body(job: Job) -> dict[str, Any]:
            pending = await asyncio.to_thread(self.store.rg_pending)
            done = errors = 0
            albums: set[str] = set()
            for i, t in enumerate(pending):
                await self._wait_guardian()
                cur = await asyncio.to_thread(self.store.track, t["path"])
                if cur is None or cur["rg_state"] != "":
                    continue           # measured meanwhile (music.gain for the track being played) or removed
                job.report(i / max(1, len(pending)), Path(t["path"]).name)
                res = await self._measure_cached(t["path"], t["key"])
                await asyncio.to_thread(self.store.set_track_rg, t["path"], res)
                done += res is not None
                errors += res is None
                albums.add(t["album_key"])
                nxt = pending[i + 1]["album_key"] if i + 1 < len(pending) else None
                if nxt != t["album_key"]:
                    await asyncio.to_thread(self.store.update_album_gain, t["album_key"])
            for ak in albums:
                await asyncio.to_thread(self.store.update_album_gain, ak)
            return {"measured": done, "errors": errors, "albums": len(albums)}

        job = self.server.jobs.submit("music.replaygain", body, priority=Priority.INDEX, heavy=True,
                                      session_id=session_id, meta={"notify": notify} if notify else {})
        self.rg_job = job
        return job

    # -- decoration --------------------------------------------------------------------------------------------

    def decorate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        plays = self.store.plays([r["key"] for r in rows])
        albums: dict[str, dict[str, Any] | None] = {}
        out = []
        for r in rows:
            ak = r["album_key"]
            if ak not in albums:
                albums[ak] = self.store.album(ak)
            a = albums[ak] or {}
            n, last = plays.get(r["key"], (0, 0.0))
            out.append({
                "path": r["path"], "key": r["key"], "title": r["title"], "label": track_label(r),
                "artist": r["artist"] or r["album_artist"] or tagmod.UNKNOWN_ARTIST, "album": r["album"],
                "album_artist": r["album_artist"], "album_key": ak, "artist_key": r["artist_key"], "year": r["year"],
                "genre": r["genre"], "track": r["track_no"], "disc": r["disc_no"], "duration": r["duration"],
                "plays": n, "last_played": last, "cover": a.get("cover", ""), "exists": os.path.exists(r["path"]),
                "has_tags": r["tag_track_gain"] is not None, "gain": r["calc_gain"], "peak": r["calc_peak"],
                "album_gain": a.get("calc_gain"), "album_peak": a.get("calc_peak"), "rg_state": r["rg_state"],
                "added": r["added"],
            })
        return out

    @staticmethod
    def album_dict(a: dict[str, Any]) -> dict[str, Any]:
        return {"key": a["album_key"], "title": a["title"], "artist": a["artist"], "artist_key": a["artist_key"],
                "year": a["year"], "tracks": a["tracks"], "duration": a["duration"], "cover": a["cover"],
                "gain": a["calc_gain"], "peak": a["calc_peak"]}

    # -- views -------------------------------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        job = self.scan_job if self.scanning else None
        return {**self.store.counts(), "scanning": self.scanning,
                "progress": round(job.progress, 3) if job else 0.0, "last_scan": self.last_scan,
                "replaygain": {**self.store.rg_counts(), "running": self.measuring},
                "playlists": len(self.playlists.list()), "default_folder": str(tagmod.xdg_music_dir() or ""),
                "settings": self.settings()}

    def artist_albums(self, artist: str) -> dict[str, Any]:
        albums = [self.album_dict(a) for a in self.store.albums(artist=artist)]
        loose = self.store.tracks(album="none|" + artist)
        names = [a["artist"] for a in albums if a["artist_key"] == artist]
        name = names[0] if names else (loose[0]["artist"] if loose else "")
        if not name:
            name = next((a["name"] for a in self.store.artists() if a["key"] == artist), tagmod.UNKNOWN_ARTIST)
        if loose:
            albums.append({"key": "none|" + artist, "title": NO_ALBUM, "artist": name, "artist_key": artist,
                           "year": None, "tracks": len(loose), "duration": sum(t["duration"] for t in loose),
                           "cover": "", "gain": None, "peak": None})
        return {"artist": {"key": artist, "name": name}, "albums": albums,
                "tracks": sum(a["tracks"] for a in albums)}

    def album_view(self, album: str) -> dict[str, Any]:
        a = self.store.album(album)
        tracks = self.decorate(self.store.tracks(album=album))
        if a is None:
            if not tracks:
                raise RpcError(NOT_FOUND, "álbum desconocido")
            info = {"key": album, "title": NO_ALBUM, "artist": tracks[0]["artist"], "artist_key": tracks[0]["artist_key"],
                    "year": None, "tracks": len(tracks), "duration": sum(t["duration"] for t in tracks), "cover": "",
                    "gain": None, "peak": None}
        else:
            info = self.album_dict(a)
        return {"album": info, "tracks": tracks}

    def search(self, q: str, limit: int = 50) -> dict[str, Any]:
        rows = self.store.search(q, 500)
        words = norm(q).split()
        artists: dict[str, dict[str, Any]] = {}
        albums: dict[str, dict[str, Any]] = {}
        for r in rows:
            name = r["album_artist"] or r["artist"]
            if r["artist_key"] and r["artist_key"] not in artists and all(w in norm(name) for w in words):
                artists[r["artist_key"]] = {"key": r["artist_key"], "name": name}
            if r["album"] and r["album_key"] not in albums and \
                    all(w in norm(f"{r['album']} {name} {r['year'] or ''}") for w in words):
                a = self.store.album(r["album_key"])
                if a is not None:
                    albums[r["album_key"]] = self.album_dict(a)
        return {"query": q, "artists": list(artists.values())[:limit], "albums": list(albums.values())[:limit],
                "tracks": self.decorate(rows[:limit])}

    def smart(self, kind: str, value: Any = None, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(2000, int(limit)))
        if kind == "recent":
            rows, title = self.store.recent(limit), SMART["recent"]
        elif kind == "top":
            rows, title = self.store.top(limit), SMART["top"]
        elif kind == "unplayed":
            rows, title = self.store.unplayed(limit), SMART["unplayed"]
        elif kind == "genre":
            if not value:
                raise RpcError(INVALID_PARAMS, "value: género")
            key = norm(str(value))
            rows = self.store.tracks(genre=key)[:limit]
            title = next((g["name"] for g in self.store.genres() if g["key"] == key), str(value))
        elif kind == "year":
            rows, title = self.store.tracks(year=int(value))[:limit], str(int(value))
        elif kind == "decade":
            d = int(value) // 10 * 10
            rows = [t for t in self.store.tracks() if t["year"] and d <= t["year"] <= d + 9][:limit]
            title = f"Años {d % 100:02d}" if d >= 1950 else f"Década de {d}"
        else:
            raise RpcError(INVALID_PARAMS, "kind: recent | top | unplayed | genre | year | decade")
        return {"kind": kind, "value": value, "title": title, "tracks": self.decorate(rows)}

    def smart_lists(self) -> list[dict[str, Any]]:
        counts = {"recent": min(200, self.store.counts()["tracks"]), "top": len(self.store.top(200)),
                  "unplayed": len(self.store.unplayed(200))}
        out = [{"kind": k, "title": t, "count": counts[k]} for k, t in SMART.items()]
        decades: dict[int, int] = {}
        for y in self.store.years():
            decades[y["year"] // 10 * 10] = decades.get(y["year"] // 10 * 10, 0) + y["tracks"]
        return out + [{"kind": "decade", "value": d, "title": f"Años {d % 100:02d}" if d >= 1950 else f"Década de {d}",
                       "count": n} for d, n in sorted(decades.items(), reverse=True)]

    # -- playlists ---------------------------------------------------------------------------------------------

    def entries_for(self, paths: list[str]) -> list[dict[str, Any]]:
        known = {t["path"]: t for t in self.store.tracks(paths=[_local(p) for p in paths])}
        out = []
        for p in paths:
            t = known.get(_local(p))
            if t is not None:
                artist = t["artist"] or t["album_artist"]
                out.append({"path": t["path"], "title": f"{artist} - {t['title']}" if artist else t["title"],
                            "duration": t["duration"]})
            else:
                out.append({"path": p if _is_url(p) else os.path.abspath(_local(p)),
                            "title": "" if _is_url(p) else Path(_local(p)).stem, "duration": -1.0})
        return out

    def playlist_view(self, name: str) -> dict[str, Any]:
        data = self.playlists.get(name)
        entries = data["entries"]
        known = {t["path"]: t for t in self.decorate(self.store.tracks(paths=[e["path"] for e in entries]))}
        tracks = []
        for i, e in enumerate(entries):
            t = known.get(e["path"])
            if t is None:
                title = e["title"] or (e["path"] if _is_url(e["path"]) else Path(e["path"]).stem)
                t = {"path": e["path"], "title": title, "label": title, "artist": "", "album": "",
                     "duration": max(0.0, e["duration"] or 0.0), "exists": _is_url(e["path"]) or os.path.exists(e["path"]),
                     "plays": 0}
            tracks.append({**t, "index": i})
        return {"name": data["name"], "file": data["file"], "tracks": tracks,
                "duration": round(sum(t.get("duration") or 0 for t in tracks), 1)}

    def sort_key(self, by: str) -> Any:
        if by == "shuffle":
            return "shuffle"
        rows: dict[str, dict[str, Any]] = {}

        def row(e: dict[str, Any]) -> dict[str, Any]:
            if e["path"] not in rows:
                rows[e["path"]] = self.store.track(e["path"]) or {}
            return rows[e["path"]]

        def text(e: dict[str, Any], k: str) -> str:
            return norm(str(row(e).get(k) or ""))

        if by == "artist":
            return lambda e: (text(e, "artist_key") or "~", row(e).get("year") or 9999, text(e, "album"),
                              row(e).get("disc_no") or 0, row(e).get("track_no") or 0, norm(e["title"]))
        if by == "album":
            return lambda e: (text(e, "album") or "~", row(e).get("disc_no") or 0, row(e).get("track_no") or 0,
                              norm(e["title"]))
        if by == "title":
            return lambda e: (text(e, "title") or norm(e["title"]))
        if by == "year":
            return lambda e: (row(e).get("year") or 9999, text(e, "album"), row(e).get("track_no") or 0)
        raise RpcError(INVALID_PARAMS, "by: artist | album | title | year | shuffle")

    # -- gain for playback -------------------------------------------------------------------------------------

    async def gain(self, path: str, measure: bool = True) -> dict[str, Any]:
        """ReplayGain known or computed for a file: from the index (measured now if still pending), or for a file
        outside the library from the artifact cache (measured once and cached by content hash)."""
        local = _local(path)
        if _is_url(path) or not os.path.isfile(local):
            return {"path": path, "indexed": False, "gain": None}
        t = await asyncio.to_thread(self.store.track, local)
        if t is not None:
            if t["rg_state"] == "" and measure:
                async with self._measure_lock:
                    t = await asyncio.to_thread(self.store.track, local)
                    if t is not None and t["rg_state"] == "":
                        res = await self._measure_cached(local, t["key"], locked=True)
                        await asyncio.to_thread(self.store.set_track_rg, local, res)
                        await asyncio.to_thread(self.store.update_album_gain, t["album_key"])
                t = await asyncio.to_thread(self.store.track, local)
            if t is not None:
                a = await asyncio.to_thread(self.store.album, t["album_key"]) or {}
                return {"path": local, "indexed": True, "has_tags": t["tag_track_gain"] is not None,
                        "gain": t["calc_gain"], "peak": t["calc_peak"], "album_gain": a.get("calc_gain"),
                        "album_peak": a.get("calc_peak"), "rg_state": t["rg_state"]}
        h = await asyncio.to_thread(file_hash, local)
        entry = await asyncio.to_thread(self.server.cache.get, h.key, RG_ARTIFACT, RG_MODEL, RG_VERSION)
        data = entry.data if entry is not None and isinstance(entry.data, dict) else None
        if data is None and measure:
            async with self._measure_lock:
                try:
                    meta = await asyncio.to_thread(self.probe, Path(local))
                    if not meta.get("has_audio", True):
                        return {"path": local, "indexed": False, "gain": None}
                    data = {"has_tags": meta["tag_track_gain"] is not None}
                    data.update(await asyncio.to_thread(self.measure, local))
                except Exception as exc:  # noqa: BLE001
                    log.info("replaygain %s: %s", local, exc)
                    data = {"has_tags": False, "gain": None, "error": str(exc)}
                await asyncio.to_thread(self.server.cache.put, h.key, RG_ARTIFACT, model=RG_MODEL, version=RG_VERSION,
                                        data=data)
        data = data or {}
        return {"path": local, "indexed": False, "has_tags": bool(data.get("has_tags")), "gain": data.get("gain"),
                "peak": data.get("peak"), "album_gain": None, "album_peak": None}


def file_hash_text(text: str) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.blake2b(text.encode("utf-8"), digest_size=6).hexdigest()


def register(server: MpvdServer, service: MusicService) -> None:  # noqa: C901 - flat list of handlers
    d = server.dispatcher
    server.services["music"] = True

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    def _folder(path: str) -> str:
        p = os.path.expanduser(_local(str(path or "")).strip())
        if not p:
            raise RpcError(INVALID_PARAMS, "path required")
        return os.path.abspath(p)

    async def _t(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except FileNotFoundError as exc:
            raise RpcError(NOT_FOUND, f"no existe la lista «{exc.args[0] if exc.args else ''}»") from None
        except PlaylistError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    def _paths(paths: Any) -> list[str]:
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise RpcError(INVALID_PARAMS, "paths: lista de rutas")
        return paths

    # -- folders and scanning --

    @d.method("music.folders.list")
    async def folders_list(ctx: RpcContext) -> list[dict[str, Any]]:
        """Music folders with their file count and last scan."""
        return await asyncio.to_thread(service.store.folders)

    @d.method("music.folders.add")
    async def folders_add(ctx: RpcContext, path: str, scan: bool = True, notify: str | None = None) -> dict[str, Any]:
        """Add a folder (a file adds its folder) and scan it in the background."""
        p = _folder(path)
        if os.path.isfile(p):
            p = os.path.dirname(p)
        if not os.path.isdir(p):
            raise RpcError(NOT_FOUND, f"no existe la carpeta: {p}")
        await asyncio.to_thread(service.store.set_meta, "default_checked", "1")
        added = await asyncio.to_thread(service.store.add_folder, p)
        job = service.submit_scan([p], notify=notify, session_id=_sid(ctx)) if scan else None
        return {"path": p, "added": added, "job": job.id if job else None,
                "folders": await asyncio.to_thread(service.store.folders)}

    @d.method("music.folders.remove")
    async def folders_remove(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Forget a folder and its tracks (the files are not touched)."""
        p = _folder(path)
        n = await asyncio.to_thread(service.store.remove_folder, p)
        if n < 0:
            raise RpcError(NOT_FOUND, f"no está en la música: {p}")
        return {"path": p, "removed": n, "folders": await asyncio.to_thread(service.store.folders)}

    @d.method("music.scan")
    async def scan(ctx: RpcContext, path: str | None = None, force: bool = False, wait: bool = False,
                   notify: str | None = None) -> dict[str, Any]:
        """Rescan every folder (or one) in the background; ``wait`` returns the result."""
        folders = [_folder(path)] if path else None
        if folders and folders[0] not in {f["path"] for f in await asyncio.to_thread(service.store.folders)}:
            raise RpcError(NOT_FOUND, f"no está en la música: {folders[0]}")
        job = service.submit_scan(folders, bool(force), notify, _sid(ctx))
        if wait:
            await job.wait()
            if job.status != Status.DONE:
                raise RpcError(UNAVAILABLE, job.error or job.status.value)
            return {"job": job.id, "status": job.status.value, "result": job.result}
        return {"job": job.id, "status": job.status.value}

    @d.method("music.replaygain.scan")
    async def rg_scan(ctx: RpcContext, wait: bool = False, notify: str | None = None) -> dict[str, Any]:
        """Measure now (background, INDEX priority) the tracks without ReplayGain tags nor a computed gain."""
        job = service.submit_replaygain(notify, _sid(ctx))
        if wait:
            await job.wait()
            if job.status != Status.DONE:
                raise RpcError(UNAVAILABLE, job.error or job.status.value)
            return {"job": job.id, "status": job.status.value, "result": job.result}
        return {"job": job.id, "status": job.status.value}

    @d.method("music.status")
    async def status(ctx: RpcContext, default_folder: bool = True) -> dict[str, Any]:
        """Counts, scan and ReplayGain state; the first call adds the user's Music folder (once)."""
        if default_folder:
            await asyncio.to_thread(service.ensure_default_folder)
        service.maybe_rescan()
        return await asyncio.to_thread(service.summary)

    @d.method("music.settings.get")
    async def settings_get(ctx: RpcContext) -> dict[str, Any]:
        """auto_replaygain: measure tracks without ReplayGain tags in the background after each scan."""
        return service.settings()

    @d.method("music.settings.set")
    async def settings_set(ctx: RpcContext, auto_replaygain: bool | None = None) -> dict[str, Any]:
        """Change auto_replaygain (yes/no)."""
        if auto_replaygain is not None:
            if not isinstance(auto_replaygain, bool):
                raise RpcError(INVALID_PARAMS, "auto_replaygain: sí/no")
            await asyncio.to_thread(service.store.set_meta, "auto_replaygain", "1" if auto_replaygain else "0")
            if auto_replaygain and service.store.rg_counts()["pending"]:
                service.submit_replaygain()
        return service.settings()

    # -- browsing --

    @d.method("music.artists")
    async def artists(ctx: RpcContext) -> list[dict[str, Any]]:
        """Artists (album artist, else track artist) with their album and track counts."""
        return await asyncio.to_thread(service.store.artists)

    @d.method("music.artist")
    async def artist(ctx: RpcContext, key: str) -> dict[str, Any]:
        """One artist: albums by year (+ «Sin álbum» for loose tracks)."""
        return await asyncio.to_thread(service.artist_albums, str(key or ""))

    @d.method("music.albums")
    async def albums(ctx: RpcContext, artist: str | None = None, genre: str | None = None, year: int | None = None,
                     decade: int | None = None) -> list[dict[str, Any]]:
        """Albums, optionally of an artist, a genre, a year or a decade."""
        rows = await asyncio.to_thread(service.store.albums, artist, norm(genre) if genre else None, year, decade)
        return [service.album_dict(a) for a in rows]

    @d.method("music.album")
    async def album(ctx: RpcContext, key: str) -> dict[str, Any]:
        """An album and its tracks in disc/track order."""
        return await asyncio.to_thread(service.album_view, str(key or ""))

    @d.method("music.tracks")
    async def tracks(ctx: RpcContext, album: str | None = None, artist: str | None = None, genre: str | None = None,
                     year: int | None = None) -> list[dict[str, Any]]:
        """Tracks filtered by album, artist, genre or year."""
        rows = await asyncio.to_thread(service.store.tracks, album, artist, norm(genre) if genre else None, year)
        return await asyncio.to_thread(service.decorate, rows)

    @d.method("music.genres")
    async def genres(ctx: RpcContext) -> list[dict[str, Any]]:
        """Genres with track and album counts."""
        return await asyncio.to_thread(service.store.genres)

    @d.method("music.years")
    async def years(ctx: RpcContext) -> list[dict[str, Any]]:
        """Years with track and album counts (newest first)."""
        return await asyncio.to_thread(service.store.years)

    @d.method("music.search")
    async def search(ctx: RpcContext, q: str, limit: int = 50) -> dict[str, Any]:
        """Accent-insensitive search in titles, artists, albums, genres, years and file names."""
        return await asyncio.to_thread(service.search, str(q or ""), max(1, min(200, int(limit))))

    @d.method("music.track")
    async def track(ctx: RpcContext, path: str) -> dict[str, Any]:
        """A file of the library (``indexed: false`` otherwise)."""
        t = await asyncio.to_thread(service.store.track, _local(str(path or "")))
        if t is None:
            return {"path": path, "indexed": False}
        return {**(await asyncio.to_thread(service.decorate, [t]))[0], "indexed": True}

    @d.method("music.describe")
    async def describe(ctx: RpcContext, paths: list[str]) -> list[dict[str, Any]]:
        """Titles for a list of paths (the queue): library tags when indexed, the file name otherwise."""
        paths = _paths(paths)[:5000]
        rows = await asyncio.to_thread(service.store.tracks, None, None, None, None, [_local(p) for p in paths])
        known = {t["path"]: t for t in await asyncio.to_thread(service.decorate, rows)}
        out = []
        for p in paths:
            t = known.get(_local(p))
            if t is not None:
                out.append({"path": p, "title": t["title"], "artist": t["artist"], "album": t["album"],
                            "duration": t["duration"], "indexed": True})
            else:
                out.append({"path": p, "title": p if _is_url(p) else Path(_local(p)).stem, "artist": "", "album": "",
                            "duration": 0.0, "indexed": False})
        return out

    @d.method("music.gain")
    async def gain(ctx: RpcContext, path: str, measure: bool = True) -> dict[str, Any]:
        """ReplayGain for playback: tags present, computed track and album gain (measured now when missing)."""
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        return await service.gain(str(path), bool(measure))

    @d.method("music.cover")
    async def cover(ctx: RpcContext, path: str | None = None, album: str | None = None) -> dict[str, Any]:
        """Cover of an album (or of the album of a file): folder image or embedded picture (cached)."""
        key = album
        if key is None and path:
            t = await asyncio.to_thread(service.store.track, _local(path))
            key = t["album_key"] if t else None
        a = await asyncio.to_thread(service.store.album, key) if key else None
        return {"cover": (a or {}).get("cover", "")}

    # -- smart lists and history --

    @d.method("music.smart")
    async def smart(ctx: RpcContext, kind: str | None = None, value: Any = None, limit: int = 200) -> Any:
        """Smart lists: recent | top | unplayed | genre (value) | year (value) | decade (value); nothing: the list."""
        if not kind:
            return await asyncio.to_thread(service.smart_lists)
        return await asyncio.to_thread(service.smart, str(kind), value, limit)

    @d.method("music.played")
    async def played(ctx: RpcContext, path: str, seconds: float = 0.0, title: str = "", artist: str = "",
                     album: str = "") -> dict[str, Any]:
        """Record a listen in the local history (never sent anywhere)."""
        local = _local(str(path or ""))
        if not local:
            raise RpcError(INVALID_PARAMS, "path required")
        t = await asyncio.to_thread(service.store.track, local)
        if t is not None:
            key, title, artist, album = t["key"], t["title"], t["artist"] or t["album_artist"], t["album"]
        elif not _is_url(local) and os.path.isfile(local):
            key = (await asyncio.to_thread(file_hash, local)).key
        else:
            key = ""
        await asyncio.to_thread(service.store.add_history, local, key, title or Path(local).stem, artist, album,
                                float(seconds or 0))
        return {"recorded": True, "indexed": t is not None}

    @d.method("music.history")
    async def history(ctx: RpcContext, limit: int = 100) -> list[dict[str, Any]]:
        """Latest listens, newest first."""
        rows = await asyncio.to_thread(service.store.history, max(1, min(1000, int(limit))))
        for r in rows:
            r["exists"] = _is_url(r["path"]) or os.path.exists(r["path"])
        return rows

    @d.method("music.history.clear")
    async def history_clear(ctx: RpcContext) -> dict[str, Any]:
        """Forget the listening history."""
        return {"removed": await asyncio.to_thread(service.store.clear_history)}

    # -- playlists (M3U8) --

    @d.method("music.playlists.list")
    async def pl_list(ctx: RpcContext) -> list[dict[str, Any]]:
        """Saved playlists (name, file, count, duration)."""
        return await asyncio.to_thread(service.playlists.list)

    @d.method("music.playlists.get")
    async def pl_get(ctx: RpcContext, name: str) -> dict[str, Any]:
        """A playlist with its tracks (index = position)."""
        return await _t(service.playlist_view, name)

    @d.method("music.playlists.create")
    async def pl_create(ctx: RpcContext, name: str, paths: list[str] | None = None,
                        replace: bool = False) -> dict[str, Any]:
        """New playlist (optionally with tracks: e.g. the current queue); ``replace`` overwrites."""
        entries = await asyncio.to_thread(service.entries_for, _paths(paths)) if paths else []
        await _t(service.playlists.create, name, entries, bool(replace))
        return await _t(service.playlist_view, name)

    @d.method("music.playlists.rename")
    async def pl_rename(ctx: RpcContext, name: str, new_name: str) -> dict[str, Any]:
        """Rename a playlist."""
        res = await _t(service.playlists.rename, name, new_name)
        return await _t(service.playlist_view, res["name"])

    @d.method("music.playlists.delete")
    async def pl_delete(ctx: RpcContext, name: str) -> dict[str, Any]:
        """Remove a playlist (the file goes to playlists/.papelera)."""
        return {"trash": await _t(service.playlists.delete, name)}

    @d.method("music.playlists.add")
    async def pl_add(ctx: RpcContext, name: str, paths: list[str], position: int | None = None,
                     create: bool = True) -> dict[str, Any]:
        """Append tracks (or insert them at ``position``); the list is created when missing."""
        entries = await asyncio.to_thread(service.entries_for, _paths(paths))
        await _t(service.playlists.add, name, entries, position, bool(create))
        return await _t(service.playlist_view, name)

    @d.method("music.playlists.remove")
    async def pl_remove(ctx: RpcContext, name: str, index: int | list[int]) -> dict[str, Any]:
        """Remove entries by position (0-based)."""
        idx = index if isinstance(index, list) else [index]
        await _t(service.playlists.remove, name, [int(i) for i in idx])
        return await _t(service.playlist_view, name)

    @d.method("music.playlists.move")
    async def pl_move(ctx: RpcContext, name: str, src: int, dst: int) -> dict[str, Any]:
        """Move the entry at ``src`` to ``dst`` (0-based final position)."""
        await _t(service.playlists.move, name, int(src), int(dst))
        return await _t(service.playlist_view, name)

    @d.method("music.playlists.sort")
    async def pl_sort(ctx: RpcContext, name: str, by: str = "artist") -> dict[str, Any]:
        """Sort by artist | album | title | year, or shuffle."""
        await _t(service.playlists.reorder, name, service.sort_key(str(by)))
        return await _t(service.playlist_view, name)

    @d.method("music.playlists.import")
    async def pl_import(ctx: RpcContext, path: str, name: str | None = None) -> dict[str, Any]:
        """Copy an .m3u/.m3u8 file into the saved playlists (relative paths resolved)."""
        src = Path(_folder(path))
        if not src.is_file():
            raise RpcError(NOT_FOUND, f"no existe: {src}")
        res = await _t(service.playlists.import_file, src, name)
        return await _t(service.playlist_view, res["name"])

    @d.method("music.playlists.export")
    async def pl_export(ctx: RpcContext, name: str, path: str, relative: bool = False) -> dict[str, Any]:
        """Write a playlist to ``path`` (a folder or a file name) as M3U8; ``relative`` paths for portable copies."""
        dest = Path(_folder(path))
        if not dest.is_dir() and dest.suffix.lower() not in (".m3u8", ".m3u"):
            dest = dest.with_name(dest.name + ".m3u8")
        if not dest.is_dir() and not dest.parent.is_dir():
            raise RpcError(NOT_FOUND, f"no existe la carpeta: {dest.parent}")
        return {"path": await _t(service.playlists.export_file, name, dest, bool(relative))}
