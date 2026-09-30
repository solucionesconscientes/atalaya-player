"""``lyrics.*``: song lyrics (H32, ADR-065).

Where they come from, in this order:

1. a ``.lrc`` file next to the song (same name, any case of the extension);
2. a lyrics tag inside the file (``LYRICS`` / ``UNSYNCEDLYRICS`` of FLAC/Ogg/Opus, ``lyrics`` / ``lyrics-<lang>`` of
   MP3 USLT and MP4 ``©lyr``), read with ffprobe;
3. only when the user turned it on (off by default, local-first): LRCLIB (``GET https://lrclib.net/api/get`` with
   ``artist_name``, ``track_name``, ``album_name``, ``duration``; verified 2026-09-30: 200 → JSON with
   ``syncedLyrics`` / ``plainLyrics`` / ``instrumental``, 404 → ``TrackNotFound``). Answers (also "not found") are
   cached for a week under ``<cache>/lyrics``.

Synced lyrics (LRC) are converted to SRT in the cache so that mpv shows them as a normal subtitle track (``sub-add``):
the user already knows how to hide them (``v``), move them (``sub-delay``) or restyle them, and no overlay has to be
redrawn by a script. Plain lyrics have no times: they are only listed in the menu.

Settings (``songs-settings.json``) and the AcoustID key of ``songid`` (``songs-secrets.json``, 0600) live in the
data dir; see :class:`SongSettings`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.library.settings import write_private
from mpvd.net import DEFAULT_USER_AGENT
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.lyrics")

LRCLIB_URL = "https://lrclib.net/api/get"
LRCLIB_TTL = 7 * 24 * 3600.0
VERSION = 1                       # bump when the SRT output changes (cache key)
LAST_CUE = 6.0                    # how long the last line stays when the song length is unknown

_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_META = re.compile(r"^\[([A-Za-z#]+):(.*)\]\s*$")
_WORD = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")   # enhanced LRC (A2) word times


# -- LRC ---------------------------------------------------------------------------------------------------------------

@dataclass
class Lyrics:
    lines: list[tuple[float, str]] = field(default_factory=list)   # (seconds, text) sorted; text may be "" (a pause)
    plain: str = ""
    meta: dict[str, str] = field(default_factory=dict)             # ar, ti, al, by, length, offset…

    @property
    def synced(self) -> bool:
        return bool(self.lines)


def _seconds(m: re.Match[str]) -> float:
    frac = m.group(3) or ""
    return int(m.group(1)) * 60 + int(m.group(2)) + (int(frac) / 10 ** len(frac) if frac else 0.0)


def parse_lrc(text: str) -> Lyrics:
    """Parse LRC: ``[mm:ss.xx]`` (also ``[mm:ss]``, ``[mm:ss:xx]``, milliseconds), several times on one line
    (``[00:10.00][01:20.00]chorus``), ``[offset:+/-ms]`` (positive = lyrics come sooner), ID tags such as
    ``[ar:]``/``[ti:]``/``[al:]`` and enhanced-LRC word times (dropped). Text without any time tag is plain lyrics."""
    out = Lyrics()
    rows: list[tuple[float, int, str]] = []
    plain: list[str] = []
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        line = raw.strip().lstrip("﻿")
        times: list[float] = []
        while True:
            m = _TIME.match(line)
            if not m:
                break
            times.append(_seconds(m))
            line = line[m.end():].lstrip()
        if times:
            words = " ".join(_WORD.sub(" ", line).split())
            rows.extend((t, n, words) for t in times)
            continue
        meta = _META.match(line)
        if meta and (not rows and not plain or meta.group(1).lower() in ("offset", "length", "ar", "ti", "al")):
            out.meta[meta.group(1).lower()] = meta.group(2).strip()
            continue
        plain.append(raw.rstrip())
    try:
        offset = float(out.meta.get("offset", "0") or 0) / 1000.0
    except ValueError:
        offset = 0.0
    rows.sort(key=lambda r: (r[0], r[1]))
    out.lines = [(max(0.0, t - offset), s) for t, _n, s in rows]
    if not out.lines:
        out.plain = "\n".join(plain).strip("\n")
    return out


def lyrics_from_text(text: str) -> Lyrics:
    """A lyrics tag or an LRCLIB field: LRC when it has time tags, plain text otherwise."""
    lyr = parse_lrc(text)
    if not lyr.synced and not lyr.plain:
        lyr.plain = text.strip()
    return lyr


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_cues(lines: list[tuple[float, str]], duration: float | None = None) -> list[dict[str, Any]]:
    """Each line stays until the next one starts; empty lines only end the previous one; the last line lasts until
    the end of the song (or ``LAST_CUE`` seconds when the length is unknown)."""
    cues = []
    for i, (t, text) in enumerate(lines):
        if not text:
            continue
        nxt = next((u for u, _s in lines[i + 1:] if u > t), None)
        end = nxt if nxt is not None else (duration if duration and duration > t else t + LAST_CUE)
        if end - t < 0.05:
            continue          # same time as the next line (a duplicate): drop it
        cues.append({"start": round(t, 3), "end": round(end, 3), "text": text})
    return cues


def to_srt(lines: list[tuple[float, str]], duration: float | None = None) -> str:
    return "".join(f"{i}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n{c['text']}\n\n"
                   for i, c in enumerate(to_cues(lines, duration), 1))


# -- sources -----------------------------------------------------------------------------------------------------------

def sidecar(path: Path) -> Path | None:
    """``song.lrc`` next to ``song.flac`` (extension in any case)."""
    try:
        for p in path.parent.iterdir():
            if p.stem == path.stem and p.suffix.lower() == ".lrc" and p.is_file():
                return p
    except OSError:
        return None
    return None


def read_text_file(p: Path) -> str:
    data = p.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def probe_tags(path: Path, timeout: float = 20.0) -> dict[str, Any]:
    """ffprobe of a local file: format + stream tags (keys lower-cased), duration."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return {"tags": {}, "duration": None}
    try:
        res = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:format_tags:stream_tags",
                              "-of", "json", str(path)], capture_output=True, text=True, timeout=timeout)
        data = json.loads(res.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"tags": {}, "duration": None}
    tags: dict[str, str] = {}
    for s in data.get("streams") or []:
        for k, v in (s.get("tags") or {}).items():
            tags.setdefault(k.lower(), str(v))
    for k, v in ((data.get("format") or {}).get("tags") or {}).items():
        tags[k.lower()] = str(v)
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {"tags": tags, "duration": duration}


