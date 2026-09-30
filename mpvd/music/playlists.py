"""Saved playlists as plain M3U8 files in the user's data dir (``<data_dir>/music/playlists/<name>.m3u8``): any
player can open them, and importing/exporting is just reading/writing the same format.

Format written: ``#EXTM3U``, ``#PLAYLIST:<name>``, then ``#EXTINF:<seconds>,<artist> - <title>`` + absolute path per
entry (UTF-8, ``\\n``). Read: extended or plain M3U/M3U8; relative paths are resolved against the list's folder, URLs
kept as they are, other ``#`` lines ignored. Deleting a list moves the file to ``playlists/.papelera/``.
"""

from __future__ import annotations

import contextlib
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any

from mpvd.subscriptions.chain import safe_component

EXT = ".m3u8"
MAX_ENTRIES = 20000


class PlaylistError(ValueError):
    pass


def is_url(s: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s)) and not s.startswith("file://")


def parse_m3u(text: str, base: Path | None = None) -> tuple[str, list[dict[str, Any]]]:
    """→ (name from #PLAYLIST, [{path, title, duration}])."""
    name = ""
    out: list[dict[str, Any]] = []
    pending: dict[str, Any] = {}
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.upper().startswith("#PLAYLIST:"):
                name = line.split(":", 1)[1].strip()
            elif line.upper().startswith("#EXTINF:"):
                info = line.split(":", 1)[1]
                dur, _, title = info.partition(",")
                with contextlib.suppress(ValueError):
                    pending["duration"] = float(dur.split()[0]) if dur.split() else -1.0
                pending["title"] = title.strip()
            continue
        path = line
        if path.startswith("file://"):
            from urllib.parse import unquote, urlsplit  # noqa: PLC0415

            path = unquote(urlsplit(path).path)
        elif not is_url(path):
            p = Path(path.replace("\\", os.sep) if os.sep != "\\" else path).expanduser()
            if not p.is_absolute() and base is not None:
                p = base / p
            path = os.path.normpath(str(p))
        out.append({"path": path, "title": pending.get("title", ""), "duration": pending.get("duration", -1.0)})
        pending = {}
        if len(out) >= MAX_ENTRIES:
            break
    return name, out


