"""Silence map for "smart speed": quiet spans of a local file (ffmpeg silencedetect, see mpvd/intro/detect.py), cached per
file hash + parameters. mu-study speeds playback up while time-pos is inside a span and restores it when speech returns."""

from __future__ import annotations

from typing import Any

from mpvd.intro.detect import detect_edges

DEFAULT_DB = -30.0
DEFAULT_MIN = 0.5
EDGE_PAD = 0.15         # keep a little of the silence at both ends so speech never gets clipped


async def silence_map(src: str, start: float, length: float, noise_db: float = DEFAULT_DB, min_seconds: float = DEFAULT_MIN,
                      pad: float = EDGE_PAD) -> list[list[float]]:
    """``[[a, b], ...]`` (media time) of silences at least ``min_seconds`` long inside [start, start+length)."""
    det = await detect_edges(src, start, length, silence_db=noise_db, silence_min=min_seconds, black_min=9999.0)
    spans: list[list[float]] = []
    for a, b in det.get("silence", []):
        p = min(pad, (b - a) / 4)          # short gaps keep at least half of their length
        a2, b2 = a + p, b - p
        if b2 - a2 >= 0.1:
            spans.append([round(a2, 3), round(b2, 3)])
    return spans


def summary(spans: list[list[float]], length: float) -> dict[str, Any]:
    quiet = sum(b - a for a, b in spans)
    return {"spans": len(spans), "quiet_seconds": round(quiet, 2), "quiet_ratio": round(quiet / length, 3) if length else 0.0}