LYRICS_KEYS = ("lyrics", "unsyncedlyrics", "unsynced lyrics", "syncedlyrics", "uslt")


def embedded(tags: dict[str, str]) -> str | None:
    """The lyrics tag, synced ones first (an LRC text in any of the keys wins)."""
    found = [v for k, v in tags.items() if (k in LYRICS_KEYS or k.startswith("lyrics-") or k.startswith("lyrics "))
             and v.strip()]
    for v in found:
        if _TIME.search(v):
            return v
    return found[0] if found else None


# -- settings ----------------------------------------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "lyrics_online": False,       # look lyrics up on LRCLIB when there are none on disk
    "songid_enabled": False,      # identify songs with Chromaprint + AcoustID (user's own application key)
}
SECRETS = ("acoustid_key",)


class SongSettings:
    """Switches in ``songs-settings.json`` and the AcoustID key apart in ``songs-secrets.json`` (0600). Online
    services are off by default and never contacted without the switch (and, for AcoustID, the key)."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "songs-settings.json"
        self.secrets_path = Path(data_dir) / "songs-secrets.json"
        self._lock = threading.Lock()
        self.values: dict[str, Any] = dict(DEFAULTS)
        self._secrets: dict[str, str] = {}
        for p, target in ((self.path, "values"), (self.secrets_path, "secrets")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                log.warning("cannot read %s: %s", p.name, type(exc).__name__)
                continue
            if not isinstance(data, dict):
                continue
            if target == "values":
                self.values.update({k: v for k, v in data.items() if k in DEFAULTS and isinstance(v, bool)})
            else:
                self._secrets = {k: v for k, v in data.items() if k in SECRETS and isinstance(v, str) and v}

    def get(self, key: str) -> Any:
        return self.values.get(key, DEFAULTS.get(key))

    def secret(self, key: str) -> str:
        return self._secrets.get(key, "")

    @property
    def songid_active(self) -> bool:
        return bool(self.values["songid_enabled"] and self.secret("acoustid_key"))

    def public(self) -> dict[str, Any]:
        return {**self.values, "has_acoustid_key": bool(self.secret("acoustid_key")),
                "songid_active": self.songid_active}

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        values, secrets = dict(self.values), dict(self._secrets)
        for k, v in changes.items():
            if k in SECRETS:
                if v is None or str(v).strip() == "":
                    secrets.pop(k, None)
                else:
                    secrets[k] = str(v).strip()
            elif k in DEFAULTS:
                if not isinstance(v, bool):
                    raise ValueError(f"{k}: se esperaba sí/no")
                values[k] = v
            else:
                raise ValueError(f"ajuste desconocido: {k}")
        with self._lock:
            if values != self.values:
                write_private(self.path, values)
                self.values = values
            if secrets != self._secrets:
                write_private(self.secrets_path, secrets)
                self._secrets = secrets
        return self.public()


# -- LRCLIB ------------------------------------------------------------------------------------------------------------

def lrclib_get(base_url: str, artist: str, title: str, album: str = "", duration: float | None = None,
               timeout: float = 10.0) -> dict[str, Any] | None:
    """One ``/api/get`` request; None when LRCLIB has no such track (404)."""
    q = {"artist_name": artist, "track_name": title}
    if album:
        q["album_name"] = album
    if duration:
        q["duration"] = str(int(round(duration)))
    req = urllib.request.Request(f"{base_url}?{urllib.parse.urlencode(q)}",
                                 headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL (tests: localhost)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


# -- service -----------------------------------------------------------------------------------------------------------

class LyricsService:
    def __init__(self, server: MpvdServer, lrclib_url: str = LRCLIB_URL):
        self.server = server
        self.settings = SongSettings(server.settings.data_dir)
        self.dir = server.settings.cache_dir / "lyrics"
        self.lrclib_url = lrclib_url

    def _online(self, artist: str, title: str, album: str, duration: float | None) -> tuple[str, str] | None:
        """(text, "synced"|"plain") from LRCLIB, cached (also misses) for a week."""
        key = hashlib.blake2b(json.dumps([artist.lower(), title.lower(), album.lower(), int(duration or 0)])
                              .encode("utf-8"), digest_size=12).hexdigest()
        cache = self.dir / f"lrclib-{key}.json"
        try:
            hit = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - float(hit.get("at", 0)) < LRCLIB_TTL:
                return (hit["text"], hit["kind"]) if hit.get("text") else None
        except (OSError, ValueError, KeyError):
            pass
        data = lrclib_get(self.lrclib_url, artist, title, album, duration)
        if data is None and album:   # the album name often differs between releases: try without it
            data = lrclib_get(self.lrclib_url, artist, title, "", duration)
        text, kind = "", ""
        if data and not data.get("instrumental"):
            if data.get("syncedLyrics"):
                text, kind = data["syncedLyrics"], "synced"
            elif data.get("plainLyrics"):
                text, kind = data["plainLyrics"], "plain"
        self.dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": time.time(), "text": text, "kind": kind}), encoding="utf-8")
        return (text, kind) if text else None

    def get(self, path: str = "", artist: str = "", title: str = "", album: str = "",
            duration: float | None = None, online: bool | None = None) -> dict[str, Any]:
        """Lyrics of a song: ``{found, source, synced, lines:[{time,text}], plain, srt, sidecar}``."""
        if path.startswith("file://"):
            local = urllib.parse.unquote(path[7:])
        else:
            local = "" if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+://", path) else path
        text, source, side = None, "", None
        if local:
            p = Path(local)
            side = sidecar(p)
            probed = probe_tags(p) if p.is_file() and (side is None or not duration) else {"tags": {}, "duration": None}
            duration = duration or probed["duration"]
            if side is not None:
                text, source = read_text_file(side), "lrc"
            else:
                tags = probed["tags"]
                artist = artist or tags.get("artist") or tags.get("album_artist") or ""
                title = title or tags.get("title") or ""
                album = album or tags.get("album") or ""
                emb = embedded(tags)
                if emb:
                    text, source = emb, "embedded"
        use_online = self.settings.get("lyrics_online") if online is None else (online and
                                                                                 self.settings.get("lyrics_online"))
        if text is None and use_online and artist and title:
            try:
                got = self._online(artist, title, album, duration)
            except (OSError, ValueError) as exc:
                log.info("LRCLIB: %s", exc)
                got = None
            if got:
                text, source = got[0], "lrclib"
        if text is None:
            return {"found": False, "source": "", "synced": False, "lines": [], "plain": "", "srt": None,
                    "sidecar": None, "online": bool(self.settings.get("lyrics_online"))}
        lyr = lyrics_from_text(text)
        srt = None
        if lyr.synced:
            body = to_srt(lyr.lines, duration)
            name = hashlib.blake2b(f"{VERSION}\0{local or path}\0{body}".encode("utf-8"), digest_size=12).hexdigest()
            self.dir.mkdir(parents=True, exist_ok=True)
            out = self.dir / f"{name}.srt"
            if not out.exists():
                tmp = out.with_suffix(".srt.tmp")
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(out)
            srt = str(out)
        return {"found": True, "source": source, "synced": lyr.synced,
                "lines": [{"time": t, "text": s} for t, s in lyr.lines if s],
                "plain": lyr.plain, "srt": srt, "sidecar": str(side) if side else None, "meta": lyr.meta,
                "online": bool(self.settings.get("lyrics_online"))}


def register(server: MpvdServer, service: LyricsService) -> None:
    d = server.dispatcher
    server.services["lyrics"] = True

    @d.method("lyrics.get")
    async def lyrics_get(ctx: RpcContext, path: str = "", artist: str = "", title: str = "", album: str = "",
                         duration: float | None = None, online: bool | None = None) -> dict[str, Any]:
        """Lyrics of a song: ``.lrc`` next to it, its lyrics tag or (only if turned on) LRCLIB. Synced lyrics come
        with an SRT in the cache (``srt``) ready for ``sub-add``."""
        if not path and not (artist and title):
            raise RpcError(INVALID_PARAMS, "path or artist+title required")
        try:
            return await asyncio.to_thread(service.get, path, artist, title, album, duration, online)
        except OSError as exc:
            raise RpcError(UNAVAILABLE, str(exc)) from exc

    @d.method("lyrics.settings.get")
    async def settings_get(ctx: RpcContext) -> dict[str, Any]:
        """Lyrics and song identification switches (the AcoustID key only as ``has_acoustid_key``)."""
        return service.settings.public()

    @d.method("lyrics.settings.set")
    async def settings_set(ctx: RpcContext, **changes: Any) -> dict[str, Any]:
        """``lyrics_online``, ``songid_enabled`` (bool) and ``acoustid_key`` ("" forgets it)."""
        try:
            return await asyncio.to_thread(service.settings.update, changes)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
