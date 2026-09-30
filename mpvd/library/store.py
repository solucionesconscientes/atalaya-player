"""SQLite index of the library (``<data_dir>/library.sqlite3``): chosen folders, one row per video file (identity, parsed
name, local poster) and one row per group (a movie or a series: display title, poster, optional online metadata).

Incremental scan: a file whose size and mtime did not change keeps its row (no hashing, no parsing); new or changed files
are hashed (``mpvd.hashing``: the same ``mu:`` key as "continue watching", plus the OpenSubtitles hash) and parsed;
files that disappeared are dropped. Everything here is synchronous: the service calls it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.hashing import file_hash
from mpvd.library.parse import EXTRA_DIRS, MediaName, is_sample, is_video, norm, parse_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
  path TEXT PRIMARY KEY,
  added REAL NOT NULL,
  last_scan REAL NOT NULL DEFAULT 0,
  files INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
  path TEXT PRIMARY KEY,
  folder TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  key TEXT NOT NULL,
  oshash TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  group_key TEXT NOT NULL,
  year INTEGER,
  season INTEGER,
  episode INTEGER,
  episode_end INTEGER,
  episode_title TEXT NOT NULL DEFAULT '',
  poster TEXT NOT NULL DEFAULT '',
  added REAL NOT NULL,
  search TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS items_key ON items(key);
CREATE INDEX IF NOT EXISTS items_group ON items(kind, group_key);
CREATE INDEX IF NOT EXISTS items_folder ON items(folder);
CREATE TABLE IF NOT EXISTS groups (
  kind TEXT NOT NULL,
  group_key TEXT NOT NULL,
  title TEXT NOT NULL,
  year INTEGER,
  poster TEXT NOT NULL DEFAULT '',
  poster_source TEXT NOT NULL DEFAULT '',
  online TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (kind, group_key)
);
"""

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
GROUP_POSTER_NAMES = ("poster", "folder", "cover", "show", "movie")
MAX_DEPTH = 8                   # folders below a library folder that are still scanned


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


def _images(folder: Path, cache: dict[Path, dict[str, str]]) -> dict[str, str]:
    """lower-case file name → real name of the images of a folder (listed once per scan)."""
    got = cache.get(folder)
    if got is None:
        got = {}
        try:
            with os.scandir(folder) as it:
                for e in it:
                    if e.name.lower().endswith(IMAGE_EXTS) and not e.name.startswith("."):
                        got[e.name.lower()] = e.name
        except OSError:
            pass
        cache[folder] = got
    return got


def _find_image(folder: Path, stems: Iterable[str], cache: dict[Path, dict[str, str]]) -> str:
    imgs = _images(folder, cache)
    if not imgs:
        return ""
    for stem in stems:
        for ext in IMAGE_EXTS:
            real = imgs.get(stem.lower() + ext)
            if real:
                return str(folder / real)
    return ""


def item_poster(path: Path, cache: dict[Path, dict[str, str]]) -> str:
    """``<name>.jpg`` / ``<name>-poster.jpg`` / ``<name>-thumb.jpg`` next to the video."""
    return _find_image(path.parent, (path.stem, path.stem + "-poster", path.stem + "-thumb"), cache)


def group_poster(path: Path, name: MediaName, root: Path, cache: dict[Path, dict[str, str]]) -> str:
    """poster/folder/cover.jpg next to the video or in the folders above it (season → series), up to the library
    folder; ``seasonNN-poster.jpg`` in the series folder too."""
    folder = path.parent
    stems: list[str] = list(GROUP_POSTER_NAMES)
    if name.kind == "episode" and name.season is not None:
        stems = [f"season{name.season:02d}-poster", f"season{name.season:02d}", *stems]
    for _ in range(3):
        found = _find_image(folder, stems, cache)
        if found:
            return found
        if folder == root or folder.parent == folder or root not in folder.parents:
            break
        folder = folder.parent
    return ""


