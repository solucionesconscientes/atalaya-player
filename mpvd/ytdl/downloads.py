"""Download queue on top of the job queue: one yt-dlp subprocess per item, JSON progress lines, cancel/retry,
persistent history and target folders (XDG user dirs by default).

Progress protocol (docs/YTDLP.md §6): stdout lines prefixed ``MU_PROGRESS`` (download hook), ``MU_PP`` (postprocessor
hook) and ``MU_DONE`` (``--print after_move``), everything else on stdout is ignored; stderr keeps the last lines for
error reporting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.brand import folder_name
from mpvd.jobs import Job, JobQueue, Priority, Status
from mpvd.ytdl.binary import YtdlpBinary
from mpvd.ytdl.presets import (
    DEFAULT_SUB_LANGS,
    DEFAULT_TEMPLATE,
    DONE_PREFIX,
    POSTPROCESS_PREFIX,
    SUBS_PREFIX,
    PROGRESS_PREFIX,
    DownloadSpec,
    build_args,
)

log = logging.getLogger("mpvd.ytdl.downloads")

FINAL = ("done", "failed", "cancelled")
MAX_HISTORY = 200
STDERR_TAIL = 12


# -- target folders --------------------------------------------------------------------------------


def _xdg_user_dir(name: str) -> Path | None:
    """Read ``XDG_<NAME>_DIR`` from ``~/.config/user-dirs.dirs`` (Linux); None elsewhere or when unset."""
    if sys.platform in ("win32", "darwin"):
        return None
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "user-dirs.dirs"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(rf'^XDG_{name}_DIR="?([^"\n]+)"?', text, re.MULTILINE)
    if not m:
        return None
    raw = m.group(1).replace("$HOME", str(Path.home()))
    return Path(os.path.expandvars(raw))


def default_media_dir(kind: str) -> Path:
    """``<Videos|Music>/MPV-UOS`` following the user's XDG dirs, falling back to ``~/Videos`` / ``~/Music``."""
    name = "VIDEOS" if kind == "video" else "MUSIC"
    base = _xdg_user_dir(name)
    if base is None or not base.is_dir():
        base = Path.home() / ("Videos" if kind == "video" else "Music")
        if not base.is_dir():
            base = Path.home() / "Downloads"
    return base / folder_name()


# yt-dlp --cookies-from-browser BROWSER[+KEYRING][:PROFILE][::CONTAINER] (browsers from its --help, 2026.08.19)
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "safari", "whale")
_BROWSER_RE = re.compile(r"^(%s)([+:][^\x00-\x1f]*)?$" % "|".join(BROWSERS))  # argv, no shell: spaces are fine


# failures that another yt-dlp version cannot fix: the video itself is not available to this user
PERMANENT_ERRORS = re.compile(
    r"private video|sign in|log ?in|members.only|not available in your country|geo.?restrict|drm|"
    r"video unavailable|has been removed|copyright|age.?restricted|premieres in|live event will begin|"
    r"no space left|permission denied|already been recorded in the archive|http error 404|cookies|"
    r"requested format is not available", re.IGNORECASE)


def worth_nightly(stderr_tail: list[str]) -> bool:
    """A yt-dlp failure the nightly build may fix: an ERROR that is not about the video's availability or the disk."""
    errors = [ln for ln in stderr_tail if ln.startswith("ERROR")]
    return bool(errors) and not any(PERMANENT_ERRORS.search(ln) for ln in errors)


def valid_browser(value: str) -> bool:
    return bool(value) and _BROWSER_RE.match(value) is not None


