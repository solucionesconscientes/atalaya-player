"""Watch history keyed by content, not path: "continue watching" + recents for the start screen and the palette.

Keys: ``mu:<hash>`` for local files (size + first/last 64 KiB, ADR-011) so a moved or renamed file resumes; ``url:<hash>``
for network URLs. Stored in ``<data_dir>/watch.sqlite3``. Exposed as ``watch.*`` JSON-RPC methods.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import file_hash, url_key
from mpvd.iptv.index import normalize
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

FINISHED_TAIL = 30.0      # seconds before the end that count as "finished"
FINISHED_RATIO = 0.95
MIN_RESUME = 20.0         # do not bother resuming below this position
MAX_ENTRIES = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch (
  key TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'local',
  duration REAL NOT NULL DEFAULT 0,
  position REAL NOT NULL DEFAULT 0,
  finished INTEGER NOT NULL DEFAULT 0,
  plays INTEGER NOT NULL DEFAULT 1,
  first_seen REAL NOT NULL,
  updated_at REAL NOT NULL,
  search TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS watch_updated ON watch(updated_at DESC);
"""


def content_key(path: str) -> str:
    """``url:`` key for anything with a scheme, ``mu:`` hash for readable local files (path hash as last resort)."""
    if "://" in path and not path.startswith("file://"):
        return url_key(path)
    local = path[7:] if path.startswith("file://") else path
    try:
        return file_hash(local).key
    except (OSError, ValueError):
        return url_key("path:" + os.path.abspath(local))


def kind_of(path: str) -> str:
    return "url" if "://" in path and not path.startswith("file://") else "local"


def is_finished(position: float, duration: float) -> bool:
    """Finished = within the last 30 s (or last 10 % for short media) or past 95 %."""
    if duration <= 0:
        return False
    tail = min(FINISHED_TAIL, duration * 0.1)
    return position >= duration - tail or position / duration >= FINISHED_RATIO


