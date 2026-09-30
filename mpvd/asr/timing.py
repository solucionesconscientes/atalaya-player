"""Subtitle timing from word timestamps (H16, ADR-042).

whisper.cpp gives one segment per sentence-ish stretch with loose times; with ``-ojf`` it also gives token times. Here:

1. :func:`words_from_cli_json` joins tokens into words with their own start/end (DTW time when available);
2. :func:`speech_regions` finds where there is voice in the chunk (energy VAD on the 16 kHz WAV, pure Python);
3. :func:`build_cues` groups words into cues: a pause cuts, long lines are cut at real word times at the best boundary
   (sentence end, comma, conjunction; never after an article or preposition), starts and ends snap to the voice onset
   and offset nearby, and reading rules apply (minimum duration, characters per second, a small gap, no overlaps).
"""

from __future__ import annotations

import math
import re
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mpvd.asr.srt import STRONG_END, WEAK_END, Segment, clean_text, cut_score

# reading rules (Netflix-like, in seconds / characters)
MAX_CHARS = 84          # two lines of 42
MAX_DURATION = 7.0
MIN_DURATION = 0.9
MAX_CPS = 17.0          # characters per second a viewer can read comfortably
MIN_GAP = 0.08          # between two cues (≈ 2 frames)
PAUSE = 0.6             # silence between words that always starts a new cue
SENTENCE_PAUSE = 0.25   # a shorter pause starts a new cue after a sentence end, after a comma once the cue has
CLAUSE_CHARS = 25       # this many characters, or anywhere once the cue fills a line
LINE_CHARS = 42
SNAP = 0.35             # how far a cue edge may move to the voice onset/offset
CUT_WINDOW = 6          # words looked back to place a cut in a long line

_SPECIAL = re.compile(r"^\[_[A-Z0-9_]+\]$|^<\|.*\|>$")


@dataclass
class Word:
    start: float
    end: float
    text: str


