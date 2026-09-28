"""User state for TV/radio: favourites, recents and custom sources (SQLite in the data dir)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from mpvd.iptv.model import Channel

_SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
  channel_id TEXT PRIMARY KEY, added_at REAL NOT NULL, position INTEGER NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recents (
  channel_id TEXT PRIMARY KEY, played_at REAL NOT NULL, plays INTEGER NOT NULL DEFAULT 1, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'tv',
  enabled INTEGER NOT NULL DEFAULT 1, added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS health (
  channel_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, checked_at REAL NOT NULL, detail TEXT);
"""


class IptvStore:
    def __init__(self, path: str | Path, max_recents: int = 50):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_recents = max_recents
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- favourites ---------------------------------------------------------------------------

    def add_favorite(self, ch: Channel) -> bool:
        with self._lock:
            if self._conn.execute("SELECT 1 FROM favorites WHERE channel_id=?", (ch.id,)).fetchone():
                return False
            pos = self._conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM favorites").fetchone()[0]
            self._conn.execute("INSERT INTO favorites VALUES (?,?,?,?)",
                               (ch.id, time.time(), pos, json.dumps(ch.to_dict(), ensure_ascii=False)))
            return True

    def remove_favorite(self, channel_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM favorites WHERE channel_id=?", (channel_id,))
            return cur.rowcount > 0

    def toggle_favorite(self, ch: Channel) -> bool:
        """Returns True if the channel is a favourite after the call."""
        if self.remove_favorite(ch.id):
            return False
        self.add_favorite(ch)
        return True

    def is_favorite(self, channel_id: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM favorites WHERE channel_id=?", (channel_id,)).fetchone() is not None

    def favorites(self) -> list[Channel]:
        with self._lock:
            rows = self._conn.execute("SELECT snapshot FROM favorites ORDER BY position").fetchall()
        return [Channel.from_dict(json.loads(r["snapshot"])) for r in rows]

    def favorite_ids(self) -> set[str]:
        with self._lock:
            return {r["channel_id"] for r in self._conn.execute("SELECT channel_id FROM favorites")}

    # -- recents --------------------------------------------------------------------------------

    def touch_recent(self, ch: Channel) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO recents (channel_id, played_at, plays, snapshot) VALUES (?,?,1,?)"
                " ON CONFLICT(channel_id) DO UPDATE SET played_at=excluded.played_at, plays=plays+1,"
                " snapshot=excluded.snapshot",
                (ch.id, time.time(), json.dumps(ch.to_dict(), ensure_ascii=False)),
            )
            self._conn.execute(
                "DELETE FROM recents WHERE channel_id NOT IN"
                " (SELECT channel_id FROM recents ORDER BY played_at DESC LIMIT ?)", (self.max_recents,))

    def recents(self, limit: int = 20) -> list[Channel]:
        with self._lock:
            rows = self._conn.execute("SELECT snapshot FROM recents ORDER BY played_at DESC LIMIT ?", (limit,)).fetchall()
        return [Channel.from_dict(json.loads(r["snapshot"])) for r in rows]

    def clear_recents(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM recents")

    # -- user sources ---------------------------------------------------------------------------

    def add_source(self, source_id: str, name: str, url: str, kind: str = "tv") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sources (id, name, url, kind, enabled, added_at) VALUES (?,?,?,?,1,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, kind=excluded.kind",
                (source_id, name, url, kind, time.time()))

    def remove_source(self, source_id: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount > 0

    def set_source_enabled(self, source_id: str, enabled: bool) -> bool:
        with self._lock:
            return self._conn.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), source_id)).rowcount > 0

    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sources ORDER BY added_at").fetchall()
        return [dict(r) | {"enabled": bool(r["enabled"])} for r in rows]

    # -- health ---------------------------------------------------------------------------------

    def set_health(self, channel_id: str, ok: bool, detail: str = "") -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO health VALUES (?,?,?,?)", (channel_id, int(ok), time.time(), detail))

    def health(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM health").fetchall()
        return {r["channel_id"]: {"ok": bool(r["ok"]), "checked_at": r["checked_at"], "detail": r["detail"]} for r in rows}
