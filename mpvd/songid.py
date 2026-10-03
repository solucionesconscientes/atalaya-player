"""``songid.*``: "¿Qué canción es?" (H32, ADR-065). OFF by default and never contacted without the switch AND the
user's own AcoustID application key (free at https://acoustid.org/new-application, stored in ``songs-secrets.json``
with permissions 0600 by :class:`mpvd.lyrics.SongSettings`).

How: Chromaprint's ``fpcalc -json`` (the same tool ``intro`` uses; first 120 s of audio) → ``POST
https://api.acoustid.org/v2/lookup`` with ``client``, ``duration``, ``fingerprint`` and
``meta=recordings releasegroups compress`` (verified 2026-09-30 with the example of the official documentation:
``{"status":"ok","results":[{"id","score","recordings":[{"id","title","duration","artists":[{"id","name"}],
"releasegroups":[{"id","title","type"}]}]}]}``; a bad key answers ``{"status":"error","error":{"code":4,…}}``).

``songid.tag`` writes the chosen title/artist/album into the file with ``ffmpeg -c copy`` (stream copy, same
container, atomic replace): only when the user asks for it, one file at a time. Nothing is recorded about what was
listened to.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.net import DEFAULT_USER_AGENT
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.lyrics import SongSettings
    from mpvd.server import MpvdServer, RpcContext

ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
TAG_KEYS = ("title", "artist", "album", "date", "genre", "album_artist", "track")


class SongIdError(RuntimeError):
    pass


def fingerprint(path: str, seconds: int = 120, timeout: float = 60.0) -> tuple[float, str]:
    """(duration, compressed fingerprint) of the first ``seconds`` of audio."""
    fpcalc = shutil.which("fpcalc")
    if fpcalc is None:
        raise SongIdError("falta fpcalc (Chromaprint)")
    try:
        res = subprocess.run([fpcalc, "-json", "-length", str(seconds), path], capture_output=True, text=True,
                             timeout=timeout)
        data = json.loads(res.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise SongIdError(f"fpcalc: {exc}") from exc
    if not data.get("fingerprint"):
        raise SongIdError("no se pudo escuchar el audio")
    return float(data.get("duration") or 0), str(data["fingerprint"])


def parse_lookup(data: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """Best recordings first: ``[{score, title, artist, album, recording_id, duration}]`` (one per recording)."""
    if data.get("status") != "ok":
        err = (data.get("error") or {}).get("message") or "respuesta no válida"
        raise SongIdError(f"AcoustID: {err}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for res in sorted(data.get("results") or [], key=lambda r: -float(r.get("score") or 0)):
        for rec in res.get("recordings") or []:
            rid = rec.get("id") or ""
            if not rec.get("title") or rid in seen:
                continue
            seen.add(rid)
            groups = rec.get("releasegroups") or []
            album = next((g.get("title") for g in groups if g.get("type") == "Album" and not g.get("secondarytypes")),
                         None) or next((g.get("title") for g in groups if g.get("title")), "")
            out.append({"score": round(float(res.get("score") or 0), 3), "title": rec["title"],
                        "artist": " & ".join(a.get("name", "") for a in rec.get("artists") or [] if a.get("name")),
                        "album": album or "", "recording_id": rid, "duration": rec.get("duration")})
            if len(out) >= limit:
                return out
    return out


def lookup(url: str, key: str, duration: float, fp: str, timeout: float = 15.0) -> list[dict[str, Any]]:
    body = urllib.parse.urlencode({"client": key, "duration": str(int(round(duration))), "fingerprint": fp,
                                   "meta": "recordings releasegroups compress", "format": "json"}).encode("ascii")
    req = urllib.request.Request(url, data=body, headers={"User-Agent": DEFAULT_USER_AGENT,
                                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL (tests: localhost)
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:   # AcoustID answers errors with a JSON body and 400
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (OSError, ValueError):
            raise SongIdError(f"AcoustID: HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise SongIdError(f"AcoustID: {exc}") from exc
    return parse_lookup(data)


def write_tags(path: str, tags: dict[str, str], timeout: float = 120.0) -> dict[str, Any]:
    """Rewrite the file with new tags (stream copy, cover art and other tags kept), then replace it atomically."""
    src = Path(path)
    if not src.is_file():
        raise SongIdError("el archivo no existe")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SongIdError("falta ffmpeg")
    clean = {k: " ".join(str(v).split()) for k, v in tags.items() if k in TAG_KEYS and v is not None}
    if not clean:
        raise SongIdError("nada que guardar")
    tmp = src.with_name(f".{src.stem}.mu-tag-{os.getpid()}{src.suffix}")
    args = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy", "-map_metadata", "0"]
    for k, v in clean.items():
        args += ["-metadata", f"{k}={v}"]
    try:
        res = subprocess.run([*args, str(tmp)], capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise SongIdError("ffmpeg: " + (res.stderr.strip().splitlines() or ["error"])[-1])
        shutil.copymode(src, tmp)
        os.replace(tmp, src)
    except subprocess.TimeoutExpired as exc:
        raise SongIdError("ffmpeg tardó demasiado") from exc
    finally:
        tmp.unlink(missing_ok=True)
    return {"path": str(src), "tags": clean}


class SongIdService:
    def __init__(self, server: MpvdServer, settings: SongSettings, url: str = ACOUSTID_URL):
        self.server = server
        self.settings = settings
        self.url = url

    def identify(self, path: str) -> dict[str, Any]:
        if not self.settings.get("songid_enabled"):
            raise SongIdError("Identificar canciones está desactivado")
        key = self.settings.secret("acoustid_key")
        if not key:
            raise SongIdError("falta tu clave de AcoustID")
        duration, fp = fingerprint(path)
        return {"path": path, "candidates": lookup(self.url, key, duration, fp)}


def register(server: MpvdServer, service: SongIdService) -> None:
    d = server.dispatcher
    server.services["songid"] = True

    def local(path: str) -> str:
        p = urllib.parse.unquote(path[7:]) if path.startswith("file://") else path
        if not p or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+://", p):
            raise RpcError(INVALID_PARAMS, t("solo archivos locales"))
        return p

    @d.method("songid.identify")
    async def songid_identify(ctx: RpcContext, path: str) -> dict[str, Any]:
        """Candidates for a local song (Chromaprint + AcoustID). Needs ``songid_enabled`` and the user's key."""
        try:
            return await asyncio.to_thread(service.identify, local(path))
        except SongIdError as exc:
            raise RpcError(UNAVAILABLE, str(exc)) from exc

    @d.method("songid.tag")
    async def songid_tag(ctx: RpcContext, path: str, title: str = "", artist: str = "", album: str = "") -> dict[str, Any]:
        """Write title/artist/album into the file (stream copy, atomic replace)."""
        tags = {k: v for k, v in (("title", title), ("artist", artist), ("album", album)) if v}
        try:
            return await asyncio.to_thread(write_tags, local(path), tags)
        except SongIdError as exc:
            raise RpcError(UNAVAILABLE, str(exc)) from exc
