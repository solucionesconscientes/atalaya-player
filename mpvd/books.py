"""``books.*``: audiobooks and podcasts (H32, ADR-065).

What counts as a book (local files are probed once with ffprobe; files with a real video track never count):

* a single file: ``.m4b``/``.aa``/``.aax``, genre tag Audiobook/Audiolibro/Hörbuch/Spoken…, or longer than an hour;
* a folder book: at least three audio files that share the album tag, last more than an hour together and look like
  chapters (median track of 8 minutes or more, a genre tag or a word such as «audiolibro»/«capítulo» in the names).
  A 70-minute music album is therefore NOT a book; the user can force it either way (``books.mark``);
* a podcast episode: genre tag Podcast (also for URLs, from mpv's metadata). Each episode keeps its own position and
  a new episode starts at the speed last used for the same show.

For every book the store keeps only what is needed to go on listening: position (track + time for folder books),
speed, bookmarks with a note and «finished». No listening history, no dates of what was heard (the ``updated``
field only orders «Seguir escuchando» and is overwritten). File: ``<data>/books.json``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import threading
import time
import unicodedata
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import content_key, url_key
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.books")

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aiff", ".aif",
              ".ape", ".wv", ".mka", ".alac", ".aa", ".aax", ".mp2", ".spx"}
BOOK_EXTS = {".m4b", ".aa", ".aax"}
GENRE_BOOK = re.compile(r"audio\s*-?\s*books?|audio\s*libros?|h[öo]rb[üu]ch|livres?\s+audio|spoken|speech|hablado",
                        re.IGNORECASE)
GENRE_PODCAST = re.compile(r"podcast", re.IGNORECASE)
NAME_HINT = re.compile(r"audio\s*libro|audiobook|h[öo]rbuch|cap[íi]tulo|chapter|kapitel|chapitre", re.IGNORECASE)
LONG = 3600.0                 # a single audio file longer than this is a book
FOLDER_MIN_TRACKS = 3
FOLDER_MEDIAN = 8 * 60.0      # typical chapter length; songs are much shorter
FOLDER_ALBUM_SHARE = 0.8
MAX_FOLDER_FILES = 300
FINISHED_TAIL = 30.0          # this close to the end of the last track counts as finished
SPEEDS = (0.5, 3.0)


def is_url(path: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+://", path)) and not path.startswith("file://")


def local_path(path: str) -> str:
    return urllib.parse.unquote(path[7:]) if path.startswith("file://") else path


def natural_key(name: str) -> list[Any]:
    name = unicodedata.normalize("NFC", name).casefold()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _num(v: Any) -> int | None:
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def probe(path: Path, timeout: float = 20.0) -> dict[str, Any]:
    """ffprobe: duration, tags (format + audio streams, lower-case keys), chapters and whether there is real video."""
    ffprobe = shutil.which("ffprobe")
    empty = {"duration": 0.0, "tags": {}, "chapters": [], "video": False}
    if ffprobe is None:
        return empty
    try:
        res = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json",
                              str(path)], capture_output=True, text=True, timeout=timeout)
        data = json.loads(res.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return empty
    tags: dict[str, str] = {}
    video = False
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            # mjpeg/png "video" streams of a cover image are marked attached_pic
            video = True
        if s.get("codec_type") == "audio":
            for k, v in (s.get("tags") or {}).items():
                tags.setdefault(k.lower(), str(v))
    fmt = data.get("format") or {}
    for k, v in (fmt.get("tags") or {}).items():
        tags[k.lower()] = str(v)
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    chapters = []
    for c in data.get("chapters") or []:
        with contextlib.suppress(TypeError, ValueError):
            chapters.append({"time": float(c["start_time"]), "title": (c.get("tags") or {}).get("title", "")})
    return {"duration": duration, "tags": tags, "chapters": chapters, "video": video}


class BooksStore:
    def __init__(self, data_dir: Path, prober: Any = probe):
        self.path = Path(data_dir) / "books.json"
        self._lock = threading.RLock()
        self._probe = prober
        self._probed: dict[tuple[str, int, int], dict[str, Any]] = {}
        self.data = self._read()

    # -- persistence ------------------------------------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"version": 1, "books": dict(data.get("books") or {}), "shows": dict(data.get("shows") or {}),
                        "marks": dict(data.get("marks") or {})}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s: %s", self.path, exc)
            with contextlib.suppress(OSError):
                self.path.replace(self.path.with_name(f"books.json.corrupt-{int(time.time())}"))
        return {"version": 1, "books": {}, "shows": {}, "marks": {}}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"books.json.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- probing ----------------------------------------------------------------------------------------------------

    def _info(self, p: Path) -> dict[str, Any]:
        try:
            st = p.stat()
        except OSError:
            return {"duration": 0.0, "tags": {}, "chapters": [], "video": False}
        key = (str(p), st.st_size, int(st.st_mtime))
        if key not in self._probed:
            if len(self._probed) > 2000:
                self._probed.clear()
            self._probed[key] = self._probe(p)
        return self._probed[key]

    @staticmethod
    def _genre_kind(tags: dict[str, str]) -> str:
        genre = tags.get("genre", "")
        if GENRE_PODCAST.search(genre):
            return "podcast"
        if GENRE_BOOK.search(genre):
            return "book"
        return ""

    def _siblings(self, p: Path) -> list[Path]:
        try:
            files = [f for f in p.parent.iterdir() if f.suffix.lower() in AUDIO_EXTS and f.is_file()
                     and not f.name.startswith(".")]
        except OSError:
            return [p]
        return sorted(files, key=lambda f: natural_key(f.name))[:MAX_FOLDER_FILES]

    def _folder(self, p: Path, forced: bool) -> dict[str, Any] | None:
        """The folder book ``p`` belongs to, or None."""
        files = self._siblings(p)
        if len(files) < (2 if forced else FOLDER_MIN_TRACKS) or p not in files:
            return None
        infos = {f: self._info(f) for f in files}
        if any(i["video"] for i in infos.values()):
            return None
        albums = Counter(i["tags"].get("album", "").strip().casefold() for i in infos.values())
        album, count = albums.most_common(1)[0]
        total = sum(i["duration"] for i in infos.values())
        if not forced:
            if not album or count / len(files) < FOLDER_ALBUM_SHARE or total < LONG:
                return None
            if any(self._genre_kind(i["tags"]) == "podcast" for i in infos.values()):
                return None          # podcast episodes are separate items, not the chapters of one book
            median = statistics.median(i["duration"] for i in infos.values())
            hint = any(self._genre_kind(i["tags"]) == "book" for i in infos.values()) \
                or NAME_HINT.search(p.parent.name) or any(NAME_HINT.search(f.name) for f in files) \
                or any(f.suffix.lower() in BOOK_EXTS for f in files)
            if median < FOLDER_MEDIAN and not hint:
                return None
            files = [f for f in files if infos[f]["tags"].get("album", "").strip().casefold() == album]
        numbered = all(_num(infos[f]["tags"].get("track")) is not None for f in files)
        if numbered:
            files.sort(key=lambda f: (_num(infos[f]["tags"].get("disc")) or 0, _num(infos[f]["tags"].get("track")),
                                      natural_key(f.name)))
        tracks = [{"path": str(f), "file": f.name, "title": infos[f]["tags"].get("title") or f.stem,
                   "duration": infos[f]["duration"]} for f in files]
        first = infos[files[0]]["tags"]
        return {"dir": str(p.parent), "tracks": tracks, "album": first.get("album") or p.parent.name,
                "author": first.get("album_artist") or first.get("artist") or "", "total": total}

    def detect(self, path: str, meta: dict[str, Any] | None = None, duration: float | None = None) -> dict[str, Any]:
        """``{is_book, kind, id, title, reason, …}`` for a file or URL. ``meta`` = mpv's ``metadata`` (URLs)."""
        meta = {str(k).lower(): str(v) for k, v in (meta or {}).items()}
        marks = self.data["marks"]
        if is_url(path):
            kind = self._genre_kind(meta)
            if marks.get(path) is False or not kind and marks.get(path) is not True:
                return {"is_book": False, "kind": "", "id": url_key(path), "reason": ""}
            show = meta.get("album") or meta.get("artist") or ""
            return {"is_book": True, "kind": kind or "book", "id": url_key(path), "reason": "genre" if kind else "mark",
                    "title": meta.get("title") or path, "show": show, "path": path, "duration": duration or 0}
        p = Path(local_path(path)).resolve()
        if not p.is_file():
            return {"is_book": False, "kind": "", "id": "", "reason": "missing"}
        info = self._info(p)
        tags = {**meta, **info["tags"]}
        base = {"is_book": False, "kind": "", "id": "", "reason": ""}
        if info["video"]:
            return base
        file_mark, dir_mark = marks.get(str(p)), marks.get(str(p.parent))
        if file_mark is False or dir_mark is False:
            return {**base, "reason": "mark"}
        kind = self._genre_kind(tags)
        if kind == "podcast":
            return {"is_book": True, "kind": "podcast", "id": content_key(str(p)), "reason": "genre",
                    "title": tags.get("title") or p.stem, "path": str(p), "duration": info["duration"],
                    "show": tags.get("album") or tags.get("artist") or p.parent.name, "chapters": info["chapters"]}
        if p.suffix.lower() not in BOOK_EXTS and file_mark is not True:
            folder = self._folder(p, forced=dir_mark is True)
            if folder:
                idx = next(i for i, t in enumerate(folder["tracks"]) if t["path"] == str(p))
                return {"is_book": True, "kind": "book", "id": "dir:" + folder["dir"], "reason": "folder",
                        "title": folder["album"], "author": folder["author"], "path": folder["dir"],
                        "tracks": folder["tracks"], "track_index": idx, "duration": folder["total"],
                        "chapters": info["chapters"]}
        reason = "mark" if file_mark is True else "m4b" if p.suffix.lower() in BOOK_EXTS else \
            "genre" if kind == "book" else "long" if info["duration"] >= LONG else ""
        if not reason:
            return base
        return {"is_book": True, "kind": "book", "id": content_key(str(p)), "reason": reason,
                "title": tags.get("album") if p.suffix.lower() in BOOK_EXTS and tags.get("album") else
                tags.get("title") or p.stem, "author": tags.get("album_artist") or tags.get("artist") or "",
                "path": str(p), "duration": info["duration"], "chapters": info["chapters"]}

    # -- API --------------------------------------------------------------------------------------------------------

    def _public_bookmarks(self, book: dict[str, Any], tracks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        names = [t["file"] for t in tracks or []]
        out = []
        for i, b in enumerate(book.get("bookmarks") or []):
            out.append({**b, "index": i, "track_index": names.index(b["track"]) if b.get("track") in names else None})
        return out

    def open(self, path: str, meta: dict[str, Any] | None = None, duration: float | None = None) -> dict[str, Any]:
        with self._lock:
            d = self.detect(path, meta, duration)
            if not d["is_book"]:
                return d
            book = self.data["books"].get(d["id"]) or {}
            tracks = d.get("tracks")
            speed = book.get("speed")
            if speed is None and d["kind"] == "podcast":
                speed = self.data["shows"].get(d.get("show") or "")
            position = None
            if book and not book.get("finished") and (float(book.get("time") or 0) > 0 or book.get("track")):
                track = book.get("track") or ""
                names = [t["file"] for t in tracks or []]
                position = {"track": track, "time": float(book.get("time") or 0),
                            "index": names.index(track) if track in names else None}
            return {**d, "known": bool(book), "speed": speed, "position": position,
                    "finished": bool(book.get("finished")), "bookmarks": self._public_bookmarks(book, tracks)}

    def _book(self, book_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        if not book_id:
            raise KeyError("book id required")
        book = self.data["books"].setdefault(book_id, {"bookmarks": []})
        for k in ("title", "kind", "path", "show", "author"):
            if fields.get(k):
                book[k] = str(fields[k])
        return book

    def save(self, book_id: str, track: str = "", time_pos: float = 0.0, speed: float | None = None,
             finished: bool = False, **fields: Any) -> dict[str, Any]:
        with self._lock:
            book = self._book(book_id, fields)
            book["track"] = os.path.basename(track) if track else ""
            book["time"] = round(max(0.0, float(time_pos)), 1)
            if speed is not None:
                book["speed"] = round(min(SPEEDS[1], max(SPEEDS[0], float(speed))), 2)
                if book.get("kind") == "podcast" and book.get("show"):
                    self.data["shows"][book["show"]] = book["speed"]
            book["finished"] = bool(finished)
            book["updated"] = int(time.time())
            self._write()
            return {"id": book_id, **{k: book.get(k) for k in ("track", "time", "speed", "finished")}}

    def bookmark_add(self, book_id: str, track: str = "", time_pos: float = 0.0, note: str = "",
                     **fields: Any) -> list[dict[str, Any]]:
        with self._lock:
            book = self._book(book_id, fields)
            marks = book.setdefault("bookmarks", [])
            marks.append({"track": os.path.basename(track) if track else "", "time": round(max(0.0, float(time_pos)), 1),
                          "note": " ".join(str(note or "").split())[:500]})
            book["updated"] = int(time.time())
            self._write()
            return self.bookmarks(book_id, fields.get("tracks"))

    def bookmarks(self, book_id: str, tracks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        book = self.data["books"].get(book_id)
        if book is None:
            return []
        if tracks is None and book_id.startswith("dir:"):
            files = self._siblings(Path(book_id[4:]) / "x")
            tracks = [{"file": f.name} for f in files]
        marks = self._public_bookmarks(book, tracks)
        return sorted(marks, key=lambda b: (b["track_index"] if b["track_index"] is not None else -1, b["time"]))

    def bookmark_delete(self, book_id: str, index: int) -> list[dict[str, Any]]:
        with self._lock:
            book = self.data["books"].get(book_id)
            if book is None or not 0 <= index < len(book.get("bookmarks") or []):
                raise KeyError("bookmark not found")
            book["bookmarks"].pop(index)
            self._write()
            return self.bookmarks(book_id)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for bid, b in self.data["books"].items():
            path = b.get("path") or ""
            open_path = os.path.join(path, b["track"]) if bid.startswith("dir:") and b.get("track") else path
            out.append({"id": bid, "title": b.get("title") or os.path.basename(path) or bid, "kind": b.get("kind", "book"),
                        "author": b.get("author", ""), "path": path, "open": open_path, "track": b.get("track", ""),
                        "time": b.get("time", 0), "speed": b.get("speed"), "finished": bool(b.get("finished")),
                        "bookmarks": len(b.get("bookmarks") or []), "updated": b.get("updated", 0)})
        return sorted(out, key=lambda x: (x["finished"], -x["updated"]))

    def mark(self, path: str, value: bool | None, folder: bool = False) -> dict[str, Any]:
        """Force (True), refuse (False) or forget (None) «this is a book» for a file or its whole folder."""
        with self._lock:
            key = path if is_url(path) else str(Path(local_path(path)).resolve())
            if folder and not is_url(path):
                key = str(Path(key).parent)
            if value is None:
                self.data["marks"].pop(key, None)
            else:
                self.data["marks"][key] = bool(value)
            self._write()
            return {"key": key, "value": value}

    def forget(self, book_id: str) -> bool:
        with self._lock:
            gone = self.data["books"].pop(book_id, None) is not None
            if gone:
                self._write()
            return gone


class BooksService:
    def __init__(self, server: MpvdServer):
        self.store = BooksStore(server.settings.data_dir)


def register(server: MpvdServer, service: BooksService) -> None:
    d = server.dispatcher
    server.services["books"] = True
    store = service.store

    def _err(exc: Exception) -> RpcError:
        return RpcError(NOT_FOUND if isinstance(exc, KeyError) else INVALID_PARAMS, str(exc).strip("'"))

    @d.method("books.open")
    async def books_open(ctx: RpcContext, path: str, meta: dict | None = None,
                         duration: float | None = None) -> dict[str, Any]:
        """Is this file/URL an audiobook or podcast? If so: id, tracks (folder books), saved position, speed and
        bookmarks."""
        return await asyncio.to_thread(store.open, path, meta, duration)

    @d.method("books.save")
    async def books_save(ctx: RpcContext, id: str, track: str = "", time: float = 0.0, speed: float | None = None,
                         finished: bool = False, title: str = "", kind: str = "", path: str = "",
                         show: str = "", author: str = "") -> dict[str, Any]:
        """Remember where the book is (track file name + time), its speed and whether it was finished."""
        try:
            return await asyncio.to_thread(store.save, id, track, time, speed, finished, title=title, kind=kind,
                                           path=path, show=show, author=author)
        except (KeyError, ValueError, TypeError) as exc:
            raise _err(exc) from exc

    @d.method("books.list")
    async def books_list(ctx: RpcContext) -> list[dict[str, Any]]:
        """Books and podcasts with a saved position (unfinished first, most recent first)."""
        return store.list()

    @d.method("books.bookmarks")
    async def books_bookmarks(ctx: RpcContext, id: str) -> list[dict[str, Any]]:
        """Bookmarks of a book in reading order (``index`` is the one to delete)."""
        return await asyncio.to_thread(store.bookmarks, id)

    @d.method("books.bookmark.add")
    async def bookmark_add(ctx: RpcContext, id: str, time: float, track: str = "", note: str = "", title: str = "",
                           kind: str = "", path: str = "", show: str = "", author: str = "") -> list[dict[str, Any]]:
        """Add a bookmark (optional note) at track + time."""
        try:
            return await asyncio.to_thread(store.bookmark_add, id, track, time, note, title=title, kind=kind,
                                           path=path, show=show, author=author)
        except (KeyError, ValueError, TypeError) as exc:
            raise _err(exc) from exc

    @d.method("books.bookmark.delete")
    async def bookmark_delete(ctx: RpcContext, id: str, index: int) -> list[dict[str, Any]]:
        """Delete a bookmark by its ``index``."""
        try:
            return await asyncio.to_thread(store.bookmark_delete, id, int(index))
        except (KeyError, ValueError) as exc:
            raise _err(exc) from exc

    @d.method("books.mark")
    async def books_mark(ctx: RpcContext, path: str, value: bool | None = None, folder: bool = False) -> dict[str, Any]:
        """Treat a file (or its whole folder) as a book (true), never as a book (false) or decide alone (null)."""
        return await asyncio.to_thread(store.mark, path, value, folder)

    @d.method("books.forget")
    async def books_forget(ctx: RpcContext, id: str) -> dict[str, Any]:
        """Forget the position, speed and bookmarks of a book."""
        return {"forgotten": await asyncio.to_thread(store.forget, id)}
