"""Saving subtitles as SRT next to the video (``subs.save``) and extracting embedded text tracks (``subs.extract``).

Naming: ``<video>.<lang>.srt``; when that exists, ``<video>.<lang>.ia.srt`` (AI track or translation) or
``<video>.<lang>.resync.srt``, then `` (2)``, `` (3)``… Files are written atomically (temporary file + rename in the
same folder). When the video folder is not writable or the video is a URL, files go to
``<XDG_VIDEOS_DIR or ~/Vídeos>/MPV-UOS/Subtítulos``.
"""

from __future__ import annotations

from mpvd.brand import folder_name
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mpvd.asr.srt import render_srt
from mpvd.subs.formats import SubtitleError, detect_format, load_cues, read_text

KINDS = ("ai", "translation", "resync", "track")
KIND_TAG = {"ai": "ia", "translation": "ia", "resync": "resync", "track": ""}
# Bitmap subtitle codecs (mpv track-list "codec" and ffprobe "codec_name" use the same FFmpeg names).
IMAGE_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "pgssub", "dvdsub", "dvbsub"})
URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
IMAGE_ERROR = "subtítulo de imagen: necesita OCR (no se puede convertir a SRT)"


class SaveError(RuntimeError):
    pass


def is_url(path: str) -> bool:
    return bool(URL_RE.match(path)) and not path.startswith("file://")


def local_path(path: str) -> Path:
    return Path(urllib.parse.unquote(path[7:]) if path.startswith("file://") else path)


def safe_filename(name: str, limit: int = 120) -> str:
    name = _UNSAFE.sub(" ", name).strip(" .")
    name = " ".join(name.split())
    return name[:limit].rstrip(" .") or "subtitulos"


def video_stem(path: str, title: str | None = None) -> str:
    """File name (without extension) the subtitle is named after: the video's, or the title/URL of a stream."""
    if not is_url(path):
        return local_path(path).stem or "subtitulos"
    t = (title or "").strip()
    if t and not is_url(t):
        return safe_filename(re.sub(r"\.[A-Za-z0-9]{2,4}$", "", t))
    tail = urllib.parse.unquote(urllib.parse.urlsplit(path).path.rstrip("/").rsplit("/", 1)[-1])
    return safe_filename(Path(tail).stem or urllib.parse.urlsplit(path).netloc)


def xdg_videos_dir() -> Path | None:
    """``XDG_VIDEOS_DIR`` from the environment or ``user-dirs.dirs`` (Linux), when that folder exists."""
    env = os.environ.get("XDG_VIDEOS_DIR")
    candidates = [env] if env else []
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*XDG_VIDEOS_DIR\s*=\s*"(.*)"\s*$', line)
            if m:
                candidates.append(m.group(1).replace("$HOME", str(Path.home())))
    except OSError:
        pass
    for c in candidates:
        p = Path(os.path.expanduser(c))
        if p.is_dir() and p != Path.home():
            return p
    return None


def fallback_dir() -> Path:
    env = os.environ.get("MPV_UOS_SUBS_SAVE_DIR")
    if env:
        return Path(env)
    videos = xdg_videos_dir() or Path.home() / "Vídeos"
    return videos / folder_name() / "Subtítulos"


def dir_writable(d: Path) -> bool:
    if not d.is_dir() or not os.access(d, os.W_OK | os.X_OK):
        return False
    try:
        fd, tmp = tempfile.mkstemp(prefix=".mpv-uos-", suffix=".tmp", dir=str(d))
    except OSError:
        return False
    os.close(fd)
    os.unlink(tmp)
    return True


def target_dir(path: str, dest_dir: str | None = None, force_fallback: bool = False) -> tuple[Path, bool]:
    """(folder, is_fallback). An explicit ``dest_dir`` must be writable; otherwise the video's folder when it is a
    writable local folder, else the fallback folder (created)."""
    if dest_dir and not force_fallback:
        d = Path(os.path.expanduser(dest_dir))
        d.mkdir(parents=True, exist_ok=True)
        if not dir_writable(d):
            raise SaveError(f"no se puede escribir en {d}")
        return d, False
    if not is_url(path) and not force_fallback:
        d = local_path(path).parent
        if dir_writable(d):
            return d, False
    fb = fallback_dir()
    try:
        fb.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SaveError(f"no se puede crear {fb}: {exc}") from exc
    if not dir_writable(fb):
        raise SaveError(f"no se puede escribir en {fb}")
    return fb, True


