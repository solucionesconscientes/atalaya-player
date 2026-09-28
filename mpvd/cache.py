"""Artifact cache: SQLite index + blob files, keyed by (file_hash, artifact, model, version, params).

Small results (JSON-serialisable) are stored inline; bytes or existing files become blobs under
``<dir>/blobs/<hh>/``. Synchronous sqlite3 with a lock; call from asyncio via ``asyncio.to_thread``
when latency matters.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY,
  file_hash TEXT NOT NULL,
  artifact TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  params TEXT NOT NULL DEFAULT '{}',
  data TEXT,
  blob_path TEXT,
  size INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_access REAL NOT NULL,
  UNIQUE (file_hash, artifact, model, version, params)
);
CREATE INDEX IF NOT EXISTS artifacts_hash ON artifacts (file_hash);
CREATE INDEX IF NOT EXISTS artifacts_access ON artifacts (last_access);
"""


def canonical_params(params: dict[str, Any] | None) -> str:
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class CacheEntry:
    id: int
    file_hash: str
    artifact: str
    model: str
    version: str
    params: dict[str, Any]
    data: Any
    blob_path: Path | None
    size: int
    created_at: float
    last_access: float

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["blob_path"] = str(self.blob_path) if self.blob_path else None
        return d


class ArtifactCache:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.blob_dir = self.dir / "blobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "artifacts.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- helpers -----------------------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> CacheEntry:
        return CacheEntry(
            id=row["id"],
            file_hash=row["file_hash"],
            artifact=row["artifact"],
            model=row["model"],
            version=row["version"],
            params=json.loads(row["params"]),
            data=json.loads(row["data"]) if row["data"] is not None else None,
            blob_path=self.dir / row["blob_path"] if row["blob_path"] else None,
            size=row["size"],
            created_at=row["created_at"],
            last_access=row["last_access"],
        )

    def _new_blob_path(self, file_hash: str, suffix: str) -> Path:
        sub = self.blob_dir / file_hash.replace(":", "_")[:2]
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{uuid.uuid4().hex}{suffix}"

    # -- API ---------------------------------------------------------------------

    def get(
        self, file_hash: str, artifact: str, model: str = "", version: str = "", params: dict[str, Any] | None = None
    ) -> CacheEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE file_hash=? AND artifact=? AND model=? AND version=? AND params=?",
                (file_hash, artifact, model, version, canonical_params(params)),
            ).fetchone()
            if row is None:
                return None
            entry = self._row_to_entry(row)
            if entry.blob_path is not None and not entry.blob_path.exists():
                self._conn.execute("DELETE FROM artifacts WHERE id=?", (entry.id,))
                return None
            now = time.time()
            self._conn.execute("UPDATE artifacts SET last_access=? WHERE id=?", (now, entry.id))
            entry.last_access = now
            return entry

    def put(
        self,
        file_hash: str,
        artifact: str,
        *,
        model: str = "",
        version: str = "",
        params: dict[str, Any] | None = None,
        data: Any = None,
        blob: bytes | str | Path | None = None,
        blob_suffix: str = ".bin",
    ) -> CacheEntry:
        """Store ``data`` (JSON) and/or ``blob`` (bytes, or a path to move into the cache)."""
        if data is None and blob is None:
            raise ValueError("put() needs data or blob")
        blob_rel: str | None = None
        size = 0
        if blob is not None:
            if isinstance(blob, (str, Path)) and Path(blob).is_file():
                src = Path(blob)
                dest = self._new_blob_path(file_hash, src.suffix or blob_suffix)
                shutil.move(str(src), dest)
            else:
                payload = blob if isinstance(blob, bytes) else str(blob).encode("utf-8")
                dest = self._new_blob_path(file_hash, blob_suffix)
                dest.write_bytes(payload)
            blob_rel = str(dest.relative_to(self.dir))
            size = dest.stat().st_size
        data_txt = json.dumps(data, ensure_ascii=False) if data is not None else None
        if data_txt is not None:
            size += len(data_txt.encode("utf-8"))
        now = time.time()
        with self._lock:
            old = self._conn.execute(
                "SELECT id, blob_path FROM artifacts WHERE file_hash=? AND artifact=? AND model=? AND version=? AND params=?",
                (file_hash, artifact, model, version, canonical_params(params)),
            ).fetchone()
            if old is not None:
                self._delete_rows([old])
            cur = self._conn.execute(
                "INSERT INTO artifacts (file_hash, artifact, model, version, params, data, blob_path, size, created_at, last_access)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (file_hash, artifact, model, version, canonical_params(params), data_txt, blob_rel, size, now, now),
            )
            row = self._conn.execute("SELECT * FROM artifacts WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._row_to_entry(row)

    def _delete_rows(self, rows: list[sqlite3.Row]) -> int:
        n = 0
        for row in rows:
            if row["blob_path"]:
                try:
                    (self.dir / row["blob_path"]).unlink()
                except FileNotFoundError:
                    pass
            self._conn.execute("DELETE FROM artifacts WHERE id=?", (row["id"],))
            n += 1
        return n

    def invalidate(
        self,
        file_hash: str | None = None,
        artifact: str | None = None,
        model: str | None = None,
        version: str | None = None,
    ) -> int:
        """Delete every entry matching the given (non-None) fields. Returns the number removed."""
        clauses, args = [], []
        for col, val in (("file_hash", file_hash), ("artifact", artifact), ("model", model), ("version", version)):
            if val is not None:
                clauses.append(f"{col}=?")
                args.append(val)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(f"SELECT id, blob_path FROM artifacts{where}", args).fetchall()
            return self._delete_rows(rows)

    def list(self, file_hash: str | None = None) -> list[CacheEntry]:
        with self._lock:
            if file_hash is None:
                rows = self._conn.execute("SELECT * FROM artifacts ORDER BY last_access DESC").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM artifacts WHERE file_hash=? ORDER BY last_access DESC", (file_hash,)
                ).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM artifacts").fetchone()
            by_artifact = self._conn.execute(
                "SELECT artifact, COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM artifacts GROUP BY artifact"
            ).fetchall()
        return {
            "dir": str(self.dir),
            "entries": row["n"],
            "bytes": row["bytes"],
            "by_artifact": {r["artifact"]: {"entries": r["n"], "bytes": r["bytes"]} for r in by_artifact},
            "schema_version": SCHEMA_VERSION,
        }

    def prune(self, max_bytes: int) -> int:
        """Evict least-recently-used entries until the total size fits in ``max_bytes``."""
        removed = 0
        with self._lock:
            total = self._conn.execute("SELECT COALESCE(SUM(size),0) FROM artifacts").fetchone()[0]
            if total <= max_bytes:
                return 0
            rows = self._conn.execute("SELECT id, blob_path, size FROM artifacts ORDER BY last_access ASC").fetchall()
            for row in rows:
                if total <= max_bytes:
                    break
                self._delete_rows([row])
                total -= row["size"]
                removed += 1
        return removed
