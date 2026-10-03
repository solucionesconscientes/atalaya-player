"""``convert.*`` and ``tasks.list`` (H20): a conversion queue on top of the job queue, one ffmpeg per file.

* ``convert.start`` takes a file or a whole folder (one task per file, outputs in ``<carpeta de salida>/<carpeta>``).
* Conversions run one at a time (ffmpeg already uses every core) at a lower CPU priority (nice 10) and as *heavy* jobs,
  so they wait while the performance guardian sees playback dropping frames.
* Progress comes from ``ffmpeg -progress pipe:1`` (``out_time_us`` against the length of the range); ffmpeg writes
  ``name.part.ext`` and the file is renamed when it is complete: a cancelled or failed conversion leaves nothing behind
  and existing files are never overwritten (``-n`` + a free name).
* VA-API (H.264/H.265) is used when ``vainfo`` offers it; if the hardware run fails the file is converted again on the
  CPU automatically.
* History in ``<data>/conversions.json``; what was queued or running when mpvd stopped is queued again at start.
* Events: ``mu-event {"event":"convert","convert":{…}}`` to ``notify`` (default mu_convert) and, for the «Tareas» panel,
  ``{"event":"task","task":{…}}`` to mu_convert for conversions and downloads alike (``tasks.list`` gives the same rows).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import AudioError, ffmpeg_path
from mpvd import notify
from mpvd.convert import hw as hw_mod
from mpvd.convert.presets import (
    AUDIO_BITRATE_CHOICES,
    GIF_FPS,
    GIF_WIDTHS,
    HEIGHTS,
    PRESETS,
    usable_presets,
    QUALITIES,
    QUALITY_LABELS,
    ConvertError,
    ConvertSpec,
    Plan,
    build_plan,
    media_files,
    output_path,
    partial_path,
    probe,
)
from mpvd.jobs import Job, Priority, job_rows
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, RpcError
from mpvd.ytdl.downloads import default_media_dir, fmt_eta

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.convert")

DEFAULT_NOTIFY = "mu_convert"
TASKS_NOTIFY = "mu_convert"       # the «Tareas» panel lives in mu-convert
FINAL = ("done", "failed", "cancelled")
# H62 · «debe avisar al terminar». Solo si ha tardado lo bastante para que te hayas ido a otra cosa: avisar de algo
# que acabó en dos segundos delante de ti es ruido. Un fallo avisa siempre, porque eso importa aunque sea rápido.
NOTIFY_AFTER = 20.0
MAX_HISTORY = 300
MAX_FOLDER = 500
STDERR_TAIL = 12
NICE = 10


def default_dir() -> Path:
    return default_media_dir("video") / "Convertidos"


@dataclass
class ConvertItem:
    src: str
    spec: ConvertSpec
    out_dir: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    message: str = "en cola"
    error: str = ""
    output: str = ""
    partial: str = ""
    warnings: list[str] = field(default_factory=list)
    hw: str = ""                      # what encoded it: cpu | vaapi
    speed: float | None = None        # × real time
    eta: float | None = None
    attempts: int = 0
    group: str = ""                   # folder conversions share one
    notify: str = DEFAULT_NOTIFY
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    job_id: str | None = None
    resume: bool = False
    argv: list[list[str]] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": "convert", "src": self.src, "title": self.title or Path(self.src).name,
            "description": self.spec.describe(), "preset": self.spec.preset, "status": self.status,
            "progress": round(self.progress, 4), "message": self.message, "error": self.error,
            "output": self.output, "outputs": [self.output] if self.output else [], "out_dir": self.out_dir,
            "warnings": list(self.warnings), "hw": self.hw, "speed": self.speed, "eta": self.eta,
            "attempts": self.attempts, "group": self.group, "created_at": self.created_at,
            "started_at": self.started_at, "finished_at": self.finished_at, "job_id": self.job_id,
            "spec": self.spec.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConvertItem:
        spec = ConvertSpec.from_dict(d.get("spec") or {})
        item = cls(src=str(d["src"]), spec=spec, out_dir=str(d.get("out_dir") or ""), id=str(d.get("id")))
        for k in ("title", "status", "progress", "message", "error", "output", "partial", "warnings", "hw", "attempts",
                  "group",
                  "created_at", "started_at", "finished_at"):
            if d.get(k) is not None:
                setattr(item, k, d[k])
        item.notify = DEFAULT_NOTIFY
        if item.status not in FINAL:   # mpvd stopped while it was queued or running: start again
            item.status, item.message, item.progress, item.resume = "queued", "se reanudará", 0.0, True
        return item


def task_row(d: dict[str, Any], kind: str) -> dict[str, Any]:
    """One row of the «Tareas» panel from a download or a conversion dict."""
    status = d.get("status") or "queued"
    active = status in ("queued", "running")
    actions = ["cancel"] if active else ["retry", "remove"]
    if d.get("out_dir"):
        actions.append("folder")
    return {
        "type": kind, "id": d.get("id"), "title": d.get("title") or d.get("url") or d.get("src") or "?",
        "description": d.get("description") or "", "status": status, "progress": d.get("progress") or 0.0,
        "message": d.get("message") or "", "error": d.get("error") or "", "out_dir": d.get("out_dir") or "",
        "outputs": list(d.get("outputs") or []), "warnings": list(d.get("warnings") or []),
        "created_at": d.get("created_at") or 0.0, "finished_at": d.get("finished_at"), "actions": actions,
    }


def _lower_priority() -> None:  # pragma: no cover - runs in the child
    with contextlib.suppress(OSError):
        os.nice(NICE)


class ConvertService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.history_path = server.settings.data_dir / "conversions.json"
        self.items: dict[str, ConvertItem] = {}
        self._order: deque[str] = deque()
        self._pending: deque[str] = deque()
        self._active: set[str] = set()
        self._reserved: set[str] = set()
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._hw: dict[str, Any] | None = None
        self._hw_task: asyncio.Task[dict[str, Any]] | None = None
        self._closing = False
        self.concurrent = 1
        self._load_history()

    # -- persistence ----------------------------------------------------------------------------

    def _load_history(self) -> None:
        try:
            rows = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in rows if isinstance(rows, list) else []:
            try:
                item = ConvertItem.from_dict(row)
            except (ValueError, TypeError, KeyError):
                continue
            self.items[item.id] = item
            self._order.append(item.id)

    def _save(self) -> None:
        rows = [self.items[i].to_dict() for i in self._order if i in self.items]
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.history_path)
        except OSError as exc:
            log.warning("cannot save conversion history: %s", exc)

    # -- events ---------------------------------------------------------------------------------

    def _push(self, target: str, key: str, status: str, payload: dict[str, Any], final: bool) -> None:
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(target, key, status, payload, min_interval=0.25, final=final)

    def _maybe_notify(self, row: dict[str, Any], kind: str) -> None:
        """Un aviso del escritorio al terminar, que es lo que se ve con el reproductor detrás o cerrado."""
        if row["status"] not in ("done", "failed"):
            return
        tardo = float(row.get("finished_at") or 0.0) - float(row.get("created_at") or 0.0)
        if row["status"] == "done" and tardo < NOTIFY_AFTER:
            return
        ok = row["status"] == "done"
        titulo = ("✓ " if ok else "✗ ") + kind + (" terminada" if ok else " ha fallado")
        cuerpo = str(row.get("title") or "")
        if ok and row.get("out_dir"):
            cuerpo += "\n" + str(row["out_dir"])
        elif not ok and row.get("error"):
            cuerpo += "\n" + str(row["error"])[:200]
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(notify.notify(titulo, cuerpo))

    def _changed(self, item: ConvertItem, persist: bool = False) -> None:
        d = item.to_dict()
        final = item.status in FINAL
        row = task_row(d, "convert")
        if final:
            self._maybe_notify(row, "Conversión")
        if item.notify == TASKS_NOTIFY:
            self._push(item.notify, "convert:" + item.id, item.status, {"event": "convert", "convert": d, "task": row},
                       final)
        else:
            self._push(item.notify, "convert:" + item.id, item.status, {"event": "convert", "convert": d}, final)
            self._push(TASKS_NOTIFY, "task:convert:" + item.id, item.status, {"event": "task", "task": row}, final)
        if persist:
            self._save()

    def download_changed(self, item: Any) -> None:
        """Chained after the download manager's own callback: downloads show up live in «Tareas» too."""
        row = task_row(item.to_dict(), "download")
        if row["status"] in FINAL:
            self._maybe_notify(row, "Descarga")
        self._push(TASKS_NOTIFY, "task:download:" + item.id, row["status"], {"event": "task", "task": row},
                   row["status"] in FINAL)

    # -- hardware -------------------------------------------------------------------------------

    async def hw_caps(self, refresh: bool = False) -> dict[str, Any]:
        if self._hw is not None and not refresh:
            return self._hw
        if self._hw_task is None or self._hw_task.done():
            self._hw_task = asyncio.create_task(asyncio.to_thread(hw_mod.detect))
        self._hw = await asyncio.shield(self._hw_task)
        return self._hw

    # -- queue ----------------------------------------------------------------------------------

    def list(self, include_finished: bool = True) -> list[dict[str, Any]]:
        rows = [self.items[i] for i in self._order if i in self.items]
        if not include_finished:
            rows = [r for r in rows if r.status not in FINAL]
        rows.sort(key=lambda r: (r.status in FINAL, r.status != "running", -r.created_at if r.status in FINAL
                                 else r.created_at))
        return [r.to_dict() for r in rows]

    def get(self, item_id: str) -> ConvertItem | None:
        return self.items.get(item_id)

    def active(self) -> int:
        return sum(1 for i in self.items.values() if i.status in ("queued", "running"))

    def submit(self, src: Path, spec: ConvertSpec, out_dir: Path, notify: str = DEFAULT_NOTIFY,
               group: str = "") -> ConvertItem:
        item = ConvertItem(src=str(src), spec=spec, out_dir=str(out_dir), title=src.name, notify=notify, group=group)
        self.items[item.id] = item
        self._order.append(item.id)
        self._trim()
        self._queue(item)
        return item

    def _trim(self) -> None:
        while len(self.items) > MAX_HISTORY:
            oldest = next((i for i in self._order if self.items.get(i) and self.items[i].status in FINAL), None)
            if oldest is None:
                return
            self.items.pop(oldest, None)
            with contextlib.suppress(ValueError):
                self._order.remove(oldest)

    def _queue(self, item: ConvertItem) -> None:
        item.status, item.message, item.error, item.progress = "queued", "en cola", "", 0.0
        item.output, item.warnings, item.hw, item.speed, item.eta = "", [], "", None, None
        item.started_at = item.finished_at = None
        item.job_id = None
        item.attempts += 1
        self._pending.append(item.id)
        self._changed(item, persist=True)
        self._pump()

    def _pump(self) -> None:
        if self._closing:
            return
        while len(self._active) < self.concurrent and self._pending:
            item = self.items.get(self._pending.popleft())
            if item is None or item.status != "queued":
                continue
            job = self.server.jobs.submit(f"convert:{item.id}", lambda job, it=item: self._run(it, job),
                                          priority=Priority.INTERACTIVE, heavy=True, meta={"convert": item.id})
            item.job_id = job.id
            self._active.add(item.id)

    def resume_pending(self) -> int:
        pending = [self.items[i] for i in self._order if i in self.items and self.items[i].resume]
        for item in pending:
            item.resume = False
            if item.partial:
                with contextlib.suppress(OSError):
                    Path(item.partial).unlink(missing_ok=True)
            item.attempts = max(0, item.attempts - 1)
            self._queue(item)
        return len(pending)

    def cancel(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status in FINAL:
            return False
        if item.status == "queued":
            with contextlib.suppress(ValueError):
                self._pending.remove(item_id)
            if item.job_id:
                self.server.jobs.cancel(item.job_id)   # waiting in the job queue (e.g. held by the guardian)
            self._active.discard(item_id)
            self._finish(item, "cancelled", "cancelada")
            self._pump()
            return True
        if item.job_id and self.server.jobs.cancel(item.job_id):
            return True
        self._finish(item, "cancelled", "cancelada")
        return True

    def retry(self, item_id: str) -> ConvertItem | None:
        item = self.items.get(item_id)
        if item is None or item.status not in FINAL:
            return None
        self._queue(item)
        return item

    def remove(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status not in FINAL:
            return False
        self.items.pop(item_id, None)
        with contextlib.suppress(ValueError):
            self._order.remove(item_id)
        self._save()
        return True

    def clear_finished(self) -> int:
        finished = [i for i, it in self.items.items() if it.status in FINAL]
        for i in finished:
            self.items.pop(i, None)
            with contextlib.suppress(ValueError):
                self._order.remove(i)
        self._save()
        return len(finished)

    async def start(self) -> None:
        n = self.resume_pending()
        if n:
            log.info("convert: %d unfinished conversion(s) queued again", n)

    async def close(self) -> None:
        """mpvd is stopping: running conversions are killed and stay queued for the next start."""
        self._closing = True
        for item_id in list(self._active):
            item = self.items.get(item_id)
            if item is not None and item.job_id:
                self.server.jobs.cancel(item.job_id)
        for proc in list(self._procs.values()):
            await self._kill(proc)
        self._save()

    # -- running --------------------------------------------------------------------------------

    def _finish(self, item: ConvertItem, status: str, message: str, error: str = "") -> None:
        item.status, item.message, item.error = status, message, error
        item.finished_at = time.time()
        item.speed = item.eta = None
        if status == "done":
            item.progress = 1.0
        self._changed(item, persist=True)

    async def _run(self, item: ConvertItem, job: Job) -> dict[str, Any]:
        try:
            return await self._convert(item, job)
        finally:
            self._active.discard(item.id)
            self._pump()

    async def _convert(self, item: ConvertItem, job: Job) -> dict[str, Any]:
        spec = item.spec
        src = Path(item.src)
        item.status, item.message, item.started_at = "running", "analizando…", time.time()
        self._changed(item)
        partial: Path | None = None
        fell_back = False
        reserved: set[str] = set()
        try:
            if not src.is_file():
                raise ConvertError(f"ya no existe: {src}")
            info = await asyncio.to_thread(probe, src)
            hw_plan = None
            if spec.hw != "cpu" and spec.vcodec in hw_mod.FFMPEG_ENCODERS:
                hw_plan = hw_mod.plan_for(await self.hw_caps(), spec.vcodec)
                if hw_plan is None and spec.hw == "vaapi":
                    item.warnings.append("la tarjeta gráfica no puede codificar esto: se usa la CPU")
            folder = Path(item.out_dir)
            folder.mkdir(parents=True, exist_ok=True)
            out = output_path(src, folder, spec, self._reserved)
            reserved.add(str(out))
            self._reserved.add(str(out))
            item.output = str(out)
            partial = partial_path(out)
            item.partial = str(partial)
            with tempfile.TemporaryDirectory(prefix="mu-convert-") as tmp:
                palette = Path(tmp) / "palette.png"
                ff = ffmpeg_path()
                plan = build_plan(spec, src, partial, info, hw_plan, ff, palette)
                item.warnings += [w for w in plan.warnings if w not in item.warnings]
                rc, tail = await self._exec(item, job, plan)
                if rc != 0 and plan.hw == "vaapi":
                    # the GPU refused this file (odd size, driver hiccup…): same conversion on the CPU
                    log.info("convert: VA-API failed for %s (%s): retrying on the CPU", src, tail[-1:] or rc)
                    partial.unlink(missing_ok=True)
                    fell_back = True
                    item.warnings.append("la tarjeta gráfica falló: convertido por CPU")
                    item.message = "la tarjeta gráfica falló: repitiendo por CPU…"
                    self._changed(item)
                    plan = build_plan(spec, src, partial, info, None, ff, palette)
                    rc, tail = await self._exec(item, job, plan)
            if rc != 0 or not partial.exists() or partial.stat().st_size == 0:
                partial.unlink(missing_ok=True)
                err = tail[-1] if tail else f"ffmpeg terminó con {rc}"
                raise ConvertError(err)
            if out.exists():   # appeared meanwhile: never overwrite
                out = output_path(src, folder, spec, self._reserved)
            os.replace(partial, out)
            item.output, item.partial = str(out), ""
        except asyncio.CancelledError:
            if partial is not None:
                partial.unlink(missing_ok=True)
            item.output = ""
            if self._closing:   # mpvd stopping: queued again on the next start
                item.status, item.message, item.progress, item.resume = "queued", "se reanudará", 0.0, True
                self._save()
            else:
                self._finish(item, "cancelled", "cancelada")
            raise
        except (ConvertError, AudioError, OSError) as exc:
            if partial is not None:
                partial.unlink(missing_ok=True)
            item.output = ""
            self._finish(item, "failed", "error", str(exc))
            raise RuntimeError(str(exc)) from exc
        finally:
            self._reserved -= reserved
        self._finish(item, "done", "completado por CPU" if fell_back else "completado")
        return {"output": item.output}

    async def _exec(self, item: ConvertItem, job: Job, plan: Plan) -> tuple[int, list[str]]:
        """Run the plan's commands in order (progress weighted per command); (return code, stderr tail)."""
        item.hw = plan.hw
        item.argv = [list(c) for c in plan.commands]
        tail: deque[str] = deque(maxlen=STDERR_TAIL)
        done_weight = 0.0
        t0 = time.monotonic()
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = _lower_priority
        for cmd, weight in zip(plan.commands, plan.weights, strict=True):
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, **kwargs)
            self._procs[item.id] = proc
            last_push = 0.0

            async def read_stderr(p: asyncio.subprocess.Process = proc) -> None:
                assert p.stderr is not None
                async for raw in p.stderr:
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        tail.append(line)

            async def read_progress(p: asyncio.subprocess.Process = proc, w: float = weight,
                                    base: float = done_weight) -> None:
                nonlocal last_push
                assert p.stdout is not None
                async for raw in p.stdout:
                    key, _, value = raw.decode("utf-8", "replace").strip().partition("=")
                    if key == "speed":
                        with contextlib.suppress(ValueError):
                            item.speed = round(float(value.rstrip("x")), 2)
                    elif key == "out_time_us" and value.isdigit() and plan.duration > 0:
                        frac = min(1.0, int(value) / 1e6 / plan.duration)
                        item.progress = min(0.99, base + w * frac)
                    elif key == "progress":
                        elapsed = time.monotonic() - t0
                        p_ = item.progress
                        item.eta = elapsed * (1 - p_) / p_ if p_ > 0.02 else None
                        parts = [f"{p_ * 100:.0f} %"]
                        if item.speed:
                            parts.append(f"{item.speed:g}x")
                        if item.eta is not None:
                            parts.append("ETA " + fmt_eta(item.eta))
                        if len(plan.commands) > 1:
                            parts.insert(0, "paleta" if base == 0 else "GIF")
                        if plan.hw == "vaapi":
                            parts.append("GPU")
                        item.message = " · ".join(parts)
                        now = time.monotonic()
                        if now - last_push >= 0.25:
                            last_push = now
                            self._changed(item)
                            job.report(item.progress, item.message)

            try:
                await asyncio.gather(read_progress(), read_stderr())
                rc = await proc.wait()
            except asyncio.CancelledError:
                await self._kill(proc)
                raise
            finally:
                self._procs.pop(item.id, None)
                item.stderr_tail = list(tail)
            if rc != 0:
                return rc, list(tail)
            done_weight += weight
        return 0, list(tail)

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 5)

    # -- starting conversions ---------------------------------------------------------------------

    def start_conversions(self, path: str, preset: str, options: dict[str, Any] | None, out_dir: str | None,
                          notify: str | None, recursive: bool = False) -> dict[str, Any]:
        try:
            spec = ConvertSpec.from_dict({**(options or {}), "preset": preset})
        except (ConvertError, TypeError, ValueError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        if not path:
            raise RpcError(INVALID_PARAMS, "path required")
        p = Path(os.path.expanduser(str(path).removeprefix("file://")))
        base = Path(os.path.expanduser(out_dir)) if out_dir else default_dir()
        target = notify or DEFAULT_NOTIFY
        if p.is_dir():
            folder_out = base / p.name
            files = media_files(p, spec.kind, recursive, skip=folder_out)[:MAX_FOLDER]
            if not files:
                raise RpcError(INVALID_PARAMS, "no hay archivos que convertir en " + str(p))
            spec.start = spec.end = None   # a range makes no sense for a whole folder
            group = uuid.uuid4().hex[:8]
            items = [self.submit(f, copy.deepcopy(spec), folder_out, target, group) for f in files]
            return {"count": len(items), "items": [i.to_dict() for i in items], "out_dir": str(folder_out),
                    "group": group}
        if not p.is_file():
            raise RpcError(INVALID_PARAMS, f"no existe: {p}")
        item = self.submit(p, spec, base, target)
        return {"count": 1, "items": [item.to_dict()], "out_dir": str(base), "group": ""}


def register(server: MpvdServer, service: ConvertService) -> None:
    d = server.dispatcher
    server.services["convert"] = True
    server.services["tasks"] = True
    ytdl = getattr(server, "ytdl", None)
    if ytdl is not None:   # downloads also appear live in «Tareas»
        previous = ytdl.downloads.on_change

        def chained(item: Any) -> None:
            if previous is not None:
                previous(item)
            service.download_changed(item)

        ytdl.downloads.on_change = chained

    @d.method("convert.presets")
    async def presets(ctx: RpcContext) -> dict[str, Any]:
        """Presets and option vocabularies for the «Convertir» menu, plus the default output folder."""
        return {"presets": usable_presets(), "heights": list(HEIGHTS), "qualities": list(QUALITIES),
                "quality_labels": QUALITY_LABELS, "audio_bitrates": list(AUDIO_BITRATE_CHOICES),
                "gif_widths": list(GIF_WIDTHS), "gif_fps": list(GIF_FPS), "default_dir": str(default_dir())}

    @d.method("convert.hw")
    async def hw(ctx: RpcContext, refresh: bool = False) -> dict[str, Any]:
        """What this machine encodes through VA-API (parsed from vainfo; cached until ``refresh``)."""
        return await service.hw_caps(refresh)

    @d.method("convert.start")
    async def start(ctx: RpcContext, path: str, preset: str, options: dict[str, Any] | None = None,
                    out_dir: str | None = None, notify: str | None = None, recursive: bool = False) -> dict[str, Any]:
        """Convert a file, or every media file of a folder (one task each, in ``<out_dir>/<folder>``).

        ``options``: height (0/1080/720/480), quality (high/normal/small), start/end (s), subtitles, container
        («small»: mp4/mkv), audio_bitrate, audio_track, gif_width, gif_fps, hw (auto/cpu/vaapi), speed (normal/fast)."""
        return service.start_conversions(path, preset, options, out_dir, notify, recursive)

    @d.method("convert.cut")
    async def cut(ctx: RpcContext, path: str, segments: list[dict[str, Any]], preset: str = "mp4",
                  joined: bool = False, options: dict[str, Any] | None = None,
                  out_dir: str | None = None, notify: str | None = None) -> dict[str, Any]:
        """H52 · save the chosen ranges of a file. `segments` are {start, end} in seconds.

        `joined=False` makes one file per segment (each named `<archivo> [inicio-fin].ext`); `joined=True` makes a
        single file with all of them stuck together, in one pass of ffmpeg."""
        ranges = []
        for seg in segments or []:
            if not isinstance(seg, dict) or seg.get("start") is None or seg.get("end") is None:
                raise RpcError(INVALID_PARAMS, "cada tramo necesita start y end")
            ranges.append([float(seg["start"]), float(seg["end"])])
        if not ranges:
            raise RpcError(INVALID_PARAMS, "no hay ningún tramo que guardar")
        # al unir se respeta EL ORDEN QUE LLEGA: es lo que pidió Ser, poder juntarlos como quiera. Sueltos da igual.
        if not joined:
            ranges.sort()
        base = dict(options or {})
        if joined or len(ranges) == 1:
            return service.start_conversions(path, preset, {**base, "ranges": ranges}, out_dir, notify)
        items, out = [], ""
        for a, b in ranges:
            res = service.start_conversions(path, preset, {**base, "start": a, "end": b}, out_dir, notify)
            items += res["items"]
            out = res["out_dir"]
        return {"count": len(items), "items": items, "out_dir": out, "group": ""}

    @d.method("convert.list")
    async def list_(ctx: RpcContext, include_finished: bool = True) -> list[dict[str, Any]]:
        """Conversion queue and history (running first, then queued, then newest finished)."""
        return service.list(include_finished)

    @d.method("convert.get")
    async def get(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """One conversion by id, with the ffmpeg argv and its last error lines."""
        item = service.get(id)
        if item is None:
            raise RpcError(NOT_FOUND, f"unknown conversion: {id}")
        return {**item.to_dict(), "argv": item.argv, "stderr": item.stderr_tail}

    @d.method("convert.cancel")
    async def cancel(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Cancel a queued or running conversion (the unfinished file is deleted)."""
        return {"cancelled": service.cancel(id)}

    @d.method("convert.retry")
    async def retry(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Queue a finished, failed or cancelled conversion again (a new file name if the old one exists)."""
        item = service.retry(id)
        if item is None:
            raise RpcError(NOT_FOUND, f"conversion not retryable: {id}")
        return item.to_dict()

    @d.method("convert.remove")
    async def remove(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Forget a finished conversion (the file is kept)."""
        return {"removed": service.remove(id)}

    @d.method("convert.clear")
    async def clear(ctx: RpcContext) -> dict[str, Any]:
        """Forget every finished conversion."""
        return {"removed": service.clear_finished()}

    def all_tasks(include_finished: bool) -> list[dict[str, Any]]:
        rows = [task_row(r, "convert") for r in service.list(include_finished)]
        if ytdl is not None:
            rows += [task_row(r, "download") for r in ytdl.downloads.list(include_finished)]
        # H62 · y TODO lo demás que pasa por detrás: subtítulos, traducción, índice por temas, intro, música…
        # Antes solo se veían conversiones y descargas, así que «lo que está trabajando» era media verdad.
        rows += job_rows(server.jobs.list(include_finished=include_finished))
        order = {"running": 0, "queued": 1}
        rows.sort(key=lambda r: (order.get(r["status"], 2),
                                 r["created_at"] if r["status"] in order else -(r["finished_at"] or r["created_at"])))
        return rows

    @d.method("tasks.list")
    async def tasks_list(ctx: RpcContext, include_finished: bool = True) -> dict[str, Any]:
        """Downloads and conversions in one list for the «Tareas» panel: type, title, status, progress, message and
        the actions that apply (cancel · retry, remove · folder)."""
        rows = all_tasks(include_finished)
        return {"tasks": rows, "active": sum(1 for r in rows if r["status"] in ("queued", "running"))}

    @d.method("tasks.clear")
    async def tasks_clear(ctx: RpcContext) -> dict[str, Any]:
        """Forget every finished download and conversion (files are kept)."""
        n = service.clear_finished()
        if ytdl is not None:
            n += ytdl.downloads.clear_finished()
        return {"removed": n}
