"""Tags, covers and loudness of music files, through the ffprobe/ffmpeg already required by the project.

* ``probe``: ``ffprobe -show_format -show_streams`` → artist, album artist, album, year, genres, track/disc numbers,
  duration, ReplayGain tags and whether there is an embedded picture (``disposition.attached_pic``). Vorbis/Opus keep
  their tags in the audio stream and the rest in the format: both are merged (format wins), keys lower-cased.
* ``measure``: ReplayGain 2.0 of a track with ffmpeg's ``ebur128`` (integrated loudness; gain = −18 LUFS − I, as rsgain
  and foobar2000 do). Sample peak, not true peak: three times cheaper and enough to keep mpv from clipping (ADR-064).
  ffmpeg's ``replaygain`` filter was discarded: it implements the old ReplayGain 1 (89 dB reference, +2.5 dB louder
  than what taggers write today).
* ``album_gain``: album loudness from the tracks' loudness weighted by duration (energy mean). It ignores the relative
  gate across tracks, so it may differ by a few tenths from a real album scan, but it needs no second decode.
Every subprocess runs at the lowest CPU priority. Nothing here ever writes to the user's files.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from mpvd.library.parse import norm

AUDIO_EXTS = frozenset((".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav", ".wv", ".ape",
                        ".aif", ".aiff", ".alac", ".wma", ".mka", ".mpc", ".tta", ".dsf", ".dff", ".spx"))
RG_REFERENCE = -18.0          # LUFS, ReplayGain 2.0
NICE = 19
UNKNOWN_ARTIST = "Artista desconocido"


def is_audio(name: str) -> bool:
    return not name.startswith(".") and os.path.splitext(name)[1].lower() in AUDIO_EXTS


def _lower_priority() -> None:  # pragma: no cover - runs in the child
    with contextlib.suppress(OSError):
        os.nice(NICE)


def run_low(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["preexec_fn"] = _lower_priority
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False,  # noqa: S603
                          encoding="utf-8", errors="replace", **kwargs)


# -- tags ------------------------------------------------------------------------------------------------------------

def _num(value: Any) -> int | None:
    """``"3/12"`` → 3, ``"03"`` → 3, garbage → None."""
    m = re.match(r"\s*(\d{1,4})", str(value or ""))
    return int(m.group(1)) if m else None


def _gain(value: Any) -> float | None:
    """``"-6.54 dB"`` → -6.54."""
    m = re.match(r"\s*([+-]?\d+(?:[.,]\d+)?)", str(value or ""))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def split_genres(value: str) -> list[str]:
    """``"Rock; Pop"`` / ``"Rock, Pop"`` / ``"Jazz / Soul"`` → distinct genres (first spelling kept)."""
    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"\s*(?:;|,|\x00| / |\|)\s*", str(value or "")):
        g = part.strip().strip("()")
        if not g or g.isdigit():          # ID3v1 numeric genres carry no readable name
            continue
        k = norm(g)
        if k and k not in seen:
            seen.add(k)
            out.append(g)
    return out


def parse_probe(data: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """ffprobe JSON → the fields of a track row (pure: tested without files)."""
    p = Path(path)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    tags: dict[str, str] = {}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    for src in ((audio or {}).get("tags") or {}, fmt.get("tags") or {}):
        for k, v in src.items():
            if isinstance(v, (str, int, float)) and str(v).strip():
                tags[str(k).lower()] = str(v).strip()

    def first(*keys: str) -> str:
        for k in keys:
            if tags.get(k):
                return tags[k]
        return ""

    title = first("title") or p.stem
    artist = first("artist", "author", "performer")
    album_artist = first("album_artist", "albumartist", "album artist")
    date = first("date", "year", "originaldate", "original_date", "tdrc", "tyer")
    ym = re.search(r"(1[89]\d\d|20\d\d)", date)
    duration = 0.0
    for d in (fmt.get("duration"), (audio or {}).get("duration")):
        with contextlib.suppress(TypeError, ValueError):
            if d is not None and float(d) > 0:
                duration = float(d)
                break
    pic = any(s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic") for s in streams)
    return {
        "title": title, "artist": artist, "album_artist": album_artist, "album": first("album"),
        "year": int(ym.group(1)) if ym else None, "genres": split_genres(first("genre")),
        "track_no": _num(first("track", "tracknumber", "trck")), "disc_no": _num(first("disc", "discnumber", "tpos")),
        "duration": round(duration, 3), "codec": str((audio or {}).get("codec_name") or ""),
        "has_audio": audio is not None, "has_pic": bool(pic),
        "tag_track_gain": _gain(first("replaygain_track_gain")), "tag_track_peak": _gain(first("replaygain_track_peak")),
        "tag_album_gain": _gain(first("replaygain_album_gain")), "tag_album_peak": _gain(first("replaygain_album_peak")),
    }


def probe(path: str | Path, timeout: float = 30.0) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe no está instalado")
    out = run_low([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr.strip().splitlines() or ["ffprobe falló"])[-1])
    return parse_probe(json.loads(out.stdout or "{}"), path)


# -- loudness (ReplayGain 2.0) ---------------------------------------------------------------------------------------

_I_RX = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*LUFS")
_PEAK_RX = re.compile(r"(?:Sample|True) peak:\s*\n\s*Peak:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dBFS")


def parse_ebur128(stderr: str) -> dict[str, float]:
    """Summary printed by ``ebur128`` at the end → loudness (LUFS), peak (linear), gain (dB, ReplayGain 2.0)."""
    mi = None
    for mi in _I_RX.finditer(stderr):
        pass
    mp_ = None
    for mp_ in _PEAK_RX.finditer(stderr):
        pass
    if mi is None:
        raise ValueError("ebur128 no devolvió la sonoridad")
    loud = float(mi.group(1))
    if not math.isfinite(loud) or loud < -70:
        raise ValueError("el audio es silencio")
    peak_db = float(mp_.group(1)) if mp_ is not None else 0.0
    peak = 10 ** (peak_db / 20) if math.isfinite(peak_db) else 0.0
    return {"loudness": loud, "peak": round(peak, 6), "gain": round(RG_REFERENCE - loud, 2)}


def measure(path: str | Path, timeout: float = 900.0) -> dict[str, float]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg no está instalado")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-threads", "1", "-i", str(path), "-map", "0:a:0",
           "-vn", "-sn", "-dn", "-af", "ebur128=peak=sample:framelog=quiet", "-f", "null", "-"]
    out = run_low(cmd, timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr.strip().splitlines() or ["ffmpeg falló"])[-1])
    return parse_ebur128(out.stderr)


def album_gain(tracks: list[tuple[float, float, float]]) -> tuple[float, float] | None:
    """[(loudness LUFS, duration s, peak linear)] → (album gain dB, album peak). Energy mean weighted by duration."""
    usable = [(lo, max(du, 1.0), pk) for lo, du, pk in tracks if lo is not None and math.isfinite(lo)]
    if not usable:
        return None
    total = sum(du for _, du, _ in usable)
    energy = sum(du * 10 ** (lo / 10) for lo, du, _ in usable) / total
    loud = 10 * math.log10(energy) if energy > 0 else -70.0
    return round(RG_REFERENCE - loud, 2), max(pk for _, _, pk in usable)


# -- covers ----------------------------------------------------------------------------------------------------------

COVER_STEMS = ("cover", "folder", "front", "album", "albumart", "albumartsmall", "thumb")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
DISC_DIR_RX = re.compile(r"^(cd|dis[ck]|disco)\s*\d+$", re.I)


def folder_cover(folder: Path, cache: dict[Path, dict[str, str]]) -> str:
    """cover/folder/front.jpg… in the album folder, or in its parent when the folder is «CD1» / «Disc 2»."""
    for d in (folder, folder.parent) if DISC_DIR_RX.match(folder.name) else (folder,):
        imgs = cache.get(d)
        if imgs is None:
            imgs = {}
            with contextlib.suppress(OSError), os.scandir(d) as it:
                for e in it:
                    if e.name.lower().endswith(IMAGE_EXTS) and not e.name.startswith("."):
                        imgs[e.name.lower()] = e.name
            cache[d] = imgs
        for stem in COVER_STEMS:
            for ext in IMAGE_EXTS:
                real = imgs.get(stem + ext)
                if real:
                    return str(d / real)
    return ""


def extract_cover(path: str | Path, dest: Path, timeout: float = 30.0) -> bool:
    """Embedded picture → JPEG (≤ 500 px) in the cache."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.jpg")
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(path), "-map", "0:v:0", "-an", "-frames:v", "1",
           "-vf", "scale='min(500,iw)':-2", "-q:v", "3", str(tmp)]
    try:
        out = run_low(cmd, timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if out.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        os.replace(tmp, dest)
        return True
    with contextlib.suppress(OSError):
        tmp.unlink()
    return False


# -- default folder --------------------------------------------------------------------------------------------------

def xdg_music_dir() -> Path | None:
    """The user's Music folder: ``MPV_UOS_MUSIC_DIR`` (empty = none; tests), XDG ``user-dirs.dirs``, ~/Music."""
    env = os.environ.get("MPV_UOS_MUSIC_DIR")
    if env is not None:
        return Path(env).expanduser() if env.strip() else None
    home = Path.home()
    if sys.platform.startswith("linux"):
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
        with contextlib.suppress(OSError):
            for line in cfg.read_text(encoding="utf-8").splitlines():
                m = re.match(r'\s*XDG_MUSIC_DIR\s*=\s*"(.*)"\s*$', line)
                if m:
                    p = Path(m.group(1).replace("$HOME", str(home))).expanduser()
                    if p != home:
                        return p
    for name in ("Music", "Música", "Musica"):
        if (home / name).is_dir():
            return home / name
    return None
