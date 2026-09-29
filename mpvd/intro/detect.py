"""Silence and black-frame edges with ffmpeg's ``silencedetect`` / ``blackdetect`` (parsed from stderr; verified with
FFmpeg 8.0). Used to snap fingerprint-based segment boundaries to natural cuts.

Only a few seconds around each boundary are decoded (``refine_edges``): the cost no longer grows with the length of the
segment (decoding a whole 5-minute intro of a 720p file took ~50-100 s; four ±6 s windows take a few seconds)."""

from __future__ import annotations

import asyncio
import re

from mpvd.asr.audio import AudioError, ffmpeg_path

SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")
BLACK = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")


EDGE_RADIUS = 6.0            # seconds decoded on each side of a boundary
MERGE_GAP = 20.0             # boundaries closer than this share one ffmpeg run


async def refine_edges(src: str, points: list[float], radius: float = EDGE_RADIUS, duration: float | None = None,
                       timeout: float = 60.0) -> dict[str, list[tuple[float, float]]]:
    """Silences and black frames within ``radius`` seconds of each point (windows closer than ``MERGE_GAP`` merged)."""
    windows: list[list[float]] = []
    for p in sorted(points):
        a = max(0.0, p - radius)
        b = p + radius if duration is None else min(duration, p + radius)
        if b <= a:
            continue
        if windows and a - windows[-1][1] <= MERGE_GAP:
            windows[-1][1] = max(windows[-1][1], b)
        else:
            windows.append([a, b])
    out: dict[str, list[tuple[float, float]]] = {"silence": [], "black": []}
    for a, b in windows:
        det = await detect_edges(src, a, b - a, timeout=timeout)
        out["silence"].extend(det["silence"])
        out["black"].extend(det["black"])
    return out


async def detect_edges(src: str, start: float, length: float, silence_db: float = -45.0, silence_min: float = 0.4,
                       black_min: float = 0.3, timeout: float = 120.0) -> dict[str, list[tuple[float, float]]]:
    """``{"silence": [(a, b)...], "black": [(a, b)...]}`` in media time for the window [start, start+length).
    Never raises: a missing ffmpeg, a timeout or a broken file just give no cuts."""
    try:
        return await _detect_edges(src, start, length, silence_db, silence_min, black_min, timeout)
    except (AudioError, OSError, asyncio.TimeoutError):
        return {"silence": [], "black": []}


async def _detect_edges(src: str, start: float, length: float, silence_db: float, silence_min: float,
                        black_min: float, timeout: float) -> dict[str, list[tuple[float, float]]]:
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
    blacks = [(start + float(m.group(1)), start + float(m.group(2))) for m in BLACK.finditer(text)]
    return {"silence": _silences(text, start, length), "black": blacks}


def _silences(text: str, start: float, length: float) -> list[tuple[float, float]]:
    """silence_start/silence_end pairs (timestamps relative to the window) → media time; an open one ends the window."""
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
    return silences


async def _audio_only(src: str, start: float, length: float, silence_db: float, silence_min: float,
                      timeout: float) -> dict[str, list[tuple[float, float]]]:
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "info", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}",
            "-i", src, "-vn", "-af", f"silencedetect=n={silence_db}dB:d={silence_min}", "-f", "null", "-"]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"silence": [], "black": []}
    return {"silence": _silences(err.decode("utf-8", "replace"), start, length), "black": []}


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
