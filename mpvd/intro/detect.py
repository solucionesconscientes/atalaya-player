"""Silence and black-frame edges with ffmpeg's ``silencedetect`` / ``blackdetect`` (parsed from stderr; verified with
FFmpeg 8.0). Used to snap fingerprint-based segment boundaries to natural cuts."""

from __future__ import annotations

import asyncio
import re

from mpvd.asr.audio import ffmpeg_path

SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")
BLACK = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")


async def detect_edges(src: str, start: float, length: float, silence_db: float = -45.0, silence_min: float = 0.4,
                       black_min: float = 0.3, timeout: float = 120.0) -> dict[str, list[tuple[float, float]]]:
    """``{"silence": [(a, b)...], "black": [(a, b)...]}`` in media time for the window [start, start+length)."""
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "info", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}",
            "-i", src, "-af", f"silencedetect=n={silence_db}dB:d={silence_min}", "-vf", f"blackdetect=d={black_min}:pix_th=0.10",
            "-f", "null", "-"]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"silence": [], "black": []}
    text = err.decode("utf-8", "replace")
    if "Output file is empty" in text or proc.returncode not in (0, None):
        # audio-only or video-only inputs make one of the filters fail: retry with the other alone
        if "-vf" in args and "silence" not in text:
            return await _audio_only(src, start, length, silence_db, silence_min, timeout)
    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in text.splitlines():
        m = SIL_START.search(line)
        if m:
            pending = float(m.group(1))
            continue
        m = SIL_END.search(line)
        if m and pending is not None:
            silences.append((start + pending, start + float(m.group(1))))
            pending = None
    if pending is not None:
        silences.append((start + pending, start + length))
    blacks = [(start + float(m.group(1)), start + float(m.group(2))) for m in BLACK.finditer(text)]
    return {"silence": silences, "black": blacks}


async def _audio_only(src: str, start: float, length: float, silence_db: float, silence_min: float,
                      timeout: float) -> dict[str, list[tuple[float, float]]]:
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "info", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}",
            "-i", src, "-vn", "-af", f"silencedetect=n={silence_db}dB:d={silence_min}", "-f", "null", "-"]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), timeout)
    text = err.decode("utf-8", "replace")
    silences, pending = [], None
    for line in text.splitlines():
        m = SIL_START.search(line)
        if m:
            pending = float(m.group(1))
            continue
        m = SIL_END.search(line)
        if m and pending is not None:
            silences.append((start + pending, start + float(m.group(1))))
            pending = None
    return {"silence": silences, "black": []}


def edges(det: dict[str, list[tuple[float, float]]]) -> list[float]:
    """Every cut point (start/end of silences and black frames), sorted."""
    out = []
    for key in ("silence", "black"):
        for a, b in det.get(key, []):
            out.extend((a, b))
    return sorted(set(round(x, 3) for x in out))


def cut_points(det: dict[str, list[tuple[float, float]]]) -> tuple[list[float], list[float]]:
    """(``starts``, ``ends``) of the quiet/black regions: content begins at an ``end`` and stops at a ``start``."""
    starts, ends = [], []
    for key in ("silence", "black"):
        for a, b in det.get(key, []):
            starts.append(round(a, 3))
            ends.append(round(b, 3))
    return sorted(set(starts)), sorted(set(ends))


def snap(t: float, cuts: list[float], tolerance: float = 1.5, fallback: list[float] | None = None) -> float:
    """Nearest cut within ``tolerance`` (then the nearest of ``fallback``), else ``t`` itself."""
    for pool in (cuts, fallback or []):
        best = min(pool, key=lambda c: abs(c - t), default=None)
        if best is not None and abs(best - t) <= tolerance:
            return best
    return t
