"""``study.*``: clip/GIF export of an A-B range (job with progress events, history in data_dir/clips.json) and silence maps
for smart speed (cached by file hash)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import probe_duration
from mpvd.hashing import file_hash
from mpvd.jobs import Job, Priority
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.study.clips import (FORMATS, MAX_SECONDS, ClipError, audio_codec, audio_copy_ext, export_clip,
                              output_path)
from mpvd.study.silence import DEFAULT_DB, DEFAULT_MIN, silence_map, summary

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.study")

ARTIFACT_SILENCE = "silence"
VERSION = "1"
HISTORY_MAX = 100
DEFAULT_NOTIFY = "mu_study"


def _local(path: str) -> Path:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not path.startswith("file://"):
        raise RpcError(UNAVAILABLE, t("solo archivos locales"))
    p = Path(path.removeprefix("file://"))
    if not p.is_file():
        raise RpcError(NOT_FOUND, t("no existe: %s") % (p,))
    return p


class StudyService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.history_path = server.settings.data_dir / "clips.json"
        self.clips: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, Job] = {}
        self._load()

    # -- clips history -------------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            for row in json.loads(self.history_path.read_text(encoding="utf-8")):
                if isinstance(row, dict) and row.get("id"):
                    row["status"] = row.get("status") if row.get("status") in ("done", "failed", "cancelled") else "failed"
                    self.clips[row["id"]] = row
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        rows = sorted(self.clips.values(), key=lambda r: r.get("created_at", 0), reverse=True)[:HISTORY_MAX]
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.history_path)
        except OSError as exc:
            log.info("study: cannot save clips history (%s)", exc)

    def _push(self, item: dict[str, Any], final: bool = False) -> None:
        payload = {"event": "clip", "clip": item}
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(item.get("notify") or DEFAULT_NOTIFY, "clip:" + item["id"], "done" if final else "progress",
                                   payload, final=final)

    def submit_clip(self, src: Path, a: float, b: float, fmt: str, directory: Path | None, audio_track: int | None,
                    title: str, notify: str, max_seconds: float | None = None) -> dict[str, Any]:
        if fmt not in FORMATS:
            raise RpcError(INVALID_PARAMS, f"formato desconocido: {fmt} (study.formats)")
        if not (b > a >= 0):
            raise RpcError(INVALID_PARAMS, t("tramo inválido: fin ≤ inicio"))
        # said now, not after «Guardando…»: the job used to accept the range and fail minutes later
        limit = MAX_SECONDS if max_seconds is None else max_seconds
        if limit > 0 and b - a > limit:
            raise RpcError(INVALID_PARAMS, t("tramo demasiado largo (máx. %s s)") % (int(limit),))
        ext = audio_copy_ext(audio_codec(src, audio_track)) if fmt == "audio-copy" else None
        out = output_path(src, a, b, fmt, directory, ext=ext)
        item: dict[str, Any] = {"id": uuid.uuid4().hex[:10], "path": str(src), "title": title or src.name, "start": round(a, 3),
                                "end": round(b, 3), "format": fmt, "file": str(out), "status": "queued", "progress": 0.0,
                                "message": "", "created_at": time.time(), "notify": notify}
        self.clips[item["id"]] = item

        async def body(job: Job) -> dict[str, Any]:
            item["status"] = "running"
            self._push(item)

            def progress(frac: float, text: str) -> None:
                item["progress"] = round(frac, 3)
                item["message"] = text
                job.report(frac, text)
                self._push(item)

            try:
                res = await export_clip(src, a, b, fmt, out, audio_track, progress, max_seconds=max_seconds)
                item.update({"status": "done", "progress": 1.0, "bytes": res["bytes"], "message": "listo",
                             "finished_at": time.time()})
            except asyncio.CancelledError:
                item.update({"status": "cancelled", "message": "cancelado"})
                self._save()
                self._push(item, final=True)
                raise
            except ClipError as exc:
                item.update({"status": "failed", "message": str(exc)})
                self._save()
                self._push(item, final=True)
                raise RpcError(UNAVAILABLE, str(exc)) from exc
            finally:
                self.jobs.pop(item["id"], None)
            self._save()
            self._push(item, final=True)
            return item

        job = self.server.jobs.submit("study.clip", body, priority=Priority.INTERACTIVE, heavy=True, session_id=None,
                                      meta={"clip": item["id"], "path": str(src)})
        self.jobs[item["id"]] = job
        item["job"] = job.id
        self._save()
        return item

    def cancel(self, clip_id: str) -> bool:
        job = self.jobs.get(clip_id)
        if job is None:
            return False
        return self.server.jobs.cancel(job.id)

    # -- silences ------------------------------------------------------------------------------------

    async def silences(self, src: Path, start: float, length: float, noise_db: float, min_seconds: float) -> dict[str, Any]:
        key = (await asyncio.to_thread(file_hash, src)).key
        duration = await asyncio.to_thread(probe_duration, str(src))
        if duration:
            length = max(0.0, min(length, duration - start))
        params = {"start": round(start, 1), "length": round(length, 1), "db": noise_db, "min": min_seconds}
        entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT_SILENCE, "silencedetect", VERSION, params)
        if entry is not None and isinstance(entry.data, dict):
            return {"cached": True, **entry.data}
        spans = await silence_map(str(src), start, length, noise_db, min_seconds)
        data = {"path": str(src), "start": start, "length": length, "silences": spans, **summary(spans, length),
                "duration": duration}
        await asyncio.to_thread(self.server.cache.put, key, ARTIFACT_SILENCE, model="silencedetect", version=VERSION,
                                params=params, data=data)
        return {"cached": False, **data}


def register(server: MpvdServer, service: StudyService) -> None:
    d = server.dispatcher
    server.services["study"] = True

    @d.method("study.formats")
    async def formats(ctx: RpcContext) -> list[dict[str, Any]]:
        """Clip formats: name, extension, kind (video/audio) and Spanish label."""
        return [{"name": k, **v} for k, v in FORMATS.items()]

    @d.method("study.clip")
    async def clip(ctx: RpcContext, path: str, start: float, end: float, format: str = "mp4",  # noqa: A002
                   dir: str | None = None, audio_track: int | None = None, title: str = "",  # noqa: A002
                   notify: str = DEFAULT_NOTIFY, max_seconds: float | None = None) -> dict[str, Any]:
        """Export [start, end] of a local file in the background; progress arrives as ``clip`` events.

        ``max_seconds`` overrides the cap for study clips (600 s); 0 means no cap, which is what «Grabar» uses.
        """
        p = _local(path)
        directory = Path(dir).expanduser() if dir else None
        return service.submit_clip(p, float(start), float(end), format, directory, audio_track, title, notify,
                                   max_seconds)

    @d.method("study.clips.list")
    async def clips_list(ctx: RpcContext, limit: int = 50) -> list[dict[str, Any]]:
        """Recent clips (running first, then newest)."""
        rows = sorted(service.clips.values(), key=lambda r: (r["status"] not in ("queued", "running"), -r.get("created_at", 0)))
        return rows[: max(1, min(int(limit), HISTORY_MAX))]

    @d.method("study.clips.cancel")
    async def clips_cancel(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Cancel a running clip export."""
        return {"id": id, "cancelled": service.cancel(id)}

    @d.method("study.silences")
    async def silences(ctx: RpcContext, path: str, start: float = 0.0, length: float = 600.0, noise_db: float = DEFAULT_DB,
                       min_seconds: float = DEFAULT_MIN) -> dict[str, Any]:
        """Quiet spans of [start, start+length) for smart speed (cached)."""
        p = _local(path)
        return await service.silences(p, max(0.0, float(start)), max(1.0, float(length)), float(noise_db), float(min_seconds))
