"""«Mis notas» (H17, ADR-043): one Markdown file per video in ``<data>/notas`` named after the video's title, with a
small front matter that ties it to the content (the same key as "continue watching": a moved or renamed file keeps its
notes)::

    ---
    titulo: La película
    video: /ruta/la película.mkv
    clave: mu:0a1b…
    ---
    # La película

    [▶ Abrir el vídeo](mpv-uos://open?path=%2Fruta%2Fla%20pel%C3%ADcula.mkv)

    - [00:01:23](mpv-uos://open?path=…&t=83.0) · 2026-09-30 12:00 — texto de la nota

``mpv-uos://open?path=…&t=…`` links open the video at that minute (bin/mpv-uos and mu-notes resolve them; the desktop
entry registers the scheme). Files of the first version (``<clave>.md``, ``mpv://seek?t=`` links) are migrated when the
folder is read. Front matter keys are Spanish (Obsidian shows them as properties).
"""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

SCHEME = "mpv-uos"
_NOTE = re.compile(r"^- (?:\[(?P<hms>[\d:]+)\]\((?P<link>[^)\s]+)\) · )?(?P<stamp>\d{4}-\d\d-\d\d \d\d:\d\d) — (?P<text>.*)$")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def hms(seconds: float) -> str:
    s = int(max(0.0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def link(path: str, t: float | None = None) -> str:
    """``mpv-uos://open?path=…[&t=…]`` (a URL path is quoted whole)."""
    is_url = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) is not None and not path.startswith("file://")
    q = quote(path.removeprefix("file://"), safe="" if is_url else "/")
    return f"{SCHEME}://open?path={q}" + (f"&t={t:.1f}" if t is not None else "")


def parse_link(url: str) -> dict[str, Any] | None:
    """``{path, t}`` of an ``mpv-uos://open`` link; ``{t}`` of a first-version ``mpv://seek?t=`` link."""
    u = urlparse(url)
    qs = parse_qs(u.query)
    t = None
    try:
        t = float(qs["t"][0]) if "t" in qs else None
    except ValueError:
        t = None
    if u.scheme == SCHEME and u.netloc == "open" and qs.get("path"):
        return {"path": qs["path"][0], "t": t}
    if u.scheme == "mpv" and u.netloc == "seek":
        return {"path": None, "t": t}
    return None


def safe_name(title: str, limit: int = 100) -> str:
    """A file name anyone can read: the title without characters that are not allowed on Windows/macOS/Linux."""
    t = unicodedata.normalize("NFC", title)
    t = _BAD.sub(" ", t)
    t = " ".join(t.split()).strip(" .")
    return t[:limit].rstrip(" .") or "Notas"


def _front(meta: dict[str, str]) -> str:
    def val(v: str) -> str:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r'[:#"\'\n]|^\s|\s$', v) else v
    return "---\n" + "".join(f"{k}: {val(v)}\n" for k, v in meta.items()) + "---\n"


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


