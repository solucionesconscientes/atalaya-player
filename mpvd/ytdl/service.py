"""yt-dlp service: binary status + daily updater, ``-J`` analysis with cache, presets and the download queue.

Exposed as ``ytdl.*`` JSON-RPC methods (see ``register``). Download progress is pushed to every connected mpv as
``script-message-to <notify> mu-event {"event":"download","download":{...}}`` (default target: ``mu_ytdl``).
"""

from __future__ import annotations

import asyncio
import json
import importlib.util
import logging
import re
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import url_key
from mpvd.jobs import Priority
from mpvd.net import HttpCache
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd import hwdecode
from mpvd.ytdl import info as info_mod
from mpvd.ytdl.binary import (
    platform_asset_name,
    NIGHTLY_RELEASES_URL,
    YtdlpBinary,
    YtdlpUpdater,
    find_nightly,
    find_ytdlp,
    nightly_path,
    vendor_path,
)
from mpvd.ytdl import chapters, policy, sections, sites, transcode
from mpvd.ytdl import subscriptions as subs_mod
from mpvd.convert import hw as hwencode
from mpvd.ytdl.downloads import BROWSERS, FINAL, DownloadItem, DownloadManager, valid_browser
from mpvd.ytdl.presets import (
    preset_rows,
    sub_lang_rows,
    SUB_LANG_CHOICES,
    resolve_sub_langs,
    AUDIO_BITRATES,
    AUDIO_FORMATS,
    CONTAINERS,
    PRESETS,
    SPONSORBLOCK_MODES,
    DownloadSpec,
    catalogo,
    estimar,
    spec_conversion,
    spec_from_preset,
)

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.ytdl")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
MAX_BATCH = 500

INFO_ARTIFACT = "ytdl-info"
INFO_TTL = 6 * 3600.0          # googlevideo URLs expire after ~6 h (docs/YTDLP.md §7)
INFO_TIMEOUT = 120.0
DEFAULT_NOTIFY = "mu_ytdl"
AUTO_UPDATE_DELAY = 30.0
SEARCH_TTL = 3600.0            # in-memory cache per (query, limit)
SEARCH_TIMEOUT = 30.0
SEARCH_MAX_RESULTS = 50
SEARCH_CACHE_SIZE = 200
SUBS_TICK = 60.0               # cada cuánto se mira si alguna suscripción toca (no es el intervalo de cada canal)