class WatchStore:
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

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        d.pop("search", None)
        d["finished"] = bool(d["finished"])
        d["resume"] = (not d["finished"]) and d["position"] >= MIN_RESUME
        return d

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM watch WHERE key=?", (key,)).fetchone())

    def get_many(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """Entries for several keys at once (the library shows progress per episode)."""
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = "SELECT * FROM watch WHERE key IN (%s)" % ",".join("?" * len(chunk))
                for r in self._conn.execute(q, chunk).fetchall():
                    out[r["key"]] = self._row(r)  # type: ignore[assignment]
        return out

    def update(self, key: str, path: str, title: str = "", duration: float = 0.0, position: float = 0.0,
               kind: str | None = None, finished: bool | None = None, new_play: bool = False) -> dict[str, Any]:
        now = time.time()
        duration = max(0.0, float(duration or 0))
        position = max(0.0, float(position or 0))
        done = bool(finished) if finished is not None else is_finished(position, duration)
        search = normalize(f"{title} {os.path.basename(path)}")
        with self._lock:
            old = self._conn.execute("SELECT * FROM watch WHERE key=?", (key,)).fetchone()
            if old is None:
                self._conn.execute(
                    "INSERT INTO watch (key,path,title,kind,duration,position,finished,plays,first_seen,updated_at,search)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (key, path, title, kind or kind_of(path), duration, position, int(done), 1, now, now, search),
                )
            else:
                self._conn.execute(
                    "UPDATE watch SET path=?, title=?, kind=?, duration=?, position=?, finished=?, plays=plays+?,"
                    " updated_at=?, search=? WHERE key=?",
                    (path, title or old["title"], kind or old["kind"], duration or old["duration"], position, int(done),
                     int(new_play), now, search, key),
                )
            self._conn.execute(
                "DELETE FROM watch WHERE key IN (SELECT key FROM watch ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
                (MAX_ENTRIES,),
            )
            self._conn.commit()
            return self._row(self._conn.execute("SELECT * FROM watch WHERE key=?", (key,)).fetchone())  # type: ignore[return-value]

    def recents(self, limit: int = 20, kind: str | None = None, unfinished_only: bool = False) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM watch", []
        where = []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if unfinished_only:
            where.append("finished=0 AND position>=?")
            args.append(MIN_RESUME)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [self._row(r) for r in self._conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        words = [w for w in normalize(q).split() if w]
        if not words:
            return self.recents(limit)
        with self._lock:
            rows = self._conn.execute("SELECT * FROM watch ORDER BY updated_at DESC LIMIT 500").fetchall()
        out = []
        for r in rows:
            s = r["search"]
            if all(w in s for w in words):
                out.append(self._row(r))
                if len(out) >= limit:
                    break
        return out  # type: ignore[return-value]

    def remove(self, key: str) -> bool:
        with self._lock:
            n = self._conn.execute("DELETE FROM watch WHERE key=?", (key,)).rowcount
            self._conn.commit()
        return n > 0

    def clear(self) -> int:
        with self._lock:
            n = self._conn.execute("DELETE FROM watch").rowcount
            self._conn.commit()
        return n

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM watch").fetchone()[0])


class WatchService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.store = WatchStore(server.settings.data_dir / "watch.sqlite3")
        self._keys: dict[str, str] = {}  # path -> key (hashing is I/O; remember per daemon lifetime)

    async def key_for(self, path: str) -> str:
        key = self._keys.get(path)
        if key is None:
            key = await asyncio.to_thread(content_key, path)
            if len(self._keys) > 2000:
                self._keys.clear()
            self._keys[path] = key
        return key

    def close(self) -> None:
        self.store.close()


def register(server: MpvdServer, service: WatchService) -> None:
    d = server.dispatcher
    server.services["watch"] = True

    def need_path(path: str | None, key: str | None) -> None:
        if not path and not key:
            raise RpcError(INVALID_PARAMS, "path or key required")

    @d.method("watch.get")
    async def get(ctx: RpcContext, path: str | None = None, key: str | None = None) -> dict[str, Any] | None:
        """Saved position for a file/URL (by content key); ``resume`` tells whether it is worth seeking."""
        need_path(path, key)
        k = key or await service.key_for(path or "")
        entry = await asyncio.to_thread(service.store.get, k)
        return {**entry, "key": k} if entry else {"key": k, "position": 0, "resume": False, "finished": False}

    @d.method("watch.update")
    async def update(ctx: RpcContext, path: str, position: float = 0.0, duration: float = 0.0, title: str = "",
                     key: str | None = None, kind: str | None = None, finished: bool | None = None,
                     new_play: bool = False) -> dict[str, Any]:
        """Record playback position (call on start, periodically, on pause/seek and at the end)."""
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        k = key or await service.key_for(path)
        return await asyncio.to_thread(service.store.update, k, path, title, duration, position, kind, finished, new_play)

    @d.method("watch.recents")
    async def recents(ctx: RpcContext, limit: int = 20, kind: str | None = None,
                      unfinished_only: bool = False) -> list[dict[str, Any]]:
        """Recently played files/URLs, newest first (``unfinished_only`` = continue watching)."""
        return await asyncio.to_thread(service.store.recents, int(limit), kind, bool(unfinished_only))

    @d.method("watch.search")
    async def search(ctx: RpcContext, q: str, limit: int = 20) -> list[dict[str, Any]]:
        """Accent-insensitive search in titles and file names of the history."""
        return await asyncio.to_thread(service.store.search, q, int(limit))

    @d.method("watch.remove")
    async def remove(ctx: RpcContext, key: str | None = None, path: str | None = None) -> dict[str, Any]:
        """Forget one entry."""
        need_path(path, key)
        k = key or await service.key_for(path or "")
        ok = await asyncio.to_thread(service.store.remove, k)
        if not ok:
            raise RpcError(NOT_FOUND, f"no history for {k}")
        return {"removed": True}

    @d.method("watch.clear")
    async def clear(ctx: RpcContext) -> dict[str, Any]:
        """Forget the whole history."""
        return {"removed": await asyncio.to_thread(service.store.clear)}