class NoteFile:
    """A parsed notes file: front matter, title, the lines around the notes and the notes themselves."""

    def __init__(self, path: Path, key: str, title: str, media: str, notes: list[dict[str, Any]]):
        self.path, self.key, self.title, self.media, self.notes = path, key, title, media, notes

    @classmethod
    def parse(cls, path: Path) -> NoteFile:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        meta: dict[str, str] = {}
        body = lines
        if lines[:1] == ["---"]:
            try:
                end = lines.index("---", 1)
            except ValueError:
                end = 0
            for ln in lines[1:end]:
                k, _, v = ln.partition(":")
                meta[k.strip()] = _unquote(v)
            body = lines[end + 1:]
        title = meta.get("titulo") or next((ln[2:].strip() for ln in body if ln.startswith("# ")), path.stem)
        media = meta.get("video") or next((ln.strip("`") for ln in body if ln.startswith("`") and ln.endswith("`")), "")
        notes = []
        for ln in body:
            m = _NOTE.match(ln)
            if not m:
                continue
            t = None
            if m.group("link"):
                parsed = parse_link(m.group("link"))
                t = parsed["t"] if parsed else None
            notes.append({"time": t, "stamp": m.group("stamp"), "text": m.group("text")})
        return cls(path, meta.get("clave", ""), title, media, notes)

    def render(self) -> str:
        out = [_front({"titulo": self.title, "video": self.media, "clave": self.key}), f"# {self.title}", ""]
        if self.media:
            out += [f"[▶ Abrir el vídeo]({link(self.media)})", ""]
        for n in self.notes:
            prefix = f"[{hms(n['time'])}]({link(self.media, n['time'])}) · " if n.get("time") is not None else ""
            out.append(f"- {prefix}{n['stamp']} — {n['text']}")
        return "\n".join(out) + "\n"

    def to_dict(self, with_notes: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {"file": str(self.path), "key": self.key, "title": self.title, "path": self.media,
                             "notes": len(self.notes), "updated": self.path.stat().st_mtime if self.path.exists() else 0}
        if with_notes:
            d["items"] = [{"index": i, **n} for i, n in enumerate(self.notes)]
        return d


class NotesStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "notas"
        self._index: dict[str, Path] = {}
        self._stamp: tuple[float, int] | None = None

    # -- files ----------------------------------------------------------------------------------------------------

    def _scan(self) -> dict[str, Path]:
        """key → file, migrating first-version files (named after the key, no front matter)."""
        if not self.dir.is_dir():
            return {}
        files = sorted(self.dir.glob("*.md"))
        stamp = (self.dir.stat().st_mtime, len(files))
        if stamp == self._stamp:
            return self._index
        index: dict[str, Path] = {}
        for p in files:
            try:
                nf = NoteFile.parse(p)
            except (OSError, UnicodeDecodeError):
                continue
            if not nf.key:      # first version: the file name is the (sanitised) key
                nf.key = p.stem
                nf.path = self._free_path(nf.title, p)
                nf.path.write_text(nf.render(), encoding="utf-8")
                if nf.path != p:
                    p.unlink()
            index[nf.key] = nf.path
        self._index = index
        self._stamp = (self.dir.stat().st_mtime, len(list(self.dir.glob("*.md"))))
        return index

    def _free_path(self, title: str, own: Path | None = None) -> Path:
        base = safe_name(title)
        for n in range(1, 1000):
            p = self.dir / (f"{base}.md" if n == 1 else f"{base} ({n}).md")
            if not p.exists() or p == own:
                return p
        raise OSError("no free file name")

    def _load(self, key: str) -> NoteFile | None:
        p = self._scan().get(key)
        return NoteFile.parse(p) if p and p.exists() else None

    def _save(self, nf: NoteFile) -> None:
        tmp = nf.path.with_suffix(".md.tmp")
        tmp.write_text(nf.render(), encoding="utf-8")
        tmp.replace(nf.path)
        self._stamp = None

    # -- API ------------------------------------------------------------------------------------------------------

    def path_for(self, key: str) -> Path | None:
        return self._scan().get(key)

    def add(self, key: str, title: str, media_path: str, text: str, time_pos: float | None) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        nf = self._load(key)
        new = nf is None
        if nf is None:
            local = Path(media_path.removeprefix("file://")) if media_path else None
            if local is not None and (not title or title == local.name):
                title = local.stem       # mpv's media-title is the file name when the file has no title tag
            title = title or "Notas"
            nf = NoteFile(self._free_path(title), key, title, media_path, [])
        elif media_path and media_path != nf.media:
            nf.media = media_path           # the video moved: links follow it
        nf.notes.append({"time": time_pos, "stamp": time.strftime("%Y-%m-%d %H:%M"), "text": " ".join(text.split())})
        nf.notes.sort(key=lambda n: (n["time"] is None, n["time"] or 0.0))
        self._save(nf)
        return {"file": str(nf.path), "key": key, "new_file": new, "time_pos": time_pos, "title": nf.title}

    def read(self, key: str) -> str | None:
        p = self.path_for(key)
        return p.read_text(encoding="utf-8") if p and p.exists() else None

    def get(self, key: str) -> dict[str, Any] | None:
        nf = self._load(key)
        return nf.to_dict(with_notes=True) if nf else None

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in self._scan().values():
            try:
                out.append(NoteFile.parse(p).to_dict())
            except (OSError, UnicodeDecodeError):
                continue
        return sorted(out, key=lambda d: d["updated"], reverse=True)

    def edit(self, key: str, index: int, text: str) -> dict[str, Any]:
        nf = self._load(key)
        if nf is None or not 0 <= index < len(nf.notes):
            raise KeyError("note not found")
        nf.notes[index]["text"] = " ".join(text.split())
        self._save(nf)
        return nf.to_dict(with_notes=True)

    def delete(self, key: str, index: int) -> dict[str, Any]:
        """Remove one note; the file goes when its last note does."""
        nf = self._load(key)
        if nf is None or not 0 <= index < len(nf.notes):
            raise KeyError("note not found")
        nf.notes.pop(index)
        if nf.notes:
            self._save(nf)
        else:
            nf.path.unlink(missing_ok=True)
            self._stamp = None
        return nf.to_dict(with_notes=True)

    def export(self, key: str, folder: str | None = None) -> dict[str, Any]:
        """Copy the notes next to the video (``<vídeo>.notas.md``) or into ``folder`` (an Obsidian vault) under the
        title's name. The copy is a snapshot: later notes go to the notes folder only."""
        nf = self._load(key)
        if nf is None:
            raise KeyError("no notes")
        if folder:
            dest_dir = Path(folder).expanduser()
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{safe_name(nf.title)}.md"
        else:
            media = Path(nf.media.removeprefix("file://"))
            if not nf.media or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", nf.media) and not nf.media.startswith("file://"):
                raise ValueError("el vídeo no es un archivo local: elige una carpeta")
            if not media.parent.is_dir():
                raise ValueError(f"no existe la carpeta del vídeo: {media.parent}")
            dest = media.with_name(media.stem + ".notas.md")
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_text(nf.render(), encoding="utf-8")
        tmp.replace(dest)
        return {"file": str(dest), "notes": len(nf.notes)}