class LibraryStore:
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

    # -- folders ---------------------------------------------------------------------------------------------

    def folders(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM folders ORDER BY path").fetchall()
        return [dict(r) | {"exists": Path(r["path"]).is_dir()} for r in rows]

    def add_folder(self, path: str) -> bool:
        with self._lock:
            cur = self._conn.execute("INSERT OR IGNORE INTO folders (path, added) VALUES (?, ?)", (path, time.time()))
            self._conn.commit()
            return cur.rowcount > 0

    def remove_folder(self, path: str) -> int:
        with self._lock:
            n = self._conn.execute("DELETE FROM folders WHERE path=?", (path,)).rowcount
            removed = self._conn.execute("DELETE FROM items WHERE folder=?", (path,)).rowcount
            self._drop_orphan_groups()
            self._conn.commit()
        return removed if n else -1

    def _drop_orphan_groups(self) -> None:
        self._conn.execute("DELETE FROM groups WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.kind=groups.kind AND"
                           " i.group_key=groups.group_key)")

    # -- scanning --------------------------------------------------------------------------------------------

    @staticmethod
    def walk(root: Path) -> list[Path]:
        """Video files below ``root`` (hidden folders, extras and samples skipped; symlinked files followed)."""
        out: list[Path] = []
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if len(d.parts) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = sorted(n for n in dirnames if not n.startswith(".") and n.lower() not in EXTRA_DIRS)
            for name in sorted(filenames):
                if is_video(name) and not is_sample(Path(name).stem):
                    out.append(d / name)
        return out

    def scan(self, folders: list[str] | None = None, progress: Callable[[float, str], None] | None = None,
             cancelled: Callable[[], bool] | None = None, force: bool = False) -> ScanStats:
        stats = ScanStats()
        t0 = time.monotonic()
        roots = [f["path"] for f in self.folders()] if not folders else folders
        with self._lock:
            # every known file, whatever folder it was found under (nested library folders must not rehash)
            known = {r["path"]: (r["size"], r["mtime"], r["folder"]) for r in
                     self._conn.execute("SELECT path, size, mtime, folder FROM items")}
        seen: set[str] = set()
        for fi, root_s in enumerate(roots):
            root = Path(root_s)
            stats.folders += 1
            if not root.is_dir():
                stats.missing.append(root_s)
                continue
            files = self.walk(root)
            images: dict[Path, dict[str, str]] = {}
            rows: list[tuple[Any, ...]] = []
            groups: dict[tuple[str, str], tuple[str, int | None, str]] = {}
            for i, f in enumerate(files):
                if cancelled is not None and cancelled():
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
                name = parse_path(f, root)
                old = known.get(sp)
                if old is not None and old[0] == st.st_size and abs(old[1] - st.st_mtime) < 1e-6 and not force:
                    stats.unchanged += 1
                else:
                    try:
                        h = file_hash(f)
                    except OSError:
                        stats.errors += 1
                        continue
                    search = norm(" ".join(filter(None, (name.title, name.episode_title, f.stem,
                                                         str(name.year or "")))))
                    rows.append((sp, old[2] if old is not None else root_s, st.st_size, st.st_mtime, h.key,
                                 h.opensubtitles if st.st_size >= 131072 else "", name.kind, name.title,
                                 name.group_key, name.year, name.season, name.episode, name.episode_end,
                                 name.episode_title, item_poster(f, images), time.time(), search))
                    if old is None:
                        stats.added += 1
                    else:
                        stats.updated += 1
                gk = (name.kind, name.group_key)
                if gk not in groups or (not groups[gk][2]):
                    groups[gk] = (name.title, name.year, group_poster(f, name, root, images))
                if progress is not None and (i % 25 == 0 or i == len(files) - 1):
                    progress((fi + (i + 1) / max(1, len(files))) / max(1, len(roots)), f.name)
            gone = [p for p, k in known.items() if k[2] == root_s and p not in seen]
            with self._lock:
                self._conn.executemany(
                    "INSERT INTO items (path,folder,size,mtime,key,oshash,kind,title,group_key,year,season,episode,"
                    "episode_end,episode_title,poster,added,search) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(path) DO UPDATE SET folder=excluded.folder, size=excluded.size, mtime=excluded.mtime,"
                    " key=excluded.key, oshash=excluded.oshash, kind=excluded.kind, title=excluded.title,"
                    " group_key=excluded.group_key, year=excluded.year, season=excluded.season,"
                    " episode=excluded.episode, episode_end=excluded.episode_end, episode_title=excluded.episode_title,"
                    " poster=excluded.poster, search=excluded.search", rows)
                self._conn.executemany("DELETE FROM items WHERE path=?", [(p,) for p in gone])
                stats.removed += len(gone)
                for (kind, gkey), (title, year, poster) in groups.items():
                    self._conn.execute(
                        "INSERT INTO groups (kind, group_key, title, year, poster, poster_source) VALUES (?,?,?,?,?,?)"
                        " ON CONFLICT(kind, group_key) DO UPDATE SET title=excluded.title,"
                        " year=COALESCE(excluded.year, groups.year),"
                        " poster=CASE WHEN excluded.poster!='' THEN excluded.poster"
                        "  WHEN groups.poster_source='local' THEN '' ELSE groups.poster END,"
                        " poster_source=CASE WHEN excluded.poster!='' THEN 'local'"
                        "  WHEN groups.poster_source='local' THEN '' ELSE groups.poster_source END",
                        (kind, gkey, title, year, poster, "local" if poster else ""))
                self._drop_orphan_groups()
                n = self._conn.execute("SELECT COUNT(*) FROM items WHERE folder=?", (root_s,)).fetchone()[0]
                self._conn.execute("UPDATE folders SET last_scan=?, files=? WHERE path=?", (time.time(), n, root_s))
                self._conn.commit()
        stats.seconds = time.monotonic() - t0
        return stats

    # -- groups ----------------------------------------------------------------------------------------------

    def groups_without_poster(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM groups WHERE poster='' ORDER BY title").fetchall()
        return [dict(r) for r in rows]

    def groups_without_online(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM groups WHERE online='' ORDER BY title").fetchall()
        return [dict(r) for r in rows]

    def set_group_poster(self, kind: str, group_key: str, poster: str, source: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE groups SET poster=?, poster_source=? WHERE kind=? AND group_key=?"
                               " AND poster_source!='local'", (poster, source, kind, group_key))
            self._conn.commit()

    def set_group_online(self, kind: str, group_key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("UPDATE groups SET online=? WHERE kind=? AND group_key=?",
                               (json.dumps(data, ensure_ascii=False), kind, group_key))
            self._conn.commit()

    def group(self, kind: str, group_key: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM groups WHERE kind=? AND group_key=?", (kind, group_key)).fetchone()
        return _group_dict(r) if r else None

    def groups(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM groups WHERE kind=? ORDER BY title COLLATE NOCASE", (kind,)).fetchall()
        return [_group_dict(r) for r in rows]

    def first_item(self, kind: str, group_key: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM items WHERE kind=? AND group_key=? ORDER BY season, episode IS NULL,"
                                   " episode, path LIMIT 1", (kind, group_key)).fetchone()
        return dict(r) if r else None

    # -- items -----------------------------------------------------------------------------------------------

    def items(self, kind: str | None = None, group_key: str | None = None, season: int | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM items", []
        where = []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if group_key is not None:
            where.append("group_key=?")
            args.append(group_key)
        if season is not None:
            where.append("season=?")
            args.append(season)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY group_key, season, episode IS NULL, episode, path"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def item(self, path: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM items WHERE path=?", (path,)).fetchone()
        return dict(r) if r else None

    def by_keys(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = "SELECT * FROM items WHERE key IN (%s) ORDER BY path" % ",".join("?" * len(chunk))
                for r in self._conn.execute(q, chunk).fetchall():
                    out.setdefault(r["key"], dict(r))
        return out

    def search(self, q: str, limit: int = 50) -> list[dict[str, Any]]:
        words = norm(q).split()
        if not words:
            return []
        sql = "SELECT * FROM items WHERE " + " AND ".join("search LIKE ?" for _ in words)
        sql += " ORDER BY kind DESC, group_key, season, episode, path LIMIT ?"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, [f"%{w}%" for w in words] + [int(limit)]).fetchall()]

    def counts(self) -> dict[str, int]:
        with self._lock:
            c = self._conn
            movies = c.execute("SELECT COUNT(*) FROM groups WHERE kind='movie'").fetchone()[0]
            shows = c.execute("SELECT COUNT(*) FROM groups WHERE kind='episode'").fetchone()[0]
            episodes = c.execute("SELECT COUNT(*) FROM items WHERE kind='episode'").fetchone()[0]
            files = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            folders = c.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
        return {"movies": movies, "shows": shows, "episodes": episodes, "files": files, "folders": folders}


def _group_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    try:
        d["online"] = json.loads(d["online"]) if d["online"] else None
    except ValueError:
        d["online"] = None
    return d
