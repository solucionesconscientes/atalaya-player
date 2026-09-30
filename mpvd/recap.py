"""``recap.*``: «¿Qué me he perdido?» (H27, ADR-062). A few sentences that sum up a stretch of the video, picked from its
own dialogue (extractive: nothing is invented, every line carries its time so the user can jump there).

Where the words come from, in this order: cues passed by the caller, a subtitle file (SRT/VTT/ASS), an embedded text
track (extracted once with ``subs.extract``'s ffmpeg argv into the cache), or the AI transcription mpvd already has
for the file. No new transcription is started here: that would make the answer wait minutes on a 4-core laptop.

How sentences are chosen: when the multilingual embedding model of ``semantic`` is installed, centrality (similarity
to the centroid of the stretch) with MMR so that the picks do not repeat each other; otherwise a word-frequency score
(content words that recur in the stretch) with the same MMR over word overlap. Both run in well under a second for
an hour of dialogue. A local LLM could write a freer summary, but on the target hardware (no GPU) it takes tens of
seconds per paragraph and may invent things: left out on purpose."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, RpcError
from mpvd.semantic.index import Sentence, sentences_from_segments

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.recap")

MMR_LAMBDA = 0.7
MIN_WORDS = 12                  # below this there is nothing to summarise: every line is returned
WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
# short function words of Spanish and English that pass the 4-letter filter
STOP = set("""
ante bajo hacia tras según segun sino cuyo cuya aquel aquella aquello aqui aquí algo algun alguna alguno algunos antes aunque bien cada casi como cómo con contra
cual cuál cuando cuándo desde donde dónde durante ella ellas ellos entonces entre esta está estaba estado estamos
estan están estar este esto estos estas eres había habia hace hacer hasta hemos luego mismo mucho muchos mucha
nada nadie nosotros nuestra nuestro otra otro otros para pero poco porque puede pues quien quién sido siempre sobre
solo sólo somos también tambien tanto tener tengo tiene tienen todo todos toda todas tuve usted ustedes vamos vaya
voy yo about after again also been before being could does doing down from have having here into just like more
most much must only other over really said same should some such than that their them then there these they this
those through very were what when where which while will with would your yours yeah okay
""".split())


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


STOP_FOLDED = {_fold(w) for w in STOP}


def content_words(text: str) -> list[str]:
    return [w for w in (_fold(m) for m in WORD_RE.findall(text)) if w not in STOP_FOLDED]


def window(cues: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [c for c in cues if float(c["end"]) > start and float(c["start"]) < end and str(c.get("text", "")).strip()]


def pick_count(seconds: float) -> int:
    """About one sentence per two minutes: 3 for a short absence, never more than 7."""
    return max(3, min(7, round(seconds / 120) + 2))


def _mmr(scores: list[float], sim: Any, k: int) -> list[int]:
    chosen: list[int] = []
    left = set(range(len(scores)))
    while left and len(chosen) < k:
        def value(i: int) -> float:
            red = max((sim(i, j) for j in chosen), default=0.0)
            return MMR_LAMBDA * scores[i] - (1 - MMR_LAMBDA) * red
        best = max(sorted(left), key=value)
        chosen.append(best)
        left.discard(best)
    return sorted(chosen)


def select_by_words(sentences: list[Sentence], k: int) -> list[int]:
    bags = [content_words(s.text) for s in sentences]
    freq: dict[str, int] = {}
    for bag in bags:
        for w in set(bag):
            freq[w] = freq.get(w, 0) + 1
    scores = []
    for bag in bags:
        if not bag:
            scores.append(0.0)
            continue
        # recurring words weigh more; long sentences are not favoured just for being long
        scores.append(sum(freq[w] - 1 for w in bag) / math.sqrt(len(bag)))
    top = max(scores) or 1.0
    scores = [s / top for s in scores]
    sets = [set(b) for b in bags]

    def sim(i: int, j: int) -> float:
        a, b = sets[i], sets[j]
        return len(a & b) / len(a | b) if a and b else 0.0
    return _mmr(scores, sim, k)


def select_by_vectors(vectors: Any, k: int) -> list[int]:
    import numpy as np  # noqa: PLC0415

    v = np.asarray(vectors, dtype=np.float32)
    centroid = v.mean(axis=0)
    n = float(np.linalg.norm(centroid)) or 1.0
    scores = [float(x) for x in (v @ (centroid / n))]
    lo, hi = min(scores), max(scores)
    scores = [(s - lo) / (hi - lo) if hi > lo else 1.0 for s in scores]
    gram = v @ v.T
    return _mmr(scores, lambda i, j: float(gram[i, j]), k)


def summarize(cues: list[dict[str, Any]], start: float, end: float, embed: Any = None) -> dict[str, Any]:
    part = window(cues, start, end)
    sentences = sentences_from_segments(part)
    words = sum(len(s.text.split()) for s in sentences)
    k = pick_count(end - start)
    if words < MIN_WORDS or len(sentences) <= k:
        idx, method = list(range(len(sentences))), "all"
    elif embed is not None:
        idx, method = select_by_vectors(embed([s.text for s in sentences]), k), "embeddings"
    else:
        idx, method = select_by_words(sentences, k), "words"
    return {"start": start, "end": end, "method": method, "cues": len(part), "words": words,
            "sentences": [{"start": round(sentences[i].start, 2), "end": round(sentences[i].end, 2),
                           "text": sentences[i].text} for i in idx]}


class RecapService:
    def __init__(self, server: MpvdServer):
        self.server = server
        server.services["recap"] = True

    async def _embed(self) -> Any:
        sem = getattr(self.server, "semantic", None)
        if sem is None or not sem.available():
            return None
        try:
            embedder = await sem.embedder()
        except RpcError:
            return None
        return embedder.encode

    async def _cues(self, params: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        from mpvd.subs.formats import SubtitleError, load_cues  # noqa: PLC0415

        if isinstance(params.get("cues"), list):
            return [dict(c) for c in params["cues"]], "cues"
        sub = params.get("sub_path")
        if sub:
            try:
                segs = await asyncio.to_thread(load_cues, sub)
            except SubtitleError as exc:
                raise RpcError(NOT_FOUND, f"no se pueden leer los subtítulos: {exc}") from exc
            return [{"start": s.start, "end": s.end, "text": s.text} for s in segs], "subtitles"
        path = params.get("path")
        if not path:
            raise RpcError(INVALID_PARAMS, "hace falta cues, sub_path o path")
        ff_index = params.get("ff_index")
        if ff_index is not None and Path(path).is_file():
            srt = await self._extract(str(path), int(ff_index))
            segs = await asyncio.to_thread(load_cues, srt)
            return [{"start": s.start, "end": s.end, "text": s.text} for s in segs], "subtitles"
        sem = getattr(self.server, "semantic", None)
        got = sem._transcript(Path(path)) if sem is not None else None
        if got is None:
            raise RpcError(NOT_FOUND, "no hay subtítulos ni transcripción de este vídeo")
        return got[1], "ai"

    async def _extract(self, path: str, ff_index: int) -> Path:
        from mpvd.hashing import file_hash  # noqa: PLC0415
        from mpvd.subs.save import SaveError, extract_srt  # noqa: PLC0415

        key = (await asyncio.to_thread(file_hash, Path(path))).key
        dest = self.server.cache.blob_dir / "recap" / f"{key}.{ff_index}.srt"
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                await extract_srt(path, ff_index, dest)
            except SaveError as exc:
                raise RpcError(NOT_FOUND, f"no se pudo leer la pista de subtítulos: {exc}") from exc
        return dest

    async def summarize(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            start, end = float(params.get("start", 0.0)), float(params["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RpcError(INVALID_PARAMS, "start y end (segundos) son obligatorios") from exc
        if end <= start:
            raise RpcError(INVALID_PARAMS, "end debe ser mayor que start")
        cues, source = await self._cues(params)
        embed = None if params.get("method") == "words" else await self._embed()
        out = await asyncio.to_thread(summarize, cues, start, end, embed)
        out["source"] = source
        return out


def register(server: MpvdServer, service: RecapService) -> None:
    d = server.dispatcher

    @d.method("recap.summarize")
    async def recap_summarize(ctx: RpcContext, end: float, start: float = 0.0, cues: list | None = None,
                              sub_path: str = "", path: str = "", ff_index: int | None = None,
                              method: str = "") -> dict[str, Any]:
        """«¿Qué me he perdido?»: the sentences that sum up [start, end] of the dialogue, with their times.
        Source: ``cues`` | ``sub_path`` | ``path`` + ``ff_index`` (embedded text track) | ``path`` (AI transcript).
        ``method="words"`` skips the embedding model."""
        return await service.summarize({"start": start, "end": end, "cues": cues, "sub_path": sub_path,
                                        "path": path, "ff_index": ff_index, "method": method})
