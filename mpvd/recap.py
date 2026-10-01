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
import hashlib
import json
import logging
import math
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd import llm as llm_mod
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.semantic.index import Sentence, sentences_from_segments

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.recap")

MMR_LAMBDA = 0.7
PROSE_ARTIFACT = "recap-prose"
PROSE_VERSION = "1"
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


# -- H38 · el índice del vídeo: secciones por significado y frases clave con su minuto ---------------

# Un modelo puede escribir «[2:15]», «[1:02:15]» o un rango «[2:15-3:40]». El rango se queda en su primer minuto:
# el menú solo puede llevarte a un sitio, y ese es el bueno. (Lo vimos en el banco de pruebas: qwen2.5-1.5b los usa.)
MARK_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*[-\u2013\u2014]\s*\d{1,2}:\d{2}(?::\d{2})?)?\]")
SECTION_SECONDS = 300.0      # cuando no hay modelo de embeddings, secciones de ~5 min: honesto y sigue sirviendo
TITLE_CHARS = 60


def _title_from(text: str, limit: int = TITLE_CHARS) -> str:
    """Una frase convertida en título: sin comillas ni guion de diálogo y cortada por una palabra entera."""
    t = " ".join(str(text or "").split()).lstrip("-–—¡¿ ").strip('"\u201c\u201d')
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0]
    return (cut or t[:limit]).rstrip(",;:.") + "…"


def time_sections(sentences: list[Sentence], seconds: float, duration: float | None = None) -> list[tuple[float, float]]:
    """Cortes cada ``seconds`` cuando no se puede medir el significado. Nunca deja una sección de menos de la mitad."""
    if not sentences:
        return []
    start = sentences[0].start
    end = float(duration) if duration else sentences[-1].end
    if end - start <= seconds * 1.5:
        return [(start, end)]
    bounds = []
    t = start
    while t < end:
        bounds.append(t)
        t += seconds
    if end - bounds[-1] < seconds / 2 and len(bounds) > 1:
        bounds.pop()
    return list(zip(bounds, [*bounds[1:], end], strict=True))


def outline(cues: list[dict[str, Any]], duration: float | None = None, embed: Any = None,
            per_section: int = 2) -> dict[str, Any]:
    """Índice del vídeo entero: secciones con su título y, dentro, las frases clave **con su minuto exacto**.

    Nada de esto lo escribe un modelo: los títulos y las frases son del propio diálogo, así que los minutos son los de
    verdad y no hay nada que validar. Instantáneo (bastante por debajo de un segundo para una hora de diálogo).
    """
    sentences = sentences_from_segments([c for c in cues if str(c.get("text", "")).strip()])
    if not sentences:
        return {"sections": [], "method": "empty", "sentences": 0, "duration": duration or 0.0}
    vectors = None
    method = "words"
    if embed is not None and len(sentences) >= 4:
        try:
            import numpy as np  # noqa: PLC0415

            vectors = np.asarray(embed([s.text for s in sentences]), dtype=np.float32)
            method = "embeddings"
        except Exception:  # noqa: BLE001 - sin modelo o sin memoria: se sigue con palabras
            vectors = None
    bounds: list[tuple[float, float]] = []
    if vectors is not None:
        from mpvd.semantic.index import chapters as topic_chapters  # noqa: PLC0415

        res = topic_chapters(vectors, sentences, duration, 45.0, 0.5, 3, 85.0, max(60.0, SECTION_SECONDS / 2))
        chaps = res.get("chapters") or []
        bounds = [(float(c["start"]), float(c["end"])) for c in chaps]
    if not bounds:
        bounds = time_sections(sentences, SECTION_SECONDS, duration)
        if method == "embeddings":
            method = "embeddings+tiempo"      # había modelo, pero el vídeo no cambia de tema: se corta por tiempo
    sections = []
    for a, b in bounds:
        idx = [i for i, sen in enumerate(sentences) if a <= sen.start < b]
        if not idx:
            continue
        k = min(per_section, len(idx))
        if vectors is not None:
            local = select_by_vectors(vectors[idx], k)
        else:
            local = select_by_words([sentences[i] for i in idx], k)
        picked = [idx[i] for i in local]
        sections.append({
            "start": round(sentences[idx[0]].start, 2), "end": round(b, 2),
            "title": _title_from(sentences[picked[0]].text),
            "points": [{"start": round(sentences[i].start, 2), "text": sentences[i].text} for i in sorted(picked)],
        })
    return {"sections": sections, "method": method, "sentences": len(sentences),
            "duration": round(float(duration), 2) if duration else round(sentences[-1].end, 2)}


