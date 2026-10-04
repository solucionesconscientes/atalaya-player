"""Audio extraction for ASR with ffmpeg: 16 kHz mono 16-bit WAV of a time window (verified with ffmpeg 8.0)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from mpvd import priority as prio

SAMPLE_RATE = 16000


class AudioError(RuntimeError):
    pass


def ffmpeg_path() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise AudioError("ffmpeg not found in PATH")
    return p


def extract_args(src: str, start: float, duration: float, out: str, audio_track: int | None = None) -> list[str]:
    """``ffmpeg -ss S -t D -i SRC -vn -map 0:a:N -ac 1 -ar 16000 -c:a pcm_s16le -f wav OUT`` (input seeking = fast)."""
    args = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.0, duration):.3f}",
            "-i", src, "-vn", "-sn", "-dn"]
    if audio_track is not None:
        args += ["-map", f"0:a:{int(audio_track)}"]
    args += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", out]
    return args


async def extract_wav(src: str, start: float, duration: float, out: Path, audio_track: int | None = None,
                      timeout: float = 120.0) -> Path:
    """Write the window to ``out`` (WAV 16 kHz mono); raises AudioError on failure or empty output."""
    out.parent.mkdir(parents=True, exist_ok=True)
    # H69 · sacar el audio es trabajo de fondo y lee el archivo entero: no compite con la reproducción
    proc = await asyncio.create_subprocess_exec(*extract_args(src, start, duration, str(out), audio_track),
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                                                **prio.background())
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise AudioError(f"ffmpeg timed out extracting {src} @ {start:.1f}s") from None
    if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 44:
        raise AudioError(f"ffmpeg failed ({proc.returncode}): {err.decode('utf-8', 'replace').strip()[-300:]}")
    return out


def wav_duration(path: Path) -> float:
    """Seconds of audio in a 16 kHz mono 16-bit WAV (from the file size)."""
    size = path.stat().st_size - 44
    return max(0.0, size / (SAMPLE_RATE * 2))


def probe_audio_streams(src: str, timeout: float = 30.0) -> int | None:
    """How many audio streams the container has (``None`` when ffprobe is missing or cannot tell).

    Needed to tell «the first audio track» from «whatever ffmpeg picks»: with a single audio stream they are the same
    thing, and treating them as different identities threw away work already done (H36).
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                              "-of", "json", src],
                             capture_output=True, text=True, timeout=timeout, check=False).stdout
        streams = json.loads(out).get("streams")
        return len(streams) if isinstance(streams, list) else None
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def probe_duration(src: str, timeout: float = 30.0) -> float | None:
    """Container duration in seconds via ffprobe (None for live streams / unknown)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", src],
                             capture_output=True, text=True, timeout=timeout, check=False).stdout
        value = json.loads(out).get("format", {}).get("duration")
        return float(value) if value not in (None, "N/A") else None
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None
