"""Live AI subtitles: chunked whisper.cpp transcription that follows playback (look-ahead) and resumes from cache.

One *task* per (file, model, language). Each task is ONE heavy job that walks a chunk plan; the next chunk is always the
first undone one at or after the playback cursor (``asr.seek`` just moves the cursor, no job cancellation), and chunks
behind the cursor are filled in afterwards. Results are merged into an SRT that mpv reloads (``sub-reload``) on every
``{"event":"asr"}`` push. State (segments + done chunks) is stored in the artifact cache so a task resumes instantly.
Only local files with a known duration are supported (URLs and live streams: ADR-023).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import AudioError, extract_wav, probe_duration, wav_duration
from mpvd.asr.engine import EngineError, WhisperEngine
from mpvd.asr.models import CATALOG, LIVE_ORDER, VAD_MODEL, ModelError, ModelStore, default_model_dirs
from mpvd.asr.srt import Segment, merge_segments, render_srt
from mpvd.hardware import hardware_info
from mpvd.hashing import file_hash
from mpvd.jobs import Job, Priority, Status
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.asr")

ARTIFACT = "asr"
STATE_VERSION = "2"   # 2: cues timed from word timestamps and the voice (H16, ADR-042)
DEFAULT_NOTIFY = "mu_subs"
# ADR-024: whisper always encodes a 30 s window, so a chunk costs the same whether it holds 20 or 28.5 s of audio;
# 28.5 s + PRE_ROLL + TAIL = 29.7 s still fits in one window (docs/BENCHMARKS.md).
DEFAULT_CHUNK = float(os.environ.get("MPV_UOS_ASR_CHUNK", "28.5"))
LEGACY_CHUNKS = (20.0,)   # earlier default: finished chunks cached with it are reused (see AsrTask.load_state)
PRE_ROLL = 0.8     # seconds of audio before the chunk start handed to whisper (context; cues there are dropped)
TAIL = 0.4         # seconds after the chunk end
MIN_CHUNK = 2.0
PROMPT_CHARS = 200  # end of the previous chunk's text handed to whisper as --prompt (style, names, punctuation)
FINAL = ("done", "failed", "cancelled")
LANG_RE = re.compile(r"^[a-z]{2,3}$")


def _live_rank(model: str) -> int:
    """How good a model is for live subtitles. -1 for the ones LIVE_ORDER does not rank (the English-only ones)."""
    return LIVE_ORDER.index(model) if model in LIVE_ORDER else -1


def plan_chunks(duration: float, chunk_seconds: float) -> list[tuple[float, float]]:
    chunks: list[tuple[float, float]] = []
    t = 0.0
    while t < duration - 0.05:
        end = min(t + chunk_seconds, duration)
        if duration - end < MIN_CHUNK:      # absorb a tiny tail into the last chunk
            end = duration
        chunks.append((t, end))
        t = end
    return chunks


def prompt_tail(text: str, limit: int = PROMPT_CHARS) -> str:
    """Last ``limit`` characters of ``text``, starting at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    return cut.split(" ", 1)[1] if " " in cut else cut