@dataclass
class DownloadSettings:
    video_dir: str = ""
    audio_dir: str = ""
    template: str = DEFAULT_TEMPLATE
    auto_update: bool = True  # daily yt-dlp update (verified against the official checksums)
    container: str = "mp4"
    sub_langs: str = DEFAULT_SUB_LANGS
    subtitles: bool = False
    chapters: bool = True
    thumbnail: bool = False
    metadata: bool = True
    sponsorblock: str = "none"
    concurrent: int = 2
    rate_limit: str = ""        # yt-dlp -r for every download ("" = no limit; "2M", "500K")
    archive: bool = True        # lists, channels and batches skip what was already downloaded
    list_folders: bool = True   # a list or channel goes to its own folder, numbered
    cookies_browser: str = ""   # «usar mi sesión del navegador» (off): yt-dlp --cookies-from-browser <this>
    nightly_fallback: bool = True  # retry a failed download once with the nightly yt-dlp

    def session_args(self) -> list[str]:
        """``--cookies-from-browser`` when the user chose a browser (never by default; DRM stays out of reach)."""
        return ["--cookies-from-browser", self.cookies_browser] if valid_browser(self.cookies_browser) else []

    def resolved_dir(self, kind: str) -> Path:
        raw = self.video_dir if kind == "video" else self.audio_dir
        return Path(os.path.expanduser(raw)) if raw else default_media_dir(kind)

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "video_dir_resolved": str(self.resolved_dir("video")),
                "audio_dir_resolved": str(self.resolved_dir("audio"))}

    @classmethod
    def load(cls, path: Path) -> DownloadSettings:
        s = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        s.update(data if isinstance(data, dict) else {})
        return s

    def update(self, data: dict[str, Any]) -> None:
        for k, v in data.items():
            if k in self.__dataclass_fields__ and not k.endswith("_resolved"):
                cur = getattr(self, k)
                if isinstance(cur, bool):
                    setattr(self, k, bool(v))
                elif isinstance(cur, int):
                    setattr(self, k, int(v))
                else:
                    setattr(self, k, "" if v is None else str(v))
        env_dir = os.environ.get("MPV_UOS_DOWNLOAD_DIR")  # tests / portable installs: overrides empty dirs
        if env_dir:
            self.video_dir = self.video_dir or env_dir
            self.audio_dir = self.audio_dir or env_dir

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in self.__dict__.items()}, ensure_ascii=False, indent=1),
                        encoding="utf-8")


# -- progress parsing ------------------------------------------------------------------------------


@dataclass
class ProgressState:
    """Aggregates yt-dlp progress lines (several files for ``a+b``) into one 0..1 figure."""

    files: dict[str, tuple[float, float]] = field(default_factory=dict)  # filename -> (downloaded, total)
    stage: str = "queued"          # queued | download | postprocess | done
    postprocessor: str = ""
    speed: float | None = None
    eta: float | None = None
    filename: str = ""
    outputs: list[str] = field(default_factory=list)
    done_info: dict[str, Any] = field(default_factory=dict)

    def feed(self, line: str) -> bool:
        """Parse one stdout line; True when it was a protocol line."""
        if line.startswith(PROGRESS_PREFIX + " "):
            return self._feed_progress(line[len(PROGRESS_PREFIX) + 1:])
        if line.startswith(POSTPROCESS_PREFIX + " "):
            return self._feed_pp(line[len(POSTPROCESS_PREFIX) + 1:])
        if line.startswith(DONE_PREFIX + " "):
            return self._feed_done(line[len(DONE_PREFIX) + 1:])
        if line.startswith(SUBS_PREFIX + " "):
            return self._feed_subs(line[len(SUBS_PREFIX) + 1:])
        return False

    def _feed_subs(self, text: str) -> bool:
        try:
            paths = json.loads(text)
        except ValueError:
            return True   # "NA" when no subtitle was requested or found
        for fp in paths if isinstance(paths, list) else []:
            if isinstance(fp, str) and fp and fp not in self.outputs:
                self.outputs.append(fp)
        return True

    @staticmethod
    def _json(text: str) -> dict[str, Any] | None:
        try:
            d = json.loads(text)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def _feed_progress(self, text: str) -> bool:
        d = self._json(text)
        if d is None:
            return False
        status = d.get("status")
        name = str(d.get("filename") or d.get("tmpfilename") or "")
        downloaded = float(d.get("downloaded_bytes") or 0)
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        try:
            total_f = float(total)
        except (TypeError, ValueError):
            total_f = 0.0
        if status == "finished":
            total_f = max(total_f, downloaded)
            downloaded = total_f
            self.speed, self.eta = None, None
        elif status == "downloading":
            self.speed = float(d["speed"]) if d.get("speed") is not None else None
            self.eta = float(d["eta"]) if d.get("eta") is not None else None
        elif status != "error":
            return True  # unknown status: ignore (forward compatible)
        self.stage = "download"
        self.filename = name
        self.files[name] = (downloaded, total_f)
        return True

    def _feed_pp(self, text: str) -> bool:
        d = self._json(text)
        if d is None:
            return False
        self.stage = "postprocess"
        self.postprocessor = str(d.get("postprocessor") or "")
        self.speed, self.eta = None, None
        return True

    def _feed_done(self, text: str) -> bool:
        d = self._json(text)
        if d is None:
            return False
        self.done_info = d
        fp = d.get("filepath")
        if fp and fp not in self.outputs:
            self.outputs.append(str(fp))
        self.stage = "done"
        return True

    @property
    def downloaded(self) -> float:
        return sum(v[0] for v in self.files.values())

    @property
    def total(self) -> float:
        return sum(v[1] for v in self.files.values())

    @property
    def progress(self) -> float:
        if self.stage == "done":
            return 1.0
        if self.stage == "postprocess":
            return 0.98
        total = self.total
        if total <= 0:
            return 0.0
        return max(0.0, min(0.97, self.downloaded / total * 0.97))

    def message(self) -> str:
        if self.stage == "postprocess":
            return f"procesando ({self.postprocessor})" if self.postprocessor else "procesando"
        if self.stage == "download":
            parts = [f"{self.progress / 0.97 * 100:.0f}%"]
            if self.speed:
                parts.append(f"{self.speed / 1e6:.1f} MB/s" if self.speed >= 1e6 else f"{self.speed / 1e3:.0f} kB/s")
            if self.eta is not None:
                parts.append("ETA " + fmt_eta(self.eta))
            return " · ".join(parts)
        if self.stage == "done":
            return "completado"
        return "en cola"