def _mark_text(seconds: float) -> str:
    """Una marca normalizada: siempre un solo instante, con horas solo si hace falta."""
    n = max(0, int(seconds))
    if n >= 3600:
        return f"[{n // 3600:d}:{n % 3600 // 60:02d}:{n % 60:02d}]"
    return f"[{n // 60:d}:{n % 60:02d}]"


def validate_marks(text: str, cues: list[dict[str, Any]], tolerance: float = 30.0) -> dict[str, Any]:
    """H38/G4 · cada ``[mm:ss]`` de un texto escrito por un modelo se comprueba contra el subtítulo.

    Si no hay diálogo en ese minuto (±``tolerance``), la marca se mueve al comienzo de la frase más cercana que sí
    exista; si no hay ninguna lo bastante cerca, **se quita**. Una marca que lleva a un sitio donde no pasa nada es
    peor que no tener marca: parece que el programa te está mintiendo.
    """
    starts = sorted(float(c["start"]) for c in cues if str(c.get("text", "")).strip())
    out: list[dict[str, Any]] = []

    def fix(m: re.Match[str]) -> str:
        h, mi, se = m.group(1), m.group(2), m.group(3)
        seconds = (int(h) * 3600 + int(mi) * 60 + int(se)) if se else (int(h) * 60 + int(mi))
        if not starts:
            out.append({"mark": m.group(0), "seconds": seconds, "action": "removed", "to": None})
            return ""
        nearest = min(starts, key=lambda t: abs(t - seconds))
        if abs(nearest - seconds) <= 1.0:
            out.append({"mark": m.group(0), "seconds": seconds, "action": "kept", "to": round(nearest, 2)})
            return _mark_text(seconds)
        if abs(nearest - seconds) <= tolerance:
            out.append({"mark": m.group(0), "seconds": seconds, "action": "moved", "to": round(nearest, 2)})
            return _mark_text(nearest)
        out.append({"mark": m.group(0), "seconds": seconds, "action": "removed", "to": None})
        return ""

    fixed_text = MARK_RE.sub(fix, text or "")
    fixed_text = re.sub(r"[ \t]{2,}", " ", fixed_text)
    fixed_text = re.sub(r" +([,.;:])", r"\1", fixed_text)
    return {"text": fixed_text.strip(), "marks": out,
            "kept": sum(1 for m in out if m["action"] == "kept"),
            "moved": sum(1 for m in out if m["action"] == "moved"),
            "removed": sum(1 for m in out if m["action"] == "removed")}


def split_marks(text: str) -> list[dict[str, Any]]:
    """El resumen en prosa, partido en filas: cada frase con el segundo al que lleva (H38/G5).

    Si el texto empieza sin marca, ese trozo se queda como entradilla sin minuto (``start`` nulo): no se le inventa uno.
    """
    rows: list[dict[str, Any]] = []
    pos = 0
    pending: str | None = None
    for m in MARK_RE.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            rows.append({"start": pending, "text": chunk}) if pending is not None else rows.append(
                {"start": None, "text": chunk})
        h, mi, se = m.group(1), m.group(2), m.group(3)
        pending = (int(h) * 3600 + int(mi) * 60 + int(se)) if se else (int(h) * 60 + int(mi))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        rows.append({"start": pending, "text": tail})
    return [r for r in rows if r["text"]]


