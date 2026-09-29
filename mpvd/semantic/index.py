"""Sentence index over a transcript, cosine search and topic-change chapters (pure NumPy; the embedder is injected).

Sentences: Whisper cues merged until ~25 words or a final punctuation mark (a cue gap > 2 s always splits). Vectors: one
L2-normalised row per sentence. Chapters: overlapping 45 s windows (mean of their sentence vectors), ``d = 1 - cos`` between
consecutive windows, 3-point moving average, local maxima above a percentile, greedy minimum chapter length, boundaries snapped
to the nearest sentence start; the title is the sentence closest to the chapter centroid (a quote, never invented).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

FINAL_PUNCT = re.compile(r"[.!?…]+[\"'”»)]*$")
GAP_SPLIT = 2.0


@dataclass
class Sentence:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def sentences_from_segments(segments: list[dict[str, Any]], max_words: int = 25, min_words: int = 4) -> list[Sentence]:
    """Merge cues into sentence-sized units keeping start/end times."""
    out: list[Sentence] = []
    cur: Sentence | None = None
    words = 0
    for seg in segments:
        text = " ".join(str(seg.get("text", "")).split())
        if not text:
            continue
        s, e = float(seg["start"]), float(seg["end"])
        n = len(text.split())
        if cur is not None and (s - cur.end > GAP_SPLIT or words + n > max_words):
            out.append(cur)
            cur = None
        if cur is None:
            cur, words = Sentence(s, e, text), n
        else:
            cur.text += " " + text
            cur.end = max(cur.end, e)
            words += n
        if words >= min_words and FINAL_PUNCT.search(cur.text):
            out.append(cur)
            cur = None
            words = 0
    if cur is not None:
        out.append(cur)
    return out


def vectors_to_bytes(vectors: Any) -> bytes:
    import numpy as np  # noqa: PLC0415

    return np.ascontiguousarray(vectors, dtype=np.float32).tobytes()


def vectors_from_bytes(blob: bytes, dim: int) -> Any:
    import numpy as np  # noqa: PLC0415

    arr = np.frombuffer(blob, dtype=np.float32)
    return arr.reshape(-1, dim) if dim > 0 else arr.reshape(0, 0)


def search(query_vec: Any, vectors: Any, sentences: list[Sentence], k: int = 10, text_query: str = "",
           text_bonus: float = 0.15) -> list[dict[str, Any]]:
    """Top-k sentences by cosine (+ a bonus when the folded query text literally appears)."""
    import numpy as np  # noqa: PLC0415

    if len(sentences) == 0 or vectors.shape[0] == 0:
        return []
    scores = vectors @ np.asarray(query_vec, dtype=np.float32).reshape(-1)
    needle = fold(text_query).strip()
    if needle:
        for i, s in enumerate(sentences):
            if needle in fold(s.text):
                scores[i] += text_bonus
    k = max(1, min(k, len(sentences)))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [{"start": sentences[i].start, "end": sentences[i].end, "text": sentences[i].text, "score": round(float(scores[i]), 4)}
            for i in top]


def _windows(vectors: Any, sentences: list[Sentence], window: float, step: float) -> tuple[list[float], Any]:
    """(start times, mean vector per window) over the transcript span; empty windows are skipped."""
    import numpy as np  # noqa: PLC0415

    if not sentences:
        return [], np.zeros((0, vectors.shape[1] if vectors.ndim == 2 else 0), dtype=np.float32)
    mids = np.array([(s.start + s.end) / 2 for s in sentences])
    t0, t1 = sentences[0].start, sentences[-1].end
    starts: list[float] = []
    rows = []
    t = t0
    while t < t1 - step / 2:
        sel = (mids >= t) & (mids < t + window)
        if sel.any():
            v = vectors[sel].mean(axis=0)
            n = float(np.linalg.norm(v))
            if n > 1e-9:
                starts.append(t)
                rows.append(v / n)
        t += step
    return starts, (np.vstack(rows) if rows else np.zeros((0, vectors.shape[1]), dtype=np.float32))


def chapters(vectors: Any, sentences: list[Sentence], duration: float | None = None, window: float = 45.0,
             overlap: float = 0.5, smooth: int = 3, percentile: float = 85.0, min_seconds: float = 180.0,
             title_chars: int = 60) -> dict[str, Any]:
    """Topic-change chapters: ``{"chapters": [{start, end, title}], "cuts": [...], "windows": n, "signal": [...]}``.

    An empty ``chapters`` list means the transcript looks homogeneous (or is too short): that is a valid answer."""
    import numpy as np  # noqa: PLC0415

    end_time = float(duration) if duration else (sentences[-1].end if sentences else 0.0)
    result: dict[str, Any] = {"chapters": [], "cuts": [], "windows": 0, "signal": []}
    if len(sentences) < 4 or vectors.shape[0] != len(sentences):
        return result
    step = max(5.0, window * (1.0 - overlap))
    starts, w = _windows(vectors, sentences, window, step)
    result["windows"] = len(starts)
    if len(starts) < 4:
        return result
    d = 1.0 - np.einsum("ij,ij->i", w[:-1], w[1:])              # distance between consecutive windows
    if smooth > 1 and len(d) >= smooth:
        kern = np.ones(smooth) / smooth
        d = np.convolve(d, kern, mode="same")
    result["signal"] = [round(float(x), 4) for x in d]
    thr = float(np.percentile(d, percentile))
    cands = [i for i in range(len(d)) if d[i] >= thr and d[i] > 0
             and (i == 0 or d[i] >= d[i - 1]) and (i == len(d) - 1 or d[i] >= d[i + 1])]
    # boundary between window i and i+1 = start of window i+1 (+ half the overlap), snapped to a sentence start
    sent_starts = np.array([s.start for s in sentences])

    def snap(t: float) -> float:
        return float(sent_starts[int(np.argmin(np.abs(sent_starts - t)))])

    cands.sort(key=lambda i: -d[i])                              # strongest first, then greedy by distance
    accepted: list[float] = []
    first = sentences[0].start
    for i in cands:
        t = snap(starts[i + 1] + (window - step) / 2 if i + 1 < len(starts) else starts[i] + window)
        if t - first < min_seconds or end_time - t < min_seconds:
            continue
        if all(abs(t - a) >= min_seconds for a in accepted):
            accepted.append(t)
    accepted.sort()
    result["cuts"] = [round(t, 2) for t in accepted]
    if not accepted:
        return result
    bounds = [first, *accepted, end_time]
    chaps = []
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        idx = [i for i, s in enumerate(sentences) if a <= s.start < b]
        title = ""
        if idx:
            c = vectors[idx].mean(axis=0)
            n = float(np.linalg.norm(c))
            if n > 1e-9:
                sims = vectors[idx] @ (c / n)
                best = sentences[idx[int(np.argmax(sims))]].text
            else:
                best = sentences[idx[0]].text
            title = _shorten(best, title_chars)
            if len(title) < 12:
                title = _shorten(sentences[idx[0]].text, title_chars)
        chaps.append({"start": round(a, 2), "end": round(b, 2), "title": title or f"Capítulo {len(chaps) + 1}"})
    result["chapters"] = chaps
    return result


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def ffmetadata(chaps: list[dict[str, Any]]) -> str:
    """FFmpeg chapter metadata (``ffmpeg -i video -i chapters.txt -map_metadata 1 …``)."""
    lines = [";FFMETADATA1"]
    for c in chaps:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(c['start'] * 1000)}", f"END={int(c['end'] * 1000)}",
                  "title=" + str(c.get("title", "")).replace("\n", " ")]
    return "\n".join(lines) + "\n"
