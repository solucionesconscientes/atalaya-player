"""Subtitle segments and incremental SRT rendering (chunks arrive out of order; overlaps are trimmed)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MAX_SEGMENT_SECONDS = 7.0
MAX_CHARS = 84


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Segment:
        return cls(float(d["start"]), float(d["end"]), str(d.get("text", "")))


def srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def clean_text(text: str) -> str:
    t = " ".join(text.replace("\n", " ").split())
    # whisper.cpp markers for non-speech, e.g. "[BLANK_AUDIO]", "(música)", "♪"
    if not t or t.upper() in ("[BLANK_AUDIO]", "[SILENCE]", "[MUSIC]", "[MÚSICA]", "[APPLAUSE]"):
        return ""
    return t


def split_long(seg: Segment) -> list[Segment]:
    """Split a long segment into readable pieces, distributing time by character count."""
    text = clean_text(seg.text)
    if not text:
        return []
    if seg.end - seg.start <= MAX_SEGMENT_SECONDS and len(text) <= MAX_CHARS:
        return [Segment(seg.start, seg.end, text)]
    words = text.split()
    pieces: list[str] = []
    cur: list[str] = []
    for w in words:
        if cur and len(" ".join([*cur, w])) > MAX_CHARS:
            pieces.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        pieces.append(" ".join(cur))
    total = sum(len(p) for p in pieces) or 1
    out, t = [], seg.start
    for p in pieces:
        d = (seg.end - seg.start) * len(p) / total
        out.append(Segment(t, t + d, p))
        t += d
    return out


def merge_segments(existing: list[Segment], new: list[Segment], chunk_start: float, chunk_end: float) -> list[Segment]:
    """Replace the cues that START inside [chunk_start, chunk_end) with ``new`` (already offset to media time).

    Cues from an earlier chunk that spill into this window are kept (they own their start) and trimmed below.
    """
    kept = [s for s in existing if s.start < chunk_start or s.start >= chunk_end]
    fresh = []
    for s in new:
        for piece in split_long(s):
            if piece.end - piece.start < 0.2 or piece.start >= chunk_end:
                continue
            fresh.append(Segment(max(piece.start, chunk_start), min(piece.end, chunk_end + 0.5), piece.text))
    merged = sorted([*kept, *fresh], key=lambda s: (s.start, s.end))
    # trim overlaps so subtitles never stack
    for i in range(1, len(merged)):
        if merged[i].start < merged[i - 1].end:
            merged[i - 1].end = max(merged[i - 1].start + 0.1, merged[i].start - 0.01)
    return merged


def render_srt(segments: list[Segment]) -> str:
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(f"{i}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{s.text}\n")
    return "\n".join(lines) + ("\n" if lines else "")


def parse_srt(text: str) -> list[Segment]:
    """Minimal SRT parser (used to read back cached files and by tests)."""
    segs: list[Segment] = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        idx = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[idx]:
            continue
        a, b = [t.strip() for t in lines[idx].split("-->")]
        segs.append(Segment(_parse_time(a), _parse_time(b), " ".join(lines[idx + 1:])))
    return segs


def _parse_time(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