class YtdlService:
    def __init__(self, server: MpvdServer):
        self.server = server
        settings = server.settings
        self.root: Path | None = server.root
        self.http = HttpCache(settings.cache_dir / "http")
        self._binary: YtdlpBinary | None = None
        self._binary_checked = 0.0
        target = vendor_path(self.root) or (settings.data_dir / "bin" / platform_asset_name())
        self.updater = YtdlpUpdater(self.http, target)
        self.nightly_updater = YtdlpUpdater(self.http, nightly_path(self.root, settings.data_dir),
                                            releases_url=os.environ.get("MPV_UOS_YTDLP_NIGHTLY_RELEASES_URL")
                                            or NIGHTLY_RELEASES_URL)
        self._nightly_lock = asyncio.Lock()
        self.downloads = DownloadManager(server.jobs, self.binary, settings.data_dir, on_change=self._download_changed,
                                         fallback=self.ensure_nightly, vcodec=lambda: hwdecode.detect()["sort"])
        self._broken: set[str] = set()        # extractores «CURRENTLY BROKEN» del binario actual (H37/D4)
        self._broken_for = ""
        self._info_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._search_tasks: dict[tuple[str, int], asyncio.Task[dict[str, Any]]] = {}
        self._search_cache: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}
        self._auto_task: asyncio.Task[None] | None = None
        self.last_update_check: dict[str, Any] = {}
        self.downloads.final_hooks.append(self._unir_trozos_al_terminar)
        self.downloads.final_hooks.append(self._convertir_al_terminar)
        # F4: suscripciones por feed (reglas por canal, sondeo incremental) — sin API y sin cuentas
        self.subs = subs_mod.Suscripciones(settings.data_dir / "ytdl-suscripciones.json")
        self._subs_task: asyncio.Task[None] | None = None
        self._sondeo_lock = asyncio.Lock()
        self.ultimo_sondeo: dict[str, Any] = {}

    # -- binary -------------------------------------------------------------------------------

    def binary(self, refresh: bool = False) -> YtdlpBinary | None:
        now = time.monotonic()
        if refresh or self._binary is None or now - self._binary_checked > 30 or not self._binary.path.is_file():
            self._binary = find_ytdlp(self.root)
            self._binary_checked = now
        return self._binary

    async def ensure_nightly(self, max_age: float = 86400.0) -> YtdlpBinary | None:
        """The nightly yt-dlp, downloaded (SHA-256 verified) when missing or older than a day; None when it cannot
        be had (offline, GitHub down): the caller keeps the stable result."""
        async with self._nightly_lock:
            nb = find_nightly(self.root, self.server.settings.data_dir)
            if nb is not None and nb.source == "env-nightly":
                return nb
            target = self.nightly_updater.target
            fresh = nb is not None and time.time() - target.stat().st_mtime < max_age
            if fresh:
                return nb
            if os.environ.get("MPV_UOS_YTDLP_AUTO_UPDATE", "1") == "0" and nb is None:
                return None   # tests and offline installs: never download behind the user's back
            st = await asyncio.to_thread(self.nightly_updater.check, "", True)
            if not st.error:
                st = await asyncio.to_thread(self.nightly_updater.apply)
            if st.error:
                log.warning("ytdl: nightly unavailable: %s", st.error)
            return find_nightly(self.root, self.server.settings.data_dir)

    async def _nightly_info(self) -> dict[str, Any]:
        nb = find_nightly(self.root, self.server.settings.data_dir)
        return {"path": str(nb.path), "version": await nb.version()} if nb is not None else {"path": "", "version": ""}

    async def require_binary(self) -> YtdlpBinary:
        b = self.binary()
        if b is None:
            raise RpcError(UNAVAILABLE, "yt-dlp not found: run tools/vendor.sh (vendor/bin/yt-dlp) or set MPV_UOS_YTDLP")
        return b

    async def status(self) -> dict[str, Any]:
        b = self.binary()
        version = await b.version() if b is not None else None
        st = self.updater.state
        return {
            "available": b is not None,
            "binary": b.to_dict() if b is not None else None,
            "version": version,
            "update": {**st.to_dict(), "auto": self.auto_update_enabled()},
            "settings": self.downloads.settings.to_dict(),
            "downloads_active": self.downloads.active(),
            "hook": self.hook_config(),
            # yt-dlp impersonates a browser (TikTok and others) only with curl_cffi importable by its Python
            "impersonate": importlib.util.find_spec("curl_cffi") is not None,
            "nightly": await self._nightly_info(),
        }

    def hook_config(self) -> dict[str, Any]:
        """What mu-ytdl should hand to mpv's ytdl_hook: the binary path and ``ytdl-raw-options`` entries."""
        b = self.binary()
        raw: dict[str, str] = {}
        if b is not None and b.js_runtime is not None:
            raw["js-runtimes"] = f"{b.js_runtime.name}:{b.js_runtime.path}"
        return {"ytdl_path": str(b.path) if b is not None else "", "raw_options": raw,
                "cookies_browser": self.downloads.settings.cookies_browser
                if valid_browser(self.downloads.settings.cookies_browser) else "",
                "python": b.argv[0] if b is not None and len(b.argv) > 1 else ""}

    # -- updater ------------------------------------------------------------------------------

    def auto_update_enabled(self) -> bool:
        if os.environ.get("MPV_UOS_YTDLP_AUTO_UPDATE", "1") == "0":
            return False
        return bool(self.downloads.settings.auto_update)

    async def update_check(self, force: bool = False) -> dict[str, Any]:
        b = self.binary()
        installed = await b.version() if b is not None else ""
        st = await asyncio.to_thread(self.updater.check, installed, force)
        return {**st.to_dict(), "auto": self.auto_update_enabled()}

    async def update_apply(self) -> dict[str, Any]:
        st = await asyncio.to_thread(self.updater.apply)
        if not st.error:
            b = self.binary(refresh=True)
            if b is not None:
                await b.version(refresh=True)
        return {**st.to_dict(), "auto": self.auto_update_enabled()}

    async def start(self) -> None:
        n = self.downloads.resume_pending()
        if n:
            log.info("ytdl: %d unfinished download(s) resumed", n)
        if self.auto_update_enabled():
            self._auto_task = asyncio.create_task(self._auto_update(), name="mpvd-ytdl-autoupdate")
        if os.environ.get("MPV_UOS_YTDL_SUBS", "1") != "0":
            # el sondeo de suscripciones: cada minuto mira si alguna toca (30 min / 6 h / diario, y su ventana de horas)
            self._subs_task = asyncio.create_task(self._bucle_subs(), name="mpvd-ytdl-subs")

    async def _bucle_subs(self) -> None:
        try:
            while True:
                await asyncio.sleep(SUBS_TICK)
                if not self.subs.items:
                    continue
                try:
                    await self.sondear()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - un sondeo roto no puede tumbar el daemon
                    log.exception("ytdl: sondeo de suscripciones falló")
        except asyncio.CancelledError:
            return

    # -- suscripciones (F4) ---------------------------------------------------------------------

    async def sondear(self, sid: str | None = None, forzar: bool = False,
                      fetcher: Any = None) -> dict[str, Any]:
        """Comprueba los canales que tocan (o uno con ``sid`` / ``forzar``) y encola solo lo nuevo.

        Solo mira el feed y el índice: no descarga metadatos de cada vídeo, así que una comprobación de 15 entradas
        cuesta una petición. Lo que el feed no dice (la duración) se filtra al bajar.
        """
        async with self._sondeo_lock:
            if sid:
                s = self.subs.get(sid)
                objetivos = [s] if s is not None else []
                if s is None:
                    raise RpcError(NOT_FOUND, f"unknown subscription: {sid}")
            elif forzar:
                objetivos = list(self.subs.items)
            else:
                objetivos = self.subs.debidas()
            out: list[dict[str, Any]] = []
            total_nuevos = 0
            indice = self.downloads.index
            for s in objetivos:
                if not s.activa or not s.regla.en_ventana():
                    continue
                try:
                    datos = await asyncio.to_thread(subs_mod.leer_feed, s.canal, fetcher)
                    filas = subs_mod.entradas(datos)
                    pendientes = subs_mod.nuevos(s, filas, indice)
                    for fila in pendientes:
                        spec = subs_mod.spec_de(fila, s)
                        self.downloads.submit(spec, out_dir=s.regla.carpeta or None, notify=DEFAULT_NOTIFY)
                    # se marca visto todo lo que el feed trae menos lo que se quedó fuera del tope: eso sigue
                    # pendiente para el próximo sondeo (si no, un canal con 15 novedades perdería las de después del 10)
                    en_cola = {f.get("video_id") for f in pendientes}
                    vistos_ahora = set(s.vistos)
                    atrasados = {f.get("video_id") for f in filas
                                 if f.get("video_id") and f.get("video_id") not in en_cola
                                 and f.get("video_id") not in vistos_ahora and s.regla.pasa(f)
                                 and (indice is None or indice.ya_descargado(str(f.get("video_id"))) is None)}
                    vistos = [f["video_id"] for f in filas
                              if f.get("video_id") and f.get("video_id") not in atrasados]
                    self.subs.anotar(s.id, nuevos=len(pendientes), vistos=vistos)
                    total_nuevos += len(pendientes)
                    out.append({"id": s.id, "canal": s.canal, "titulo": s.titulo, "nuevos": len(pendientes),
                                "encolados": [f.get("url") for f in pendientes], "error": ""})
                except (subs_mod.SubError, OSError, ValueError) as exc:
                    self.subs.anotar(s.id, nuevos=0, error=str(exc))
                    out.append({"id": s.id, "canal": s.canal, "titulo": s.titulo, "nuevos": 0, "encolados": [],
                                "error": str(exc)})
            self.ultimo_sondeo = {"cuando": time.time(), "nuevos": total_nuevos, "canales": out}
            return {"sondeadas": len(out), "nuevos": total_nuevos, "canales": out}

    def subs_lista(self) -> dict[str, Any]:
        idx = self.downloads.index
        total = idx.total() if idx is not None else {"items": 0, "bytes": 0}
        return {"suscripciones": self.subs.lista(), "ultimo_sondeo": self.ultimo_sondeo,
                "indice": total, "intervalos": list(subs_mod.INTERVALOS),
                "perfiles": list(subs_mod.PERFILES_SUSCRIPCION)}

    def subs_add(self, url: str, titulo: str = "", **cambios: Any) -> dict[str, Any]:
        try:
            s = self.subs.add(url, titulo=titulo, **cambios)
        except subs_mod.SubError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        return {**s.to_dict(), "estado": s.estado()}

    def subs_update(self, id: str, **cambios: Any) -> dict[str, Any]:  # noqa: A002
        try:
            s = self.subs.update(id, **cambios)
        except subs_mod.SubError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        return {**s.to_dict(), "estado": s.estado()}

    def subs_borrar(self, id: str) -> dict[str, Any]:  # noqa: A002
        return {"removed": self.subs.borrar(id)}

    def subs_historico(self, id: str, desde: float | None = None, hasta: float | None = None) -> dict[str, Any]:
        """Fondo histórico de un canal: todo lo anterior a lo que ya hay, con archivo y corte en lo conocido."""
        s = self.subs.get(id)
        if s is None:
            raise RpcError(NOT_FOUND, f"unknown subscription: {id}")
        spec = subs_mod.spec_historico(s.url or s.canal, s, desde=desde, hasta=hasta,
                                       archivo=self.downloads.archive_path)
        item = self.downloads.submit(spec, out_dir=s.regla.carpeta or None, notify=DEFAULT_NOTIFY)
        return item.to_dict()

    def indice_historial(self, n: int = 20, canal: str | None = None) -> dict[str, Any]:
        idx = self.downloads.index
        if idx is None:
            return {"items": [], "total": {"items": 0, "bytes": 0}}
        return {"items": [i.to_dict() for i in idx.historial(n, canal=canal)], "total": idx.total()}

    def indice_faltan(self, ids: list[str], perfil: str | None = None) -> dict[str, Any]:
        idx = self.downloads.index
        if idx is None:
            return {"faltan": list(ids), "ya": []}
        faltan = idx.faltan(ids, perfil=perfil)
        return {"faltan": faltan, "ya": [i for i in ids if i not in set(faltan)]}

    def ficha_lista(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """«32 vídeos · ~6,4 GB · solo los que no tengas», con lo que el índice ya tiene y un tamaño de referencia.

        El tamaño es una estimación a 2,4 Mbit/s (720p VP9 + Opus): sirve para que el usuario sepa si cabe, no para
        prometer bytes. Con índice solo cuenta lo que falta, que es lo que se va a bajar.
        """
        idx = self.downloads.index
        ids = [str(r.get("id") or "") for r in rows if r.get("id")]
        faltan = set(idx.faltan(ids)) if idx is not None and ids else set(ids)
        if idx is not None and ids:
            rows = [r for r in rows if not r.get("id") or str(r.get("id")) in faltan]
        bytes_ = sum(int(float(r.get("duration") or 0) * 300_000) for r in rows)
        n = len(rows)
        ya = len(ids) - len(faltan) if ids else 0
        partes = [t("%d vídeos") % n]
        if bytes_:
            partes.append("~%.1f GB" % (bytes_ / 1e9) if bytes_ >= 1e9 else "~%d MB" % (bytes_ / 1e6))
        if ya:
            partes.append(t("solo los que no tengas"))
        return {"count": n, "bytes": bytes_, "already": ya, "text": " · ".join(partes)}

    async def _auto_update(self) -> None:
        await asyncio.sleep(float(os.environ.get("MPV_UOS_YTDLP_AUTO_UPDATE_DELAY", AUTO_UPDATE_DELAY)))
        b = self.binary()
        if b is None or b.source == "system":
            return  # nothing we own to replace

        async def body(job: Any) -> dict[str, Any]:
            st = await self.update_check()
            if st.get("update_available"):
                job.report(0.5, f"actualizando yt-dlp a {st.get('latest')}")
                st = await self.update_apply()
            self.last_update_check = st
            if self.downloads.settings.nightly_fallback:
                await self.ensure_nightly()   # ready before a site breaks (playback retries cannot wait long)
            return st

        self.server.jobs.submit("ytdl.update", body, priority=Priority.INDEX, heavy=False)

    async def close(self) -> None:
        if self._auto_task is not None:
            self._auto_task.cancel()
        if self._subs_task is not None:
            self._subs_task.cancel()
        # stopping mpvd is not the viewer cancelling: the queue must be there on the next start (H19)
        await self.downloads.cancel_all(closing=True)

    # -- info (-J) ----------------------------------------------------------------------------

    async def _run_json(self, args: list[str], timeout: float = INFO_TIMEOUT) -> dict[str, Any]:
        b = await self.require_binary()
        cmd = b.command(*self.downloads.settings.session_args(), *args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()  # reap it (no zombie, no transport left behind)
            raise RpcError(UNAVAILABLE, f"yt-dlp timed out after {timeout:.0f}s") from None
        text = out.decode("utf-8", "replace").strip()
        if proc.returncode != 0 or not text:
            lines = [ln for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()]
            detail = next((ln for ln in reversed(lines) if ln.startswith("ERROR")), lines[-1] if lines else "")
            raise RpcError(UNAVAILABLE, detail or f"yt-dlp exited with {proc.returncode}")
        try:
            data = json.loads(text.splitlines()[-1])
        except ValueError as exc:
            raise RpcError(UNAVAILABLE, f"yt-dlp returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise RpcError(UNAVAILABLE, t("yt-dlp returned no object"))
        return data

    async def raw_info(self, url: str, flat: bool = False, force: bool = False,
                       seed: str | None = None) -> dict[str, Any]:
        """``-J`` output for ``url`` (single item unless ``flat`` = playlist entries), cached per URL for INFO_TTL.

        ``seed``: raw ``-J`` stdout already obtained by mpv's ytdl_hook (``user-data/mpv/ytdl/json-subprocess-result``);
        used instead of running yt-dlp again when the cache is cold.
        """
        if not url or not isinstance(url, str):
            raise RpcError(INVALID_PARAMS, "url required")
        b = await self.require_binary()
        version = await b.version()
        key = url_key(url)
        params = {"flat": bool(flat)}
        cache = self.server.cache
        if not force:
            entry = await asyncio.to_thread(cache.get, key, INFO_ARTIFACT, "yt-dlp", version, params)
            if entry is not None and isinstance(entry.data, dict) and time.time() - entry.created_at < INFO_TTL:
                return entry.data
            if seed and not flat:
                try:
                    seeded = json.loads(seed)
                except ValueError:
                    seeded = None
                if isinstance(seeded, dict) and seeded.get("formats"):
                    await asyncio.to_thread(lambda: cache.put(key, INFO_ARTIFACT, model="yt-dlp", version=version,
                                                              params=params, data=seeded))
                    return seeded
        task_key = f"{key}:{int(flat)}"
        task = self._info_tasks.get(task_key)
        if task is None or task.done():
            args = ["-J", "--flat-playlist", "--yes-playlist"] if flat else ["-J", "--no-playlist"]
            task = asyncio.create_task(self._run_json([*args, "--", url]))
            self._info_tasks[task_key] = task
        try:
            data = await asyncio.shield(task)
        finally:
            if task.done():
                self._info_tasks.pop(task_key, None)
        await asyncio.to_thread(lambda: cache.put(key, INFO_ARTIFACT, model="yt-dlp", version=version, params=params,
                                                  data=data))
        return data

    async def list_entries(self, url: str, limit: int = 15, timeout: float = INFO_TIMEOUT) -> dict[str, Any]:
        """The first ``limit`` entries of a list or channel, flat and uncached (H23 subscriptions):
        ``--flat-playlist -J -I 1:N`` (``--playlist-end`` is not in the vendored ``--help`` any more; ``-I`` is), so
        a channel with thousands of videos costs one page. Returns the summary plus ``entries``."""
        n = max(1, min(200, int(limit)))
        data = await self._run_json(["-J", "--flat-playlist", "--yes-playlist", "-I", f"1:{n}", "--", url],
                                    timeout=timeout)
        return {**info_mod.summary(data), "raw_type": data.get("_type") or "video",
                "entries": info_mod.flat_entries(data)[:n]}

    async def analyze(self, url: str, force: bool = False, seed: str | None = None) -> dict[str, Any]:
        data = await self.raw_info(url, force=force, seed=seed)
        if (data.get("_type") or "video") == "playlist":
            entries = info_mod.flat_entries(data)
            return {**info_mod.summary(data), "entries": entries, "formats": {}, "counts": {},
                    "has_combined": False}
        hw = (await asyncio.to_thread(hwdecode.detect))["hw"]
        return {**info_mod.analyze(data, set(hw) if hw is not None else None), "url": url}

    # -- capítulos y fragmentos (F2) -----------------------------------------------------------

    async def chapters_of(self, url: str, force: bool = False,
                          expr: str | list[int] | None = None) -> dict[str, Any]:
        """Los capítulos que declara el origen: número, título, inicio, fin y duración.

        Con ``expr`` se devuelve además la selección ya resuelta (``2,4,7``, ``3-``, ``/entrevista/``), que es lo que
        la pantalla manda al descargar. Un vídeo sin capítulos no es un error: la lista viene vacía y la vista dice
        por qué, con la acción que lo resuelve.
        """
        datos = await self.raw_info(url, force=force)
        caps = chapters.capitulos(datos)
        out: dict[str, Any] = {"url": url, "title": datos.get("title") or "", "count": len(caps),
                               "capitulos": caps, "duration": float(datos.get("duration") or 0)}
        if expr not in (None, "", []):
            out["seleccion"] = [int(c["numero"]) for c in chapters.seleccionar(caps, expr)]
        return out

    async def sections_preview(self, url: str, expr: Any, perfil: str | None = None,
                               cola: float | None = None, duration: float | None = None) -> dict[str, Any]:
        """Los rangos normalizados, la orden que saldría y el tamaño estimado, antes de descargar.

        El tamaño sale de la ficha del perfil (``policy.plan``) repartida por la parte del vídeo que se pide: es una
        estimación, y se dice como tal en la pantalla.
        """
        rangos = sections.parsear(expr, sections.MARGEN_COLA if cola is None else float(cola))
        if not rangos:
            raise ValueError(t("no hay ningún rango de tiempo"))
        perfil = perfil if perfil in policy.PERFILES else sections.PERFIL_FRAGMENTOS
        datos = await self.raw_info(url)
        total_video = float(duration or datos.get("duration") or 0)
        plan = policy.plan(datos, perfil)
        suma = sections.total(rangos, total_video or None)
        cod = {str(r["id"]): str(r["familia"]) for r in policy.pistas(datos)["video"]}
        cod.update({str(r["id"]): str(r["familia"]) for r in policy.pistas(datos)["audio"]})
        bytes_est = None
        if plan.bytes_estimados and total_video > 0:
            bytes_est = int(plan.bytes_estimados * min(1.0, suma / total_video))
        return {"url": url, "perfil": perfil, "nota": plan.nota,
                "rangos": [r.to_dict(total_video or None) for r in rangos],
                "args": sections.args_rangos(rangos), "total": suma, "duration": total_video,
                "bytes": bytes_est, "union": len(rangos) > 1,
                "aviso": sections.precision(cod.get(plan.video_id or "", ""), cod.get(plan.audio_id or "", ""))}

    # -- fichas y pistas del origen (F3) --------------------------------------------------------

    async def plan_of(self, url: str, perfil: str = "video_720", video_id: str | None = None,
                      audio_id: str | None = None, destino: dict[str, Any] | None = None,
                      seed: str | None = None) -> dict[str, Any]:
        """La ficha previa: qué pistas, en qué contenedor, cuánto ocupa y cuánto tarda.

        ``destino`` (solo con el perfil «convertir») dice a qué se convierte: ``{"vcod": "hevc", "acod": "aac",
        "altura": 720, "crf": 28, "audio_bitrate": 128, "fps": None, "two_pass": False, "hardware": False,
        "audio_canales": "conservar", "loudness": "ninguna"}``. El tamaño del resultado lo estima
        ``presets.estimar`` y se dice que es una estimación; lo que se descarga son las pistas del origen.
        """
        if perfil not in policy.PERFILES:
            raise RpcError(INVALID_PARAMS, f"perfil de descarga desconocido: {perfil}")
        datos = await self.raw_info(url, seed=seed)
        destino = destino if isinstance(destino, dict) and perfil == "convertir" else None
        # la política primero: es la que rechaza con el motivo en castellano (pareja imposible, códec fuera del catálogo)
        d = policy.plan(datos, perfil, video_id, audio_id,
                        destino={"vcod": destino.get("vcod"), "acod": destino.get("acod"),
                                 "altura": destino.get("altura")} if destino else None)
        if destino:
            spec = spec_conversion(url, acod=destino.get("acod"), vcod=destino.get("vcod"),
                                   altura=destino.get("altura"), audio_bitrate=destino.get("audio_bitrate"),
                                   crf=destino.get("crf"), video_bitrate=destino.get("video_bitrate"),
                                   fps=destino.get("fps"), two_pass=bool(destino.get("two_pass")),
                                   hardware=bool(destino.get("hardware")),
                                   audio_canales=str(destino.get("audio_canales") or "conservar"),
                                   loudness=str(destino.get("loudness") or "ninguna"))
        else:
            spec = DownloadSpec(url=url, perfil=perfil, video_id=video_id, audio_id=audio_id,
                                kind="audio_original" if perfil == "opus_origen" else "video")
        est = estimar(spec, datos)
        return {**d.to_dict(), "url": url, "perfil": perfil,
                "duration": float(datos.get("duration") or 0.0) or None,
                "bytes": est.get("bytes"), "bytes_origen": est.get("bytes_origen"),
                "segundos": est.get("segundos"), "kbps": est.get("kbps"),
                "descripcion": est.get("descripcion"), "recodificar": bool(d.recodificar)}

    async def tracks_of(self, url: str, seed: str | None = None) -> dict[str, Any]:
        """Las pistas reales del origen, con lo que hace falta para elegir: códec, altura, fps, kbps, bytes, idioma.

        ``auto`` es la pareja que elegiría la política (VP9 ≤720p + el Opus del origen): la vista la marca «Todo a
        Auto» y así la primera pantalla ya dice qué saldría sin tocar nada.
        """
        datos = await self.raw_info(url, seed=seed)
        p = policy.pistas(datos)
        out: dict[str, Any] = {"url": url, "title": datos.get("title") or "",
                               "duration": float(datos.get("duration") or 0.0),
                               "video": p["video"], "audio": p["audio"],
                               "counts": {"video": len(p["video"]), "audio": len(p["audio"])}, "auto": None}
        try:
            out["auto"] = policy.plan(datos, "video_720").to_dict()
        except ValueError as exc:            # sin pistas separadas: la vista lo dice, no se inventa una pareja
            out["motivo"] = str(exc)
        return out

    # -- convertir al terminar (F3) -------------------------------------------------------------

    def _convertir_al_terminar(self, item: DownloadItem) -> None:
        """La conversión explícita se hace **después** de descargar y en una tarea aparte.

        La descarga baja la pista del origen tal cual (sin ``--recode-video`` ni ``--audio-format``): así el original
        existe si algo falla, y el trabajo de ffmpeg puede tardar sin bloquear la cola.
        """
        if item.status != "done" or not item.spec.convertir:
            return
        ficheros = [p for p in item.outputs if p and Path(p).is_file()]
        if not ficheros:
            return
        try:
            bucle = asyncio.get_running_loop()
        except RuntimeError:                      # sin bucle (llamada desde un hilo de pruebas): no se convierte
            return
        bucle.create_task(self._convertir(item, ficheros[0]), name="ytdl-convertir")

    async def _convertir(self, item: DownloadItem, origen: str) -> None:
        spec = item.spec
        dispositivo = None
        if spec.hardware:
            caps = await asyncio.to_thread(hwencode.detect)
            if caps.get("available") and spec.vcod_destino in (caps.get("codecs") or []):
                dispositivo = caps.get("device")
        ajustes: dict[str, Any] = {
            "vcodec": spec.vcod_destino, "acodec": spec.acod_destino, "crf": spec.crf,
            "video_bitrate": spec.video_bitrate, "audio_kbps": spec.audio_bitrate,
            "altura": spec.height, "fps": spec.fps, "two_pass": spec.two_pass, "hardware": dispositivo,
            "canales": spec.audio_canales, "loudness": spec.loudness,
        }
        item.stage, item.message = "convert", t("convirtiendo a %s…") % (
            policy.NOMBRE_CODEC.get(spec.vcod_destino or spec.acod_destino or "", "?"))
        self.downloads.changed(item)
        res = await asyncio.to_thread(transcode.convertir, Path(origen), Path(origen).with_suffix(""), **ajustes)
        if res.ok:
            item.outputs = [res.salida] + [p for p in item.outputs if p != origen]
            if Path(origen) != Path(res.salida):
                Path(origen).unlink(missing_ok=True)     # el original no se queda: era el paso intermedio
            item.message = t("listo: %s %s") % (res.contenedor.upper(), transcode.formato_bytes(res.bytes))
            if res.avisos:
                item.message += " · " + " · ".join(res.avisos)
        else:
            item.message = t("descargado, pero no se pudo convertir: %s") % res.error
            log.warning("ytdl: no se pudo convertir %s: %s", item.id, res.error)
        item.post = {**(item.post or {}), "conversion": res.to_dict()}
        self.downloads.changed(item)

    async def resolve_chapters(self, spec: DownloadSpec) -> None:
        """``"3-5"`` o ``[3, 5]`` → las filas del capítulo, para que la orden lleve rangos y no una suposición.

        La lista completa va en ``spec.extra["chapters_all"]`` porque es lo que decide si cabe ``--split-chapters``
        (todos) o hay que pedir un trozo por capítulo (unos pocos).
        """
        c = spec.chapters
        if not c or isinstance(c, bool):
            return
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], dict):
            spec.extra.setdefault("chapters_all", list(c))
        else:
            todos = chapters.capitulos(await self.raw_info(spec.url))
            spec.chapters = chapters.seleccionar(todos, c)
            spec.extra["chapters_all"] = todos
        if spec.chapters_mode == chapters.UNIDO:
            spec.sections_join = True

    def _unir_trozos_al_terminar(self, item: DownloadItem) -> None:
        """Los trozos que se pidieron unidos se unen al terminar, **sin recodificar**, y se cuenta cómo fue.

        Se hace en una tarea aparte: unir es un ffmpeg que puede tardar, y el cierre de la descarga no puede esperar
        a que termine para avisar de que el vídeo ya está.
        """
        if item.status != "done" or not item.spec.sections_join:
            return
        trozos = [p for p in item.outputs if p and Path(p).is_file()]
        if len(trozos) < 2:
            return
        try:
            bucle = asyncio.get_running_loop()
        except RuntimeError:                      # sin bucle (llamada desde un hilo de pruebas): no se une
            return
        bucle.create_task(self._unir(item, trozos), name="ytdl-unir")

    async def _unir(self, item: DownloadItem, trozos: list[str]) -> None:
        destino = Path(trozos[0]).with_name(chapters.nombre_unido(trozos))
        res = await asyncio.to_thread(chapters.unir, trozos, destino)
        if res.ok:
            item.outputs = [str(destino)] + [p for p in item.outputs if p not in trozos]
            item.message = t("unido en %s (%d trozos)") % (destino.name, len(trozos))
        else:
            item.message = t("no se pudo unir: %s") % (res.error or "")
            log.warning("ytdl: no se pudo unir %s: %s", item.id, res.error)
        item.post = {**(item.post or {}), "union": res.to_dict()}
        self.downloads.changed(item)

    # -- qué sabe abrir el yt-dlp instalado (H37/D4) -------------------------------------------

    async def site_support(self, url: str) -> dict[str, Any]:
        """Si esa URL tiene extractor, y qué decir si no. La lista de extractores roesos la da el propio binario
        (`--list-extractors`), y se consulta una vez por binario: no cambia mientras no se actualice."""
        if not isinstance(url, str) or not url.strip():
            raise RpcError(INVALID_PARAMS, "url required")
        b = self.binary()
        roto: set[str] = set()
        if b is not None:
            if self._broken_for != str(b.path):
                self._broken = await asyncio.to_thread(sites.broken_extractors, b)
                self._broken_for = str(b.path)
            roto = self._broken
        out = dict(sites.describe(url, roto))
        out["url"] = url
        out["private_hint"] = sites.PRIVATE_HINT
        return out

    # -- search -------------------------------------------------------------------------------

    async def search(self, query: str, limit: int = 15) -> list[dict[str, Any]]:
        """YouTube search: one ``yt-dlp --flat-playlist -J -- ytsearchN:<query>`` run (same binary, safe args and JS
        runtime as everything else), 30 s timeout, results cached in memory per (query, limit) for one hour.
        Concurrent identical searches share the same yt-dlp process; failures are not cached."""
        if not isinstance(query, str) or not query.strip():
            raise RpcError(INVALID_PARAMS, "query required")
        try:
            n = max(1, min(SEARCH_MAX_RESULTS, int(limit)))
        except (TypeError, ValueError):
            raise RpcError(INVALID_PARAMS, "limit must be an integer") from None
        q = " ".join(query.split())
        key = (q.casefold(), n)
        hit = self._search_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < SEARCH_TTL:
            return hit[1]
        task = self._search_tasks.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._run_json(["--flat-playlist", "-J", "--", f"ytsearch{n}:{q}"],
                                                      timeout=SEARCH_TIMEOUT))
            # the error still reaches the callers; this only avoids "exception never retrieved" if they all left
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._search_tasks[key] = task
        try:
            data = await asyncio.shield(task)
        finally:
            if task.done():
                self._search_tasks.pop(key, None)
        results = info_mod.search_results(data, n)
        now = time.monotonic()
        self._search_cache[key] = (now, results)
        if len(self._search_cache) > SEARCH_CACHE_SIZE:
            live = {k: v for k, v in self._search_cache.items() if now - v[0] < SEARCH_TTL}
            newest = sorted(live.items(), key=lambda kv: kv[1][0])[-SEARCH_CACHE_SIZE:]
            self._search_cache = dict(newest)
        return results

    # -- downloads ----------------------------------------------------------------------------

    def _download_changed(self, item: DownloadItem) -> None:
        target = item.notify or DEFAULT_NOTIFY
        payload = {"event": "download", "download": item.to_dict()}
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(target, "download:" + item.id, item.status, payload, min_interval=0.25,
                                   final=item.status in FINAL)

    async def resolve_langs(self, spec: DownloadSpec) -> None:
        """«orig» in the subtitle languages → the video's language (from its -J info; a list keeps ``.*-orig``)."""
        if "orig" not in spec.sub_langs.split(",") or not (spec.subtitles or spec.subs_mode == "only"):
            return
        language = None
        if not spec.playlist:
            try:
                data = await self.raw_info(spec.url)
                language = data.get("language") if isinstance(data, dict) else None
            except Exception as exc:  # noqa: BLE001 - no info: keep the other languages and YouTube's -orig
                log.info("ytdl: no info for the subtitle language of %s (%s)", spec.url, exc)
        spec.sub_langs = resolve_sub_langs(spec.sub_langs, language)

    def spec_from_params(self, params: dict[str, Any]) -> DownloadSpec:
        url = params.get("url")
        if not url:
            raise RpcError(INVALID_PARAMS, "url required")
        s = self.downloads.settings
        defaults = {"container": s.container, "sub_langs": s.sub_langs, "subtitles": s.subtitles,
                    "chapters": s.chapters, "thumbnail": s.thumbnail, "metadata": s.metadata,
                    "sponsorblock": s.sponsorblock}
        options = {**defaults, **(params.get("options") or {})}
        try:
            if params.get("preset"):
                spec = spec_from_preset(str(params["preset"]), str(url), options)
            else:
                spec_dict = {**defaults, **{k: v for k, v in params.items() if k not in ("preset", "options", "notify",
                                                                                         "title", "out_dir")}}
                spec_dict.update(params.get("options") or {})
                spec_dict["url"] = url
                spec = DownloadSpec.from_dict(spec_dict)
            given = {**params, **(params.get("options") or {})}
            if spec.playlist:   # a list or a channel: own numbered folder and no repeats, unless asked otherwise
                if "list_folder" not in given:
                    spec.list_folder = s.list_folders
                if "archive" not in given:
                    # en listas el archivo va puesto por defecto (F4): sin él, cada pasada vuelve a bajar lo mismo.
                    # Quien quiera repetir lo dice con «archive»: false.
                    spec.archive = True
                if "archive_file" not in given:
                    spec.extra["archive_file"] = str(self.downloads.archive_path)
            return spec
        except KeyError as exc:
            raise RpcError(NOT_FOUND, f"unknown preset: {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc


def register(server: MpvdServer, service: YtdlService) -> None:  # noqa: C901 - flat list of handlers
    d = server.dispatcher
    server.services["ytdl"] = True
    server.services["ytdl_search"] = True

    @d.method("ytdl.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """yt-dlp binary in use (path, version, JS runtime), update state, settings and hook config."""
        return await service.status()

    @d.method("ytdl.hook")
    async def hook(ctx: RpcContext) -> dict[str, Any]:
        """ytdl_path and ytdl-raw-options that mpv's ytdl_hook should use."""
        return service.hook_config()

    @d.method("ytdl.update.check")
    async def update_check(ctx: RpcContext, force: bool = False) -> dict[str, Any]:
        """Ask GitHub for the latest release (cached 24 h unless force)."""
        return await service.update_check(force=force)

    @d.method("ytdl.update.apply")
    async def update_apply(ctx: RpcContext) -> dict[str, Any]:
        """Download the latest release, verify SHA2-256SUMS and replace vendor/bin/yt-dlp."""
        if not service.updater.state.latest:
            await service.update_check()
        return await service.update_apply()

    @d.method("ytdl.info")
    async def info(ctx: RpcContext, url: str, force: bool = False, raw: bool = False,
                   seed: str | None = None) -> dict[str, Any]:
        """Media summary + all formats grouped (combined / video / audio) from ``yt-dlp -J`` (cached per URL).

        ``seed`` = raw JSON that mpv's ytdl_hook already fetched for this URL (avoids a second yt-dlp run)."""
        if raw:
            return await service.raw_info(url, force=force, seed=seed)
        return await service.analyze(url, force=force, seed=seed)

    @d.method("ytdl.chapters")
    async def ytdl_chapters(ctx: RpcContext, url: str, expr: str | list[int] | None = None,
                            force: bool = False) -> dict[str, Any]:
        """Capítulos del vídeo para la vista «Capítulos»: lista con número, título, inicio, fin y duración, y la
        selección ya resuelta cuando se pide una expresión (``3``, ``3-5``, ``2,4,7``, ``/entrevista/``)."""
        try:
            return await service.chapters_of(url, force=force, expr=expr)
        except ValueError as exc:                     # selección vacía, capítulo que no existe, regex rota
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    @d.method("ytdl.sections.preview")
    async def sections_preview(ctx: RpcContext, url: str, expr: str | list[str],
                               perfil: str | None = None, cola: float | None = None,
                               duration: float | None = None) -> dict[str, Any]:
        """Fragmentos: los rangos normalizados, el tamaño estimado y el aviso de precisión, antes de descargar."""
        try:
            return await service.sections_preview(url, expr, perfil=perfil, cola=cola, duration=duration)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    @d.method("ytdl.plan")
    async def ytdl_plan(ctx: RpcContext, url: str, perfil: str = "video_720", video_id: str | None = None,
                        audio_id: str | None = None, destino: dict[str, Any] | None = None,
                        seed: str | None = None) -> dict[str, Any]:
        """La ficha previa de una descarga: pistas, contenedor, tamaño y tiempo aproximados, **antes** de empezar.

        Con ``perfil`` «pistas» y sus dos ids se comprueba la pareja (contenedor y motivo si no cabe); con
        «convertir» y ``destino`` se dice a qué códec se convierte y cuánto va a ocupar el resultado.
        """
        try:
            return await service.plan_of(url, perfil=perfil, video_id=video_id, audio_id=audio_id,
                                         destino=destino, seed=seed)
        except ValueError as exc:                    # pareja imposible, pista que ya no está, perfil raro
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    @d.method("ytdl.pistas")
    async def ytdl_pistas(ctx: RpcContext, url: str, seed: str | None = None) -> dict[str, Any]:
        """Las pistas reales del origen (códec, altura, fps, kbps, bytes, idioma) y la pareja automática."""
        return await service.tracks_of(url, seed=seed)

    @d.method("ytdl.hw")
    async def hw(ctx: RpcContext) -> dict[str, Any]:
        """Video codecs this machine decodes in hardware (vainfo on Linux), the ``-S vcodec`` limit used for
        downloads and the ``ytdl-format`` mu-ytdl uses for playback (H31)."""
        det = await asyncio.to_thread(hwdecode.detect)
        hw_set = set(det["hw"]) if det["hw"] is not None else None
        return {**det, "playback_format": hwdecode.playback_format(hw_set)}

    @d.method("ytdl.playlist")
    async def playlist(ctx: RpcContext, url: str, force: bool = False) -> dict[str, Any]:
        """Flat playlist entries (``--flat-playlist -J``), con la ficha «N vídeos · ~X GB · solo los que no tengas»."""
        data = await service.raw_info(url, flat=True, force=force)
        rows = info_mod.flat_entries(data)
        return {**info_mod.summary(data), "entries": rows, "ficha": service.ficha_lista(rows)}

    @d.method("ytdl.search")
    async def search(ctx: RpcContext, query: str, limit: int = 15) -> list[dict[str, Any]]:
        """YouTube search (``ytsearchN:``, flat, 30 s timeout, cached 1 h per query):
        ``[{url, title, duration, channel, view_count, is_live}]``."""
        return await service.search(query, limit)

    @d.method("ytdl.site_support")
    async def site_support(ctx: RpcContext, url: str) -> dict[str, Any]:
        """H37/D4: qué se puede hacer con esa URL antes de intentarlo (guardados de Instagram, favoritos de TikTok,
        perfiles y colecciones), según los extractores del yt-dlp instalado."""
        return await service.site_support(url)

    @d.method("ytdl.presets")
    async def presets(ctx: RpcContext) -> dict[str, Any]:
        """Download presets and the option vocabularies for the menu (and the explicit conversion catalogue)."""
        hw = await asyncio.to_thread(hwencode.detect)
        return {"presets": preset_rows(), "containers": list(CONTAINERS), "audio_formats": list(AUDIO_FORMATS),
                "sub_langs": sub_lang_rows(),
                "audio_bitrates": list(AUDIO_BITRATES), "sponsorblock": list(SPONSORBLOCK_MODES),
                "catalogo": catalogo(hw),
                "settings": service.downloads.settings.to_dict()}

    @d.method("ytdl.download")
    async def download(ctx: RpcContext, url: str, preset: str | None = None, options: dict[str, Any] | None = None,
                       title: str | None = None, notify: str | None = None, out_dir: str | None = None,
                       **spec: Any) -> dict[str, Any]:
        """Queue a download: ``preset`` (see ytdl.presets) or explicit spec fields (kind, format, container...)."""
        params = {"url": url, "preset": preset, "options": options, **spec}
        ds = service.spec_from_params(params)
        if title:
            ds.title = title
        await service.require_binary()
        try:
            # «3-5» o [3, 5] → los rangos del capítulo: la orden no lleva números que yt-dlp no sabría usar
            await service.resolve_chapters(ds)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        await service.resolve_langs(ds)
        item = service.downloads.submit(ds, title=title or "", notify=notify or DEFAULT_NOTIFY, out_dir=out_dir)
        return item.to_dict()

    @d.method("ytdl.nightly")
    async def nightly(ctx: RpcContext) -> dict[str, Any]:
        """The nightly yt-dlp (downloaded and verified if missing or older than a day), for retrying what the stable
        one cannot open; ``available`` false when it cannot be had."""
        nb = await service.ensure_nightly()
        if nb is None:
            return {"available": False, "path": "", "version": ""}
        return {"available": True, "path": str(nb.path), "version": await nb.version(), "argv": nb.argv}

    @d.method("ytdl.download.batch")
    async def download_batch(ctx: RpcContext, text: str | None = None, urls: list[str] | None = None,
                             file: str | None = None, preset: str | None = None, options: dict[str, Any] | None = None,
                             notify: str | None = None, out_dir: str | None = None,
                             first: int | None = None) -> dict[str, Any]:
        """Queue several URLs at once: pasted text (any separator), a list, or a text file with one URL per line
        (``#`` comments). Duplicates are dropped; with the archive on, what was downloaded before is skipped.

        Guarda de tamaño: más de ``max_items`` (50 por defecto) no se encola a ciegas — se pide confirmación y el
        menú ofrece «los primeros N» (``first``), que es lo que baja exactamente N de la lista.
        """
        found: list[str] = []
        sources = [text or ""]
        if file:
            try:
                sources.append(Path(file).expanduser().read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                raise RpcError(INVALID_PARAMS, t("no se puede leer %s: %s") % (file, exc)) from None
        for src in sources:
            for line in src.splitlines():
                if line.lstrip().startswith(("#", ";", "]")):
                    continue
                found += URL_RE.findall(line)
        found += [u for u in (urls or []) if isinstance(u, str) and URL_RE.fullmatch(u.strip())]
        unique = list(dict.fromkeys(u.strip().rstrip(".,;)") for u in found))[:MAX_BATCH]
        if not unique:
            raise RpcError(INVALID_PARAMS, t("no hay ninguna URL"))
        tope = max(1, int(service.downloads.settings.max_items or MAX_BATCH))
        if len(unique) > tope:
            if first is None:
                # no se baja nada sin decirlo: el menú pregunta y ofrece «los primeros N»
                raise RpcError(INVALID_PARAMS, t("son %d vídeos, más de %d: confirma cuántos quieres")
                               % (len(unique), tope),
                               data={"count": len(unique), "max": tope, "asks": "confirm"})
            unique = unique[: max(0, int(first))]
        await service.require_binary()
        items = []
        for url in unique:
            ds = service.spec_from_params({"url": url, "preset": preset, "options": options})
            if "archive" not in (options or {}):
                ds.archive = ds.archive or service.downloads.settings.archive
            await service.resolve_langs(ds)
            items.append(service.downloads.submit(ds, notify=notify or DEFAULT_NOTIFY, out_dir=out_dir).to_dict())
        return {"count": len(items), "items": items}

    @d.method("ytdl.downloads.list")
    async def downloads_list(ctx: RpcContext, include_finished: bool = True) -> list[dict[str, Any]]:
        """Download queue and history (active first)."""
        return service.downloads.list(include_finished)

    @d.method("ytdl.downloads.get")
    async def downloads_get(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """One download by id (with argv and the last stderr lines)."""
        item = service.downloads.get(id)
        if item is None:
            raise RpcError(NOT_FOUND, f"unknown download: {id}")
        return {**item.to_dict(), "argv": item.argv, "stderr": item.stderr_tail}

    @d.method("ytdl.downloads.cancel")
    async def downloads_cancel(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Cancel a queued or running download."""
        return {"cancelled": service.downloads.cancel(id)}

    @d.method("ytdl.downloads.retry")
    async def downloads_retry(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Re-queue a failed or cancelled download with the same options."""
        item = service.downloads.retry(id)
        if item is None:
            raise RpcError(NOT_FOUND, f"download not retryable: {id}")
        return item.to_dict()

    @d.method("ytdl.downloads.remove")
    async def downloads_remove(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Forget a finished download (files are kept)."""
        return {"removed": service.downloads.remove(id)}

    @d.method("ytdl.downloads.clear")
    async def downloads_clear(ctx: RpcContext) -> dict[str, Any]:
        """Forget all finished downloads."""
        return {"removed": service.downloads.clear_finished()}

    # -- suscripciones, índice y cola (F4) ---------------------------------------------------------

    @d.method("ytdl.subs.list")
    async def subs_list(ctx: RpcContext) -> dict[str, Any]:
        """Canales seguidos con su estado (cada cuánto, cuántos nuevos, último error) y el tamaño del índice."""
        return service.subs_lista()

    @d.method("ytdl.subs.add")
    async def subs_add(ctx: RpcContext, url: str, titulo: str = "", perfil: str | None = None,
                       carpeta: str | None = None, duracion_min: int | None = None, duracion_max: int | None = None,
                       titulo_excluir: str | None = None, hora_desde: int | None = None, hora_hasta: int | None = None,
                       tope: int | None = None, intervalo: int | None = None) -> dict[str, Any]:
        """Seguir un canal (id ``UC…`` o su dirección) con sus reglas: perfil, carpeta, filtros y cada cuánto."""
        cambios = {k: v for k, v in {"perfil": perfil, "carpeta": carpeta, "duracion_min": duracion_min,
                                     "duracion_max": duracion_max, "titulo_excluir": titulo_excluir,
                                     "hora_desde": hora_desde, "hora_hasta": hora_hasta, "tope": tope,
                                     "intervalo": intervalo}.items() if v is not None}
        return service.subs_add(url, titulo=titulo, **cambios)

    @d.method("ytdl.subs.update")
    async def subs_update(ctx: RpcContext, id: str, **cambios: Any) -> dict[str, Any]:  # noqa: A002
        """Cambiar una regla o pausar el canal (``activa``)."""
        return service.subs_update(id, **cambios)

    @d.method("ytdl.subs.remove")
    async def subs_remove(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Dejar de seguir un canal (lo descargado se queda donde está)."""
        return service.subs_borrar(id)

    @d.method("ytdl.subs.check")
    async def subs_check(ctx: RpcContext, id: str | None = None, forzar: bool = True) -> dict[str, Any]:
        """Comprobar ahora: solo mira el feed y el índice y encola lo nuevo (sin descargar metadatos de cada vídeo)."""
        return await service.sondear(sid=id, forzar=forzar)

    @d.method("ytdl.subs.historico")
    async def subs_historico(ctx: RpcContext, id: str, desde: float | None = None,
                             hasta: float | None = None) -> dict[str, Any]:
        """Bajar el fondo histórico del canal: todo lo anterior, con archivo y corte en lo ya conocido."""
        try:
            return service.subs_historico(id, desde=desde, hasta=hasta)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    @d.method("ytdl.index.historial")
    async def index_historial(ctx: RpcContext, n: int = 20, canal: str | None = None) -> dict[str, Any]:
        """Lo último que se bajó, según el índice local (no según la lista de trabajos, que se limpia)."""
        return service.indice_historial(n, canal=canal)

    @d.method("ytdl.index.faltan")
    async def index_faltan(ctx: RpcContext, ids: list[str], perfil: str | None = None) -> dict[str, Any]:
        """De estos ids, cuáles no están en el índice (dedupe por id + perfil)."""
        return service.indice_faltan(ids, perfil=perfil)

    @d.method("ytdl.queue.pause")
    async def queue_pause(ctx: RpcContext) -> dict[str, Any]:
        """Pausar la cola entera: no arranca nada y lo que está en marcha se para para reanudarlo."""
        return service.downloads.pause_all()

    @d.method("ytdl.queue.resume")
    async def queue_resume(ctx: RpcContext) -> dict[str, Any]:
        """Reanudar la cola (yt-dlp --continue recoge el .part donde estaba)."""
        return service.downloads.resume_all()

    @d.method("ytdl.queue.priority")
    async def queue_priority(ctx: RpcContext, id: str, prioridad: str = "normal") -> dict[str, Any]:  # noqa: A002
        """Prioridad de un trabajo: ``high``, ``normal`` o ``low`` (las suscripciones entran en ``low``)."""
        try:
            item = service.downloads.set_priority(id, prioridad)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        if item is None:
            raise RpcError(NOT_FOUND, f"unknown download: {id}")
        return item.to_dict()

    @d.method("ytdl.downloads.report")
    async def downloads_report(ctx: RpcContext) -> dict[str, Any]:
        """Informe del final: cuántos bien, cuántos mal y el motivo de cada fallido."""
        return service.downloads.report()

    @d.method("ytdl.settings.get")
    async def settings_get(ctx: RpcContext) -> dict[str, Any]:
        """Download folders, filename template, default options and auto-update flag."""
        return service.downloads.settings.to_dict()

    @d.method("ytdl.settings.set")
    async def settings_set(ctx: RpcContext, **values: Any) -> dict[str, Any]:
        """Update settings (only known keys); persisted in data_dir/ytdl.json."""
        s = service.downloads.settings
        if "container" in values and values["container"] not in CONTAINERS:
            raise RpcError(INVALID_PARAMS, f"container must be one of {CONTAINERS}")
        if values.get("cookies_browser") and not valid_browser(str(values["cookies_browser"])):
            raise RpcError(INVALID_PARAMS, t("navegador no admitido: %s") % (", ".join(BROWSERS),))
        if "sponsorblock" in values and values["sponsorblock"] not in SPONSORBLOCK_MODES:
            raise RpcError(INVALID_PARAMS, f"sponsorblock must be one of {SPONSORBLOCK_MODES}")
        s.update(values)
        service.downloads.save_settings()
        return s.to_dict()
