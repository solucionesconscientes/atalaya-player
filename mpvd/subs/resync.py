"""Resynchronise external subtitles against a Whisper transcription of the same audio.

Cues and reference segments are matched by word overlap (accent-folded token sets) inside a time band, the best
monotonic chain of matches is picked (weighted longest-increasing-subsequence over the reference index), and the
time offset is estimated per window (median of the matched pairs) and interpolated linearly in between, so both a
constant delay and a slow drift (different frame rate, cut adverts) are corrected. Pure Python, no numpy.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from mpvd.asr.srt import Segment

BAND_SECONDS = 120.0       # a subtitle is never looked for further than this from where it is
MIN_SIM = 0.25
STOPWORDS = {"el", "la", "los", "las", "de", "del", "que", "y", "a", "en", "un", "una", "es", "no", "se", "lo", "por", "con",
             "the", "a", "an", "of", "to", "and", "in", "is", "it", "that", "i", "you", "he", "she", "we", "they", "on", "for"}
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in WORD_RE.findall(fold(text)) if len(w) > 1 and w not in STOPWORDS)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / math.sqrt(len(a) * len(b))


@dataclass
class Match:
    cue: int
    ref: int
    sim: float
    delta: float          # ref.start - cue.start (what has to be ADDED to the cue)
    usable: bool = True   # False when the cue is only a fragment of the reference segment (position unknown)


@dataclass
class ResyncResult:
    cues: list[Segment]
    matches: list[Match] = field(default_factory=list)
    knots: list[tuple[float, float]] = field(default_factory=list)   # (cue time, offset)
    stats: dict[str, Any] = field(default_factory=dict)


class _PrefixMax:
    """Fenwick tree for prefix maximum with argmax (values are (score, node index))."""

    def __init__(self, n: int):
        self.n = n
        self.tree: list[tuple[float, int]] = [(-1.0, -1)] * (n + 1)

    def update(self, i: int, value: tuple[float, int]) -> None:
        i += 1
        while i <= self.n:
            if value > self.tree[i]:
                self.tree[i] = value
            i += i & -i

    def query(self, i: int) -> tuple[float, int]:
        i += 1
        best = (-1.0, -1)
        while i > 0:
            if self.tree[i] > best:
                best = self.tree[i]
            i -= i & -i
        return best


def candidates(cues: list[Segment], ref: list[Segment], band: float = BAND_SECONDS,
               min_sim: float = MIN_SIM) -> list[Match]:
    """All (cue, ref) pairs inside the band with enough word overlap; the reference is also tried two segments at a time."""
    ref_tok = [tokens(r.text) for r in ref]
    pair_tok = [ref_tok[j] | ref_tok[j + 1] if j + 1 < len(ref) else ref_tok[j] for j in range(len(ref))]
    starts = [r.start for r in ref]
    out: list[Match] = []
    lo = 0
    for i, c in enumerate(cues):
        ct = tokens(c.text)
        if not ct:
            continue
        while lo < len(ref) and starts[lo] < c.start - band:
            lo += 1
        j = lo
        while j < len(ref) and starts[j] <= c.start + band:
            s1 = similarity(ct, ref_tok[j])
            s2 = similarity(ct, pair_tok[j]) * 0.9
            s = max(s1, s2)
            if s >= min_sim:
                # a short cue fully inside a longer reference segment tells us little about its exact offset
                fragment = len(ct) < 0.6 * len(ref_tok[j]) and ct <= ref_tok[j] and (ref[j].end - ref[j].start) > 1.6 * (c.end - c.start)
                out.append(Match(i, j, s, ref[j].start - c.start, usable=not fragment))
            j += 1
    return out


def best_chain(cands: list[Match], n_ref: int) -> list[Match]:
    """Maximum-weight chain with strictly increasing cue index and non-decreasing reference index."""
    if not cands:
        return []
    cands.sort(key=lambda m: (m.cue, m.ref))
    tree = _PrefixMax(n_ref)
    score = [0.0] * len(cands)
    prev = [-1] * len(cands)
    k = 0
    while k < len(cands):
        # all candidates of the same cue are scored against the tree BEFORE any of them is inserted (cue strictly increases)
        k2 = k
        while k2 < len(cands) and cands[k2].cue == cands[k].cue:
            best, idx = tree.query(cands[k2].ref)
            score[k2] = cands[k2].sim + max(best, 0.0)
            prev[k2] = idx
            k2 += 1
        for t in range(k, k2):
            tree.update(cands[t].ref, (score[t], t))
        k = k2
    end = max(range(len(cands)), key=lambda t: score[t])
    chain: list[Match] = []
    while end != -1:
        chain.append(cands[end])
        end = prev[end]
    chain.reverse()
    return chain


@dataclass
class OffsetModel:
    """Piecewise-linear offset: knots (cue time, offset) joined by straight lines; the first/last window's slope is
    used to extrapolate before/after them (so a drift keeps being corrected at both ends)."""

    knots: list[tuple[float, float]] = field(default_factory=list)
    slope_first: float = 0.0
    slope_last: float = 0.0

    def offset_at(self, t: float) -> float:
        k = self.knots
        if not k:
            return 0.0
        if t <= k[0][0]:
            return k[0][1] + self.slope_first * (t - k[0][0])
        if t >= k[-1][0]:
            return k[-1][1] + self.slope_last * (t - k[-1][0])
        for (t0, d0), (t1, d1) in zip(k, k[1:], strict=False):
            if t0 <= t <= t1:
                return d1 if t1 - t0 < 1e-6 else d0 + (d1 - d0) * (t - t0) / (t1 - t0)
        return k[-1][1]


def theil_sen(points: list[tuple[float, float]], max_pairs: int = 600) -> tuple[float, float]:
    """Robust line d = a + b*t through (t, d) points: median of pairwise slopes, median intercept."""
    n = len(points)
    if n < 2:
        return (points[0][1] if points else 0.0), 0.0
    slopes = []
    step = max(1, (n * (n - 1) // 2) // max_pairs)
    k = 0
    for i in range(n):
        for j in range(i + 1, n):
            k += 1
            if k % step:
                continue
            dt = points[j][0] - points[i][0]
            if abs(dt) > 1e-3:
                slopes.append((points[j][1] - points[i][1]) / dt)
    b = statistics.median(slopes) if slopes else 0.0
    b = max(-0.2, min(0.2, b))   # more than 20 % drift is not a frame-rate problem: treat as a step
    a = statistics.median(d - b * t for t, d in points)
    return a, b


def build_model(matches: list[Match], cues: list[Segment], min_per_window: int = 5, window_seconds: float = 300.0,
                max_windows: int = 40, step_threshold: float = 1.5) -> OffsetModel:
    usable = [m for m in matches if m.usable]
    if len(usable) < 3:
        usable = list(matches)
    if not usable:
        return OffsetModel()
    usable.sort(key=lambda m: cues[m.cue].start)
    pts = [(cues[m.cue].start, m.delta) for m in usable]
    # split into windows by time, and additionally wherever the offset jumps (a cut): consecutive deltas differ a lot
    groups: list[list[tuple[float, float]]] = [[pts[0]]]
    for prev, cur in zip(pts, pts[1:], strict=False):
        jump = abs(cur[1] - prev[1]) > step_threshold
        if jump or cur[0] - groups[-1][0][0] > window_seconds:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    # tiny groups (mis-matches) are dropped when there is enough good material around them
    big = [g for g in groups if len(g) >= 2]
    if big and sum(len(g) for g in big) >= 0.6 * len(pts):
        groups = big
    while len(groups) > max_windows:
        # merge the two shortest neighbours
        i = min(range(len(groups) - 1), key=lambda k: len(groups[k]) + len(groups[k + 1]))
        groups[i:i + 2] = [groups[i] + groups[i + 1]]
    model = OffsetModel()
    slopes: list[float] = []
    for g in groups:
        if len(g) >= 3:
            a, b = theil_sen(g)
            t0, t1 = g[0][0], g[-1][0]
            if t1 - t0 < 1e-3:
                model.knots.append((t0, statistics.median(d for _, d in g)))
                slopes.append(0.0)
            else:
                model.knots.append((t0, a + b * t0))
                model.knots.append((t1, a + b * t1))
                slopes.append(b)
        else:
            model.knots.append((statistics.median(t for t, _ in g), statistics.median(d for _, d in g)))
            slopes.append(0.0)
    model.knots.sort()
    if slopes:
        model.slope_first, model.slope_last = slopes[0], slopes[-1]
    return model


def offset_at(model: OffsetModel | list[tuple[float, float]], t: float) -> float:
    if isinstance(model, list):
        model = OffsetModel(model)
    return model.offset_at(t)


def apply_offsets(cues: list[Segment], model: OffsetModel | list[tuple[float, float]]) -> list[Segment]:
    if isinstance(model, list):
        model = OffsetModel(model)
    out = []
    for c in cues:
        d = model.offset_at(c.start)
        start = max(0.0, c.start + d)
        out.append(Segment(start, max(start + 0.2, c.end + d), c.text))
    out.sort(key=lambda s: (s.start, s.end))
    for i in range(1, len(out)):
        if out[i].start < out[i - 1].end:
            out[i - 1].end = max(out[i - 1].start + 0.1, out[i].start - 0.01)
    return out


def resync(cues: list[Segment], ref: list[Segment], band: float = BAND_SECONDS) -> ResyncResult:
    cands = candidates(cues, ref, band)
    chain = best_chain(cands, len(ref))
    model = build_model(chain, cues)
    knots = model.knots
    shifted = apply_offsets(cues, model) if knots else list(cues)
    deltas = [m.delta for m in chain if m.usable] or [m.delta for m in chain]
    drift = 0.0
    if len(knots) >= 2 and knots[-1][0] - knots[0][0] > 1.0:
        drift = (knots[-1][1] - knots[0][1]) / (knots[-1][0] - knots[0][0])
    stats = {
        "cues": len(cues), "reference": len(ref), "candidates": len(cands), "matched": len(chain),
        "coverage": round(len(chain) / len(cues), 3) if cues else 0.0,
        "mean_similarity": round(sum(m.sim for m in chain) / len(chain), 3) if chain else 0.0,
        "offset_median": round(statistics.median(deltas), 3) if deltas else 0.0,
        "offset_first": round(model.offset_at(cues[0].start), 3) if knots and cues else 0.0,
        "offset_last": round(model.offset_at(cues[-1].start), 3) if knots and cues else 0.0,
        "drift_ppm": round(drift * 1e6, 1),
        "knots": [(round(t, 2), round(d, 3)) for t, d in knots],
        "ok": bool(knots) and len(chain) >= min(3, len(cues), len(ref)) and len(chain) / max(1, len(cues)) >= 0.15,
    }
    return ResyncResult(shifted, chain, knots, stats)