class RecapService:
    def __init__(self, server: MpvdServer):
        self.server = server
        server.services["recap"] = True
        # H38/G3: el modelo local para la prosa. Si no está, todo lo demás (el índice) sigue funcionando igual.
        self.llm = llm_mod.LlmEngine(llm_mod.LlmStore(llm_mod.model_dirs(server.root, server.settings.data_dir)),
                                     llm_mod.find_binary(server.root))

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

    # -- H38/G3 · nivel 2: prosa escrita por un modelo local, con los minutos del nivel 1 ----------

    def llm_status(self) -> dict[str, Any]:
        st = self.llm.status()
        st["languages"] = sorted(llm_mod.SYSTEM)
        st["lengths"] = ["short", "long"]
        return st

    def download_model(self, name: str, notify: str, session_id: str | None) -> Any:
        from mpvd.jobs import Priority  # noqa: PLC0415

        if name not in llm_mod.CATALOG:
            raise RpcError(INVALID_PARAMS, f"modelo desconocido {name!r}")

        async def body(job: Any) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def progress(frac: float, message: str) -> None:
                def report() -> None:
                    job.report(frac, message)
                    self._push(session_id, notify, "prose-model:" + name, f"running:{frac:.2f}",
                               {"event": "recap-model", "model": name, "job": job.to_dict()})
                loop.call_soon_threadsafe(report)

            path = await self.llm.store.download(name, progress)
            self._push(session_id, notify, "prose-model:" + name, "done",
                       {"event": "recap-model", "model": name, "path": str(path),
                        "job": {**job.to_dict(), "status": "done"}}, final=True)
            return {"model": name, "path": str(path)}

        return self.server.jobs.submit(f"recap.model.{name}", body, priority=Priority.INTERACTIVE, heavy=False,
                                      session_id=None, meta={"notify": notify, "model": name})

    def _push(self, session_id: str | None, target: str, key: str, status: str, payload: dict[str, Any],
              final: bool = False) -> None:
        if not session_id:
            return
        session = self.server.sessions.get(session_id)
        if session is not None and session.connected:
            session.push_event(target, key, status, payload, min_interval=0.5, final=final)

    async def prose(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resumen en prosa del vídeo entero, corto o largo, en español, inglés o francés.

        Los minutos NO los pone el modelo: el prompt lleva el índice del nivel 1 con sus marcas y, al volver, cada
        ``[mm:ss]`` se comprueba contra el subtítulo (G4). Tarda del orden de un minuto en un portátil de 4 núcleos, así
        que va como trabajo con progreso, no como una respuesta que se espera.
        """
        from mpvd.jobs import Priority  # noqa: PLC0415

        length = params.get("length") or "short"
        language = params.get("language") or "es"
        model = params.get("model") or llm_mod.DEFAULT_MODEL
        if length not in ("short", "long"):
            raise RpcError(INVALID_PARAMS, "length: short o long")
        if language not in llm_mod.SYSTEM:
            raise RpcError(INVALID_PARAMS, "language: " + ", ".join(sorted(llm_mod.SYSTEM)))
        if model not in llm_mod.CATALOG:
            raise RpcError(INVALID_PARAMS, f"modelo desconocido {model!r}")
        if not self.llm.available:
            raise RpcError(UNAVAILABLE, "falta llama-cli: ejecuta tools/vendor_llama.sh",
                           {"install": "tools/vendor_llama.sh"})
        if self.llm.store.find(model) is None:
            raise RpcError(UNAVAILABLE, f"el modelo {model} no está descargado",
                           {"download": model, "size_mb": llm_mod.CATALOG[model].size_mb})
        cues, source = await self._cues(params)
        base = await asyncio.to_thread(outline, cues, params.get("duration"), await self._embed(), 2)
        sections = base.get("sections") or []
        if not sections:
            raise RpcError(NOT_FOUND, "no hay suficiente diálogo para resumir este vídeo")
        system, prompt, tokens = llm_mod.prompt_from_outline(sections, length, language)
        cache_key = "prose:" + hashlib.sha256(
            json.dumps([sections, length, language, model], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
        cached = await asyncio.to_thread(self.server.cache.get, cache_key, PROSE_ARTIFACT, model, PROSE_VERSION, {})
        if cached is not None and isinstance(cached.data, dict) and cached.data.get("text"):
            return {"status": "done", "cached": True, "source": source, **cached.data}
        notify = params.get("notify") or "mu_recap"
        session_id = params.get("session_id")

        async def body(job: Any) -> dict[str, Any]:
            job.report(0.05, "pensando…")
            self._push(session_id, notify, "prose:" + job.id, "running:0.05",
                       {"event": "recap-prose", "job": job.to_dict()})
            gen = await self.llm.generate(model, system, prompt, tokens)
            checked = await asyncio.to_thread(validate_marks, gen["text"], cues)
            data = {"text": checked["text"], "rows": split_marks(checked["text"]),
                    "marks": {k: checked[k] for k in ("kept", "moved", "removed")},
                    "model": model, "length": length, "language": language,
                    "tokens_per_second": gen.get("tokens_per_second"), "sections": len(sections)}
            await asyncio.to_thread(self.server.cache.put, cache_key, PROSE_ARTIFACT, model=model,
                                   version=PROSE_VERSION, params={}, data=data)
            self._push(session_id, notify, "prose:" + job.id, "done",
                       {"event": "recap-prose", "job": {**job.to_dict(), "status": "done"}, "result": data},
                       final=True)
            return data

        job = self.server.jobs.submit(f"recap.prose.{length}.{language}", body, priority=Priority.INTERACTIVE,
                                      heavy=True, session_id=None,
                                      meta={"notify": notify, "model": model, "length": length, "language": language})
        return {"status": "queued", "job": job.to_dict(), "source": source, "sections": len(sections),
                "model": model, "length": length, "language": language}

    async def outline(self, params: dict[str, Any]) -> dict[str, Any]:
        """H38/G1-G2: el índice del vídeo, de las fuentes que ya existen. **Nunca** lanza una transcripción."""
        cues, source = await self._cues(params)
        duration = params.get("duration")
        try:
            duration = float(duration) if duration else None
        except (TypeError, ValueError):
            duration = None
        embed = None if params.get("method") == "words" else await self._embed()
        per_section = max(1, min(5, int(params.get("per_section") or 2)))
        out = await asyncio.to_thread(outline, cues, duration, embed, per_section)
        out["source"] = source
        out["cues"] = len(cues)
        return out

    async def marks(self, params: dict[str, Any]) -> dict[str, Any]:
        """H38/G4: comprueba los ``[mm:ss]`` de un texto contra el subtítulo y los mueve o los quita."""
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RpcError(INVALID_PARAMS, "text required")
        cues, source = await self._cues(params)
        tol = float(params.get("tolerance") or 30.0)
        out = await asyncio.to_thread(validate_marks, text, cues, tol)
        out["source"] = source
        return out

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

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    @d.method("recap.llm.status")
    async def llm_status(ctx: RpcContext) -> dict[str, Any]:
        """H38/G6: si está el binario de llama.cpp, qué modelos hay y cuál es el de serie, con su tamaño."""
        return service.llm_status()

    @d.method("recap.llm.download")
    async def llm_download(ctx: RpcContext, model: str, notify: str = "mu_recap") -> dict[str, Any]:
        """Descarga el modelo del resumen (verificado por SHA-256) en segundo plano, con progreso."""
        return service.download_model(model, notify, _sid(ctx)).to_dict()

    @d.method("recap.llm.remove")
    async def llm_remove(ctx: RpcContext, model: str) -> dict[str, Any]:
        """Borra un modelo descargado."""
        return {"removed": service.llm.store.remove(model)}

    @d.method("recap.prose")
    async def recap_prose(ctx: RpcContext, cues: list | None = None, sub_path: str = "", path: str = "",
                          ff_index: int | None = None, duration: float | None = None, length: str = "short",
                          language: str = "es", model: str | None = None,
                          notify: str = "mu_recap") -> dict[str, Any]:
        """H38/G3 · resumen en prosa del vídeo, corto o largo, en es/en/fr, escrito por el modelo local a partir del
        índice del nivel 1. Los minutos son los del subtítulo: lo que el modelo invente se quita (G4). Tarda del orden
        de un minuto, así que devuelve un trabajo con progreso (eventos ``recap-prose``)."""
        return await service.prose({"cues": cues, "sub_path": sub_path, "path": path, "ff_index": ff_index,
                                    "duration": duration, "length": length, "language": language, "model": model,
                                    "notify": notify, "session_id": _sid(ctx)})

    @d.method("recap.outline")
    async def recap_outline(ctx: RpcContext, cues: list | None = None, sub_path: str = "", path: str = "",
                            ff_index: int | None = None, duration: float | None = None, method: str = "",
                            per_section: int = 2) -> dict[str, Any]:
        """H38 · índice del vídeo: secciones por significado con su título y, dentro, las frases clave con su minuto
        exacto. Todo sale del propio diálogo (nada escrito por un modelo), así que es instantáneo y los minutos son los
        de verdad. Fuentes, en este orden: ``cues`` | ``sub_path`` | ``path`` + ``ff_index`` | la transcripción que ya
        haya de ``path``. **Nunca** se lanza una transcripción nueva para esto."""
        return await service.outline({"cues": cues, "sub_path": sub_path, "path": path, "ff_index": ff_index,
                                      "duration": duration, "method": method, "per_section": per_section})

    @d.method("recap.marks")
    async def recap_marks(ctx: RpcContext, text: str, cues: list | None = None, sub_path: str = "", path: str = "",
                          ff_index: int | None = None, tolerance: float = 30.0) -> dict[str, Any]:
        """Comprueba cada ``[mm:ss]`` de ``text`` contra el subtítulo: lo deja, lo mueve a la frase más cercana (hasta
        ``tolerance`` segundos) o lo quita. Para los resúmenes escritos por un modelo (H38/G4)."""
        return await service.marks({"text": text, "cues": cues, "sub_path": sub_path, "path": path,
                                   "ff_index": ff_index, "tolerance": tolerance})

    @d.method("recap.summarize")
    async def recap_summarize(ctx: RpcContext, end: float, start: float = 0.0, cues: list | None = None,
                              sub_path: str = "", path: str = "", ff_index: int | None = None,
                              method: str = "") -> dict[str, Any]:
        """«¿Qué me he perdido?»: the sentences that sum up [start, end] of the dialogue, with their times.
        Source: ``cues`` | ``sub_path`` | ``path`` + ``ff_index`` (embedded text track) | ``path`` (AI transcript).
        ``method="words"`` skips the embedding model."""
        return await service.summarize({"start": start, "end": end, "cues": cues, "sub_path": sub_path,
                                        "path": path, "ff_index": ff_index, "method": method})
