"""``subs.*``: external subtitle files — info, constant shift, and resynchronisation against the Whisper transcription
of the same media (``mpvd/subs/resync.py``). Outputs are SRT files under ``<cache>/subs/<hash>/`` that mpv loads with
``sub-add``. Translation lives in ``mpvd/subs/translate.py`` (registered from here when its runtime is available)."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.srt import Segment, render_srt
from mpvd.hashing import file_hash
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.jobs import Job, Priority
from mpvd.subs.formats import SubtitleError, load_cues
from mpvd.subs.resync import resync
from mpvd.subs.translate import ArgosEngine, ArgosStore, TranslateError, default_dirs, translate_cues

if TYPE_CHECKING:
    from mpvd.asr.service import AsrTask
    from mpvd.server import MpvdServer, RpcContext


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:80]


TRANSLATE_ARTIFACT = "translate"
TRANSLATE_VERSION = "1"
LANG_RE = re.compile(r"^[a-z]{2,3}$")


class SubsService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.out_dir = server.settings.cache_dir / "subs"
        root = server.root
        self.argos = ArgosStore(default_dirs(root, server.settings.data_dir),
                                (root / "vendor" / "dl") if root else server.settings.cache_dir / "dl")
        env_threads = os.environ.get("MPV_UOS_TRANSLATE_THREADS")
        self.translator = ArgosEngine(self.argos, threads=int(env_threads) if env_threads else None,
                                      beam_size=int(os.environ.get("MPV_UOS_TRANSLATE_BEAM", "2")))
        self.translations: dict[str, dict[str, Any]] = {}   # job id -> last result/state (for asr-style polling)

    def _out(self, key: str, stem: str, suffix: str) -> Path:
        d = self.out_dir / safe_name(key)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_name(stem)}.{suffix}.srt"

    async def _cues(self, srt: str) -> list[Segment]:
        try:
            return await asyncio.to_thread(load_cues, srt)
        except SubtitleError as exc:
            raise RpcError(NOT_FOUND, str(exc)) from exc

    async def _key(self, path: str) -> str:
        p = Path(path.removeprefix("file://"))
        if not p.is_file():
            raise RpcError(NOT_FOUND, f"no existe: {p}")
        return (await asyncio.to_thread(file_hash, p)).key

    def info(self, cues: list[Segment], srt: str) -> dict[str, Any]:
        return {"srt": srt, "cues": len(cues), "start": round(cues[0].start, 3), "end": round(cues[-1].end, 3),
                "chars": sum(len(c.text) for c in cues)}

    async def shift(self, srt: str, offset: float, speed: float = 1.0, key: str | None = None) -> dict[str, Any]:
        if speed <= 0:
            raise RpcError(INVALID_PARAMS, "speed must be > 0")
        cues = await self._cues(srt)
        out = [Segment(max(0.0, c.start * speed + offset), max(0.0, c.end * speed + offset), c.text) for c in cues]
        out = [c for c in out if c.end > c.start]
        dest = self._out(key or "shift", Path(srt).stem, f"shift{offset:+.2f}" + (f"x{speed:.4f}" if speed != 1.0 else ""))
        await asyncio.to_thread(dest.write_text, render_srt(out), "utf-8")
        return {"status": "done", "srt": str(dest), "cues": len(out), "offset": offset, "speed": speed}

    def _reference_task(self, key: str) -> AsrTask | None:
        tasks = [t for t in self.server.asr.tasks.values() if t.key == key and t.segments]
        done = [t for t in tasks if t.complete]
        pool = done or tasks
        if not pool:
            return None
        return max(pool, key=lambda t: (len(t.done), t.updated_at))

    async def resync_file(self, path: str, srt: str, language: str = "auto", model: str | None = None,
                          notify: str = "mu_subs", session_id: str | None = None) -> dict[str, Any]:
        key = await self._key(path)
        cues = await self._cues(srt)
        task = self._reference_task(key)
        if task is None or not task.complete:
            # (re)use the ASR machinery: instant when cached, otherwise a low-priority job the caller can follow
            task = await self.server.asr.start(path, language, model, "precompute", 0.0, None, False, None, notify,
                                               session_id)
            if not task.complete:
                return {"status": "pending", "task": task.to_dict(), "cues": len(cues)}
        res = await asyncio.to_thread(resync, cues, task.segments)
        dest = self._out(key, Path(srt).stem, "resync")
        await asyncio.to_thread(dest.write_text, render_srt(res.cues), "utf-8")
        return {"status": "done", "srt": str(dest), "source": srt, "task": task.id, "stats": res.stats,
                "cues": len(res.cues)}


    # -- translation -------------------------------------------------------------------------------------------

    def translate_status(self) -> dict[str, Any]:
        return {"engine": self.translator.status(), "packages": [p.to_dict() for p in self.argos.catalogue()]}

    async def _source_language(self, path: str | None, srt: str, source: str) -> str:
        if source and source != "auto":
            return source
        # the AI track knows its language; anything else must say it
        for t in self.server.asr.tasks.values():
            if t.srt_path is not None and str(t.srt_path) == srt:
                lang = t.detected or (t.language if t.language != "auto" else "")
                if lang:
                    return lang
        raise RpcError(INVALID_PARAMS, "indica el idioma de origen (source) del subtítulo")

    def _push(self, session_id: str | None, target: str, key: str, status: str, payload: dict[str, Any],
              final: bool = False) -> None:
        if not session_id:
            return
        session = self.server.sessions.get(session_id)
        if session is not None and session.connected:
            session.push_event(target, key, status, payload, min_interval=0.5, final=final)

    async def translate(self, srt: str, source: str, target: str, path: str | None = None,
                        notify: str = "mu_subs", session_id: str | None = None) -> dict[str, Any]:
        target = (target or "").lower()
        source = (source or "auto").lower()
        if not LANG_RE.match(target) or (source != "auto" and not LANG_RE.match(source)):
            raise RpcError(INVALID_PARAMS, "códigos de idioma ISO 639-1 (es, en, fr…)")
        if not self.translator.available:
            raise RpcError(UNAVAILABLE, "falta el runtime de traducción: uv sync --extra translate")
        source = await self._source_language(path, srt, source)
        if source == target:
            raise RpcError(INVALID_PARAMS, f"el subtítulo ya está en {target}")
        missing = self.translator.missing_for(source, target)
        if missing:
            raise RpcError(UNAVAILABLE, "falta el paquete de traducción " + ", ".join(f"{a}→{b}" for a, b in missing),
                           {"missing": [list(m) for m in missing]})
        cues = await self._cues(srt)
        srt_path = Path(srt)
        key = await self._key(path) if path else "srt:" + safe_name(srt_path.stem)
        route = "+".join(f"{a}_{b}" for a, b in self.translator.route(source, target))
        params = {"source": source, "target": target, "beam": self.translator.beam_size,
                  "srt_hash": _text_hash(render_srt(cues))}
        dest = self._out(key, srt_path.stem, target)
        entry = await asyncio.to_thread(self.server.cache.get, key, TRANSLATE_ARTIFACT, route, TRANSLATE_VERSION, params)
        if entry is not None and isinstance(entry.data, dict) and entry.data.get("srt_text"):
            await asyncio.to_thread(dest.write_text, entry.data["srt_text"], "utf-8")
            return {"status": "done", "srt": str(dest), "source": source, "target": target, "cues": len(cues),
                    "cached": True, "route": route}

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def report(frac: float) -> None:   # runs on the event loop
                job.report(0.05 + 0.9 * frac, f"{int(frac * 100)} %")
                self._push(session_id, notify, "translate:" + job.id, f"running:{job.progress:.2f}",
                           {"event": "subs-translate", "job": job.to_dict()})

            def progress(frac: float) -> None:  # called from the worker thread
                loop.call_soon_threadsafe(report, frac)

            def work() -> list[Segment]:
                return translate_cues(cues, lambda batch: self.translator.translate(batch, source, target),
                                      progress=progress)

            try:
                out = await asyncio.to_thread(work)
            except Exception as exc:
                self._push(session_id, notify, "translate:" + job.id, "failed", {"event": "subs-translate",
                           "job": {**job.to_dict(), "status": "failed", "error": str(exc)}}, final=True)
                raise
            text = render_srt(out)
            await asyncio.to_thread(dest.write_text, text, "utf-8")
            self.server.cache.put(key, TRANSLATE_ARTIFACT, model=route, version=TRANSLATE_VERSION, params=params,
                                  data={"srt_text": text, "srt": str(dest)})
            result = {"status": "done", "srt": str(dest), "source": source, "target": target, "cues": len(out),
                      "cached": False, "route": route, "job": job.id}
            job.report(1.0, "listo")
            self._push(session_id, notify, "translate:" + job.id, "done",
                       {"event": "subs-translate", "job": {**job.to_dict(), "status": "done"}, "result": result}, final=True)
            return result

        job = self.server.jobs.submit(f"subs.translate.{source}-{target}", body, priority=Priority.INTERACTIVE, heavy=True,
                                      session_id=None, meta={"notify": notify, "srt": srt, "source": source,
                                                             "target": target, "session": session_id})
        return {"status": "queued", "job": job.to_dict(), "source": source, "target": target, "cues": len(cues),
                "route": route, "srt": str(dest)}

    def download_package(self, source: str, target: str, notify: str, session_id: str | None) -> Job:
        pair = f"{source}_{target}"

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def report(frac: float, message: str) -> None:
                job.report(frac, message)
                self._push(session_id, notify, "translate-model:" + pair, f"running:{frac:.2f}",
                           {"event": "subs-translate-model", "pair": pair, "job": job.to_dict()})

            def progress(frac: float, message: str) -> None:   # from the download thread
                loop.call_soon_threadsafe(report, frac, message)

            try:
                path = await self.argos.download(source, target, progress=progress)
            except (TranslateError, OSError, ValueError) as exc:
                self._push(session_id, notify, "translate-model:" + pair, "failed",
                           {"event": "subs-translate-model", "pair": pair,
                            "job": {**job.to_dict(), "status": "failed", "error": str(exc)}}, final=True)
                raise TranslateError(str(exc)) from exc
            self._push(session_id, notify, "translate-model:" + pair, "done",
                       {"event": "subs-translate-model", "pair": pair, "job": {**job.to_dict(), "status": "done"}},
                       final=True)
            return {"pair": pair, "path": str(path)}

        return self.server.jobs.submit(f"subs.translate.download.{pair}", body, priority=Priority.INTERACTIVE,
                                       heavy=False, session_id=None, meta={"notify": notify, "pair": pair})


def _text_hash(text: str) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()

def register(server: MpvdServer, service: SubsService) -> None:
    d = server.dispatcher
    server.services["subs"] = True
    server.services["translate"] = service.translator.available

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    @d.method("subs.info")
    async def info(ctx: RpcContext, srt: str) -> dict[str, Any]:
        """Cue count and time span of an external subtitle file (SRT, VTT, ASS)."""
        return service.info(await service._cues(srt), srt)

    @d.method("subs.shift")
    async def shift(ctx: RpcContext, srt: str, offset: float, speed: float = 1.0) -> dict[str, Any]:
        """Write a copy of the subtitle file with a constant offset (seconds) and optional speed factor."""
        return await service.shift(srt, float(offset), float(speed))

    @d.method("subs.resync")
    async def resync_(ctx: RpcContext, path: str, srt: str, language: str = "auto",
                      model: str | None = None, notify: str = "mu_subs") -> dict[str, Any]:
        """Align an external subtitle file with the Whisper transcription of ``path`` (delay + drift, per window).

        Returns ``status: pending`` with the ASR task when the transcription is not finished yet."""
        if not server.asr.engine.available:
            raise RpcError(UNAVAILABLE, "whisper-cli no encontrado: ejecuta tools/vendor_whisper.sh")
        return await service.resync_file(path, srt, language, model, notify, _sid(ctx))

    @d.method("subs.translate")
    async def translate(ctx: RpcContext, srt: str, target: str, source: str = "auto", path: str | None = None,
                        notify: str = "mu_subs") -> dict[str, Any]:
        """Translate a subtitle file offline (Argos/CTranslate2). Cached → ``done`` at once; else a job that pushes
        ``subs-translate`` events to ``notify`` and ends with the new SRT path."""
        return await service.translate(srt, source, target, path, notify, _sid(ctx))

    @d.method("subs.translate.models")
    async def translate_models(ctx: RpcContext, index: bool = False) -> dict[str, Any]:
        """Translation runtime state and packages (present, pinned, and — with ``index`` — the whole official index)."""
        st = service.translate_status()
        if index:
            st["packages"] = [p.to_dict() for p in await asyncio.to_thread(service.argos.catalogue, True)]
        return st

    @d.method("subs.translate.download")
    async def translate_download(ctx: RpcContext, source: str, target: str, notify: str = "mu_subs") -> dict[str, Any]:
        """Download a translation package in the background (progress pushed as ``subs-translate-model``)."""
        if not (LANG_RE.match(source or "") and LANG_RE.match(target or "")):
            raise RpcError(INVALID_PARAMS, "códigos de idioma ISO 639-1")
        return service.download_package(source, target, notify, _sid(ctx)).to_dict()

    @d.method("subs.translate.remove")
    async def translate_remove(ctx: RpcContext, source: str, target: str) -> dict[str, Any]:
        """Delete a downloaded translation package."""
        service.translator.unload()
        return {"removed": service.argos.remove(source, target)}