@dataclass
class AsrTask:
    id: str
    key: str
    path: str
    duration: float
    model: str
    language: str                       # requested: "auto" or ISO code
    purpose: str = "live"               # live | precompute
    audio_track: int | None = None
    translate: bool = False
    chunk_seconds: float = DEFAULT_CHUNK
    notify: str = DEFAULT_NOTIFY
    detected: str = ""
    chunks: list[tuple[float, float]] = field(default_factory=list)
    done: set[int] = field(default_factory=set)
    failed: dict[int, str] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    pos: float = 0.0
    srt_path: Path | None = None
    job: Job | None = None
    sessions: set[str] = field(default_factory=set)
    status: str = "queued"
    error: str | None = None
    rtf_elapsed: float = 0.0
    rtf_audio: float = 0.0
    last_chunk: tuple[float, float] | None = None
    saved: list[dict[str, Any]] = field(default_factory=list)   # SRT files saved from this task (subs.save)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    seq: int = 0

    # -- plan ---------------------------------------------------------------------

    def plan(self) -> None:
        self.chunks = plan_chunks(self.duration, self.chunk_seconds)

    def prompt_for(self, i: int) -> str | None:
        """Text of the previous chunk (when it is transcribed) as context for chunk ``i``."""
        if i <= 0 or (i - 1) not in self.done:
            return None
        a, b = self.chunks[i - 1]
        text = " ".join(s.text for s in self.segments if a - 0.01 <= s.start < b)
        return prompt_tail(text) or None

    def next_chunk(self) -> int | None:
        """First undone chunk covering or after the cursor; when everything ahead is done, the earliest behind."""
        ahead: int | None = None
        behind: int | None = None
        for i, (start, end) in enumerate(self.chunks):
            if i in self.done or i in self.failed:
                continue
            if end > self.pos:
                if ahead is None:
                    ahead = i
            elif behind is None:
                behind = i
        return ahead if ahead is not None else behind

    @property
    def complete(self) -> bool:
        return bool(self.chunks) and len(self.done) + len(self.failed) >= len(self.chunks) and not self.failed

    @property
    def progress(self) -> float:
        return len(self.done) / len(self.chunks) if self.chunks else 0.0

    def covered_ahead(self) -> float:
        """Seconds of transcribed audio available from the cursor onwards (what the viewer will see next)."""
        t = self.pos
        for i, (start, end) in enumerate(self.chunks):
            if end <= t:
                continue
            if i in self.done:
                t = end
            else:
                break
        return max(0.0, t - self.pos)

    @property
    def rtf(self) -> float | None:
        return round(self.rtf_elapsed / self.rtf_audio, 3) if self.rtf_audio > 0 else None

    def to_dict(self, with_segments: bool = False) -> dict[str, Any]:
        d = {
            "id": self.id, "key": self.key, "path": self.path, "duration": self.duration, "model": self.model,
            "language": self.language, "detected": self.detected, "purpose": self.purpose, "translate": self.translate,
            "status": self.status, "error": self.error, "progress": round(self.progress, 4),
            "done": len(self.done), "failed": len(self.failed), "total": len(self.chunks), "complete": self.complete,
            "pos": round(self.pos, 2), "ahead": round(self.covered_ahead(), 1), "rtf": self.rtf,
            "srt": str(self.srt_path) if self.srt_path else None, "cues": len(self.segments),
            "chunk_seconds": self.chunk_seconds, "last_chunk": list(self.last_chunk) if self.last_chunk else None,
            "job": self.job.id if self.job else None, "updated_at": self.updated_at, "seq": self.seq,
            "saved": [dict(x) for x in self.saved],
        }
        if with_segments:
            d["segments"] = [s.to_dict() for s in self.segments]
        return d

    def state(self) -> dict[str, Any]:
        return {"segments": [s.to_dict() for s in self.segments], "done": sorted(self.done), "detected": self.detected,
                "chunk_seconds": self.chunk_seconds, "duration": self.duration, "complete": self.complete,
                "saved": self.saved}

    def load_state(self, data: dict[str, Any]) -> None:
        """Restore cached progress. A state planned with another chunk size (the old 20 s default) keeps every new
        chunk fully covered by old finished chunks; the rest is transcribed again (merge replaces its cues)."""
        if not isinstance(data, dict):
            return
        self.segments = [Segment.from_dict(s) for s in data.get("segments", [])]
        self.detected = str(data.get("detected") or "")
        self.saved = [dict(x) for x in data.get("saved") or [] if isinstance(x, dict)]
        old_chunk = float(data.get("chunk_seconds", self.chunk_seconds))
        done_old = {int(i) for i in data.get("done", [])}
        if abs(old_chunk - self.chunk_seconds) <= 1e-6:
            self.done = {i for i in done_old if 0 <= i < len(self.chunks)}
            return
        if data.get("complete"):
            self.done = set(range(len(self.chunks)))
            return
        old_plan = plan_chunks(float(data.get("duration") or self.duration), old_chunk)
        covered = sorted(old_plan[i] for i in done_old if 0 <= i < len(old_plan))
        merged: list[list[float]] = []
        for a, b in covered:
            if merged and a <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self.done = {i for i, (a, b) in enumerate(self.chunks)
                     if any(ma - 1e-6 <= a and b <= mb + 1e-6 for ma, mb in merged)}


