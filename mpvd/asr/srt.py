"""Subtitle segments and incremental SRT rendering (chunks arrive out of order; overlaps are trimmed)."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

MAX_SEGMENT_SECONDS = 7.0
MAX_CHARS = 84
MIN_PIECE_CHARS = 30   # a long-but-sparse segment is not split into pieces shorter than this

# -- where to cut a line of text (shared with the translation redistribution, mpvd/subs/translate.py) -------------
STRONG_END = re.compile(r"[.!?…][\"'»”’)\]]*$")
WEAK_END = re.compile(r"[,;:][\"'»”’)\]]*$")
DASH_START = re.compile(r"^[-–—]")
_BARE = re.compile(r"^[¿¡\"'«»“”‘’(\[]+|[\"'«»“”‘’)\].,;:!?…]+$")
# Words a subtitle line should not end with (articles, prepositions, possessives) and conjunctions/relatives a line may
# start with, for the languages of the menus (es, en, ca, fr, it, pt, de). Ambiguous short words go to CONJUNCTIONS;
# Catalan "i" is left out (it is English "I").
FUNCTION_WORDS = frozenset("""
a an the of to in on at by for from with into onto my your his her its our their this these those
el la los las un una unos unas lo al del de en con por para sin sobre mi mis tu tus su sus nuestro nuestra nuestros
nuestras vuestro vuestra este esta estos estas ese esa esos esas
les des du le au aux une mon ton son ma ta sa mes tes ses
il gli di da nel nella nei sul sulla
os as um uma do dos das na no pelo pela
der die das den dem des ein eine einen einem einer zu im am vom zum zur
""".split())
CONJUNCTIONS = frozenset("""
and or but so because when while if although though that which who whom whose where then nor unless until
y e o u ni pero porque cuando mientras si aunque que quien quienes donde pues entonces sino
però perquè quan mentre
et ou mais donc parce quand qui où
ma perché quando mentre che
mas enquanto
und oder aber weil wenn als dass
""".split())


def _bare(word: str) -> str:
    return _BARE.sub("", word).lower()


def cut_score(words: list[str], j: int) -> float:
    """How good it is to end a line after ``words[j-1]`` and start the next one with ``words[j]``: the best of sentence
    end (10), new speaker dash (10), comma/colon (8) and conjunction (6), +1 when two coincide; −10 for leaving an
    article, preposition or conjunction dangling at the end of the line."""
    prev, nxt = words[j - 1], words[j]
    marks = []
    if STRONG_END.search(prev):
        marks.append(10.0)
    elif WEAK_END.search(prev):
        marks.append(8.0)
    elif _bare(prev) in FUNCTION_WORDS or _bare(prev) in CONJUNCTIONS:
        return -10.0          # "... coming to my" | "parents' dinner"
    if DASH_START.match(nxt):
        marks.append(10.0)
    if _bare(nxt) in CONJUNCTIONS:
        marks.append(6.0)
    return max(marks) + (1.0 if len(marks) > 1 else 0.0) if marks else 0.0


def cut_words(words: list[str], weights: list[float], window: int = 3) -> list[int]:
    """Word indices where to cut ``words`` into ``len(weights)`` parts of lengths roughly proportional to ``weights``
    (increasing, every part keeps ≥1 word; needs more words than parts).

    Each cut starts at the character position that gives the rest of the text proportionally to the remaining weights
    and moves within ±``window`` words to the best boundary (:func:`cut_score`); the distance, in average words,
    is subtracted from the score."""
    n, parts = len(words), len(weights)
    if parts <= 1:
        return []
    if n < parts:
        raise ValueError("more parts than words")
    ends = [0] * (n + 1)
    pos = 0
    for j, w in enumerate(words):
        pos += len(w) + (1 if j else 0)
        ends[j + 1] = pos
    avg = max(1.0, ends[n] / n)
    w = [max(1e-6, float(x)) for x in weights]
    cuts: list[int] = []
    prev = 0
    for k in range(parts - 1):
        start = ends[prev] + (1 if prev else 0)
        target = start + (ends[n] - start) * w[k] / sum(w[k:])
        lo, hi = prev + 1, n - (parts - 1 - k)
        base = min(range(lo, hi + 1), key=lambda j: abs(ends[j] - target))
        best_j, best = base, -math.inf
        for j in range(max(lo, base - window), min(hi, base + window) + 1):
            score = cut_score(words, j) - abs(ends[j] - target) / avg
            if score > best + 1e-9:
                best_j, best = j, score
        cuts.append(best_j)
        prev = best_j
    return cuts


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


def split_text(text: str, parts: int) -> list[str]:
    """Split ``text`` into ``parts`` pieces of similar length, cutting at punctuation/conjunctions when close."""
    words = text.split()
    parts = max(1, min(parts, len(words)))
    if parts == 1:
        return [" ".join(words)]
    bounds = [0, *cut_words(words, [1.0] * parts), len(words)]
    return [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:], strict=False)]


def split_long(seg: Segment) -> list[Segment]:
    """Split a long segment into readable pieces of similar length (no short leftovers), cutting at punctuation when
    possible, and distribute its time by character count."""
    text = clean_text(seg.text)
    if not text:
        return []
    duration = seg.end - seg.start
    if duration <= MAX_SEGMENT_SECONDS and len(text) <= MAX_CHARS:
        return [Segment(seg.start, seg.end, text)]
    n_words = len(text.split())
    parts = max(math.ceil(len(text) / MAX_CHARS),
                min(math.ceil(duration / MAX_SEGMENT_SECONDS), len(text) // MIN_PIECE_CHARS), 1)
    while True:
        pieces = split_text(text, parts)
        if all(len(p) <= MAX_CHARS for p in pieces) or parts >= n_words:
            break
        parts += 1
    total = sum(len(p) for p in pieces) or 1
    out, t = [], seg.start
    for p in pieces:
        d = duration * len(p) / total
        out.append(Segment(t, t + d, p))
        t += d
    out[-1].end = seg.end
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


def parse_srt(text: str, keep_lines: bool = False) -> list[Segment]:
    """Minimal SRT parser (used to read back cached files and by tests). Multi-line cues are joined with a space, or
    with ``\\n`` when ``keep_lines``."""
    segs: list[Segment] = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        idx = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[idx]:
            continue
        a, b = [t.strip() for t in lines[idx].split("-->")]
        segs.append(Segment(_parse_time(a), _parse_time(b), ("\n" if keep_lines else " ").join(lines[idx + 1:])))
    return segs


def _parse_time(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
