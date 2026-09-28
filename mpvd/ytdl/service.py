"""yt-dlp service: binary status + daily updater, ``-J`` analysis with cache, presets and the download queue.

Exposed as ``ytdl.*`` JSON-RPC methods (see ``register``). Download progress is pushed to every connected mpv as
``script-message-to <notify> mu-event {"event":"download","download":{...}}`` (default target: ``mu_ytdl``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import url_key
from mpvd.jobs import Priority
from mpvd.net import HttpCache
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.ytdl import info as info_mod
from mpvd.ytdl.binary import YtdlpBinary, YtdlpUpdater, find_ytdlp, vendor_path
from mpvd.ytdl.downloads import FINAL, DownloadItem, DownloadManager
from mpvd.ytdl.presets import (
    AUDIO_BITRATES,
    AUDIO_FORMATS,
    CONTAINERS,
    PRESETS,
    SPONSORBLOCK_MODES,
    DownloadSpec,
    spec_from_preset,
)

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.ytdl")

INFO_ARTIFACT = "ytdl-info"
INFO_TTL = 6 * 3600.0          # googlevideo URLs expire after ~6 h (docs/YTDLP.md §7)
INFO_TIMEOUT = 120.0
DEFAULT_NOTIFY = "mu_ytdl"
AUTO_UPDATE_DELAY = 30.0


class YtdlService:
    def __init__(self, server: MpvdServer):
        self.server = server
        settings = server.settings
        self.root: Path | None = server.root
        self.http = HttpCache(settings.cache_dir / "http")
        self._binary: YtdlpBinary | None = None
        self._binary_checked = 0.0
        target = vendor_path(self.root) or (settings.data_dir / "bin" / "yt-dlp")
        self.updater = YtdlpUpdater(self.http, target)
        self.downloads = DownloadManager(server.jobs, self.binary, settings.data_dir, on_change=self._download_changed)
        self._info_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._auto_task: asyncio.Task[None] | None = None
        self.last_update_check: dict[str, Any] = {}

    # -- binary -------------------------------------------------------------------------------

    def binary(self, refresh: bool = False) -> YtdlpBinary | None:
        now = time.monotonic()
        if refresh or self._binary is None or now - self._binary_checked > 30 or not self._binary.path.is_file():
            self._binary = find_ytdlp(self.root)
            self._binary_checked = now
        return self._binary

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
        }

    def hook_config(self) -> dict[str, Any]:
        """What mu-ytdl should hand to mpv's ytdl_hook: the binary path and ``ytdl-raw-options`` entries."""
        b = self.binary()
        raw: dict[str, str] = {}
        if b is not None and b.js_runtime is not None:
            raw["js-runtimes"] = f"{b.js_runtime.name}:{b.js_runtime.path}"
        return {"ytdl_path": str(b.path) if b is not None else "", "raw_options": raw,
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
        if self.auto_update_enabled():
            self._auto_task = asyncio.create_task(self._auto_update(), name="mpvd-ytdl-autoupdate")

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
            return st

        self.server.jobs.submit("ytdl.update", body, priority=Priority.INDEX, heavy=False)

    async def close(self) -> None:
        if self._auto_task is not None:
            self._auto_task.cancel()
        await self.downloads.cancel_all()

    # -- info (-J) ----------------------------------------------------------------------------

    async def _run_json(self, args: list[str], timeout: float = INFO_TIMEOUT) -> dict[str, Any]:
        b = await self.require_binary()
        cmd = b.command(*args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
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
            raise RpcError(UNAVAILABLE, "yt-dlp returned no object")
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

    async def analyze(self, url: str, force: bool = False, seed: str | None = None) -> dict[str, Any]:
        data = await self.raw_info(url, force=force, seed=seed)
        if (data.get("_type") or "video") == "playlist":
            entries = info_mod.flat_entries(data)
            return {**info_mod.summary(data), "entries": entries, "formats": {}, "counts": {},
                    "has_combined": False}
        return {**info_mod.analyze(data), "url": url}

    # -- downloads ----------------------------------------------------------------------------

    def _download_changed(self, item: DownloadItem) -> None:
        target = item.notify or DEFAULT_NOTIFY
        payload = {"event": "download", "download": item.to_dict()}
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(target, "download:" + item.id, item.status, payload, min_interval=0.25,
                                   final=item.status in FINAL)

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
                return spec_from_preset(str(params["preset"]), str(url), options)
            spec_dict = {**defaults, **{k: v for k, v in params.items() if k not in ("preset", "options", "notify",
                                                                                     "title", "out_dir")}}
            spec_dict.update(params.get("options") or {})
            spec_dict["url"] = url
            return DownloadSpec.from_dict(spec_dict)
        except KeyError as exc:
            raise RpcError(NOT_FOUND, f"unknown preset: {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc


def register(server: MpvdServer, service: YtdlService) -> None:  # noqa: C901 - flat list of handlers
    d = server.dispatcher
    server.services["ytdl"] = True

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

    @d.method("ytdl.playlist")
    async def playlist(ctx: RpcContext, url: str, force: bool = False) -> dict[str, Any]:
        """Flat playlist entries (``--flat-playlist -J``)."""
        data = await service.raw_info(url, flat=True, force=force)
        return {**info_mod.summary(data), "entries": info_mod.flat_entries(data)}

    @d.method("ytdl.presets")
    async def presets(ctx: RpcContext) -> dict[str, Any]:
        """Download presets and the option vocabularies for the menu."""
        return {"presets": PRESETS, "containers": list(CONTAINERS), "audio_formats": list(AUDIO_FORMATS),
                "audio_bitrates": list(AUDIO_BITRATES), "sponsorblock": list(SPONSORBLOCK_MODES),
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
        item = service.downloads.submit(ds, title=title or "", notify=notify or DEFAULT_NOTIFY, out_dir=out_dir)
        return item.to_dict()

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
        if "sponsorblock" in values and values["sponsorblock"] not in SPONSORBLOCK_MODES:
            raise RpcError(INVALID_PARAMS, f"sponsorblock must be one of {SPONSORBLOCK_MODES}")
        s.update(values)
        service.downloads.save_settings()
        return s.to_dict()
