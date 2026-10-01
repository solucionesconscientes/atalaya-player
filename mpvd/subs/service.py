"""``subs.*``: external subtitle files — info, constant shift, resynchronisation against the Whisper transcription
of the same media (``mpvd/subs/resync.py``), offline translation (Argos / OPUS-MT, ``mpvd/subs/translate.py``),
extraction of embedded text tracks and saving any of them as SRT next to the video (``mpvd/subs/save.py``).
Outputs of the transformations are SRT files under ``<cache>/subs/<hash>/`` that mpv loads with ``sub-add``."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import probe_duration
from mpvd.asr.srt import Segment, render_srt
from mpvd.hashing import file_hash
from mpvd.jobs import Job, Priority, Status
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.hashing import url_key
from mpvd.subs import opus as opus_mod
from mpvd.subs import web as web_mod
from mpvd.subs.formats import SubtitleError, load_cues
from mpvd.subs.resync import resync
from mpvd.subs.save import (IMAGE_CODECS, IMAGE_ERROR, KINDS, SaveError, choose_name, extract_srt, is_url, local_path,
                            srt_text, subtitle_stream, target_dir, video_stem, write_atomic)
from mpvd.subs.translate import (ENGINE_NAMES, ENGINES, ArgosEngine, ArgosStore, TranslateError, TranslationRouter,
                                 default_dirs, normalize_lang, translate_cues)

if TYPE_CHECKING:
    from mpvd.asr.service import AsrTask
    from mpvd.server import MpvdServer, RpcContext


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:80]


TRANSLATE_ARTIFACT = "translate"
TRANSLATE_VERSION = "2"   # 2: OPUS-MT engine, punctuation-aware redistribution, pause grouping, dialogue line breaks
LANG_RE = re.compile(r"^[a-z]{2,3}$")

# C5 · orden de fiabilidad de un subtítulo encontrado (de más a menos). Lo usa `SubsService.find`.
RELIABILITY = ("canal", "hash", "nombre", "auto")

# Proveedores comprobados contra el servicio real el 2026-10-01 y que NO se pueden ofrecer todavía. Se enseñan en el
# menú con su motivo: decir «no hay subtítulos» cuando lo que pasa es que falta una clave es mentir.
UNAVAILABLE_PROVIDERS = (
    ("subdl", "Subdl", "hace falta una clave gratuita de subdl.com (ver NEEDS_HUMAN.md)"),
    ("podnapisi", "Podnapisi", "el sitio ya no existe (su dominio no resuelve)"),
)


class SubsService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.out_dir = server.settings.cache_dir / "subs"
        root = server.root
        self.argos = ArgosStore(default_dirs(root, server.settings.data_dir),
                                (root / "vendor" / "dl") if root else server.settings.cache_dir / "dl")
        env_threads = os.environ.get("MPV_UOS_TRANSLATE_THREADS")
        threads = int(env_threads) if env_threads else None
        self.translator = ArgosEngine(self.argos, threads=threads,
                                      beam_size=int(os.environ.get("MPV_UOS_TRANSLATE_BEAM", "2")))
        dirs, download_to, dl_dirs = opus_mod.default_dirs(root, server.settings.data_dir)
        self.opus = opus_mod.OpusStore(dirs, download_to, dl_dirs)
        self.opus_engine = opus_mod.OpusEngine(self.opus, threads=threads,
                                               beam_size=int(os.environ.get("MPV_UOS_TRANSLATE_BEAM_OPUS", "4")))
        self.router = TranslationRouter(self.translator, self.opus_engine)
        self.translations: dict[str, dict[str, Any]] = {}   # job id -> last result/state (for asr-style polling)
        self.extracting: dict[str, Job] = {}                # "<key>:<ff_index>" -> running extraction job
        self.waiting: set[asyncio.Task[Any]] = set()        # "complete and save" watchers of AI tasks

    @staticmethod
    def subtitle_key(path: str | None, srt_path: Path) -> str:
        """Cache/output key of a translation when the video is not a local file.

        The web subtitles of every video are saved as ``<lang>.<kind>.srt`` (H29), so the stem alone would send the
        translation of every video to the same file: the URL, or the folder the SRT lives in, is what tells them apart.
        """
        if path:
            return "url:" + safe_name(url_key(path))
        return "srt:" + safe_name(f"{srt_path.parent.name}-{srt_path.stem}")

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

    # -- cascada de proveedores (H36/C5) ---------------------------------------------------------------------------

    async def find(self, path: str, languages: str | None = None) -> dict[str, Any]:
        """Todo lo que hay para este archivo o esta URL, de todos los proveedores, en una sola lista ordenada.

        Fiabilidad, de más a menos: ``canal`` (los que trae el propio sitio para ESE vídeo) · ``hash`` (OpenSubtitles
        reconoce el archivo exacto) · ``nombre`` (coincide el título, puede ser otra versión y descuadrar) · ``auto``
        (subtítulos automáticos de la web: son transcripción de máquina). Cada proveedor dice también por qué no está
        disponible, que es la diferencia entre «no hay subtítulos» y «te falta una clave».
        """
        langs = [x for x in (languages or self.server.library.settings.get("osub_languages") or "es,en").split(",") if x]
        providers: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        is_web = is_url(path) and not path.startswith("file://")

        # 1. el propio sitio del vídeo (H29): lo más fiable que hay, porque son de ese vídeo
        if is_web:
            try:
                web = await self.web_list(path, langs[0] if langs else "es")
                providers.append({"id": "web", "name": "La web del vídeo", "ok": True, "reason": ""})
                for t in web.get("tracks", []):
                    auto = t.get("kind") == "auto"
                    sources.append({"provider": "web", "provider_name": "La web del vídeo",
                                    "reliability": "auto" if auto else "canal",
                                    "language": t.get("lang", ""), "label": t.get("label") or t.get("lang", ""),
                                    "downloads": 0, "pick": {"web": {"url": path, "lang": t.get("lang"),
                                                                     "kind": t.get("kind", "manual")}}})
            except RpcError as exc:
                providers.append({"id": "web", "name": "La web del vídeo", "ok": False, "reason": exc.message})
        else:
            providers.append({"id": "web", "name": "La web del vídeo", "ok": False,
                              "reason": "esto no es un vídeo de internet"})

        # 2. OpenSubtitles.com (ADR-050): por hash primero y por nombre después, ya ordenado por el propio buscador
        if is_web:
            providers.append({"id": "opensubtitles", "name": "OpenSubtitles", "ok": False,
                              "reason": "solo busca archivos de tu equipo (necesita el hash)"})
        else:
            try:
                found = await self.server.library.subs_search(path, ",".join(langs))
                providers.append({"id": "opensubtitles", "name": "OpenSubtitles", "ok": True, "reason": ""})
                for r in found.get("results", []):
                    sources.append({"provider": "opensubtitles", "provider_name": "OpenSubtitles",
                                    "reliability": "hash" if r.get("hash_match") else "nombre",
                                    "language": r.get("language", ""),
                                    "label": r.get("release") or r.get("file_name") or "",
                                    "downloads": int(r.get("downloads") or 0),
                                    "hearing_impaired": bool(r.get("hearing_impaired")),
                                    "machine": bool(r.get("machine_translated") or r.get("ai_translated")),
                                    "pick": {"opensubtitles": {"path": path, "file_id": r.get("file_id")}}})
            except RpcError as exc:
                providers.append({"id": "opensubtitles", "name": "OpenSubtitles", "ok": False,
                                  "reason": exc.message})

        # 3. los que todavía no se pueden ofrecer, dicho aquí para que el menú no los invente (ver NEEDS_HUMAN.md)
        for pid, name, reason in UNAVAILABLE_PROVIDERS:
            providers.append({"id": pid, "name": name, "ok": False, "reason": reason})

        sources.sort(key=lambda s: (RELIABILITY.index(s["reliability"]) if s["reliability"] in RELIABILITY else 9,
                                    langs.index(s["language"]) if s["language"] in langs else len(langs),
                                    1 if s.get("machine") else 0, -s.get("downloads", 0)))
        return {"path": path, "languages": ",".join(langs), "web": is_web, "sources": sources,
                "providers": providers}

    async def pick(self, source: dict[str, Any], notify: str = "mu_subs",
                   session_id: str | None = None) -> dict[str, Any]:
        """Un resultado de ``find`` → SRT en la caché. Se le pasa el ``pick`` tal cual, así el menú no tiene que saber
        qué RPC toca para cada proveedor."""
        if not isinstance(source, dict):
            raise RpcError(INVALID_PARAMS, "source debe ser el campo «pick» de subs.find")
        web = source.get("web")
        if isinstance(web, dict):
            return await self.web_fetch(str(web.get("url") or ""), str(web.get("lang") or ""),
                                        str(web.get("kind") or "manual"))
        osub = source.get("opensubtitles")
        if isinstance(osub, dict):
            return await self.server.library.subs_download(str(osub.get("path") or ""), osub.get("file_id"),
                                                           notify=notify, session_id=session_id)
        raise RpcError(INVALID_PARAMS, "ese resultado no viene de subs.find")

    # -- subtitles of internet videos (H29) ------------------------------------------------------------------------

    async def web_list(self, url: str, prefer: str = "es") -> dict[str, Any]:
        """Manual tracks and the original-language automatic captions the site offers for ``url`` (from the cached
        ``yt-dlp -J``)."""
        info = await self.server.ytdl.raw_info(url)
        tracks = web_mod.list_tracks(info, prefer)
        return {"url": url, "title": info.get("title") or "", "language": info.get("language") or "",
                "tracks": tracks}

    async def web_fetch(self, url: str, lang: str, kind: str = "manual") -> dict[str, Any]:
        """One of those tracks as a clean SRT in the cache (``srt``); automatic captions are de-rolled. An expired link
        (the site's URLs last ~6 h) fetches the ``-J`` again once; HTTP 429 → UNAVAILABLE (the site refuses now)."""
        if kind not in ("manual", "auto"):
            raise RpcError(INVALID_PARAMS, "kind: manual o auto")
        dest = self.out_dir / "web" / safe_name(url_key(url)) / f"{safe_name(lang)}.{kind}.srt"
        if dest.is_file() and dest.stat().st_size > 0:
            cues = await self._cues(str(dest))
            return self._web_result(url, lang, kind, dest, cues, cached=True)
        text = ""
        ext = ""
        for attempt in (0, 1):
            info = await self.server.ytdl.raw_info(url, force=attempt == 1)
            entry = web_mod.entry_for(info, lang, kind)
            if entry is None:
                raise RpcError(NOT_FOUND, f"la web no ofrece subtítulos {kind} en «{lang}»")
            try:
                text = await asyncio.to_thread(web_mod.fetch_text, str(entry["url"]))
                ext = str(entry.get("ext") or "")
                break
            except web_mod.urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise RpcError(UNAVAILABLE, "la web no deja bajar los subtítulos ahora (429); prueba más tarde")\
                        from exc
                if exc.code not in web_mod.EXPIRED or attempt == 1:
                    raise RpcError(UNAVAILABLE, f"no se pudieron bajar los subtítulos: HTTP {exc.code}") from exc
            except OSError as exc:
                raise RpcError(UNAVAILABLE, f"no se pudieron bajar los subtítulos: {exc}") from exc
        cues = web_mod.to_cues(text, ext, kind)
        if not cues:
            raise RpcError(NOT_FOUND, "los subtítulos de la web están vacíos")
        await asyncio.to_thread(web_mod.write_srt, dest, cues)
        return self._web_result(url, lang, kind, dest, cues, cached=False)

    @staticmethod
    def _web_result(url: str, lang: str, kind: str, dest: Path, cues: list[Segment], cached: bool) -> dict[str, Any]:
        label = web_mod.lang_label(lang) + (" (automáticos)" if kind == "auto" else "")
        return {"url": url, "lang": lang, "kind": kind, "srt": str(dest), "cues": len(cues), "cached": cached,
                "title": f"{label} · web"}

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

    def _push(self, session_id: str | None, target: str, key: str, status: str, payload: dict[str, Any],
              final: bool = False) -> None:
        if not session_id:
            return
        session = self.server.sessions.get(session_id)
        if session is not None and session.connected:
            session.push_event(target, key, status, payload, min_interval=0.5, final=final)

    # -- translation -------------------------------------------------------------------------------------------

    def engines_status(self) -> list[dict[str, Any]]:
        runtime = self.translator.available
        opus_pairs = self.opus.catalogue()
        first = opus_pairs[0] if opus_pairs else {}
        return [
            {"id": "argos", "name": ENGINE_NAMES["argos"], "available": runtime, "beam_size": self.translator.beam_size,
             "present": [f"{s}_{t}" for s, t in self.argos.present()], "size_mb": 90,
             "note": "todos los pares del índice de Argos; pivota por inglés"},
            {"id": "opus-big", "name": ENGINE_NAMES["opus-big"], "available": runtime and opus_mod.converter_available(),
             "beam_size": self.opus_engine.beam_size, "size_mb": first.get("size_mb", 234),
             "download_mb": first.get("download_mb", 863), "license": opus_mod.LICENSE,
             "present": [f"{p['source']}_{p['target']}" for p in opus_pairs if p["present"]], "pairs": opus_pairs,
             "note": "OPUS-MT tc-big (Helsinki-NLP): es/ca/fr ↔ en; se descarga y convierte una vez"},
        ]

    def translate_status(self) -> dict[str, Any]:
        return {"engine": self.translator.status(), "packages": [p.to_dict() for p in self.argos.catalogue()],
                "engines": self.engines_status(), "default_engine": "opus-big"}

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

    async def translate(self, srt: str, source: str, target: str, path: str | None = None,
                        notify: str = "mu_subs", session_id: str | None = None,
                        engine: str = "auto") -> dict[str, Any]:
        target = normalize_lang(target)
        source = normalize_lang(source or "auto")
        engine = (engine or "auto").lower()
        if not LANG_RE.match(target) or (source != "auto" and not LANG_RE.match(source)):
            raise RpcError(INVALID_PARAMS, "códigos de idioma ISO 639-1 (es, en, fr…)")
        if engine not in ENGINES:
            raise RpcError(INVALID_PARAMS, f"motor desconocido {engine!r} (auto, argos, opus-big)")
        if not self.translator.available:
            raise RpcError(UNAVAILABLE, "falta el runtime de traducción: uv sync --extra translate")
        source = await self._source_language(path, srt, source)
        if source == target:
            raise RpcError(INVALID_PARAMS, f"el subtítulo ya está en {target}")
        missing = await asyncio.to_thread(self.router.missing_for, source, target, engine)
        if missing:
            raise RpcError(UNAVAILABLE, "falta el modelo de traducción " + ", ".join(
                f"{lg.source}→{lg.target} ({ENGINE_NAMES.get(lg.engine, lg.engine)})" for lg in missing),
                {"missing": [[lg.source, lg.target, lg.engine] for lg in missing]})
        cues = await self._cues(srt)
        srt_path = Path(srt)
        key = await self._key(path) if path and not is_url(path) else self.subtitle_key(path, srt_path)
        try:
            legs = self.router.plan(source, target, engine)
        except TranslateError as exc:
            raise RpcError(UNAVAILABLE, str(exc)) from exc
        route = "+".join(leg.key for leg in legs)
        engines_used = sorted({leg.engine for leg in legs})
        params = {"source": source, "target": target, "beams": self.router.beams(legs),
                  "srt_hash": _text_hash(render_srt(cues))}
        dest = self._out(key, srt_path.stem, target)
        entry = await asyncio.to_thread(self.server.cache.get, key, TRANSLATE_ARTIFACT, route, TRANSLATE_VERSION, params)
        if entry is not None and isinstance(entry.data, dict) and entry.data.get("srt_text"):
            await asyncio.to_thread(dest.write_text, entry.data["srt_text"], "utf-8")
            return {"status": "done", "srt": str(dest), "source": source, "target": target, "cues": len(cues),
                    "cached": True, "route": route, "engines": engines_used}

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def report(frac: float) -> None:   # runs on the event loop
                job.report(0.05 + 0.9 * frac, f"{int(frac * 100)} %")
                self._push(session_id, notify, "translate:" + job.id, f"running:{job.progress:.2f}",
                           {"event": "subs-translate", "job": job.to_dict()})

            def progress(frac: float) -> None:  # called from the worker thread
                loop.call_soon_threadsafe(report, frac)

            def work() -> list[Segment]:
                try:
                    return translate_cues(cues, lambda batch: self.router.translate(batch, legs), progress=progress)
                finally:
                    self.router.unload()     # OPUS-MT big holds ~430 MB: never keep it idle in the daemon

            t0 = time.monotonic()
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
                      "cached": False, "route": route, "engines": engines_used, "job": job.id,
                      "seconds": round(time.monotonic() - t0, 2)}
            job.report(1.0, "listo")
            self._push(session_id, notify, "translate:" + job.id, "done",
                       {"event": "subs-translate", "job": {**job.to_dict(), "status": "done"}, "result": result}, final=True)
            return result

        job = self.server.jobs.submit(f"subs.translate.{source}-{target}", body, priority=Priority.INTERACTIVE, heavy=True,
                                      session_id=None, meta={"notify": notify, "srt": srt, "source": source,
                                                             "target": target, "session": session_id, "route": route})
        return {"status": "queued", "job": job.to_dict(), "source": source, "target": target, "cues": len(cues),
                "route": route, "engines": engines_used, "srt": str(dest)}

    def download_package(self, source: str, target: str, notify: str, session_id: str | None,
                         engine: str = "argos") -> Job:
        pair = f"{source}_{target}"
        key = f"translate-model:{engine}:{pair}"

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def report(frac: float, message: str) -> None:
                job.report(frac, message)
                self._push(session_id, notify, key, f"running:{frac:.2f}",
                           {"event": "subs-translate-model", "pair": pair, "engine": engine, "job": job.to_dict()})

            def progress(frac: float, message: str) -> None:   # from the download thread
                loop.call_soon_threadsafe(report, frac, message)

            try:
                if engine == "opus-big":
                    path = await self.opus.download(source, target, progress=progress)
                else:
                    path = await self.argos.download(source, target, progress=progress)
            except (TranslateError, opus_mod.OpusError, OSError, ValueError) as exc:
                self._push(session_id, notify, key, "failed",
                           {"event": "subs-translate-model", "pair": pair, "engine": engine,
                            "job": {**job.to_dict(), "status": "failed", "error": str(exc)}}, final=True)
                raise TranslateError(str(exc)) from exc
            self._push(session_id, notify, key, "done",
                       {"event": "subs-translate-model", "pair": pair, "engine": engine,
                        "job": {**job.to_dict(), "status": "done"}}, final=True)
            return {"pair": pair, "engine": engine, "path": str(path)}

        return self.server.jobs.submit(f"subs.translate.download.{engine}.{pair}", body, priority=Priority.INTERACTIVE,
                                       heavy=False, session_id=None, meta={"notify": notify, "pair": pair,
                                                                           "engine": engine})

    # -- embedded tracks -----------------------------------------------------------------------------------------

    def _extract_dest(self, key: str, ff_index: int) -> Path:
        d = self.out_dir / safe_name(key)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"track{int(ff_index)}.srt"

    async def extract(self, path: str, ff_index: int, codec: str | None = None, notify: str = "mu_subs",
                      session_id: str | None = None) -> dict[str, Any]:
        """Embedded text track → SRT in the cache (instant when already extracted, else an ffmpeg job)."""
        if is_url(path):
            raise RpcError(UNAVAILABLE, "extraer pistas internas solo funciona con archivos locales")
        if codec and codec in IMAGE_CODECS:
            raise RpcError(UNAVAILABLE, IMAGE_ERROR, {"reason": "image", "codec": codec})
        src = str(local_path(path))
        key = await self._key(src)
        try:
            stream = await asyncio.to_thread(subtitle_stream, src, int(ff_index))
        except SaveError as exc:
            raise RpcError(NOT_FOUND, str(exc)) from exc
        codec = str(stream.get("codec_name") or codec or "")
        lang = str((stream.get("tags") or {}).get("language") or "")
        if codec in IMAGE_CODECS:
            raise RpcError(UNAVAILABLE, IMAGE_ERROR, {"reason": "image", "codec": codec})
        dest = self._extract_dest(key, ff_index)
        base = {"path": src, "ff_index": int(ff_index), "codec": codec, "lang": lang, "srt": str(dest)}
        if dest.is_file() and dest.stat().st_size > 0:
            return {"status": "done", "cached": True, **base}
        jkey = f"{key}:{int(ff_index)}"
        running = self.extracting.get(jkey)
        if running is not None and running.status in (Status.QUEUED, Status.RUNNING):
            return {"status": "queued", "job": running.to_dict(), **base}
        duration = await asyncio.to_thread(probe_duration, src)

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            push_key = "extract:" + job.id

            def progress(frac: float) -> None:
                def _r() -> None:
                    job.report(frac, f"{int(frac * 100)} %")
                    self._push(session_id, notify, push_key, f"running:{frac:.2f}",
                               {"event": "subs-extract", "job": job.to_dict(), **base})
                loop.call_soon_threadsafe(_r)

            try:
                cues = await extract_srt(src, int(ff_index), dest, duration, progress)
            except (SaveError, OSError) as exc:
                self._push(session_id, notify, push_key, "failed", {"event": "subs-extract", **base,
                           "job": {**job.to_dict(), "status": "failed", "error": str(exc)}}, final=True)
                raise
            finally:
                self.extracting.pop(jkey, None)
            result = {"status": "done", "cached": False, "cues": cues, **base}
            job.report(1.0, "listo")
            self._push(session_id, notify, push_key, "done", {"event": "subs-extract", **base, "result": result,
                                                             "job": {**job.to_dict(), "status": "done"}}, final=True)
            return result

        job = self.server.jobs.submit(f"subs.extract.{int(ff_index)}", body, priority=Priority.INTERACTIVE, heavy=False,
                                      session_id=None, meta={"notify": notify, "path": src, "ff_index": int(ff_index)})
        self.extracting[jkey] = job
        return {"status": "queued", "job": job.to_dict(), **base}

    # -- saving ------------------------------------------------------------------------------------------------------

    def _ai_task(self, path: str, srt: str | None) -> AsrTask | None:
        tasks = list(self.server.asr.tasks.values())
        if srt:
            hit = [t for t in tasks if t.srt_path is not None and str(t.srt_path) == srt]
            if hit:
                return hit[0]
        p = str(local_path(path)) if path else ""
        pool = [t for t in tasks if t.path == p and t.model != "injected"] or [t for t in tasks if t.path == p]
        if not pool:
            return None
        return max(pool, key=lambda t: (t.complete, len(t.done), t.updated_at))

    @staticmethod
    def _coverage(task: AsrTask) -> float:
        if task.complete:
            return 1.0
        if not task.chunks or task.duration <= 0:
            return 0.0
        covered = sum(task.chunks[i][1] - task.chunks[i][0] for i in task.done if 0 <= i < len(task.chunks))
        return round(min(1.0, covered / task.duration), 4)

    async def _write_file(self, path: str, text: str, lines: int, lang: str, kind: str, dest_dir: str | None,
                          overwrite: bool, title: str | None, avoid: tuple[Path, ...] = ()) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            folder, fallback = target_dir(path, dest_dir)
            dest = choose_name(folder, video_stem(path, title), lang, kind, overwrite, avoid)
            try:
                write_atomic(dest, text)
            except OSError:
                if fallback or dest_dir:
                    raise
                folder, fallback = target_dir(path, None, force_fallback=True)   # the folder refused the write
                dest = choose_name(folder, video_stem(path, title), lang, kind, overwrite, avoid)
                write_atomic(dest, text)
            return {"status": "done", "path": str(dest), "name": dest.name, "dir": str(folder), "fallback": fallback,
                    "lines": lines, "kind": kind, "lang": lang}

        try:
            return await asyncio.to_thread(_do)
        except (SaveError, OSError) as exc:
            raise RpcError(UNAVAILABLE, f"no se pudo guardar: {exc}") from exc

    async def save(self, path: str, kind: str, lang: str = "", srt: str | None = None, ff_index: int | None = None,
                   codec: str | None = None, dest_dir: str | None = None, overwrite: bool = False,
                   title: str | None = None, allow_partial: bool = False, complete: bool = False,
                   notify: str = "mu_subs", session_id: str | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise RpcError(INVALID_PARAMS, f"tipo desconocido {kind!r} ({', '.join(KINDS)})")
        lang = normalize_lang(lang)
        lang = "" if lang == "auto" else lang
        if kind == "ai":
            task = self._ai_task(path, srt)
            if task is None:
                raise RpcError(NOT_FOUND, "no hay pista IA para este archivo (inicia los subtítulos IA)")
            lang = lang or task.detected or (task.language if task.language != "auto" else "")
            coverage = self._coverage(task)
            if task.complete and not overwrite and not dest_dir:
                prev = next((x for x in reversed(task.saved) if x.get("complete") and x.get("cues") == len(task.segments)
                             and Path(str(x.get("path"))).is_file()), None)
                if prev is not None:     # same finished transcription already saved: do not litter copies
                    pp = Path(str(prev["path"]))
                    return {"status": "done", "path": str(pp), "name": pp.name, "dir": str(pp.parent),
                            "fallback": False, "lines": len(task.segments), "kind": kind, "lang": lang,
                            "partial": False, "coverage": 1.0, "task": task.id, "already": True}
            if not task.complete and complete:
                return await self._complete_then_save(task, lang, dest_dir, overwrite, title, notify, session_id)
            if not task.complete and not allow_partial:
                return {"status": "partial", "coverage": coverage, "task": task.id, "lines": len(task.segments),
                        "lang": lang, "kind": kind}
            if not task.segments:
                raise RpcError(UNAVAILABLE, "la pista IA todavía no tiene texto")
            res = await self._write_file(path, render_srt(task.segments), len(task.segments), lang, kind, dest_dir,
                                         overwrite, title)
            res.update({"partial": not task.complete, "coverage": coverage, "task": task.id})
            self.server.asr.mark_saved(task, res["path"], task.complete)
            return res
        if kind == "track" and ff_index is not None and not srt:
            if codec and codec in IMAGE_CODECS:
                raise RpcError(UNAVAILABLE, IMAGE_ERROR, {"reason": "image", "codec": codec})
            ex = await self.extract(path, int(ff_index), codec, notify, session_id)
            lang = lang or normalize_lang(ex.get("lang", ""))
            if ex["status"] != "done":
                return await self._save_after_extract(ex, path, lang, dest_dir, overwrite, title, notify, session_id)
            srt = ex["srt"]
        if not srt:
            raise RpcError(INVALID_PARAMS, "falta el archivo de subtítulos (srt)")
        if not Path(srt).is_file():
            raise RpcError(NOT_FOUND, f"no existe: {srt}")
        try:
            text, lines = await asyncio.to_thread(srt_text, srt)
        except SaveError as exc:
            raise RpcError(NOT_FOUND, str(exc)) from exc
        src = Path(srt)
        if (kind == "track" and not dest_dir and not is_url(path) and src.suffix.lower() == ".srt"
                and src.parent.resolve() == local_path(path).parent.resolve()
                and src.name.startswith(video_stem(path, title) + ".")):
            # the selected track already is an SRT next to the video: nothing to write
            return {"status": "done", "path": str(src), "name": src.name, "dir": str(src.parent), "fallback": False,
                    "lines": lines, "kind": kind, "lang": lang, "already": True}
        return await self._write_file(path, text, lines, lang, kind, dest_dir, overwrite, title, (src,))

    async def _save_after_extract(self, ex: dict[str, Any], path: str, lang: str, dest_dir: str | None,
                                  overwrite: bool, title: str | None, notify: str,
                                  session_id: str | None) -> dict[str, Any]:
        job = self.server.jobs.get(ex["job"]["id"])

        async def finish() -> None:
            push_key = "save:track:" + ex["job"]["id"]
            try:
                if job is not None:
                    await job.wait()
                if job is not None and job.status != Status.DONE:
                    raise SaveError(job.error or "la extracción falló")
                text, lines = await asyncio.to_thread(srt_text, ex["srt"])
                res = await self._write_file(path, text, lines, lang, "track", dest_dir, overwrite, title)
                self._push(session_id, notify, push_key, "done", {"event": "subs-save", "result": res}, final=True)
            except (SaveError, RpcError, OSError) as exc:
                msg = exc.message if isinstance(exc, RpcError) else str(exc)
                self._push(session_id, notify, push_key, "failed", {"event": "subs-save", "kind": "track",
                                                                    "error": msg}, final=True)

        self._spawn(finish())
        return {"status": "queued", "kind": "track", "job": ex["job"], "srt": ex["srt"], "lang": lang}

    async def _complete_then_save(self, task: AsrTask, lang: str, dest_dir: str | None, overwrite: bool,
                                  title: str | None, notify: str, session_id: str | None) -> dict[str, Any]:
        """Let the AI task finish (resuming it at low priority if it was stopped) and save it when complete; progress
        and the result arrive as ``subs-save`` events. A watcher coroutine, not a job: it must not hold a worker."""
        asr = self.server.asr
        if task.status not in ("queued", "running"):
            task = await asr.start(task.path, task.language, task.model, "precompute", task.pos, task.audio_track,
                                   task.translate, task.chunk_seconds, notify, session_id)
        push_key = "save:ai:" + task.id

        async def watch() -> None:
            restarts = 0
            try:
                while not task.complete:
                    await asyncio.sleep(1.0)
                    self._push(session_id, notify, push_key, f"waiting:{self._coverage(task):.2f}",
                               {"event": "subs-save", "kind": "ai", "status": "waiting", "task": task.id,
                                "coverage": self._coverage(task)})
                    if task.status in ("failed", "cancelled") and not task.complete:
                        if task.status == "failed" or restarts >= 3:
                            raise SaveError(task.error or "la transcripción se detuvo")
                        restarts += 1
                        await asr.start(task.path, task.language, task.model, "precompute", task.pos,
                                        task.audio_track, task.translate, task.chunk_seconds, notify, None)
                res = await self._write_file(task.path, render_srt(task.segments), len(task.segments), lang, "ai",
                                             dest_dir, overwrite, title)
                res.update({"partial": False, "coverage": 1.0, "task": task.id})
                asr.mark_saved(task, res["path"], True)
                self._push(session_id, notify, push_key, "done", {"event": "subs-save", "result": res}, final=True)
            except (SaveError, RpcError, OSError) as exc:
                msg = exc.message if isinstance(exc, RpcError) else str(exc)
                self._push(session_id, notify, push_key, "failed", {"event": "subs-save", "kind": "ai", "error": msg},
                           final=True)

        self._spawn(watch())
        return {"status": "waiting", "kind": "ai", "task": task.id, "coverage": self._coverage(task), "lang": lang}

    def _spawn(self, coro: Any) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self.waiting.add(t)
        t.add_done_callback(self.waiting.discard)

    async def close(self) -> None:
        for t in list(self.waiting):
            t.cancel()
            with contextlib.suppress(BaseException):
                await t


def _text_hash(text: str) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def register(server: MpvdServer, service: SubsService) -> None:  # noqa: C901 - flat list of handlers
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
                        notify: str = "mu_subs", engine: str = "auto") -> dict[str, Any]:
        """Translate a subtitle file offline. ``engine``: auto (OPUS-MT when downloaded for the pair, else Argos) |
        argos | opus-big. Cached → ``done`` at once; else a job that pushes ``subs-translate`` events to ``notify``.
        Missing models → error with ``data.missing = [[source, target, engine], …]``."""
        return await service.translate(srt, source, target, path, notify, _sid(ctx), engine)

    @d.method("subs.find")
    async def find(ctx: RpcContext, path: str, languages: str | None = None) -> dict[str, Any]:
        """Cascada de proveedores (C5): todo lo que hay para este archivo o URL, en una lista ordenada por fiabilidad
        (canal · hash · nombre · auto), más qué proveedores hay y por qué alguno no está disponible."""
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        return await service.find(path, languages)

    @d.method("subs.pick")
    async def pick(ctx: RpcContext, source: dict[str, Any], notify: str = "mu_subs") -> dict[str, Any]:
        """Trae uno de los resultados de ``subs.find`` (el campo ``pick`` tal cual) como SRT listo para `sub-add`."""
        return await service.pick(source, notify, _sid(ctx))

    @d.method("subs.web.list")
    async def web_list(ctx: RpcContext, url: str, prefer: str = "es") -> dict[str, Any]:
        """Subtitles an internet video offers (manual + automatic in its own language; never the site's machine
        translations): ``{tracks: [{lang, kind, label, ext}]}`` (H29)."""
        return await service.web_list(url, prefer)

    @d.method("subs.web.fetch")
    async def web_fetch(ctx: RpcContext, url: str, lang: str, kind: str = "manual") -> dict[str, Any]:
        """One web subtitle track → clean SRT in the cache (``srt``, ``title`` for sub-add); translate it offline with
        subs.translate and save it with subs.save like any external track."""
        return await service.web_fetch(url, lang, kind)

    @d.method("subs.translate.models")
    async def translate_models(ctx: RpcContext, index: bool = False) -> dict[str, Any]:
        """Translation engines (Argos, OPUS-MT big: which pairs are downloaded) and Argos packages (with ``index``,
        the whole official index)."""
        st = service.translate_status()
        if index:
            st["packages"] = [p.to_dict() for p in await asyncio.to_thread(service.argos.catalogue, True)]
        return st

    @d.method("subs.translate.download")
    async def translate_download(ctx: RpcContext, source: str, target: str, notify: str = "mu_subs",
                                 engine: str = "argos") -> dict[str, Any]:
        """Download a translation model in the background (progress pushed as ``subs-translate-model``). OPUS-MT big
        is downloaded (~860 MB) and converted to CTranslate2 int8 (~234 MB) once."""
        if not (LANG_RE.match(source or "") and LANG_RE.match(target or "")):
            raise RpcError(INVALID_PARAMS, "códigos de idioma ISO 639-1")
        if engine not in ("argos", "opus-big"):
            raise RpcError(INVALID_PARAMS, "motor: argos u opus-big")
        if engine == "opus-big" and opus_mod.model_for(source, target) is None:
            raise RpcError(INVALID_PARAMS, f"OPUS-MT no cubre {source}→{target} (solo es/ca/fr ↔ en)")
        return service.download_package(source, target, notify, _sid(ctx), engine).to_dict()

    @d.method("subs.translate.remove")
    async def translate_remove(ctx: RpcContext, source: str, target: str, engine: str = "argos") -> dict[str, Any]:
        """Delete a downloaded translation model."""
        service.translator.unload()
        service.opus_engine.unload()
        if engine == "opus-big":
            return {"removed": service.opus.remove(source, target)}
        return {"removed": service.argos.remove(source, target)}

    @d.method("subs.extract")
    async def extract(ctx: RpcContext, path: str, ff_index: int, codec: str | None = None,
                      notify: str = "mu_subs") -> dict[str, Any]:
        """Embedded text subtitle track (``ff_index`` = mpv ``track-list/N/ff-index``) → SRT in the cache, so it can be
        translated, resynchronised or saved. ``done`` when already extracted, else ``queued`` + ``subs-extract``
        events. Bitmap tracks (PGS, VobSub, DVB) → error: they need OCR."""
        return await service.extract(path, int(ff_index), codec, notify, _sid(ctx))

    @d.method("subs.save")
    async def save(ctx: RpcContext, path: str, kind: str, lang: str = "", srt: str | None = None,
                   ff_index: int | None = None, codec: str | None = None, dest_dir: str | None = None,
                   overwrite: bool = False, title: str | None = None, allow_partial: bool = False,
                   complete: bool = False, notify: str = "mu_subs") -> dict[str, Any]:
        """Save subtitles as ``<video>.<lang>.srt`` next to the video (``kind``: ai | translation | resync | track).

        ASS/VTT are converted to SRT; embedded tracks are extracted first (``queued`` + ``subs-save`` event).
        An unfinished AI track returns ``partial`` with its ``coverage`` unless ``allow_partial`` (save what there is)
        or ``complete`` (finish transcribing, then save: ``waiting`` + ``subs-save`` events). Unwritable folder or URL
        → ``~/Vídeos/MPV-UOS/Subtítulos`` (``fallback: true``)."""
        return await service.save(path, kind, lang, srt, ff_index, codec, dest_dir, bool(overwrite), title,
                                  bool(allow_partial), bool(complete), notify, _sid(ctx))