def fmt_eta(seconds: float) -> str:
    s = int(max(0, seconds))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


# -- items ---------------------------------------------------------------------------------------


@dataclass
class DownloadItem:
    spec: DownloadSpec
    out_dir: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    message: str = "en cola"
    stage: str = "queued"
    speed: float | None = None
    eta: float | None = None
    downloaded: float = 0.0
    total: float = 0.0
    outputs: list[str] = field(default_factory=list)
    error: str = ""
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    job_id: str | None = None
    notify: str | None = None
    resume: bool = False
    argv: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    _proc: asyncio.subprocess.Process | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "url": self.spec.url, "title": self.title or self.spec.title or self.spec.url,
            "description": self.spec.describe(), "kind": self.spec.kind, "preset": self.spec.extra.get("preset"),
            "status": self.status, "progress": round(self.progress, 4), "message": self.message, "stage": self.stage,
            "speed": self.speed, "eta": self.eta, "downloaded": self.downloaded, "total": self.total,
            "outputs": list(self.outputs), "out_dir": self.out_dir, "error": self.error, "attempts": self.attempts,
            "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
            "job_id": self.job_id, "spec": self.spec.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DownloadItem:
        spec = DownloadSpec.from_dict(d.get("spec") or {"url": d.get("url", "")})
        item = cls(spec=spec, out_dir=str(d.get("out_dir") or ""), id=str(d.get("id") or uuid.uuid4().hex[:10]))
        for k in ("title", "status", "progress", "message", "stage", "downloaded", "total", "outputs", "error",
                  "attempts", "created_at", "started_at", "finished_at"):
            if k in d and d[k] is not None:
                setattr(item, k, d[k])
        if item.status not in FINAL:  # interrupted by a daemon restart: the manager resumes it (yt-dlp --continue)
            item.status, item.message = "queued", "se reanudará"
            item.resume = True
        return item