def _token_time(tok: dict[str, Any], key: str) -> float | None:
    off = tok.get("offsets") or {}
    try:
        return float(off[key]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None


def words_from_cli_json(data: dict[str, Any]) -> list[Word]:
    """Words of a whisper-cli ``-ojf`` result: a token starting with a space (or the first of a segment) opens a word;
    special tokens (``[_BEG_]``, ``[_TT_…]``) are skipped. Times are the token offsets (ms); with ``--dtw`` (``t_dtw``,
    centiseconds, −1 when missing) a token's DTW time marks its END, so a word starts at the DTW time of the token
    before it and ends at the DTW time of its last token (verified on 1.9.3, docs/WHISPER.md)."""
    words: list[Word] = []
    for row in data.get("transcription") or []:
        cur: Word | None = None
        prev_dtw: float | None = None
        for tok in row.get("tokens") or []:
            text = str(tok.get("text", ""))
            dtw = tok.get("t_dtw")
            dtw_s = float(dtw) / 100.0 if isinstance(dtw, (int, float)) and dtw >= 0 else None
            if not text or _SPECIAL.match(text.strip()):
                continue
            t0, t1 = _token_time(tok, "from"), _token_time(tok, "to")
            if t0 is None or t1 is None:
                continue
            if dtw_s is not None:
                t0, t1 = (prev_dtw if prev_dtw is not None else t0), dtw_s
            prev_dtw = dtw_s
            if cur is None or text.startswith(" "):
                if cur is not None and cur.text.strip():
                    words.append(cur)
                cur = Word(t0, max(t0, t1), text.strip())
            else:
                cur.text += text
                cur.end = max(cur.end, t1)
        if cur is not None and cur.text.strip():
            words.append(cur)
    out = [w for w in words if clean_text(w.text)]
    for i in range(1, len(out)):  # token times are monotonic in a segment, not always across segments
        if out[i].start < out[i - 1].start:
            out[i].start = out[i - 1].start
        out[i].end = max(out[i].end, out[i].start)
    return out


# -- whisper.cpp VAD: token times are in "speech only" time --------------------------------------------------------

_VAD_INFO = re.compile(r"vad_segment_info: orig_start: ([\d.]+), orig_end: ([\d.]+), vad_start: ([\d.]+), "
                       r"vad_end: ([\d.]+)")


def parse_vad_segments(stderr: str) -> list[tuple[float, float, float, float]]:
    """``(orig_start, orig_end, vad_start, vad_end)`` from whisper-cli's log (printed without ``-np`` when ``--vad`` is
    on; verified with 1.9.3). With VAD, whisper decodes the speech stretches glued together: segment times come back
    mapped to the original audio, token times do not."""
    return [tuple(float(x) for x in m.groups()) for m in _VAD_INFO.finditer(stderr)]  # type: ignore[misc]


def vad_to_orig(t: float, table: list[tuple[float, float, float, float]]) -> float:
    """Original-audio time of a time in the glued "speech only" audio (inside a stretch: same offset; in the short
    padding between two stretches: right after the end of the previous one)."""
    if not table:
        return t
    prev = None
    for o0, o1, v0, v1 in table:
        if t < v0:
            if prev is None:
                return max(0.0, o0 - (v0 - t))
            return min(prev[1] + (t - prev[3]), o0)
        if t <= v1:
            return o0 + (t - v0)
        prev = (o0, o1, v0, v1)
    assert prev is not None
    return prev[1] + (t - prev[3])


def align_to_stretches(words: list[Word], table: list[tuple[float, float, float, float]]) -> list[Word]:
    """Put every word inside one speech stretch (VAD time). Token times drift by a few hundred ms, the stretches found
    by Silero do not: each gap between two stretches goes to the word boundary closest to it (a sentence end or comma
    nearby wins), and the words on each side are clamped into their stretch."""
    if len(table) < 2 or len(words) < 2:
        return words
    words = [Word(w.start, w.end, w.text) for w in words]
    texts = [w.text for w in words]
    owner = [0] * len(words)
    first = 1
    for i in range(len(table) - 1):
        gap = (table[i][3] + table[i + 1][2]) / 2
        best_j, best = None, math.inf
        for j in range(first, len(words)):
            b = (words[j - 1].end + words[j].start) / 2
            dist = abs(b - gap)
            if dist > 1.5:
                if b > gap:
                    break
                continue
            cost = dist - 0.08 * cut_score(texts, j)  # sentence end −0.8 s … after an article +0.8 s
            if cost < best:
                best_j, best = j, cost
        if best_j is None:
            continue
        for k in range(best_j, len(words)):
            owner[k] = i + 1
        first = best_j + 1
    for k, w in enumerate(words):
        _, _, v0, v1 = table[owner[k]]
        w.start = min(max(w.start, v0), v1)
        w.end = min(max(w.end, w.start), v1)
        if k > 0 and owner[k] != owner[k - 1]:
            w.start = v0                           # the voice of this stretch starts with this word
            words[k - 1].end = table[owner[k - 1]][3]  # … and the previous stretch ends with the previous word
    return words


def remap_words(words: list[Word], table: list[tuple[float, float, float, float]]) -> list[Word]:
    """Words in VAD time → words in original time (aligned to the speech stretches first)."""
    if not table:
        return words
    return [Word(vad_to_orig(w.start, table), max(vad_to_orig(w.end, table), vad_to_orig(w.start, table)), w.text)
            for w in align_to_stretches(words, table)]


# -- voice activity -----------------------------------------------------------------------------------------------

def speech_regions(wav: Path, frame: float = 0.02, min_speech: float = 0.1, hangover: float = 0.15,
                   max_seconds: float | None = None) -> list[tuple[float, float]]:
    """Stretches with voice in a 16-bit mono WAV: frame energy above a threshold set between the noise floor (10th
    percentile) and the loud frames (90th percentile), never below −50 dBFS; gaps shorter than ``hangover`` are
    bridged and blips shorter than ``min_speech`` dropped. Pure Python: ~0.1 s for a 30 s chunk."""
    with wave.open(str(wav), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            return []
        rate = w.getframerate()
        n = w.getnframes() if max_seconds is None else min(w.getnframes(), int(max_seconds * rate))
        samples = array("h")
        samples.frombytes(w.readframes(n))
    step = max(1, int(rate * frame))
    energies = []
    for i in range(0, len(samples) - step + 1, step):
        chunk = samples[i:i + step]
        energies.append(math.sqrt(sum(x * x for x in chunk) / step))
    if not energies:
        return []
    ordered = sorted(energies)
    floor = ordered[len(ordered) // 10]
    loud = ordered[(len(ordered) * 9) // 10]
    threshold = max(32768 * 10 ** (-50 / 20), floor + (loud - floor) * 0.15, floor * 2.0)
    regions: list[list[float]] = []
    for k, e in enumerate(energies):
        if e < threshold:
            continue
        t0, t1 = k * frame, (k + 1) * frame
        if regions and t0 - regions[-1][1] <= hangover:
            regions[-1][1] = t1
        else:
            regions.append([t0, t1])
    return [(a, b) for a, b in regions if b - a >= min_speech]


def split_stretches(stretches: list[tuple[float, float]], regions: list[tuple[float, float]],
                    min_gap: float = 0.25) -> list[tuple[float, float]]:
    """Split each (Silero) stretch at the pauses of at least ``min_gap`` that the energy VAD sees inside it: Silero
    glues phrases separated by short pauses, and those pauses are where a cue should change."""
    gaps = [(b, c) for (_, b), (c, _) in zip(regions, regions[1:]) if c - b >= min_gap]
    out: list[tuple[float, float]] = []
    for a, b in stretches:
        start = a
        for g0, g1 in gaps:
            if start + 0.2 < g0 and g1 < b - 0.2:
                out.append((start, g0))
                start = g1
        out.append((start, b))
    return out


def snap_start(t: float, regions: list[tuple[float, float]], reach: float = SNAP) -> float:
    """Move a cue start to the voice onset: back to the start of the region it falls in, or forward to the next onset
    when it falls in silence (both within ``reach``)."""
    for a, b in regions:
        if a <= t <= b:
            return a if t - a <= reach else t
        if t < a:
            return a if a - t <= reach else t
    return t


def snap_end(t: float, regions: list[tuple[float, float]], reach: float = SNAP) -> float:
    """Move a cue end to the voice offset: forward to the end of the region it falls in, or back to the previous offset
    when it falls in silence (both within ``reach``)."""
    prev_end = None
    for a, b in regions:
        if a <= t <= b:
            return b if b - t <= reach else t
        if t < a:
            break
        prev_end = b
    if prev_end is not None and t - prev_end <= reach:
        return prev_end
    return t


# -- grouping ------------------------------------------------------------------------------------------------------

def _text(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def _best_cut(seq: list[Word]) -> int:
    """Index where the next cue starts when ``seq`` is too long: the best boundary among the last words, weighing the
    boundary quality (:func:`cut_score`), the silence there and how full the first part is."""
    texts = [w.text for w in seq]
    best_j, best = len(seq) - 1, -math.inf
    for j in range(max(1, len(seq) - CUT_WINDOW), len(seq)):
        head = _text(seq[:j])
        if len(head) > MAX_CHARS and j > 1:
            continue
        gap = max(0.0, seq[j].start - seq[j - 1].end)
        score = cut_score(texts, j) + min(gap, 1.0) * 8.0 - (8.0 if len(head) < 20 else 0.0) + j * 0.01
        if score > best:
            best_j, best = j, score
    return best_j


def group_words(words: list[Word]) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            n = len(_text(cur))
            pause_cut = gap >= SENTENCE_PAUSE and (STRONG_END.search(cur[-1].text) is not None or n >= LINE_CHARS
                                                   or (n >= CLAUSE_CHARS and WEAK_END.search(cur[-1].text) is not None))
            if gap >= PAUSE or pause_cut:
                groups.append(cur)
                cur = []
            elif len(_text([*cur, w])) > MAX_CHARS or w.end - cur[0].start > MAX_DURATION:
                seq = [*cur, w]
                j = _best_cut(seq)
                groups.append(seq[:j])
                cur = seq[j:]
                continue
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def apply_reading_rules(cues: list[Segment], limit: float | None = None) -> list[Segment]:
    """Minimum duration and characters per second by extending the end into the following silence, a ``MIN_GAP``
    between cues and no overlaps. ``limit``: nothing may end after it (end of the audio)."""
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start - MIN_GAP if i + 1 < len(cues) else math.inf
        if limit is not None:
            nxt = min(nxt, limit)
        want = max(c.end, c.start + MIN_DURATION, c.start + len(c.text) / MAX_CPS)
        want = min(want, c.start + MAX_DURATION)
        c.end = max(min(want, nxt), c.start + 0.3) if c.end <= nxt else max(nxt, c.start + 0.3)
    for i in range(1, len(cues)):  # a too-short neighbour may still overlap: never stack
        if cues[i].start < cues[i - 1].end:
            cues[i].start = cues[i - 1].end + 0.01
            cues[i].end = max(cues[i].end, cues[i].start + 0.3)
    return cues


def build_cues(words: list[Word], regions: list[tuple[float, float]] | None = None,
               limit: float | None = None) -> list[Segment]:
    """Cues (chunk time) from words and the voice regions of the chunk."""
    regions = regions or []
    cues: list[Segment] = []
    for group in group_words(words):
        text = clean_text(_text(group))
        if not text:
            continue
        start = snap_start(group[0].start, regions)
        end = snap_end(max(group[-1].end, start + 0.1), regions)
        if cues and start < cues[-1].end:  # snapping must not reach into the previous cue
            start = max(group[0].start, cues[-1].end)
        cues.append(Segment(start, max(end, start + 0.1), text))
    return apply_reading_rules(cues, limit)
