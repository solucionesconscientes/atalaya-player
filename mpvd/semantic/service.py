"""``semantic.*``: index a file's transcript (from the ``asr`` service) with sentence embeddings, search it, and derive
topic-change chapters. Vectors live in the artifact cache as a float32 blob keyed by (file hash, "embed", model, version,
params); chapters are cached too. Everything heavy runs in a thread; the embedder loads once per daemon."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import file_hash
from mpvd.jobs import Job, Priority
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.semantic import embed as embed_mod
from mpvd.semantic.index import (Sentence, chapters as topic_chapters, ffmetadata,
                                 highlights as pick_highlights, search as vector_search,
                                 sentences_from_segments, vectors_from_bytes, vectors_to_bytes)

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.semantic")

ARTIFACT_EMBED = "embed"
ARTIFACT_CHAPTERS = "chapters"
VERSION = "1"
DEFAULT_NOTIFY = "mu_menu"


class SemanticService:
    def __init__(self, server: MpvdServer, embedder_factory: Any = None):
        self.server = server
        self._embedder: Any = None
        self._factory = embedder_factory or embed_mod.Embedder
        self.fake = embedder_factory is not None          # injected embedder: no ONNX model needed
        self._lock = asyncio.Lock()
        self.jobs: dict[str, Job] = {}
        self.download_job: Job | None = None
        self.model_dir = embed_mod.default_model_dir()

    # -- status / model ----------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "runtime": embed_mod.runtime_available(),
            "model": getattr(self._factory, "name", embed_mod.MODEL_NAME),
            "model_present": self.fake or embed_mod.model_present(self.model_dir),
            "model_dir": str(self.model_dir),
            "model_size_mb": round(sum(f.size for f in embed_mod.FILES) / (1 << 20), 1),
            "loaded": self._embedder is not None,
            "threads": embed_mod.threads_for_hardware(),
            "download": self.download_job.to_dict() if self.download_job else None,
            "indexing": {k: j.to_dict() for k, j in self.jobs.items()},
        }

    def available(self) -> bool:
        return embed_mod.runtime_available() and (self.fake or embed_mod.model_present(self.model_dir))

    async def embedder(self) -> Any:
        async with self._lock:
            if self._embedder is None:
                if not embed_mod.runtime_available():
                    raise RpcError(UNAVAILABLE, t("falta el extra 'semantic' (uv sync --extra semantic)"))
                if not self.fake and not embed_mod.model_present(self.model_dir):
                    raise RpcError(UNAVAILABLE, t("el modelo de embeddings no está descargado (semantic.models.download)"))
                self._embedder = await asyncio.to_thread(self._factory, self.model_dir)
            return self._embedder

    def submit_download(self, notify: str, session_id: str | None) -> Job:
        if self.download_job is not None and self.download_job.status.value in ("queued", "running"):
            return self.download_job

        async def body(job: Job) -> dict[str, Any]:
            def progress(frac: float, text: str) -> None:
                job.report(frac, text)
                self._push(session_id, notify, "semantic-model", {"event": "semantic-model", "progress": frac, "text": text})

            await embed_mod.download_model(self.model_dir, progress)
            self._push(session_id, notify, "semantic-model", {"event": "semantic-model", "progress": 1.0, "done": True}, final=True)
            return self.status()

        self.download_job = self.server.jobs.submit("semantic.models.download", body, priority=Priority.INTERACTIVE, heavy=False,
                                                    session_id=None, meta={"notify": notify})
        return self.download_job

    def _push(self, session_id: str | None, notify: str, kind: str, payload: dict[str, Any], final: bool = False) -> None:
        if not session_id:
            return
        session = self.server.sessions.get(session_id)
        if session is not None and session.connected:
            session.push_event(notify, kind, "done" if final else "progress", payload, final=final)

    # -- transcript access -------------------------------------------------------------------------

    def _transcript(self, path: Path) -> tuple[str, list[dict[str, Any]], float] | None:
        """(key, cues, duration) of the most complete transcription task of ``path`` (any model/language)."""
        asr = self.server.asr
        best = None
        for t in asr.tasks.values():
            if t.path == str(path) and t.segments and (best is None or len(t.segments) > len(best.segments)):
                best = t
        if best is None:
            return None
        return best.key, [s.to_dict() for s in best.segments], best.duration

    async def _key(self, path: Path) -> str:
        return (await asyncio.to_thread(file_hash, path)).key

    def _params(self, model: str) -> dict[str, Any]:
        return {"model": model}

    # -- index -------------------------------------------------------------------------------------

    async def load_index(self, path: Path) -> tuple[list[Sentence], Any] | None:
        entry = await asyncio.to_thread(self.server.cache.get, await self._key(path), ARTIFACT_EMBED, embed_mod.MODEL_NAME,
                                        VERSION, {})
        if entry is None or not isinstance(entry.data, dict) or entry.blob_path is None:
            return None
        sentences = [Sentence(float(s["start"]), float(s["end"]), str(s["text"])) for s in entry.data.get("sentences", [])]
        blob = await asyncio.to_thread(entry.blob_path.read_bytes)
        vectors = vectors_from_bytes(blob, int(entry.data.get("dim") or 0))
        if vectors.shape[0] != len(sentences):
            return None
        return sentences, vectors

    async def build_index(self, path: Path, job: Job | None = None) -> dict[str, Any]:
        tr = self._transcript(path)
        if tr is None:
            raise RpcError(NOT_FOUND, t("no hay transcripción de ese archivo (asr.precompute primero)"))
        key, cues, duration = tr
        sentences = sentences_from_segments(cues)
        if job:
            job.report(0.05, f"{len(sentences)} frases")
        emb = await self.embedder()
        texts = [s.text for s in sentences]
        vectors = await asyncio.to_thread(emb.encode, texts)
        if job:
            job.report(0.9, "guardando")
        data = {"sentences": [s.to_dict() for s in sentences], "dim": int(vectors.shape[1]) if vectors.ndim == 2 else 0,
                "n": len(sentences), "cues": len(cues), "model": emb.name, "duration": duration, "indexed_at": time.time()}
        await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_EMBED, model=embed_mod.MODEL_NAME, version=VERSION,
                                params={}, data=data, blob=vectors_to_bytes(vectors), blob_suffix=".f32")
        # a new index invalidates cached chapters
        await asyncio.to_thread(self.server.cache.invalidate, key, ARTIFACT_CHAPTERS)
        if job:
            job.report(1.0, "listo")
        return {"path": str(path), "key": key, "sentences": len(sentences), "cues": len(cues), "model": emb.name}

    def submit_index(self, path: Path, notify: str, session_id: str | None, priority: Priority = Priority.PRECOMPUTE) -> Job:
        skey = str(path)
        job = self.jobs.get(skey)
        if job is not None and job.status.value in ("queued", "running"):
            return job

        async def body(j: Job) -> dict[str, Any]:
            try:
                res = await self.build_index(path, j)
            finally:
                self.jobs.pop(skey, None)
            self._push(session_id, notify, "semantic:" + skey, {"event": "semantic", "result": res}, final=True)
            return res

        job = self.server.jobs.submit("semantic.index", body, priority=priority, heavy=True, session_id=None,
                                      meta={"notify": notify, "path": skey})
        self.jobs[skey] = job
        return job

    # -- search / chapters -------------------------------------------------------------------------

    async def search(self, path: Path, query: str, k: int) -> dict[str, Any]:
        idx = await self.load_index(path)
        if idx is None:
            return {"status": "unindexed", "path": str(path), "hits": []}
        sentences, vectors = idx
        emb = await self.embedder()
        qv = (await asyncio.to_thread(emb.encode, [query]))[0]
        hits = await asyncio.to_thread(vector_search, qv, vectors, sentences, k, query)
        return {"status": "done", "path": str(path), "hits": hits, "sentences": len(sentences)}

    async def highlights(self, path: Path, target: float, min_segment: float) -> dict[str, Any]:
        loaded = await self.load_index(path)
        if loaded is None:
            raise RpcError(NOT_FOUND, t("no hay índice de ese archivo"))
        sentences, vectors = loaded
        dur = sentences[-1].end if sentences else 0.0
        res = await asyncio.to_thread(pick_highlights, vectors, sentences, target, dur, min_segment)
        return {**res, "asked": round(target, 1), "duration": round(dur, 1)}

    async def chapters(self, path: Path, min_seconds: float, percentile: float, window: float,
                       force: bool = False) -> dict[str, Any]:
        key = await self._key(path)
        params = {"min_seconds": min_seconds, "percentile": percentile, "window": window}
        if not force:
            entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT_CHAPTERS, embed_mod.MODEL_NAME, VERSION, params)
            if entry is not None and isinstance(entry.data, dict):
                return {"status": "done", "cached": True, **entry.data}
        idx = await self.load_index(path)
        if idx is None:
            return {"status": "unindexed", "path": str(path), "chapters": []}
        sentences, vectors = idx
        tr = self._transcript(path)
        duration = tr[2] if tr else None
        res = await asyncio.to_thread(topic_chapters, vectors, sentences, duration, window, 0.5, 3, percentile, min_seconds)
        data = {"path": str(path), "key": key, "chapters": res["chapters"], "cuts": res["cuts"], "windows": res["windows"],
                "params": params, "ffmetadata": ffmetadata(res["chapters"]) if res["chapters"] else ""}
        await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_CHAPTERS, model=embed_mod.MODEL_NAME, version=VERSION,
                                params=params, data=data)
        return {"status": "done", "cached": False, **data}


def _local(path: str) -> Path:
    import re  # noqa: PLC0415

    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not path.startswith("file://"):
        raise RpcError(UNAVAILABLE, t("solo archivos locales"))
    p = Path(path.removeprefix("file://"))
    if not p.is_file():
        raise RpcError(NOT_FOUND, t("no existe: %s") % (p,))
    return p


def register(server: MpvdServer, service: SemanticService) -> None:
    d = server.dispatcher
    server.services["semantic"] = service.available()

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    async def _fallback_text_search(path: Path, q: str, k: int) -> list[dict[str, Any]]:
        tr = service._transcript(path)
        if tr is None:
            return []
        from mpvd.semantic.index import fold  # noqa: PLC0415

        needle = fold(q).strip()
        hits = [{"start": c["start"], "end": c["end"], "text": c["text"], "score": 1.0}
                for c in tr[1] if needle and needle in fold(c["text"])]
        return hits[:k]

    @d.method("semantic.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """Runtime/model availability, loaded state, running downloads and index jobs."""
        return service.status()

    @d.method("semantic.models.download")
    async def download(ctx: RpcContext, notify: str = DEFAULT_NOTIFY) -> dict[str, Any]:
        """Download the pinned embedding model (SHA-256 verified) in the background."""
        if not embed_mod.runtime_available():
            raise RpcError(UNAVAILABLE, t("falta el extra 'semantic' (uv sync --extra semantic)"))
        job = service.submit_download(notify, _sid(ctx))
        server.services["semantic"] = service.available()
        return {"job": job.to_dict(), "present": embed_mod.model_present(service.model_dir)}

    @d.method("semantic.index")
    async def index(ctx: RpcContext, path: str, wait: bool = False, notify: str = DEFAULT_NOTIFY,
                    interactive: bool = False) -> dict[str, Any]:
        """Embed the sentences of the file's transcript (needs an asr task with cues); cached by file hash."""
        p = _local(path)
        if not service.available():
            raise RpcError(UNAVAILABLE, t("búsqueda semántica no disponible: %s") % (_why(service),))
        if await service.load_index(p) is not None and not wait:
            return {"status": "done", "path": str(p), "cached": True}
        if wait:
            return {"status": "done", **(await service.build_index(p))}
        if service._transcript(p) is None:
            raise RpcError(NOT_FOUND, t("no hay transcripción de ese archivo (asr.precompute primero)"))
        job = service.submit_index(p, notify, _sid(ctx), Priority.INTERACTIVE if interactive else Priority.PRECOMPUTE)
        return {"status": "indexing", "path": str(p), "job": job.to_dict()}

    @d.method("semantic.search")
    async def search(ctx: RpcContext, q: str, path: str | None = None, k: int = 10, index: bool = True,  # noqa: A002
                     notify: str = DEFAULT_NOTIFY) -> dict[str, Any]:
        """Semantic search in one file's transcript. Without an index (or without the model) it falls back to literal text
        matching and, when ``index`` is set and possible, starts indexing in the background."""
        q = (q or "").strip()
        if not q:
            raise RpcError(INVALID_PARAMS, "empty query")
        if not path and ctx.session is not None and ctx.session.connected:
            try:
                path = await ctx.session.client.get_property("path")
            except Exception:  # noqa: BLE001 - idle player or IPC hiccup: report "path required" below
                path = None
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        p = _local(path)
        k = max(1, min(int(k), 50))
        if service.available():
            res = await service.search(p, q, k)
            if res["status"] == "done":
                res["mode"] = "semantic"
                return res
            if index and service._transcript(p) is not None:
                job = service.submit_index(p, notify, _sid(ctx), Priority.INTERACTIVE)
                res["job"] = job.to_dict()
        else:
            res = {"status": "unavailable", "path": str(p), "hits": [], "reason": _why(service)}
        res["hits"] = await _fallback_text_search(p, q, k)
        res["mode"] = "text"
        return res

    @d.method("semantic.highlights")
    async def highlights(ctx: RpcContext, path: str, minutes: float = 15.0, min_segment: float = 25.0,
                         index: bool = True) -> dict[str, Any]:
        """H58 · los tramos que mejor representan el archivo, para verlo entero en `minutes` minutos.

        Devuelve `{segments: [{start, end}], total, sentences}`. Se corta por frases enteras, así que un tramo
        nunca empieza ni acaba a mitad de palabra."""
        p = _local(path)
        if not service.available():
            raise RpcError(UNAVAILABLE, t("no disponible: %s") % (_why(service),))
        if float(minutes) <= 0:
            raise RpcError(INVALID_PARAMS, t("¿de cuántos minutos?"))
        if index and await service.load_index(p) is None:
            await service.build_index(p)
        return await service.highlights(p, float(minutes) * 60.0, float(min_segment))

    @d.method("semantic.chapters")
    async def chapters(ctx: RpcContext, path: str, min_seconds: float = 180.0, percentile: float = 85.0,
                       window: float = 45.0, force: bool = False, index: bool = True) -> dict[str, Any]:
        """Topic-change chapters of a file (cached per parameters). Indexes synchronously first if needed."""
        p = _local(path)
        if not service.available():
            raise RpcError(UNAVAILABLE, t("capítulos automáticos no disponibles: %s") % (_why(service),))
        if index and await service.load_index(p) is None:
            await service.build_index(p)
        return await service.chapters(p, float(min_seconds), float(percentile), float(window), force)


def _why(service: SemanticService) -> str:
    if not embed_mod.runtime_available():
        return "falta el extra 'semantic' (uv sync --extra semantic)"
    if not embed_mod.model_present(service.model_dir):
        return "modelo no descargado (semantic.models.download)"
    return "desconocido"