class DownloadManager:
    def __init__(self, jobs: JobQueue, binary_provider: Callable[[], YtdlpBinary | None], data_dir: Path,
                 on_change: Callable[[DownloadItem], None] | None = None,
                 fallback: Callable[[], Awaitable[YtdlpBinary | None]] | None = None):
        self.jobs = jobs
        self.fallback = fallback
        self._binary = binary_provider
        self.data_dir = data_dir
        self.settings_path = data_dir / "ytdl.json"
        self.history_path = data_dir / "downloads.json"
        self.archive_path = data_dir / "ytdl-archive.txt"   # yt-dlp --download-archive (ids already downloaded)
        self.settings = DownloadSettings.load(self.settings_path)
        self.on_change = on_change
        self.items: dict[str, DownloadItem] = {}
        self._order: deque[str] = deque(maxlen=MAX_HISTORY)
        self._load_history()

    # -- persistence --------------------------------------------------------------------------

    def _load_history(self) -> None:
        try:
            rows = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in rows if isinstance(rows, list) else []:
            try:
                item = DownloadItem.from_dict(row)
            except (ValueError, TypeError, KeyError):
                continue
            self.items[item.id] = item
            self._order.append(item.id)

    def _save_history(self) -> None:
        rows = [self.items[i].to_dict() for i in self._order if i in self.items]
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.history_path)
        except OSError as exc:
            log.warning("cannot save download history: %s", exc)

    def save_settings(self) -> None:
        self.settings.save(self.settings_path)

    # -- queue --------------------------------------------------------------------------------

    def _changed(self, item: DownloadItem, persist: bool = False) -> None:
        if self.on_change is not None:
            try:
                self.on_change(item)
            except Exception:  # noqa: BLE001
                log.exception("download on_change failed")
        if persist:
            self._save_history()

    def list(self, include_finished: bool = True) -> list[dict[str, Any]]:
        rows = [self.items[i] for i in reversed(self._order) if i in self.items]
        if not include_finished:
            rows = [r for r in rows if r.status not in FINAL]
        rows.sort(key=lambda r: (r.status in FINAL, -r.created_at))
        return [r.to_dict() for r in rows]

    def get(self, item_id: str) -> DownloadItem | None:
        return self.items.get(item_id)

    def active(self) -> int:
        return sum(1 for i in self.items.values() if i.status in ("queued", "running"))

    def submit(self, spec: DownloadSpec, title: str = "", notify: str | None = None,
               out_dir: str | None = None) -> DownloadItem:
        spec.validate()
        kind = "audio" if spec.is_audio else "video"
        target = Path(os.path.expanduser(out_dir)) if out_dir else self.settings.resolved_dir(kind)
        item = DownloadItem(spec=spec, out_dir=str(target), title=title or spec.title or "", notify=notify)
        self.items[item.id] = item
        self._order.append(item.id)
        while len(self.items) > MAX_HISTORY and self._order:
            oldest = next((i for i in self._order if self.items.get(i) and self.items[i].status in FINAL), None)
            if oldest is None:
                break
            self.items.pop(oldest, None)
            with contextlib.suppress(ValueError):
                self._order.remove(oldest)
        self._start(item)
        return item

    def _start(self, item: DownloadItem) -> None:
        item.status, item.stage, item.message, item.error = "queued", "queued", "en cola", ""
        item.progress, item.speed, item.eta, item.downloaded, item.total = 0.0, None, None, 0.0, 0.0
        item.outputs, item.stderr_tail = [], []
        item.started_at = item.finished_at = None
        item.attempts += 1
        job = self.jobs.submit(f"download:{item.id}", lambda job: self._run(item, job), priority=Priority.INTERACTIVE,
                               heavy=False, meta={"download": item.id})
        item.job_id = job.id
        self._changed(item, persist=True)

    def resume_pending(self) -> int:
        """Queue again what a previous mpvd left unfinished (yt-dlp --continue picks up the .part files)."""
        pending = [self.items[i] for i in self._order if i in self.items and self.items[i].resume]
        for item in pending:
            item.resume = False
            item.attempts = max(0, item.attempts - 1)   # a restart is not a retry
            self._start(item)
        return len(pending)

    def cancel(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status in FINAL:
            return False
        if item.job_id and self.jobs.cancel(item.job_id):
            return True
        self._finish(item, "cancelled", "cancelada")
        return True

    def retry(self, item_id: str) -> DownloadItem | None:
        item = self.items.get(item_id)
        if item is None or item.status not in FINAL:
            return None
        self._start(item)
        return item

    def remove(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if item is None or item.status not in FINAL:
            return False
        self.items.pop(item_id, None)
        with contextlib.suppress(ValueError):
            self._order.remove(item_id)
        self._save_history()
        return True

    def clear_finished(self) -> int:
        finished = [i for i, it in self.items.items() if it.status in FINAL]
        for i in finished:
            self.items.pop(i, None)
            with contextlib.suppress(ValueError):
                self._order.remove(i)
        self._save_history()
        return len(finished)

    async def cancel_all(self) -> None:
        for item in list(self.items.values()):
            if item.status in ("queued", "running"):
                self.cancel(item.id)

    # -- running ------------------------------------------------------------------------------

    def _finish(self, item: DownloadItem, status: str, message: str, error: str = "") -> None:
        item.status, item.message, item.error = status, message, error
        item.finished_at = time.time()
        item.speed = item.eta = None
        if status == "done":
            item.progress, item.stage = 1.0, "done"
        self._changed(item, persist=True)

    def _apply(self, item: DownloadItem, ps: ProgressState) -> None:
        item.progress, item.stage, item.message = ps.progress, ps.stage, ps.message()
        item.speed, item.eta, item.downloaded, item.total = ps.speed, ps.eta, ps.downloaded, ps.total
        if ps.outputs:
            item.outputs = list(ps.outputs)
        if ps.done_info.get("title") and not item.title:
            item.title = str(ps.done_info["title"])

    async def _exec(self, item: DownloadItem, job: Job, binary: YtdlpBinary) -> tuple[int, ProgressState, list[str]]:
        """Run one yt-dlp process for ``item`` (progress pushed as it goes); returns (rc, progress, stderr tail)."""
        args = build_args(item.spec, item.out_dir, self.settings.template, self.settings.rate_limit,
                          str(self.archive_path))
        item.argv = binary.command(*self.settings.session_args(), *args)
        ps = ProgressState()
        stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = await asyncio.create_subprocess_exec(
            *item.argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            cwd=item.out_dir, limit=4 * 1024 * 1024,
        )
        item._proc = proc
        last_push = 0.0

        async def read_stderr() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    stderr_tail.append(line)

        async def read_stdout() -> None:
            nonlocal last_push
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not ps.feed(line):
                    continue
                self._apply(item, ps)
                now = time.monotonic()
                if ps.stage != "download" or now - last_push >= 0.2:
                    last_push = now
                    self._changed(item)
                    job.report(item.progress, item.message)

        try:
            await asyncio.gather(read_stdout(), read_stderr())
            rc = await proc.wait()
        except asyncio.CancelledError:
            await self._kill(proc)
            raise
        finally:
            item._proc = None
            item.stderr_tail = list(stderr_tail)
        return rc, ps, list(stderr_tail)

    async def _run(self, item: DownloadItem, job: Job) -> dict[str, Any]:
        binary = self._binary()
        if binary is None:
            self._finish(item, "failed", "yt-dlp no disponible", "yt-dlp not found (vendor/bin, $MPV_UOS_YTDLP or PATH)")
            raise RuntimeError(item.error)
        # Wait for a slot (the job queue has more workers than we want concurrent downloads).
        while sum(1 for i in self.items.values() if i.status == "running") >= max(1, self.settings.concurrent):
            await asyncio.sleep(0.5)
        Path(item.out_dir).mkdir(parents=True, exist_ok=True)
        item.status, item.stage, item.message, item.started_at = "running", "download", "iniciando…", time.time()
        self._changed(item)
        job.report(0.0, item.message)
        try:
            rc, ps, tail = await self._exec(item, job, binary)
            used_nightly = False
            if not (rc == 0 and (ps.outputs or ps.stage == "done")) and self.settings.nightly_fallback \
                    and binary.source not in ("nightly", "env-nightly") and self.fallback is not None \
                    and worth_nightly(tail):
                # the site changed and the stable yt-dlp does not know yet (ok.ru, 2026-09): the nightly build usually
                # does; downloaded on demand (and refreshed daily with the stable one)
                item.message = "la versión estable falló: probando con yt-dlp nightly…"
                self._changed(item)
                nightly = await self.fallback()
                if nightly is not None:
                    rc, ps, tail = await self._exec(item, job, nightly)
                    used_nightly = True
        except asyncio.CancelledError:
            self._finish(item, "cancelled", "cancelada")
            raise
        if rc == 0 and (ps.outputs or ps.stage == "done"):
            self._finish(item, "done", "completado con yt-dlp nightly" if used_nightly else "completado")
            return {"outputs": item.outputs}
        errors = [line for line in tail if line.startswith("ERROR")] or list(tail)
        err = errors[-1] if errors else f"yt-dlp exited with {rc}"
        if rc == 0:
            err = "yt-dlp terminó sin producir ningún archivo (¿ya existía?): " + err
        self._finish(item, "failed", "error", err)
        raise RuntimeError(err)

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


__all__ = ["DownloadItem", "DownloadManager", "DownloadSettings", "ProgressState", "Status", "default_media_dir",
           "fmt_eta"]
