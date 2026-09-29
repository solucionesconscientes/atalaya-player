"""Export a [a, b] range of a local file as mp4 (stream copy or re-encode), gif, mp3 or opus with ffmpeg.

Stream copy is fast but cuts on keyframes (the clip may start slightly earlier); ``mp4`` re-encodes with libx264/aac for
frame-accurate cuts. GIF uses the palettegen/paletteuse pair (12 fps, 480 px wide). Output names are ``<stem> [a-b].<ext>`` in the
user's ``<Videos|Music>/MPV-UOS/clips`` folder unless a directory is given. Progress is parsed from ``-progress pipe:1``."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mpvd.asr.audio import ffmpeg_path
from mpvd.ytdl.downloads import default_media_dir

FORMATS = {
    "mp4": {"ext": ".mp4", "kind": "video", "label": "MP4 recodificado (corte exacto)"},
    "mp4-copy": {"ext": ".mp4", "kind": "video", "label": "MP4 sin recodificar (rápido, corte en keyframe)"},
    "mkv-copy": {"ext": ".mkv", "kind": "video", "label": "MKV sin recodificar"},
    "gif": {"ext": ".gif", "kind": "video", "label": "GIF (12 fps, 480 px)"},
    "mp3": {"ext": ".mp3", "kind": "audio", "label": "MP3 192 kbps"},
    "opus": {"ext": ".opus", "kind": "audio", "label": "Opus 96 kbps"},
    "wav": {"ext": ".wav", "kind": "audio", "label": "WAV"},
}
MAX_SECONDS = 600.0
_TIME = re.compile(r"^out_time_us=(\d+)")
_END = re.compile(r"^progress=end")


class ClipError(RuntimeError):
    pass


def hms_name(t: float) -> str:
    s = int(max(0.0, t))
    return f"{s // 3600:02d}.{s % 3600 // 60:02d}.{s % 60:02d}"


def output_path(src: Path, a: float, b: float, fmt: str, directory: Path | None = None) -> Path:
    spec = FORMATS[fmt]
    folder = directory or (default_media_dir(spec["kind"]) / "clips")
    name = f"{src.stem} [{hms_name(a)}-{hms_name(b)}]{spec['ext']}"
    out = folder / name
    n = 1
    while out.exists():
        n += 1
        out = folder / f"{src.stem} [{hms_name(a)}-{hms_name(b)}] ({n}){spec['ext']}"
    return out


def ffmpeg_args(src: Path, a: float, b: float, fmt: str, out: Path, audio_track: int | None = None,
                gif_width: int = 480, gif_fps: int = 12) -> list[str]:
    """Exact argv for each format (verified against FFmpeg 8 in docs/ESTUDIO.md)."""
    if fmt not in FORMATS:
        raise ClipError(f"formato desconocido: {fmt}")
    dur = b - a
    base = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    amap = ["-map", f"0:a:{audio_track}"] if audio_track is not None else ["-map", "0:a:0?"]
    if fmt in ("mp4-copy", "mkv-copy"):
        # -ss before -i seeks by keyframe (fast); copy both streams, drop subtitles/data
        args = [*base, "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-map", "0:v:0?", *amap, "-c", "copy",
                "-avoid_negative_ts", "make_zero"]
        if fmt == "mp4-copy":
            args += ["-movflags", "+faststart"]
    elif fmt == "mp4":
        args = [*base, "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-map", "0:v:0?", *amap, "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart"]
    elif fmt == "gif":
        fc = (f"[0:v]fps={gif_fps},scale={gif_width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
              f"[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        args = [*base, "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-filter_complex", fc, "-loop", "0", "-an"]
    elif fmt == "mp3":
        args = [*base, "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vn", *amap, "-c:a", "libmp3lame", "-b:a", "192k"]
    elif fmt == "opus":
        args = [*base, "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vn", *amap, "-c:a", "libopus", "-b:a", "96k"]
    else:  # wav
        args = [*base, "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vn", *amap, "-c:a", "pcm_s16le"]
    return [*args, str(out)]


async def export_clip(src: Path, a: float, b: float, fmt: str, out: Path, audio_track: int | None = None,
                      progress: Callable[[float, str], None] | None = None, timeout: float = 900.0) -> dict[str, Any]:
    if not src.is_file():
        raise ClipError(f"no existe: {src}")
    if b <= a:
        raise ClipError("el final del tramo debe ser mayor que el inicio")
    if b - a > MAX_SECONDS:
        raise ClipError(f"tramo demasiado largo (máx. {int(MAX_SECONDS)} s)")
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ffmpeg_args(src, a, b, fmt, out, audio_track)
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    dur_us = (b - a) * 1e6

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            m = _TIME.match(line.decode("utf-8", "replace"))
            if m and progress is not None and dur_us > 0:
                progress(min(0.99, int(m.group(1)) / dur_us), "exportando")

    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out.unlink(missing_ok=True)
        raise ClipError("ffmpeg no terminó a tiempo") from None
    except asyncio.CancelledError:
        proc.kill()
        out.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        err = (await proc.stderr.read()).decode("utf-8", "replace").strip() if proc.stderr else ""
        out.unlink(missing_ok=True)
        raise ClipError(f"ffmpeg falló: {err[-300:]}")
    if progress is not None:
        progress(1.0, "listo")
    return {"file": str(out), "format": fmt, "start": a, "end": b, "seconds": round(b - a, 3), "bytes": out.stat().st_size}