def format_m3u(name: str, entries: list[dict[str, Any]], relative_to: Path | None = None) -> str:
    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
    for e in entries:
        dur = e.get("duration")
        title = str(e.get("title") or "").replace("\n", " ").strip()
        if title or (dur is not None and dur >= 0):
            lines.append(f"#EXTINF:{int(round(dur)) if dur is not None and dur >= 0 else -1},{title}")
        path = str(e["path"])
        if relative_to is not None and not is_url(path):
            with contextlib.suppress(ValueError):
                path = os.path.relpath(path, relative_to)
        lines.append(path)
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Playlists:
    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self._lock = threading.Lock()

    def _file(self, name: str) -> Path:
        return self.dir / (safe_component(name, 120) + EXT)

    @staticmethod
    def clean_name(name: Any) -> str:
        n = re.sub(r"\s+", " ", str(name or "")).strip()
        if not n:
            raise PlaylistError("la lista necesita un nombre")
        return n[:120]

    def _read(self, name: str) -> tuple[Path, str, list[dict[str, Any]]]:
        f = self._file(name)
        if not f.is_file():
            raise FileNotFoundError(name)
        title, entries = parse_m3u(f.read_text(encoding="utf-8", errors="replace"), f.parent)
        return f, title or f.stem, entries

    def _save(self, name: str, entries: list[dict[str, Any]]) -> None:
        _atomic_write(self._file(name), format_m3u(name, entries))

    def list(self) -> list[dict[str, Any]]:
        out = []
        if not self.dir.is_dir():
            return out
        for f in sorted(self.dir.glob("*" + EXT), key=lambda p: p.stem.casefold()):
            try:
                title, entries = parse_m3u(f.read_text(encoding="utf-8", errors="replace"), f.parent)
            except OSError:
                continue
            out.append({"name": title or f.stem, "file": str(f), "count": len(entries),
                        "duration": round(sum(max(0.0, e["duration"] or 0) for e in entries), 1),
                        "modified": f.stat().st_mtime})
        return out

    def exists(self, name: str) -> bool:
        return self._file(name).is_file()

    def get(self, name: str) -> dict[str, Any]:
        f, title, entries = self._read(self.clean_name(name))
        return {"name": title, "file": str(f), "entries": entries}

    def create(self, name: str, entries: list[dict[str, Any]] | None = None, replace: bool = False) -> dict[str, Any]:
        name = self.clean_name(name)
        with self._lock:
            if self.exists(name) and not replace:
                raise PlaylistError(f"ya existe una lista «{name}»")
            self._save(name, list(entries or [])[:MAX_ENTRIES])
        return self.get(name)

    def rename(self, name: str, new: str) -> dict[str, Any]:
        name, new = self.clean_name(name), self.clean_name(new)
        with self._lock:
            f, _title, entries = self._read(name)
            if self._file(new) != f and self.exists(new):
                raise PlaylistError(f"ya existe una lista «{new}»")
            self._save(new, entries)
            if self._file(new) != f:
                f.unlink()
        return self.get(new)

    def delete(self, name: str) -> str:
        name = self.clean_name(name)
        with self._lock:
            f = self._file(name)
            if not f.is_file():
                raise FileNotFoundError(name)
            trash = self.dir / ".papelera"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / f"{f.stem}-{time.strftime('%Y%m%d-%H%M%S')}{EXT}"
            os.replace(f, dest)
        return str(dest)

    def add(self, name: str, entries: list[dict[str, Any]], position: int | None = None,
            create: bool = True) -> dict[str, Any]:
        name = self.clean_name(name)
        with self._lock:
            try:
                _f, _t, cur = self._read(name)
            except FileNotFoundError:
                if not create:
                    raise
                cur = []
            if position is None or position < 0 or position > len(cur):
                position = len(cur)
            cur[position:position] = entries
            self._save(name, cur[:MAX_ENTRIES])
        return self.get(name)

    def remove(self, name: str, indexes: list[int]) -> dict[str, Any]:
        name = self.clean_name(name)
        with self._lock:
            _f, _t, cur = self._read(name)
            drop = {int(i) for i in indexes}
            if any(i < 0 or i >= len(cur) for i in drop):
                raise PlaylistError("posición fuera de la lista")
            self._save(name, [e for i, e in enumerate(cur) if i not in drop])
        return self.get(name)

    def move(self, name: str, src: int, dst: int) -> dict[str, Any]:
        """Entry at ``src`` ends at position ``dst`` (both 0-based, final positions)."""
        name = self.clean_name(name)
        with self._lock:
            _f, _t, cur = self._read(name)
            if not (0 <= src < len(cur)) or not (0 <= dst < len(cur)):
                raise PlaylistError("posición fuera de la lista")
            e = cur.pop(src)
            cur.insert(dst, e)
            self._save(name, cur)
        return self.get(name)

    def reorder(self, name: str, key: Any) -> dict[str, Any]:
        """Sort with ``key(entry)``; ``key == "shuffle"`` shuffles."""
        name = self.clean_name(name)
        with self._lock:
            _f, _t, cur = self._read(name)
            if key == "shuffle":
                random.shuffle(cur)
            else:
                cur.sort(key=key)
            self._save(name, cur)
        return self.get(name)

    def import_file(self, src: Path, name: str | None = None) -> dict[str, Any]:
        text = src.read_text(encoding="utf-8", errors="replace")
        title, entries = parse_m3u(text, src.parent)
        if not entries:
            raise PlaylistError("la lista está vacía o no es M3U")
        base = self.clean_name(name or title or src.stem)
        final, n = base, 2
        while self.exists(final):
            final = f"{base} ({n})"
            n += 1
        return self.create(final, entries)

    def export_file(self, name: str, dest: Path, relative: bool = False) -> str:
        data = self.get(name)
        if dest.is_dir():
            dest = dest / (safe_component(data["name"], 120) + EXT)
        _atomic_write(dest, format_m3u(data["name"], data["entries"], dest.parent if relative else None))
        return str(dest)