def choose_name(folder: Path, stem: str, lang: str, kind: str, overwrite: bool = False,
                avoid: tuple[Path, ...] = ()) -> Path:
    """``<stem>.<lang>.srt``, then ``<stem>.<lang>.<tag>.srt`` (ia/resync), then `` (2)``… (never one of ``avoid``)."""
    lang = (lang or "").strip().lower()
    base = f"{stem}.{lang}" if lang and lang not in ("und", "auto", "?") else stem
    avoid_set = {p.resolve() for p in avoid}

    def free(p: Path) -> bool:
        return p.resolve() not in avoid_set and (overwrite or not p.exists())

    primary = folder / f"{base}.srt"
    if free(primary):
        return primary
    tag = KIND_TAG.get(kind, "")
    stem2 = f"{base}.{tag}" if tag else base
    if tag and free(folder / f"{stem2}.srt"):
        return folder / f"{stem2}.srt"
    for n in range(2, 1000):
        p = folder / f"{stem2} ({n}).srt"
        if p.resolve() not in avoid_set and not p.exists():
            return p
    raise SaveError("demasiados archivos con el mismo nombre")


def write_atomic(dest: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.stem[:40]}-", suffix=".srt.tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def srt_text(source: str | Path) -> tuple[str, int]:
    """SRT text of a subtitle file: SRT is kept as written (re-encoded to UTF-8, italics and all), ASS/SSA/WebVTT are
    converted (every line kept, styling dropped). Returns (text, number of cues)."""
    p = Path(source)
    try:
        cues = load_cues(p, lines="keep")
    except SubtitleError as exc:
        raise SaveError(str(exc)) from exc
    raw = read_text(p)
    if detect_format(p, raw) == "srt":
        text = raw.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        return text, len(cues)
    return render_srt(cues), len(cues)


# -- embedded tracks -----------------------------------------------------------------------------------------------


def probe_streams(path: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SaveError("ffprobe no encontrado")
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                              "stream=index,codec_type,codec_name:stream_tags=language,title", "-of", "json", path],
                             capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SaveError(f"ffprobe: {exc}") from exc
    try:
        return list(json.loads(out.stdout or "{}").get("streams") or [])
    except ValueError as exc:
        raise SaveError(f"ffprobe: salida no válida ({out.stderr.strip()[-200:]})") from exc


def subtitle_stream(path: str, ff_index: int) -> dict[str, Any]:
    for s in probe_streams(path):
        if int(s.get("index", -1)) == int(ff_index):
            if s.get("codec_type") != "subtitle":
                raise SaveError(f"el flujo {ff_index} no es un subtítulo ({s.get('codec_type')})")
            return s
    raise SaveError(f"no existe el flujo {ff_index} en el archivo")


def extract_args(path: str, ff_index: int, out: str) -> list[str]:
    """``ffmpeg -i V -map 0:<ff-index> -c:s srt -f srt OUT`` (+ progress on stdout)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SaveError("ffmpeg no encontrado")
    return [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", path, "-map", f"0:{int(ff_index)}", "-c:s", "srt",
            "-f", "srt", "-progress", "pipe:1", "-nostats", out]


async def extract_srt(path: str, ff_index: int, dest: Path, duration: float | None = None,
                      progress: Callable[[float], None] | None = None, timeout: float = 1800.0) -> int:
    """Run the extraction into ``dest`` (atomic). Returns the number of cues; raises SaveError."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.stem}.part.srt")
    proc = await asyncio.create_subprocess_exec(*extract_args(path, ff_index, str(tmp)), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)

    async def read_progress() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            key, _, value = line.decode("utf-8", "replace").strip().partition("=")
            if key == "out_time_us" and duration and progress is not None:
                try:
                    progress(max(0.0, min(0.99, int(value) / 1e6 / duration)))
                except ValueError:
                    pass

    try:
        reader = asyncio.create_task(read_progress())
        assert proc.stderr is not None
        err = await asyncio.wait_for(proc.stderr.read(), timeout)
        await asyncio.wait_for(proc.wait(), timeout)
        await reader
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        tmp.unlink(missing_ok=True)
        raise
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise SaveError(f"ffmpeg no pudo extraer la pista ({proc.returncode}): "
                        f"{err.decode('utf-8', 'replace').strip()[-300:]}")
    try:
        cues = load_cues(tmp, lines="keep")
    except SubtitleError as exc:
        tmp.unlink(missing_ok=True)
        raise SaveError("la pista no tiene texto que guardar") from exc
    os.replace(tmp, dest)
    return len(cues)
