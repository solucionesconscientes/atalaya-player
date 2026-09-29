"""Manual intro/credits marks: user data (not cache), one JSON file in the data dir, keyed by content key (``mu:<hash>``).

An entry per file: ``{"path", "name", "duration", "intro": Mark, "credits": Mark}`` where a mark is
``{"start", "end", "origin", "origin_name", "at"}``. ``origin`` is the key of the file the user marked: equal to the
entry key for an original mark, another episode's key for a mark propagated through the season (``propagated``)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("mpvd.intro")

KINDS = ("intro", "credits")


class MarkStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) and isinstance(raw.get("files"), dict) else {"files": {}}
            except FileNotFoundError:
                self._data = {"files": {}}
            except (OSError, ValueError) as exc:
                log.warning("intro: unreadable %s (%s): starting empty", self.path, exc)
                self._data = {"files": {}}
        return self._data

    def _save(self) -> None:
        data = self._load()
        data["version"] = 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str) -> dict[str, Any]:
        with self._lock:
            entry = self._load()["files"].get(key)
            return json.loads(json.dumps(entry)) if isinstance(entry, dict) else {}

    def set(self, key: str, path: Path, duration: float, kind: str, start: float, end: float, origin: str,
            origin_name: str) -> dict[str, Any]:
        mark = {"start": round(start, 2), "end": round(end, 2), "origin": origin, "origin_name": origin_name,
                "at": time.time()}
        if origin != key:
            mark["propagated"] = True
        with self._lock:
            files = self._load()["files"]
            entry = files.setdefault(key, {})
            entry.update({"path": str(path), "name": path.name, "duration": round(duration, 3)})
            entry[kind] = mark
            self._save()
        return mark

    def clear(self, key: str, kind: str | None = None) -> int:
        """Remove this file's marks (one kind or both) and every mark propagated from them."""
        kinds = [kind] if kind else list(KINDS)
        n = 0
        with self._lock:
            files = self._load()["files"]
            for k, entry in list(files.items()):
                for kd in kinds:
                    m = entry.get(kd)
                    if isinstance(m, dict) and (k == key or m.get("origin") == key):
                        entry.pop(kd, None)
                        n += 1
                if not any(kd in entry for kd in KINDS):
                    files.pop(k, None)
            if n:
                self._save()
        return n

    def originals(self, paths: set[str]) -> list[dict[str, Any]]:
        """Original (user-made) marks of the given files: ``[{"key", "path", "duration", "kind", "mark"}]``."""
        out = []
        with self._lock:
            for k, entry in self._load()["files"].items():
                if entry.get("path") not in paths:
                    continue
                for kd in KINDS:
                    m = entry.get(kd)
                    if isinstance(m, dict) and m.get("origin") == k:
                        out.append({"key": k, "path": entry["path"], "duration": float(entry.get("duration") or 0),
                                    "kind": kd, "mark": dict(m)})
        return out

    def current(self, key: str, kind: str, at: float) -> bool:
        """True while the mark ``kind`` of ``key`` is still the one created at ``at`` (a newer mark supersedes it)."""
        with self._lock:
            m = self._load()["files"].get(key, {}).get(kind)
            return isinstance(m, dict) and abs(float(m.get("at", 0)) - at) < 1e-6