class AsrService:
    def __init__(self, server: MpvdServer):
        self.server = server
        settings = server.settings
        self.root = server.root
        self.models = ModelStore(default_model_dirs(self.root, settings.data_dir))
        cpu = os.cpu_count() or 2
        env_threads = os.environ.get("MPV_UOS_ASR_THREADS")
        self.engine = WhisperEngine(
            self.models, self.root, threads=int(env_threads) if env_threads else None,
            use_vad=os.environ.get("MPV_UOS_ASR_VAD", "1") != "0",
            beam_size=int(os.environ["MPV_UOS_ASR_BEAM"]) if os.environ.get("MPV_UOS_ASR_BEAM") else None,
            best_of=int(os.environ["MPV_UOS_ASR_BEST_OF"]) if os.environ.get("MPV_UOS_ASR_BEST_OF") else None,
        )
        self.cpu = cpu
        self.srt_dir = settings.cache_dir / "asr"
        self.tmp_dir = settings.cache_dir / "tmp" / "asr"
        self.tasks: dict[str, AsrTask] = {}
        self.tier = str(hardware_info().get("tier", "small"))

    # -- helpers --------------------------------------------------------------------

    def recommended(self, purpose: str = "live") -> str:
        return self.models.pick(self.tier, purpose)

    def status(self) -> dict[str, Any]:
        return {
            "engine": self.engine.status(), "tier": self.tier,
            "recommended": {"live": self.recommended("live"), "precompute": self.recommended("precompute")},
            "models_present": self.models.present(),
            "tasks": [t.to_dict() for t in self.tasks.values()],
        }

    def find_task(self, key: str, model: str, language: str, translate: bool,
                  audio_track: int | None = None) -> AsrTask | None:
        for t in self.tasks.values():
            if (t.key == key and t.model == model and t.language == language and t.translate == translate
                    and t.audio_track == audio_track):
                return t
        return None

    def task_for_path(self, path: str) -> AsrTask | None:
        for t in self.tasks.values():
            if t.path == path:
                return t
        return None

    def _srt_path(self, key: str, model: str, language: str, translate: bool,
                  audio_track: int | None = None) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        suffix = ".en-translated" if translate else ""
        # the audio track is part of the identity: a film with its original voices and a dub must not share subtitles
        track = "" if audio_track is None else f".a{int(audio_track)}"
        return self.srt_dir / safe / f"{model}.{language}{track}{suffix}.srt"

    def _cache_params(self, task: AsrTask) -> dict[str, Any]:
        return {"lang": task.language, "translate": task.translate, "chunk": task.chunk_seconds,
                "audio": task.audio_track}

    def _write(self, task: AsrTask) -> None:
        assert task.srt_path is not None
        task.srt_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = task.srt_path.with_suffix(".srt.tmp")
        tmp.write_text(render_srt(task.segments), encoding="utf-8")
        tmp.replace(task.srt_path)
        task.updated_at = time.time()
        task.seq += 1
        self.server.cache.put(task.key, ARTIFACT, model=task.model, version=STATE_VERSION,
                              params=self._cache_params(task), data=task.state())

    def _push(self, task: AsrTask, final: bool = False) -> None:
        payload = {"event": "asr", "task": task.to_dict()}
        for sid in list(task.sessions):
            session = self.server.sessions.get(sid)
            if session is None or not session.connected:
                task.sessions.discard(sid)
                continue
            session.push_event(task.notify, "asr:" + task.id, f"{task.status}:{task.seq}", payload, min_interval=0.5,
                               final=final)

    async def _resolve(self, path: str) -> tuple[str, float]:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not path.startswith("file://"):
            raise RpcError(UNAVAILABLE, "los subtítulos IA solo funcionan con archivos locales por ahora (ADR-023)")
        p = Path(path.removeprefix("file://"))
        if not p.is_file():
            raise RpcError(NOT_FOUND, f"no existe: {p}")
        try:
            key = (await asyncio.to_thread(file_hash, p)).key
        except OSError as exc:
            raise RpcError(NOT_FOUND, f"cannot read {p}: {exc}") from exc
        duration = await asyncio.to_thread(probe_duration, str(p))
        if not duration or duration <= 0:
            raise RpcError(UNAVAILABLE, "duración desconocida: los directos no se admiten todavía (ADR-023)")
        return key, float(duration)

    # -- tasks ----------------------------------------------------------------------

    async def start(self, path: str, language: str = "auto", model: str | None = None, purpose: str = "live",
                    time_pos: float = 0.0, audio_track: int | None = None, translate: bool = False,
                    chunk_seconds: float | None = None, notify: str = DEFAULT_NOTIFY,
                    session_id: str | None = None) -> AsrTask:
        language = (language or "auto").lower()
        if language != "auto" and not LANG_RE.match(language):
            raise RpcError(INVALID_PARAMS, f"bad language {language!r}")
        purpose = purpose if purpose in ("live", "precompute") else "live"
        auto = not model or model == "auto"
        if auto:
            model = self.recommended(purpose)
        if model != VAD_MODEL and model not in CATALOG:
            raise RpcError(INVALID_PARAMS, f"unknown model {model!r}")
        if not self.engine.available:
            raise RpcError(UNAVAILABLE, "whisper-cli no encontrado: ejecuta tools/vendor_whisper.sh")
        key, duration = await self._resolve(path)
        chunk = float(chunk_seconds or DEFAULT_CHUNK)
        if auto:
            model = await self._adopt_model(key, language, translate, chunk, audio_track) or model
        if self.models.find(model) is None:
            raise RpcError(UNAVAILABLE, f"el modelo {model} no está descargado (asr.models.download)")
        task = self.find_task(key, model, language, translate, audio_track)
        if task is not None and abs(task.chunk_seconds - chunk) < 1e-6:
            if session_id:
                task.sessions.add(session_id)
            task.pos = max(0.0, float(time_pos))
            if purpose == "live" and task.purpose == "precompute" and not task.complete:
                # the viewer now needs it: restart as an interactive job
                self._cancel_job(task)
                task.purpose = "live"
                task.notify = notify
                self._submit(task, session_id)
            elif task.status in ("failed", "cancelled") and not task.complete:
                task.failed.clear()
                task.purpose = purpose
                self._submit(task, session_id if purpose == "live" else None)
            return task
        task = AsrTask(id=uuid.uuid4().hex[:12], key=key, path=str(Path(path.removeprefix("file://"))),
                       duration=duration, model=model, language=language, purpose=purpose, audio_track=audio_track,
                       translate=translate, chunk_seconds=chunk, notify=notify, pos=max(0.0, float(time_pos)))
        task.plan()
        task.srt_path = self._srt_path(key, model, language, translate, audio_track)
        if session_id:
            task.sessions.add(session_id)
        entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT, model, STATE_VERSION,
                                        self._cache_params(task))
        for legacy in LEGACY_CHUNKS:
            if entry is not None:
                break
            if abs(legacy - chunk) > 1e-6:
                entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT, model, STATE_VERSION,
                                                {**self._cache_params(task), "chunk": legacy})
        if entry is not None:
            task.load_state(entry.data)
        # always (re)write the SRT so mpv can sub-add it right away, even when empty
        await asyncio.to_thread(self._write_srt_only, task)
        self.tasks[task.id] = task
        if task.complete:
            task.status = "done"
        else:
            self._submit(task, session_id if purpose == "live" else None)
        self._prune_tasks()
        return task

    async def _adopt_model(self, key: str, language: str, translate: bool, chunk: float,
                           audio_track: int | None = None) -> str | None:
        """With ``model=auto``, reuse a finished transcription of this file made with another model (typically the
        pre-subtitling one, small-q8_0, while live uses base) instead of starting a worse one from scratch."""
        best: AsrTask | None = None
        for t in self.tasks.values():
            if (t.key == key and t.language == language and t.translate == translate and t.complete
                    and t.audio_track == audio_track
                    and t.model in CATALOG and self.models.find(t.model) is not None):
                # CATALOG holds English-only models (base.en…) that LIVE_ORDER does not rank: never index() blindly
                if best is None or _live_rank(t.model) > _live_rank(best.model):
                    best = t
        if best is not None:
            return best.model
        for name in reversed(LIVE_ORDER):
            if self.models.find(name) is None:
                continue
            probe = AsrTask(id="", key=key, path="", duration=0.0, model=name, language=language, translate=translate,
                            chunk_seconds=chunk, audio_track=audio_track)
            for c in (chunk, *LEGACY_CHUNKS):
                entry = await asyncio.to_thread(self.server.cache.get, key, ARTIFACT, name, STATE_VERSION,
                                                {**self._cache_params(probe), "chunk": c})
                if entry is not None and isinstance(entry.data, dict) and entry.data.get("complete"):
                    return name
        return None

    def _write_srt_only(self, task: AsrTask) -> None:
        assert task.srt_path is not None
        task.srt_path.parent.mkdir(parents=True, exist_ok=True)
        task.srt_path.write_text(render_srt(task.segments), encoding="utf-8")

    def _submit(self, task: AsrTask, session_id: str | None) -> None:
        priority = Priority.INTERACTIVE if task.purpose == "live" else Priority.PRECOMPUTE
        task.status = "queued"
        task.error = None
        task.job = self.server.jobs.submit(f"asr.{task.purpose}", lambda job: self._run(task, job), priority=priority,
                                           heavy=True, session_id=session_id,
                                           meta={"asr": task.id, "path": task.path, "model": task.model})

    def _cancel_job(self, task: AsrTask) -> None:
        if task.job is not None and task.job.status in (Status.QUEUED, Status.RUNNING):
            self.server.jobs.cancel(task.job.id)
        task.job = None

    def _prune_tasks(self, keep: int = 20) -> None:
        finished = [t for t in self.tasks.values() if t.status in FINAL and not t.sessions]
        finished.sort(key=lambda t: t.updated_at)
        for t in finished[: max(0, len(finished) - keep)]:
            self.tasks.pop(t.id, None)

    async def _run(self, task: AsrTask, job: Job) -> dict[str, Any]:
        task.status = "running"
        task.error = None
        self._push(task)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            while True:
                i = task.next_chunk()
                if i is None:
                    break
                guardian = self.server.guardian
                while guardian.throttled and task.purpose != "live":
                    await asyncio.sleep(min(2.0, max(0.2, guardian.throttle_remaining)))
                start, end = task.chunks[i]
                ext_start = max(0.0, start - PRE_ROLL)
                ext_end = min(task.duration, end + TAIL)
                wav = self.tmp_dir / f"{task.id}-{i}.wav"
                try:
                    await extract_wav(task.path, ext_start, ext_end - ext_start, wav, task.audio_track)
                    lang = task.detected or (None if task.language == "auto" else task.language)
                    result = await self.engine.transcribe(wav, task.model, lang, translate=task.translate,
                                                          prompt=task.prompt_for(i), audio_seconds=wav_duration(wav))
                except (AudioError, EngineError, ModelError) as exc:
                    task.failed[i] = str(exc)
                    log.warning("asr %s chunk %d failed: %s", task.id, i, exc)
                    if len(task.failed) >= 3 and not task.done:
                        raise
                    continue
                finally:
                    wav.unlink(missing_ok=True)
                if task.language == "auto" and not task.detected and result.language and result.segments:
                    task.detected = result.language
                fresh = [Segment(s.start + ext_start, s.end + ext_start, s.text) for s in result.segments]
                task.segments = merge_segments(task.segments, fresh, start, end)
                task.done.add(i)
                task.failed.pop(i, None)
                task.last_chunk = (start, end)
                task.rtf_elapsed += result.elapsed
                task.rtf_audio += result.audio_seconds
                await asyncio.to_thread(self._write, task)
                job.report(task.progress, f"{len(task.done)}/{len(task.chunks)} · {end:.0f}s")
                self._push(task)
        except asyncio.CancelledError:
            task.status = "cancelled"
            await asyncio.to_thread(self._write, task)
            self._push(task, final=True)
            raise
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"
            self._push(task, final=True)
            raise
        task.status = "done" if not task.failed else "failed"
        if task.failed:
            task.error = next(iter(task.failed.values()))
        task.job = None
        self._push(task, final=True)
        return task.to_dict()

    def seek(self, task_id: str, time_pos: float) -> AsrTask:
        task = self.tasks.get(task_id)
        if task is None:
            raise RpcError(NOT_FOUND, f"no task {task_id}")
        task.pos = max(0.0, float(time_pos))
        return task

    def stop(self, task_id: str) -> AsrTask:
        task = self.tasks.get(task_id)
        if task is None:
            raise RpcError(NOT_FOUND, f"no task {task_id}")
        if task.job is not None and task.status in ("queued", "running"):
            self._cancel_job(task)
            if task.status == "queued":
                task.status = "cancelled"
                self._push(task, final=True)
        return task

    def mark_saved(self, task: AsrTask, path: str, complete: bool) -> None:
        """Remember that this transcription was saved as ``path`` (mu-subs then adopts that file when mpv auto-loads
        it next to the video instead of adding the same subtitles twice)."""
        task.saved = [x for x in task.saved if x.get("path") != path]
        task.saved.append({"path": path, "complete": bool(complete), "cues": len(task.segments), "at": time.time()})
        task.seq += 1
        if task.model != "injected":
            self.server.cache.put(task.key, ARTIFACT, model=task.model, version=STATE_VERSION,
                                  params=self._cache_params(task), data=task.state())

    async def close(self) -> None:
        for t in list(self.tasks.values()):
            with contextlib.suppress(Exception):
                self._cancel_job(t)

    # -- models ---------------------------------------------------------------------

    def download_model(self, name: str, notify: str | None, session_id: str | None) -> Job:
        if name != VAD_MODEL and name not in CATALOG:
            raise RpcError(INVALID_PARAMS, f"unknown model {name!r}")

        target = notify or DEFAULT_NOTIFY

        def push(job: Job, status: str | None = None, final: bool = False) -> None:
            # the job is NOT tied to the session (a download must survive the player closing), so progress is
            # pushed by hand to whoever asked for it while their mpv is still around
            if session_id is None:
                return
            session = self.server.sessions.get(session_id)
            if session is None or not session.connected:
                return
            data = job.to_dict()
            data["status"] = status or data.get("status")
            session.push_event(target, "asr-model:" + name, f"{data['status']}:{job.progress:.2f}",
                               {"event": "asr-model", "model": name, "job": data}, min_interval=0.5, final=final)

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def report(frac: float, message: str) -> None:
                job.report(frac, message)
                push(job)

            def progress(frac: float, message: str) -> None:   # called from the download thread
                loop.call_soon_threadsafe(report, frac, message)

            push(job)
            try:
                path = await self.models.download(name, progress=progress)
            except asyncio.CancelledError:
                push(job, "cancelled", final=True)
                raise
            except Exception as exc:
                job.report(None, str(exc))
                push(job, "failed", final=True)
                raise
            job.report(1.0, "listo")
            push(job, "done", final=True)
            return {"name": name, "path": str(path)}

        return self.server.jobs.submit(f"asr.model.{name}", body, priority=Priority.INTERACTIVE, heavy=False,
                                       session_id=None, meta={"notify": target, "model": name, "session": session_id})


