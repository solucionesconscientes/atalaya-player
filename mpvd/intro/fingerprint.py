"""Chromaprint fingerprints (``fpcalc -raw -json``, verified with fpcalc 1.6.0) and the search for a common audio run
between two files (the intro or the credits of a series).

A raw fingerprint is a list of 32-bit sub-fingerprints, ~8 per second. Two positions match when their Hamming
distance is small. Candidate alignments come from exact matches on the high bits (a vote per diagonal); each strong
diagonal is then walked to find the longest run of near-identical sub-fingerprints, tolerating a few misses.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from mpvd.asr.audio import extract_wav

MASK_HIGH = 0xFFFFF000       # exact-match key: top 20 bits (the low bits flip with encoding noise)
MAX_BITS = 6                 # Hamming distance considered "the same sound" (random audio differs in ~16 of 32)
MAX_GAP = 2                  # consecutive misses tolerated inside a run


class FingerprintError(RuntimeError):
    pass


@dataclass
class Fingerprint:
    values: list[int]
    duration: float          # seconds of audio fingerprinted
    offset: float            # media time of values[0]

    @property
    def rate(self) -> float:
        return len(self.values) / self.duration if self.duration > 0 else 8.0

    def time_at(self, index: int) -> float:
        return self.offset + index / self.rate

    def to_dict(self) -> dict:
        return {"values": self.values, "duration": self.duration, "offset": self.offset}

    @classmethod
    def from_dict(cls, d: dict) -> Fingerprint:
        return cls([int(v) for v in d["values"]], float(d["duration"]), float(d.get("offset", 0.0)))


@dataclass
class Run:
    a_start: float           # media time in A
    a_end: float
    b_start: float           # media time in B
    b_end: float
    score: float             # matched positions / run length

    @property
    def length(self) -> float:
        return self.a_end - self.a_start

    def to_dict(self) -> dict:
        return {"a": [round(self.a_start, 2), round(self.a_end, 2)], "b": [round(self.b_start, 2), round(self.b_end, 2)],
                "length": round(self.length, 2), "score": round(self.score, 3)}


def fpcalc_path() -> str:
    p = shutil.which("fpcalc")
    if not p:
        raise FingerprintError("fpcalc (chromaprint) not found in PATH")
    return p


async def fingerprint_window(src: str, start: float, length: float, tmp_dir: Path, audio_track: int | None = None,
                             timeout: float = 120.0) -> Fingerprint:
    """Fingerprint ``length`` seconds of ``src`` from ``start`` (audio extracted with ffmpeg first: any container)."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav = tmp_dir / f"fp-{abs(hash((src, start, length))) % 10**9}.wav"
    try:
        await extract_wav(src, start, length, wav, audio_track)
        proc = await asyncio.create_subprocess_exec(fpcalc_path(), "-raw", "-json", "-length", str(int(length) + 1),
                                                    str(wav), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise FingerprintError("fpcalc timed out") from None
        if proc.returncode != 0:
            raise FingerprintError(f"fpcalc failed: {err.decode('utf-8', 'replace').strip()[-200:]}")
        try:
            data = json.loads(out.decode("utf-8"))
        except ValueError as exc:
            raise FingerprintError(f"bad fpcalc output: {exc}") from exc
        values = [int(v) & 0xFFFFFFFF for v in data.get("fingerprint") or []]
        if len(values) < 8:
            raise FingerprintError("fingerprint too short (silence or no audio?)")
        return Fingerprint(values, float(data.get("duration") or length), start)
    finally:
        wav.unlink(missing_ok=True)


def popcount(x: int) -> int:
    return bin(x).count("1")


def flatness(values: list[int], start: int, end: int) -> float:
    """Share of consecutive sub-fingerprints that barely change (steady tones, silence): near 1.0 means no information."""
    n = end - start
    if n < 2:
        return 1.0
    same = sum(1 for i in range(start + 1, end) if popcount(values[i] ^ values[i - 1]) <= 2)
    return same / (n - 1)


def runs_on_diagonal(a: Fingerprint, b: Fingerprint, off: int, min_len: int, max_bits: int, max_gap: int) -> list[Run]:
    """Every run (not just the longest) of near-identical sub-fingerprints along j = i + off."""
    i0 = max(0, -off)
    i1 = min(len(a.values), len(b.values) - off)
    out: list[Run] = []
    start = None
    hits = 0
    gap = 0
    end = i0

    def flat_at(i: int) -> bool:
        return i > 0 and popcount(a.values[i] ^ a.values[i - 1]) <= 2

    def flush() -> None:
        nonlocal start
        if start is not None:
            # trim silence / steady-tone edges (identical in every episode, but not part of the intro)
            s0, e0 = start, end
            while s0 < e0 - 1 and flat_at(s0 + 1):
                s0 += 1
            while e0 > s0 + 1 and flat_at(e0 - 1):
                e0 -= 1
            if e0 - s0 >= min_len and hits / (end - start) >= 0.6 and flatness(a.values, s0, e0) < 0.8:
                out.append(Run(a.time_at(s0), a.time_at(e0), b.time_at(s0 + off), b.time_at(e0 + off), hits / (end - start)))
        start = None

    for i in range(i0, i1):
        if popcount(a.values[i] ^ b.values[i + off]) <= max_bits:
            if start is None:
                start, hits, gap = i, 0, 0
            hits += 1
            gap = 0
            end = i + 1
        elif start is not None:
            gap += 1
            if gap > max_gap:
                flush()
    flush()
    return out


def match(a: Fingerprint, b: Fingerprint, min_seconds: float = 6.0, max_bits: int = MAX_BITS, max_gap: int = MAX_GAP,
          top_offsets: int = 8) -> list[Run]:
    """Runs of matching audio between A and B (different files or different windows of the same series), longest first."""
    if not a.values or not b.values:
        return []
    index: dict[int, list[int]] = {}
    for j, v in enumerate(b.values):
        index.setdefault(v & MASK_HIGH, []).append(j)
    votes: Counter[int] = Counter()
    for i, v in enumerate(a.values):
        for j in index.get(v & MASK_HIGH, ()):
            votes[j - i] += 1
    if not votes:
        return []
    min_len = max(4, int(min_seconds * a.rate))
    runs: list[Run] = []
    seen_offsets: set[int] = set()
    for off, n in votes.most_common(top_offsets * 3):
        if n < 3 or any(abs(off - o) <= 1 for o in seen_offsets):
            continue
        seen_offsets.add(off)
        if len(seen_offsets) > top_offsets:
            break
        runs.extend(runs_on_diagonal(a, b, off, min_len, max_bits, max_gap))
    # drop runs contained in a longer one (from a neighbouring diagonal)
    runs.sort(key=lambda r: -r.length)
    kept: list[Run] = []
    for r in runs:
        if not any(k.a_start - 0.5 <= r.a_start and r.a_end <= k.a_end + 0.5 for k in kept):
            kept.append(r)
    return kept
