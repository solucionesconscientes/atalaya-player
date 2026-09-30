"""SQLite index of the music library (``<data_dir>/music.sqlite3``): chosen folders, one row per audio file (tags read
with ffprobe, content key of ``mpvd.hashing``), albums rebuilt after every scan (display artist, year, cover, computed
album gain), genres, and the local listening history (no scrobbling: it never leaves the machine).

Incremental scan like the video library (``mpvd/library/store.py``): a file whose size and mtime did not change keeps
its row (no ffprobe, no hashing); rows are written in small batches so the lock is never held for long. Albums are
keyed by album artist + album title, or by folder + album title when there is no album artist (compilations stay
together). ReplayGain computed by mpvd lives in ``loudness/calc_gain/calc_peak`` (the files are never rewritten).
Everything here is synchronous: the service calls it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.hashing import file_hash
from mpvd.library.parse import norm
from mpvd.music import tags as tagmod

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
  path TEXT PRIMARY KEY,
  added REAL NOT NULL,
  last_scan REAL NOT NULL DEFAULT 0,
  files INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tracks (
  path TEXT PRIMARY KEY,
  folder TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  key TEXT NOT NULL,
  title TEXT NOT NULL,
  artist TEXT NOT NULL DEFAULT '',
  album_artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  year INTEGER,
  genre TEXT NOT NULL DEFAULT '',
  track_no INTEGER,
  disc_no INTEGER,
  duration REAL NOT NULL DEFAULT 0,
  codec TEXT NOT NULL DEFAULT '',
  has_pic INTEGER NOT NULL DEFAULT 0,
  artist_key TEXT NOT NULL DEFAULT '',
  album_key TEXT NOT NULL DEFAULT '',
  tag_track_gain REAL, tag_track_peak REAL, tag_album_gain REAL, tag_album_peak REAL,
  loudness REAL, calc_gain REAL, calc_peak REAL,
  rg_state TEXT NOT NULL DEFAULT '',
  added REAL NOT NULL,
  search TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_key);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist_key);
CREATE INDEX IF NOT EXISTS tracks_key ON tracks(key);
CREATE INDEX IF NOT EXISTS tracks_folder ON tracks(folder);
CREATE TABLE IF NOT EXISTS track_genres (
  path TEXT NOT NULL,
  genre_key TEXT NOT NULL,
  genre TEXT NOT NULL,
  PRIMARY KEY (path, genre_key)
);
CREATE INDEX IF NOT EXISTS genres_key ON track_genres(genre_key);
CREATE TABLE IF NOT EXISTS albums (
  album_key TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  artist TEXT NOT NULL,
  artist_key TEXT NOT NULL,
  year INTEGER,
  tracks INTEGER NOT NULL DEFAULT 0,
  duration REAL NOT NULL DEFAULT 0,
  folder TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  cover_source TEXT NOT NULL DEFAULT '',
  cover_track TEXT NOT NULL DEFAULT '',
  calc_gain REAL, calc_peak REAL
);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  key TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  at REAL NOT NULL,
  seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS history_key ON history(key);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

MAX_DEPTH = 10
BATCH = 50
NO_ALBUM = "Sin álbum"
VARIOUS = "Varios artistas"

ProbeFn = Callable[[Path], dict[str, Any]]


@dataclass
class ScanStats:
    folders: int = 0
    seen: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    errors: int = 0
    seconds: float = 0.0
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("folders", "seen", "added", "updated", "unchanged", "removed", "errors",
                                               "missing")} | {"seconds": round(self.seconds, 3)}


def album_dir(path: str) -> str:
    d = Path(path).parent
    return str(d.parent if tagmod.DISC_DIR_RX.match(d.name) else d)


def keys_for(meta: dict[str, Any], path: str) -> tuple[str, str]:
    """(artist_key, album_key) of a probed track."""
    lead = meta.get("album_artist") or meta.get("artist") or ""
    artist_key = norm(lead)
    album = norm(meta.get("album") or "")
    if not album:
        return artist_key, "none|" + artist_key
    if meta.get("album_artist"):
        return artist_key, norm(meta["album_artist"]) + "|" + album
    dir_id = hashlib.blake2b(album_dir(path).encode("utf-8", "surrogateescape"), digest_size=6).hexdigest()
    return artist_key, f"dir:{dir_id}|{album}"


class MusicStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _all(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: Any = ()) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    # -- meta ------------------------------------------------------------------------------------------------------

    def get_meta(self, k: str, default: str = "") -> str:
        r = self._one("SELECT v FROM meta WHERE k=?", (k,))
        return r["v"] if r else default

    def set_meta(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._conn.commit()

    # -- folders ---------------------------------------------------------------------------------------------------

    def folders(self) -> list[dict[str, Any]]:
        return [r | {"exists": Path(r["path"]).is_dir()} for r in self._all("SELECT * FROM folders ORDER BY path")]

    def add_folder(self, path: str) -> bool:
        with self._lock:
            cur = self._conn.execute("INSERT OR IGNORE INTO folders (path, added) VALUES (?, ?)", (path, time.time()))
            self._conn.commit()
            return cur.rowcount > 0

    def remove_folder(self, path: str) -> int:
        with self._lock:
            n = self._conn.execute("DELETE FROM folders WHERE path=?", (path,)).rowcount
            removed = self._conn.execute("DELETE FROM tracks WHERE folder=?", (path,)).rowcount
            self._conn.execute("DELETE FROM track_genres WHERE path NOT IN (SELECT path FROM tracks)")
            self._conn.commit()
        self.rebuild_albums()
        return removed if n else -1

    # -- scanning --------------------------------------------------------------------------------------------------

    @staticmethod
    def walk(root: Path) -> list[Path]:
        out: list[Path] = []
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if len(d.parts) - base >= MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
            out.extend(d / n for n in sorted(filenames) if tagmod.is_audio(n))
        return out

    def scan(self, probe: ProbeFn, folders: list[str] | None = None,
             progress: Callable[[float, str], None] | None = None, cancelled: Callable[[], bool] | None = None,
             force: bool = False) -> ScanStats:
        stats = ScanStats()
        t0 = time.monotonic()
        roots = folders or [f["path"] for f in self.folders()]
        with self._lock:
            known = {r["path"]: (r["size"], r["mtime"], r["folder"]) for r in
                     self._conn.execute("SELECT path, size, mtime, folder FROM tracks")}
        seen: set[str] = set()
        for fi, root_s in enumerate(roots):
            root = Path(root_s)
            stats.folders += 1
            if not root.is_dir():
                stats.missing.append(root_s)
                continue
            files = self.walk(root)
            batch: list[tuple[str, str, os.stat_result, dict[str, Any], str, bool]] = []
            for i, f in enumerate(files):
                if cancelled is not None and cancelled():
                    self._write(batch)
                    raise InterruptedError("scan cancelled")
                sp = str(f)
                if sp in seen:
                    continue
                seen.add(sp)
                stats.seen += 1
                try:
                    st = f.stat()
                except OSError:
                    stats.errors += 1
                    continue
                old = known.get(sp)
                if old is not None and old[0] == st.st_size and abs(old[1] - st.st_mtime) < 1e-6 and not force:
                    stats.unchanged += 1
                else:
                    try:
                        meta = probe(f)
                        key = file_hash(f).key
                    except Exception:  # noqa: BLE001 - one unreadable file must not stop the scan
                        stats.errors += 1
                        continue
                    if not meta.get("has_audio", True):
                        continue
                    batch.append((sp, old[2] if old is not None else root_s, st, meta, key, old is None))
                    if old is None:
                        stats.added += 1
                    else:
                        stats.updated += 1
                    if len(batch) >= BATCH:
                        self._write(batch)
                        batch = []
                if progress is not None and (i % 20 == 0 or i == len(files) - 1):
                    progress((fi + (i + 1) / max(1, len(files))) / max(1, len(roots)), f.name)
            self._write(batch)
            gone = [p for p, k in known.items() if k[2] == root_s and p not in seen]
            with self._lock:
                self._conn.executemany("DELETE FROM tracks WHERE path=?", [(p,) for p in gone])
                self._conn.executemany("DELETE FROM track_genres WHERE path=?", [(p,) for p in gone])
                stats.removed += len(gone)
                n = self._conn.execute("SELECT COUNT(*) FROM tracks WHERE folder=?", (root_s,)).fetchone()[0]
                self._conn.execute("UPDATE folders SET last_scan=?, files=? WHERE path=?", (time.time(), n, root_s))
                self._conn.commit()
        self.rebuild_albums()
        stats.seconds = time.monotonic() - t0
        return stats

    def _write(self, batch: list[tuple[str, str, os.stat_result, dict[str, Any], str, bool]]) -> None:
        if not batch:
            return
        now = time.time()
        rows, genres = [], []
        for sp, folder, st, m, key, _new in batch:
            artist_key, album_key = keys_for(m, sp)
            search = norm(" ".join(str(x) for x in (m["title"], m["artist"], m["album_artist"], m["album"],
                                                     " ".join(m["genres"]), m["year"] or "", Path(sp).stem) if x))
            rows.append((sp, folder, st.st_size, st.st_mtime, key, m["title"], m["artist"], m["album_artist"],
                         m["album"], m["year"], "; ".join(m["genres"]), m["track_no"], m["disc_no"], m["duration"],
                         m["codec"], int(m["has_pic"]), artist_key, album_key, m["tag_track_gain"], m["tag_track_peak"],
                         m["tag_album_gain"], m["tag_album_peak"], "tags" if m["tag_track_gain"] is not None else "",
                         now, search))
            genres.extend((sp, norm(g), g) for g in m["genres"] if norm(g))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO tracks (path,folder,size,mtime,key,title,artist,album_artist,album,year,genre,track_no,"
                "disc_no,duration,codec,has_pic,artist_key,album_key,tag_track_gain,tag_track_peak,tag_album_gain,"
                "tag_album_peak,rg_state,added,search) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(path) DO UPDATE SET folder=excluded.folder, size=excluded.size, mtime=excluded.mtime,"
                " key=excluded.key, title=excluded.title, artist=excluded.artist, album_artist=excluded.album_artist,"
                " album=excluded.album, year=excluded.year, genre=excluded.genre, track_no=excluded.track_no,"
                " disc_no=excluded.disc_no, duration=excluded.duration, codec=excluded.codec, has_pic=excluded.has_pic,"
                " artist_key=excluded.artist_key, album_key=excluded.album_key, tag_track_gain=excluded.tag_track_gain,"
                " tag_track_peak=excluded.tag_track_peak, tag_album_gain=excluded.tag_album_gain,"
                " tag_album_peak=excluded.tag_album_peak, rg_state=excluded.rg_state, search=excluded.search,"
                " loudness=CASE WHEN tracks.key=excluded.key THEN tracks.loudness END,"
                " calc_gain=CASE WHEN tracks.key=excluded.key THEN tracks.calc_gain END,"
                " calc_peak=CASE WHEN tracks.key=excluded.key THEN tracks.calc_peak END", rows)
            # an unchanged audio stream (only the tags were edited) keeps its measured loudness
            self._conn.execute("UPDATE tracks SET rg_state='done' WHERE rg_state='' AND calc_gain IS NOT NULL")
            self._conn.executemany("DELETE FROM track_genres WHERE path=?", [(b[0],) for b in batch])
            self._conn.executemany("INSERT OR IGNORE INTO track_genres (path, genre_key, genre) VALUES (?,?,?)", genres)
            self._conn.commit()

    def rebuild_albums(self) -> None:
        """Albums from the tracks: display artist (album artist, the only artist or «Varios artistas»), the most common
        year, cover found in the album folder (or an embedded picture to extract later). Covers and computed gains of
        albums that still exist are kept."""
        tracks = self._all("SELECT path, album_key, album, album_artist, artist, artist_key, year, duration, has_pic"
                           " FROM tracks WHERE album_key NOT LIKE 'none|%' ORDER BY disc_no, track_no, path")
        groups: dict[str, list[dict[str, Any]]] = {}
        for t in tracks:
            groups.setdefault(t["album_key"], []).append(t)
        with self._lock:
            old = {r["album_key"]: dict(r) for r in self._conn.execute("SELECT * FROM albums")}
        images: dict[Path, dict[str, str]] = {}
        rows = []
        for ak, ts in groups.items():
            artists = {norm(t["artist"]) for t in ts if t["artist"]}
            if ts[0]["album_artist"]:
                artist = ts[0]["album_artist"]
            elif len(artists) > 1:
                artist = VARIOUS
            else:
                artist = next((t["artist"] for t in ts if t["artist"]), tagmod.UNKNOWN_ARTIST)
            years = Counter(t["year"] for t in ts if t["year"])
            folder = album_dir(ts[0]["path"])
            prev = old.get(ak) or {}
            cover = tagmod.folder_cover(Path(ts[0]["path"]).parent, images)
            source = "folder" if cover else ""
            cover_track = next((t["path"] for t in ts if t["has_pic"]), "")
            if not cover and prev.get("cover_source") == "embedded" and prev.get("cover") and \
                    os.path.exists(prev["cover"]) and cover_track:
                cover, source = prev["cover"], "embedded"
            rows.append((ak, ts[0]["album"] or NO_ALBUM, artist, norm(artist) if artist != VARIOUS else "varios",
                         years.most_common(1)[0][0] if years else None, len(ts),
                         round(sum(t["duration"] or 0 for t in ts), 3), folder, cover, source, cover_track,
                         prev.get("calc_gain"), prev.get("calc_peak")))
        with self._lock:
            self._conn.execute("DELETE FROM albums")
            self._conn.executemany("INSERT INTO albums (album_key,title,artist,artist_key,year,tracks,duration,folder,"
                                   "cover,cover_source,cover_track,calc_gain,calc_peak) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                   rows)
            self._conn.commit()

    # -- covers and ReplayGain -------------------------------------------------------------------------------------

    def albums_needing_cover(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM albums WHERE cover='' AND cover_track!='' ORDER BY title")

    def set_album_cover(self, album_key: str, cover: str, source: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE albums SET cover=?, cover_source=? WHERE album_key=?", (cover, source, album_key))
            self._conn.commit()

    def rg_pending(self, limit: int = 100000) -> list[dict[str, Any]]:
        return self._all("SELECT path, key, album_key, duration FROM tracks WHERE rg_state='' ORDER BY album_key,"
                         " disc_no, track_no, path LIMIT ?", (int(limit),))

    def rg_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT rg_state, COUNT(*) FROM tracks GROUP BY rg_state").fetchall()
        c = {r[0] or "pending": r[1] for r in rows}
        return {"pending": c.get("pending", 0), "done": c.get("done", 0), "tags": c.get("tags", 0),
                "error": c.get("error", 0)}

    def set_track_rg(self, path: str, res: dict[str, float] | None) -> None:
        with self._lock:
            if res is None:
                self._conn.execute("UPDATE tracks SET rg_state='error' WHERE path=?", (path,))
            else:
                self._conn.execute("UPDATE tracks SET loudness=?, calc_gain=?, calc_peak=?, rg_state='done' WHERE path=?",
                                   (res["loudness"], res["gain"], res["peak"], path))
            self._conn.commit()

    def update_album_gain(self, album_key: str) -> tuple[float, float] | None:
        """Album gain from its measured tracks, once none of them is waiting."""
        ts = self._all("SELECT loudness, duration, calc_peak, rg_state FROM tracks WHERE album_key=?", (album_key,))
        if not ts or any(t["rg_state"] == "" for t in ts) or album_key.startswith("none|"):
            return None
        res = tagmod.album_gain([(t["loudness"], t["duration"] or 0.0, t["calc_peak"] or 0.0)
                                 for t in ts if t["loudness"] is not None])
        if res is None:
            return None
        with self._lock:
            self._conn.execute("UPDATE albums SET calc_gain=?, calc_peak=? WHERE album_key=?", (res[0], res[1], album_key))
            self._conn.commit()
        return res

    # -- views -----------------------------------------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        with self._lock:
            c = self._conn
            return {
                "tracks": c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
                "albums": c.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
                "artists": c.execute("SELECT COUNT(DISTINCT artist_key) FROM tracks").fetchone()[0],
                "genres": c.execute("SELECT COUNT(DISTINCT genre_key) FROM track_genres").fetchone()[0],
                "folders": c.execute("SELECT COUNT(*) FROM folders").fetchone()[0],
                "history": c.execute("SELECT COUNT(*) FROM history").fetchone()[0],
            }

    def artists(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT artist_key AS key, COUNT(*) AS tracks, COUNT(DISTINCT album_key) AS albums,"
                         " MIN(CASE WHEN album_artist!='' THEN album_artist ELSE artist END) AS name"
                         " FROM tracks GROUP BY artist_key")
        for r in rows:
            r["name"] = r["name"] or tagmod.UNKNOWN_ARTIST
        rows.sort(key=lambda r: (r["key"] == "", norm(r["name"])))
        return rows

    def albums(self, artist: str | None = None, genre: str | None = None, year: int | None = None,
               decade: int | None = None) -> list[dict[str, Any]]:
        sql, args, where = "SELECT a.* FROM albums a", [], []
        if artist is not None:
            # the albums of an artist: where they are the album artist or sing at least one track
            where.append("(a.artist_key=? OR a.album_key IN (SELECT album_key FROM tracks WHERE artist_key=?))")
            args += [artist, artist]
        if genre is not None:
            where.append("a.album_key IN (SELECT t.album_key FROM tracks t JOIN track_genres g ON g.path=t.path"
                         " WHERE g.genre_key=?)")
            args.append(genre)
        if year is not None:
            where.append("a.year=?")
            args.append(int(year))
        if decade is not None:
            where.append("a.year BETWEEN ? AND ?")
            args += [int(decade), int(decade) + 9]
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self._all(sql, args)
        if artist is not None:
            rows.sort(key=lambda r: (r["year"] or 9999, norm(r["title"])))
        else:
            rows.sort(key=lambda r: (norm(r["title"]), norm(r["artist"])))
        return rows

    def album(self, album_key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM albums WHERE album_key=?", (album_key,))

    def tracks(self, album: str | None = None, artist: str | None = None, genre: str | None = None,
               year: int | None = None, paths: list[str] | None = None,
               decade: int | None = None) -> list[dict[str, Any]]:
        sql, args, where = "SELECT t.* FROM tracks t", [], []
        if album is not None:
            where.append("t.album_key=?")
            args.append(album)
        if artist is not None:
            where.append("t.artist_key=?")
            args.append(artist)
        if genre is not None:
            where.append("t.path IN (SELECT path FROM track_genres WHERE genre_key=?)")
            args.append(genre)
        if year is not None:
            where.append("t.year=?")
            args.append(int(year))
        if decade is not None:       # filtered in SQL: «Años 90» used to bring the whole table into Python
            d = int(decade) // 10 * 10
            where.append("t.year BETWEEN ? AND ?")
            args += [d, d + 9]
        if paths is not None:
            out: dict[str, dict[str, Any]] = {}
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                for r in self._all("SELECT * FROM tracks WHERE path IN (%s)" % ",".join("?" * len(chunk)), chunk):
                    out[r["path"]] = r
            return [out[p] for p in paths if p in out]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t.album_key, t.disc_no, t.track_no, t.path"
        rows = self._all(sql, args)
        if album is None:
            rows.sort(key=lambda r: (r["year"] or 9999, norm(r["album"]), r["disc_no"] or 0, r["track_no"] or 0,
                                     r["path"]))
        return rows

    def track(self, path: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM tracks WHERE path=?", (path,))

    def track_by_key(self, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM tracks WHERE key=? ORDER BY path LIMIT 1", (key,))

    def genres(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT g.genre_key AS key, MIN(g.genre) AS name, COUNT(*) AS tracks,"
                         " COUNT(DISTINCT t.album_key) AS albums FROM track_genres g JOIN tracks t ON t.path=g.path"
                         " GROUP BY g.genre_key")
        rows.sort(key=lambda r: norm(r["name"]))
        return rows

    def years(self) -> list[dict[str, Any]]:
        return self._all("SELECT year, COUNT(*) AS tracks, COUNT(DISTINCT album_key) AS albums FROM tracks"
                         " WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC")

    def search(self, q: str, limit: int = 200) -> list[dict[str, Any]]:
        words = norm(q).split()
        if not words:
            return []
        sql = "SELECT * FROM tracks WHERE " + " AND ".join("search LIKE ?" for _ in words)
        sql += " ORDER BY artist_key, album_key, disc_no, track_no, path LIMIT ?"
        return self._all(sql, [f"%{w}%" for w in words] + [int(limit)])

    # -- smart lists -----------------------------------------------------------------------------------------------

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM tracks ORDER BY added DESC, album_key, disc_no, track_no, path LIMIT ?",
                         (int(limit),))

    def top(self, limit: int) -> list[dict[str, Any]]:
        return self._all("SELECT t.*, h.n AS plays FROM tracks t JOIN (SELECT key, COUNT(*) AS n, MAX(at) AS last"
                         " FROM history WHERE key!='' GROUP BY key) h ON h.key=t.key ORDER BY h.n DESC, h.last DESC"
                         " LIMIT ?", (int(limit),))

    def unplayed(self, limit: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM tracks WHERE key NOT IN (SELECT key FROM history WHERE key!='')"
                         " ORDER BY added DESC, album_key, disc_no, track_no, path LIMIT ?", (int(limit),))

    def plays(self, keys: list[str]) -> dict[str, tuple[int, float]]:
        out: dict[str, tuple[int, float]] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            for r in self._all("SELECT key, COUNT(*) AS n, MAX(at) AS last FROM history WHERE key IN (%s) GROUP BY key"
                               % ",".join("?" * len(chunk)), chunk):
                out[r["key"]] = (r["n"], r["last"])
        return out

    # -- history ---------------------------------------------------------------------------------------------------

    def add_history(self, path: str, key: str, title: str, artist: str, album: str, seconds: float) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO history (path, key, title, artist, album, at, seconds) VALUES (?,?,?,?,?,?,?)",
                               (path, key, title, artist, album, time.time(), float(seconds)))
            self._conn.commit()

    def history(self, limit: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM history ORDER BY id DESC LIMIT ?", (int(limit),))

    def clear_history(self) -> int:
        with self._lock:
            n = self._conn.execute("DELETE FROM history").rowcount
            self._conn.commit()
        return n