def register(server: MpvdServer, service: AsrService) -> None:  # noqa: C901 - flat list of handlers
    d = server.dispatcher
    server.services["asr"] = service.engine.available

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    @d.method("asr.status")
    async def status(ctx: RpcContext, id: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Engine state, recommended models and tasks (or one task by id)."""
        if id:
            t = service.tasks.get(id)
            if t is None:
                raise RpcError(NOT_FOUND, f"no task {id}")
            return t.to_dict()
        return service.status()

    @d.method("asr.models")
    async def models(ctx: RpcContext) -> dict[str, Any]:
        """Catalogue of whisper models: present/downloadable, recommended per purpose, hardware tier."""
        return {"models": [m.to_dict() for m in service.models.list()], "tier": service.tier,
                "recommended": {"live": service.recommended("live"), "precompute": service.recommended("precompute")},
                "download_dir": str(service.models.download_dir()), "engine": service.engine.status()}

    @d.method("asr.models.download")
    async def models_download(ctx: RpcContext, name: str, notify: str | None = None) -> dict[str, Any]:
        """Download a model in the background (job progress is pushed to ``notify``)."""
        return service.download_model(name, notify, _sid(ctx)).to_dict()

    @d.method("asr.models.remove")
    async def models_remove(ctx: RpcContext, name: str) -> dict[str, Any]:
        """Delete a downloaded model file."""
        return {"removed": service.models.remove(name)}

    @d.method("asr.start")
    async def start(ctx: RpcContext, path: str, language: str = "auto", model: str | None = None,
                    time_pos: float = 0.0, audio_track: int | None = None, translate: bool = False,
                    chunk_seconds: float | None = None, purpose: str = "live",
                    notify: str = DEFAULT_NOTIFY) -> dict[str, Any]:
        """Start (or resume) live transcription of a local file; returns the task with its SRT path."""
        t = await service.start(path, language, model, purpose, time_pos, audio_track, translate, chunk_seconds,
                                notify, _sid(ctx))
        return t.to_dict()

    @d.method("asr.precompute")
    async def precompute(ctx: RpcContext, path: str, language: str = "auto", model: str | None = None,
                         audio_track: int | None = None, translate: bool = False, chunk_seconds: float | None = None,
                         notify: str = DEFAULT_NOTIFY) -> dict[str, Any]:
        """Transcribe a file at low priority (next playlist item); survives the session that asked."""
        t = await service.start(path, language, model, "precompute", 0.0, audio_track, translate, chunk_seconds, notify,
                                _sid(ctx))
        return t.to_dict()

    @d.method("asr.seek")
    async def seek(ctx: RpcContext, id: str, time_pos: float) -> dict[str, Any]:  # noqa: A002
        """Move the look-ahead cursor (call on seeks and periodically during playback)."""
        return service.seek(id, time_pos).to_dict()

    @d.method("asr.stop")
    async def stop(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Cancel a task's job (partial results stay cached)."""
        return service.stop(id).to_dict()

    @d.method("asr.inject")
    async def inject(ctx: RpcContext, path: str, segments: list[dict[str, Any]], duration: float | None = None,
                     language: str = "es", done_fraction: float = 1.0) -> dict[str, Any]:
        """Test/tooling hook: register a transcription for ``path`` from given cues (no whisper involved); finished
        unless ``done_fraction`` < 1 (then only that share of the chunks counts as transcribed)."""
        key, dur = await service._resolve(path)
        task = AsrTask(id=uuid.uuid4().hex[:12], key=key, path=str(Path(path.removeprefix("file://"))),
                       duration=float(duration or dur or 0.0), model="injected", language=language, purpose="precompute")
        task.plan()
        n_done = round(len(task.chunks) * max(0.0, min(1.0, float(done_fraction))))
        task.done = set(range(n_done))
        task.segments = [Segment.from_dict(s) for s in segments]
        task.status = "done" if task.complete else "cancelled"
        task.detected = language
        task.updated_at = time.time()
        service.tasks[task.id] = task
        return task.to_dict()

    @d.method("asr.search")
    async def search(ctx: RpcContext, q: str, path: str | None = None, id: str | None = None,  # noqa: A002
                     limit: int = 20) -> dict[str, Any]:
        """Accent-insensitive search in transcribed cues (of one task/file, or of every known task)."""
        import unicodedata  # noqa: PLC0415

        def fold(t: str) -> str:
            return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")

        needle = fold(q or "").strip()
        if not needle:
            raise RpcError(INVALID_PARAMS, "empty query")
        tasks = list(service.tasks.values())
        if id:
            tasks = [t for t in tasks if t.id == id]
        elif path:
            p = str(Path(path.removeprefix("file://")))
            tasks = [t for t in tasks if t.path == p]
        hits: list[dict[str, Any]] = []
        for t in sorted(tasks, key=lambda t: -t.updated_at):
            for s in t.segments:
                if needle in fold(s.text):
                    hits.append({"task": t.id, "path": t.path, "start": s.start, "end": s.end, "text": s.text})
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
        return {"query": q, "hits": hits, "tasks": len(tasks)}

    @d.method("asr.segments")
    async def segments(ctx: RpcContext, id: str, start: float | None = None,  # noqa: A002
                       end: float | None = None) -> dict[str, Any]:
        """Transcribed cues of a task, optionally limited to a time range."""
        t = service.tasks.get(id)
        if t is None:
            raise RpcError(NOT_FOUND, f"no task {id}")
        rows = [s for s in t.segments if (start is None or s.end >= start) and (end is None or s.start <= end)]
        return {"id": t.id, "segments": [s.to_dict() for s in rows], "text": " ".join(s.text for s in rows)}
