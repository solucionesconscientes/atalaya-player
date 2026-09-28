"""Asyncio job queue with priorities, cancellation, progress and guardian-aware throttling.

Priorities (lower runs first): URGENT (subtitles about to be displayed), INTERACTIVE (user actions),
PRECOMPUTE (next playlist item...), INDEX (library indexing). Jobs marked ``heavy`` wait while the
performance guardian is throttling; light jobs always proceed.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import heapq
import itertools
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mpvd.guardian import PerformanceGuardian

log = logging.getLogger("mpvd.jobs")


class Priority(enum.IntEnum):
    URGENT = 0
    INTERACTIVE = 1
    PRECOMPUTE = 2
    INDEX = 3

    @classmethod
    def parse(cls, value: Any) -> Priority:
        if isinstance(value, Priority):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[str(value).upper()]


class Status(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


JobFn = Callable[["Job"], Awaitable[Any]]


@dataclass
class Job:
    name: str
    fn: JobFn = field(repr=False)
    priority: Priority = Priority.INTERACTIVE
    heavy: bool = False
    session_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: Status = Status.QUEUED
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    _task: asyncio.Task[Any] | None = field(default=None, repr=False)
    _queue: JobQueue | None = field(default=None, repr=False)
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def report(self, progress: float | None = None, message: str | None = None) -> None:
        """Called by the job body to publish progress (0..1) and a short message."""
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            self.message = message
        if self._queue is not None:
            self._queue._changed(self)

    async def wait(self, timeout: float | None = None) -> Any:
        await asyncio.wait_for(self._done.wait(), timeout)
        return self.result

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "priority": self.priority.name.lower(),
            "heavy": self.heavy,
            "session_id": self.session_id,
            "status": self.status.value,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "meta": self.meta,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobQueue:
    def __init__(
        self,
        workers: int = 2,
        guardian: PerformanceGuardian | None = None,
        history: int = 200,
        on_change: Callable[[Job], None] | None = None,
    ):
        self.workers = max(1, workers)
        self.guardian = guardian or PerformanceGuardian()
        self.guardian.add_listener(self._on_throttle)
        self._heap: list[tuple[int, int, Job]] = []
        self._seq = itertools.count()
        self._jobs: dict[str, Job] = {}
        self._history: deque[str] = deque(maxlen=history)
        self._wakeup = asyncio.Event()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._on_change = on_change
        self._throttle_timer: asyncio.TimerHandle | None = None

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [asyncio.create_task(self._worker(i), name=f"mpvd-worker-{i}") for i in range(self.workers)]

    async def stop(self) -> None:
        self._running = False
        for job in list(self._jobs.values()):
            if job.status in (Status.QUEUED, Status.RUNNING):
                self.cancel(job.id)
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._workers = []

    # -- submission ------------------------------------------------------------

    def submit(
        self,
        name: str,
        fn: JobFn,
        *,
        priority: Priority | int | str = Priority.INTERACTIVE,
        heavy: bool = False,
        session_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Job:
        job = Job(name=name, fn=fn, priority=Priority.parse(priority), heavy=heavy, session_id=session_id,
                  meta=meta or {})
        job._queue = self
        self._jobs[job.id] = job
        heapq.heappush(self._heap, (int(job.priority), next(self._seq), job))
        self._changed(job)
        self._wakeup.set()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status not in (Status.QUEUED, Status.RUNNING):
            return False
        if job.status == Status.RUNNING and job._task is not None:
            job._task.cancel()
            return True
        # queued: mark and let the worker skip it
        self._finish(job, Status.CANCELLED)
        return True

    def list(self, include_finished: bool = True, session_id: str | None = None) -> list[dict[str, Any]]:
        jobs = [j for j in self._jobs.values() if include_finished or j.status in (Status.QUEUED, Status.RUNNING)]
        if session_id is not None:
            jobs = [j for j in jobs if j.session_id == session_id]
        jobs.sort(key=lambda j: (j.status not in (Status.RUNNING, Status.QUEUED), j.priority, j.created_at))
        return [j.to_dict() for j in jobs]

    def cancel_session(self, session_id: str) -> int:
        n = 0
        for job in list(self._jobs.values()):
            if job.session_id == session_id and job.status in (Status.QUEUED, Status.RUNNING):
                n += int(self.cancel(job.id))
        return n

    def pending(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in (Status.QUEUED, Status.RUNNING))

    # -- internals -------------------------------------------------------------

    def _changed(self, job: Job) -> None:
        if self._on_change is not None:
            try:
                self._on_change(job)
            except Exception:  # noqa: BLE001
                log.exception("on_change callback failed")

    def _finish(self, job: Job, status: Status, result: Any = None, error: str | None = None) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.finished_at = time.time()
        if status == Status.DONE:
            job.progress = 1.0
        job._done.set()
        self._history.append(job.id)
        # Trim finished jobs beyond the history window.
        finished = [j for j in self._jobs.values() if j.status not in (Status.QUEUED, Status.RUNNING)]
        if len(finished) > self._history.maxlen:  # type: ignore[arg-type]
            finished.sort(key=lambda j: j.finished_at or 0)
            for old in finished[: len(finished) - self._history.maxlen]:  # type: ignore[operator]
                self._jobs.pop(old.id, None)
        self._changed(job)

    def _on_throttle(self, throttled: bool) -> None:
        self._wakeup.set()
        if throttled:
            # Wake workers again when the cooldown expires.
            loop = asyncio.get_event_loop()
            if self._throttle_timer is not None:
                self._throttle_timer.cancel()
            self._throttle_timer = loop.call_later(self.guardian.throttle_remaining + 0.05, self._wakeup.set)

    def _pop_runnable(self) -> Job | None:
        throttled = self.guardian.throttled
        skipped: list[tuple[int, int, Job]] = []
        picked: Job | None = None
        while self._heap:
            item = heapq.heappop(self._heap)
            job = item[2]
            if job.status != Status.QUEUED:
                continue  # cancelled while queued
            if job.heavy and throttled:
                skipped.append(item)
                continue
            picked = job
            break
        for item in skipped:
            heapq.heappush(self._heap, item)
        if picked is None and skipped and throttled:
            loop = asyncio.get_event_loop()
            if self._throttle_timer is None or self._throttle_timer.cancelled():
                self._throttle_timer = loop.call_later(self.guardian.throttle_remaining + 0.05, self._wakeup.set)
        return picked

    async def _worker(self, index: int) -> None:
        while self._running:
            job = self._pop_runnable()
            if job is None:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            self._wakeup.set()  # let other workers re-check immediately
            job.status = Status.RUNNING
            job.started_at = time.time()
            job._task = asyncio.current_task()
            self._changed(job)
            try:
                result = await job.fn(job)
            except asyncio.CancelledError:
                self._finish(job, Status.CANCELLED)
                if not self._running:
                    raise
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("job %s (%s) failed: %s", job.id, job.name, exc)
                self._finish(job, Status.FAILED, error=f"{type(exc).__name__}: {exc}")
                continue
            finally:
                job._task = None
            self._finish(job, Status.DONE, result=result)
