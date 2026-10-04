"""What happens to a file once it is downloaded (H23): equal loudness, a new name, a library folder and AI subtitles
(+ translation). SponsorBlock is not a step here: it is a yt-dlp option of the download itself (presets.py).

Steps run in this order, each one recorded in ``DownloadItem.post`` (visible in «Descargas» and «Tareas»); a failed
step is reported and the next ones still run:

1. **Volumen igualado**: ``loudnorm`` in two passes on the file (EBU R128, −16 LUFS, true peak −1.5 dBTP, LRA 11):
   pass 1 measures, pass 2 applies it linearly with the measured values. Only the first audio track is re-encoded (same
   codec family, a sensible bitrate); video, other tracks, subtitles, chapters and tags are copied. ReplayGain tags were
   not enough: many players ignore them and mpv only reads them with ``--replaygain``.
2. **Renombrar** with a template (``{date} - {title}``…; missing fields vanish with their separators).
3. **Mover** to a folder (a subscription gets its own subfolder there); when the folder belongs to the library, it is
   rescanned.
4. **Subtítulos IA** (only when Whisper and a model are installed) as a low priority transcription, saved next to the
   file, and optionally **translated** offline (subs.translate) and saved too.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd import priority as prio
from mpvd.jobs import Priority, Status

if TYPE_CHECKING:
    from mpvd.ytdl.downloads import DownloadItem

log = logging.getLogger("mpvd.subscriptions.chain")

TARGET_I = -16.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
NICE = 10
LANG_RE = re.compile(r"^[a-z]{2,3}$")
FIELDS = ("title", "date", "uploader", "feed", "id")
RENAME_PRESETS = ("{date} - {title}", "{uploader} - {title}", "{feed} - {date} - {title}", "{title}")
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".ogg", ".oga", ".flac", ".wav", ".m4v", ".mov",
              ".aac", ".m4b", ".avi"}
SIDECAR_EXTS = {".srt", ".vtt", ".ass", ".lrc", ".jpg", ".png", ".webp", ".nfo", ".json"}
STEP_LABELS = {"loudnorm": "igualar el volumen", "rename": "renombrar", "move": "mover a la carpeta",
               "subtitles": "subtítulos IA", "translate": "traducir los subtítulos"}


class ChainError(RuntimeError):
    pass


@dataclass
class ChainConfig:
    loudnorm: bool = False
    subtitles: bool = False
    translate: str = ""      # target language of the AI subtitles ("" = no translation)
    rename: str = ""         # file name template ("" = keep yt-dlp's name)
    move_to: str = ""        # folder ("" = stay in the download folder)

    def active(self) -> bool:
        return bool(self.loudnorm or self.subtitles or self.rename or self.move_to)

    def steps(self) -> list[str]:
        out = []
        if self.loudnorm:
            out.append("loudnorm")
        if self.rename:
            out.append("rename")
        if self.move_to:
            out.append("move")
        if self.subtitles:
            out.append("subtitles")
            if self.translate:
                out.append("translate")
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> ChainConfig:
        c = cls()
        if isinstance(d, dict):
            c.update(d)
        return c

    def update(self, d: dict[str, Any]) -> None:
        """Validated update (unknown keys ignored; raises ValueError on bad values)."""
        for k in ("loudnorm", "subtitles"):
            if k in d:
                setattr(self, k, bool(d[k]))
        if "translate" in d:
            lang = str(d["translate"] or "").strip().lower()
            if lang and not LANG_RE.match(lang):
                raise ValueError("idioma de traducción no válido (es, en, fr…)")
            self.translate = lang
        if "rename" in d:
            tpl = str(d["rename"] or "").strip()
            if tpl:
                check_template(tpl)
            self.rename = tpl
        if "move_to" in d:
            self.move_to = str(d["move_to"] or "").strip()


# -- names --------------------------------------------------------------------------------------------------------

_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


def safe_component(text: str, limit: int = 150) -> str:
    """A single file or folder name: no separators or characters Windows refuses, no leading dots, ≤ limit bytes."""
    s = _BAD.sub(" ", str(text or "")).strip()
    s = re.sub(r"\s+", " ", s).strip(" .")
    while len(s.encode("utf-8")) > limit:
        s = s[:-1]
    return s.rstrip(" .") or "sin título"


def check_template(tpl: str) -> None:
    names = re.findall(r"\{([^{}]*)\}", tpl)
    if not names:
        raise ValueError("la plantilla necesita al menos un campo: " + ", ".join("{%s}" % f for f in FIELDS))
    bad = [n for n in names if n not in FIELDS]
    if bad:
        raise ValueError("campos desconocidos en la plantilla: " + ", ".join(bad))
    if "/" in tpl or "\\" in tpl:
        raise ValueError("la plantilla es un nombre, no una ruta (sin / ni \\)")


def render_name(tpl: str, meta: dict[str, Any]) -> str:
    """``{date} - {title}`` + meta → file stem. A missing field vanishes with the separator next to it."""
    def sub(m: re.Match[str]) -> str:
        return str(meta.get(m.group(1)) or "").strip()

    s = re.sub(r"\{([a-z_]+)\}", sub, tpl)
    s = re.sub(r"(\s*[-–_·|]\s*){2,}", " - ", s)          # «2026-09-30 -  - Title» → «2026-09-30 - Title»
    s = re.sub(r"^\s*[-–_·|]\s*|\s*[-–_·|]\s*$", "", s)    # leading / trailing separators
    return safe_component(s)


def meta_for(item: DownloadItem) -> dict[str, str]:
    """Rename fields from the download: yt-dlp's MU_DONE info, then what the subscription knew about the entry."""
    info = item.info or {}
    extra = item.spec.extra or {}
    date = ""
    ud = str(info.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", ud):
        date = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}"
    elif extra.get("published"):
        with contextlib.suppress(TypeError, ValueError, OSError):
            date = dt.datetime.fromtimestamp(float(extra["published"])).strftime("%Y-%m-%d")
    if not date:
        date = dt.datetime.fromtimestamp(item.finished_at or time.time()).strftime("%Y-%m-%d")
    raw_title = str(info.get("title") or "")
    if extra.get("entry_title") and (not raw_title or raw_title == str(info.get("id") or "")):
        raw_title = str(extra["entry_title"])      # direct podcast files: yt-dlp only sees a number
    return {"title": raw_title or item.title or item.spec.title or "", "date": date,
            "uploader": str(info.get("uploader") or info.get("channel") or extra.get("feed_title") or ""),
            "feed": str(extra.get("feed_title") or ""), "id": str(info.get("id") or "")}


def free_path(p: Path) -> Path:
    if not p.exists():
        return p
    for n in range(2, 1000):
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not cand.exists():
            return cand
    raise ChainError(f"no hay un nombre libre para {p.name}")


def main_file(item: DownloadItem) -> Path | None:
    cur = (item.post or {}).get("file")
    if cur and Path(cur).is_file():
        return Path(cur)
    fp = (item.info or {}).get("filepath")
    if fp and Path(fp).is_file():
        return Path(fp)
    for o in item.outputs:
        p = Path(o)
        if p.suffix.lower() in MEDIA_EXTS and p.is_file():
            return p
    return None


def sidecars(item: DownloadItem, main: Path) -> list[Path]:
    """Files written with the video (subtitles in «file» mode…) that should follow it when renamed or moved."""
    out = []
    for o in item.outputs:
        p = Path(o)
        if p != main and p.is_file() and p.parent == main.parent and p.name.startswith(main.stem + "."):
            out.append(p)
    return out


def rename_file(main: Path, extra: list[Path], stem: str) -> tuple[Path, list[Path]]:
    if stem == main.stem:
        return main, extra
    target = free_path(main.with_name(stem + main.suffix))
    moved = []
    for s in extra:   # «Old.es.srt» → «New.es.srt»
        dst = s.with_name(target.stem + s.name[len(main.stem):])
        if not dst.exists():
            os.replace(s, dst)
            moved.append(dst)
        else:
            moved.append(s)
    os.replace(main, target)
    return target, moved


def move_files(main: Path, extra: list[Path], folder: Path) -> tuple[Path, list[Path]]:
    folder.mkdir(parents=True, exist_ok=True)
    if main.parent.resolve() == folder.resolve():
        return main, extra
    target = free_path(folder / main.name)
    moved = []
    for s in extra:
        dst = folder / (target.stem + s.name[len(main.stem):])
        if dst.exists():
            moved.append(s)
            continue
        shutil.move(str(s), str(dst))
        moved.append(dst)
    shutil.move(str(main), str(target))
    return target, moved


# -- loudness -----------------------------------------------------------------------------------------------------


def ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise ChainError("ffmpeg no está instalado")
    return p


def audio_stream(src: Path, timeout: float = 30.0) -> dict[str, Any] | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ChainError("ffprobe no está instalado")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_name,bit_rate,sample_rate,channels", "-of", "json", str(src)],
                         capture_output=True, text=True, timeout=timeout, check=False)
    if out.returncode != 0:
        raise ChainError("no se puede leer el archivo: " + (out.stderr.strip().splitlines() or ["?"])[-1])
    streams = (json.loads(out.stdout or "{}").get("streams") or [])
    return streams[0] if streams else None


# codec of the original track → (encoder, default bitrate kbps or None for lossless); same family keeps the container
ENCODERS = {"aac": ("aac", 192), "mp3": ("libmp3lame", 192), "opus": ("libopus", 128), "vorbis": ("libvorbis", 160),
            "flac": ("flac", None), "alac": ("alac", None), "pcm_s16le": ("pcm_s16le", None),
            "pcm_s24le": ("pcm_s24le", None)}
BY_EXT = {".mp4": "aac", ".m4a": "aac", ".m4v": "aac", ".mov": "aac", ".mkv": "opus", ".webm": "opus",
          ".mp3": "mp3", ".opus": "opus", ".ogg": "vorbis", ".oga": "vorbis", ".flac": "flac", ".wav": "pcm_s16le"}


def encoder_args(stream: dict[str, Any], ext: str) -> list[str]:
    codec = str(stream.get("codec_name") or "")
    if codec not in ENCODERS:
        codec = BY_EXT.get(ext.lower(), "aac")
    enc, default_kbps = ENCODERS[codec]
    args = ["-c:a:0", enc]
    if default_kbps is not None:
        try:
            orig = int(stream.get("bit_rate") or 0) // 1000
        except (TypeError, ValueError):
            orig = 0
        kbps = min(320, max(default_kbps if orig <= 0 else orig, 96))
        args += ["-b:a:0", f"{kbps}k"]
    rate = 48000 if enc == "libopus" else int(stream.get("sample_rate") or 48000)
    args += ["-ar:a:0", str(rate)]   # loudnorm works at 192 kHz: give the track its own rate back
    return args


def measure_args(src: Path) -> list[str]:
    return [ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-i", str(src), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json", "-f", "null", "-"]


def parse_measure(stderr: str) -> dict[str, float]:
    """The JSON block loudnorm prints at the end of pass 1."""
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not m:
        raise ChainError("loudnorm no devolvió medidas")
    d = json.loads(m.group(0))
    out = {}
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            out[k] = float(d[k])
        except (KeyError, TypeError, ValueError) as exc:
            raise ChainError(f"medida de loudnorm no válida: {k}") from exc
    if out["input_i"] == float("-inf") or out["input_i"] < -70:
        raise ChainError("el audio es silencio")
    return out


def apply_args(src: Path, dst: Path, measured: dict[str, float], stream: dict[str, Any]) -> list[str]:
    ln = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:measured_I={measured['input_i']}:"
          f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
          f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true")
    args = [ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0", "-dn",
            "-c", "copy", *encoder_args(stream, src.suffix), "-filter:a:0", ln]
    if src.suffix.lower() in (".mp4", ".m4a", ".m4v", ".mov"):
        args += ["-movflags", "+faststart"]
    return [*args, str(dst)]




async def _run(cmd: list[str]) -> tuple[int, str]:
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["preexec_fn"] = prio.lower
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE, **kwargs)
    try:
        _, err = await proc.communicate()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, err.decode("utf-8", "replace")


async def loudnorm_file(src: Path) -> dict[str, Any]:
    """Normalise ``src`` in place (two passes). Returns the measured input and the target."""
    stream = await asyncio.to_thread(audio_stream, src)
    if stream is None:
        raise ChainError("el archivo no tiene audio")
    rc, err = await _run(measure_args(src))
    if rc != 0:
        raise ChainError("loudnorm (medida): " + (err.strip().splitlines() or ["?"])[-1])
    measured = parse_measure(err)
    tmp = src.with_name(src.stem + ".loudnorm-part" + src.suffix)
    try:
        rc, err = await _run(apply_args(src, tmp, measured, stream))
        if rc != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            raise ChainError("loudnorm: " + (err.strip().splitlines() or ["?"])[-1])
        os.replace(tmp, src)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return {"input_i": measured["input_i"], "target_i": TARGET_I}


# -- the runner ---------------------------------------------------------------------------------------------------


class PostChain:
    """Runs the steps for one download at a time (the heavy ffmpeg pass as a job of low priority that waits while
    playback drops frames); AI subtitles do not hold the others back (they are their own low priority job)."""

    def __init__(self, server: Any, downloads: Any,
                 on_complete: Callable[[DownloadItem], Awaitable[None] | None] | None = None):
        self.server = server
        self.downloads = downloads
        self.on_complete = on_complete
        self._lock = asyncio.Lock()
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def running(self) -> int:
        return sum(1 for t in self.tasks.values() if not t.done())

    def start(self, item: DownloadItem, cfg: ChainConfig) -> None:
        if item.id in self.tasks and not self.tasks[item.id].done():
            return
        steps = cfg.steps()
        post = item.post or {}
        old = {s.get("id"): s for s in post.get("steps") or [] if isinstance(s, dict)}
        item.post = {"status": "running", "config": cfg.to_dict(), "file": post.get("file") or "",
                     "steps": [old.get(s) if old.get(s, {}).get("status") in ("done", "skipped")
                               else {"id": s, "label": STEP_LABELS[s], "status": "pending", "message": ""}
                               for s in steps], "outputs": list(post.get("outputs") or [])}
        t = asyncio.get_running_loop().create_task(self._run(item, cfg), name=f"chain-{item.id}")
        self.tasks[item.id] = t
        t.add_done_callback(lambda _t, i=item.id: self.tasks.pop(i, None) if self.tasks.get(i) is _t else None)

    async def close(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        for t in list(self.tasks.values()):
            with contextlib.suppress(BaseException):
                await t

    def _set(self, item: DownloadItem, step: dict[str, Any], status: str, message: str = "") -> None:
        step["status"], step["message"] = status, message
        done = [s for s in item.post["steps"] if s["status"] in ("done", "skipped", "failed")]
        if status == "running":
            item.message = f"completado · {step['label']}…"
        else:
            item.message = f"completado · {len(done)}/{len(item.post['steps'])} pasos"
        self.downloads.changed(item, persist=status != "running")

    async def _run(self, item: DownloadItem, cfg: ChainConfig) -> None:
        try:
            main = main_file(item)
            if main is None:
                raise ChainError("no se encuentra el archivo descargado")
            item.post["file"] = str(main)
            extra = sidecars(item, main)
            async with self._lock:
                for step in item.post["steps"]:
                    if step["status"] in ("done", "skipped") or step["id"] in ("subtitles", "translate"):
                        continue
                    self._set(item, step, "running")
                    try:
                        if step["id"] == "loudnorm":
                            res = await self._job("loudnorm", lambda m=main: loudnorm_file(m))
                            msg = f"{res['input_i']:.0f} → {TARGET_I:.0f} LUFS"
                        elif step["id"] == "rename":
                            main, extra = await asyncio.to_thread(rename_file, main, extra,
                                                                  render_name(cfg.rename, meta_for(item)))
                            msg = main.name
                        else:
                            folder = Path(os.path.expanduser(cfg.move_to))
                            if item.spec.extra.get("feed_title"):
                                folder = folder / safe_component(str(item.spec.extra["feed_title"]))
                            main, extra = await asyncio.to_thread(move_files, main, extra, folder)
                            msg = str(folder)
                            self._rescan(folder)
                        item.post["file"] = str(main)
                        item.outputs = [str(main), *[str(p) for p in extra]]
                        self._set(item, step, "done", msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - one failed step must not stop the next ones
                        log.warning("chain %s: %s failed: %s", item.id, step["id"], exc)
                        self._set(item, step, "failed", str(exc))
            await self._subtitles(item, main)
            failed = [s for s in item.post["steps"] if s["status"] == "failed"]
            item.post["status"] = "failed" if failed else "done"
            item.message = "completado" if not item.post["steps"] else (
                "completado · " + ("; ".join(f"{s['label']}: {s['message']}" for s in failed) if failed
                                   else "pasos hechos"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("chain %s failed: %s", item.id, exc)
            item.post["status"] = "failed"
            item.message = f"completado · {exc}"
        self.downloads.changed(item, persist=True)
        if self.on_complete is not None:
            res = self.on_complete(item)
            if asyncio.iscoroutine(res):
                await res

    def _rescan(self, folder: Path) -> None:
        lib = getattr(self.server, "library", None)
        if lib is None:
            return
        try:
            roots = [f["path"] for f in lib.store.folders()]
        except Exception:  # noqa: BLE001
            return
        target = folder.resolve()
        for r in roots:
            with contextlib.suppress(OSError, ValueError):
                if target == Path(r).resolve() or Path(r).resolve() in target.parents:
                    lib.submit_scan([r])
                    return

    async def _job(self, name: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        jobs = getattr(self.server, "jobs", None)
        if jobs is None:
            return await fn()

        async def body(job: Any) -> Any:
            return await fn()

        job = jobs.submit(f"chain.{name}", body, priority=Priority.INDEX, heavy=True)
        await job.wait()
        if job.status != Status.DONE:
            raise ChainError(job.error or job.status.value)
        return job.result

    async def _subtitles(self, item: DownloadItem, main: Path) -> None:
        steps = {s["id"]: s for s in item.post["steps"]}
        sub, tr = steps.get("subtitles"), steps.get("translate")
        if sub is None or sub["status"] == "done" and (tr is None or tr["status"] in ("done", "skipped")):
            return
        asr, subs = getattr(self.server, "asr", None), getattr(self.server, "subs", None)
        if asr is None or subs is None or not getattr(asr.engine, "available", False):
            self._set(item, sub, "skipped", "Whisper no está instalado")
            if tr is not None:
                self._set(item, tr, "skipped", "sin subtítulos IA")
            return
        lang = ""
        srt = ""
        if sub["status"] != "done":
            self._set(item, sub, "running")
            try:
                task = await asr.start(str(main), "auto", None, "precompute")
                restarts = 0
                while not task.complete:
                    await asyncio.sleep(2.0)
                    if task.status in ("failed", "cancelled") and not task.complete:
                        if task.status == "failed" or restarts >= 3:
                            raise ChainError(task.error or "la transcripción se detuvo")
                        restarts += 1
                        task = await asr.start(str(main), "auto", task.model, "precompute")
                lang = task.detected or (task.language if task.language != "auto" else "")
                res = await subs.save(str(main), "ai", lang=lang)
                srt = res["path"]
                item.post.setdefault("outputs", []).append(srt)
                sub["lang"], sub["srt"] = lang, srt
                self._set(item, sub, "done", Path(srt).name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                msg = getattr(exc, "message", None) or str(exc)
                self._set(item, sub, "failed", msg)
                if tr is not None:
                    self._set(item, tr, "skipped", "sin subtítulos IA")
                return
        else:
            lang, srt = str(sub.get("lang") or ""), str(sub.get("srt") or "")
        if tr is None or tr["status"] in ("done", "skipped"):
            return
        cfg = ChainConfig.from_dict(item.post.get("config"))
        if not lang or not srt:
            self._set(item, tr, "skipped", "idioma del audio desconocido")
            return
        if lang == cfg.translate:
            self._set(item, tr, "skipped", f"ya está en {lang}")
            return
        self._set(item, tr, "running")
        try:
            out = await subs.translate(srt, lang, cfg.translate, path=str(main), notify="mu_feeds")
            if out.get("status") == "queued":
                job = self.server.jobs.get(out["job"]["id"])
                if job is not None:
                    await job.wait()
                    if job.status != Status.DONE:
                        raise ChainError(job.error or "la traducción falló")
            res = await subs.save(str(main), "translation", lang=cfg.translate, srt=out["srt"])
            item.post.setdefault("outputs", []).append(res["path"])
            self._set(item, tr, "done", Path(res["path"]).name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._set(item, tr, "failed", getattr(exc, "message", None) or str(exc))


__all__ = ["ChainConfig", "ChainError", "PostChain", "RENAME_PRESETS", "check_template", "loudnorm_file",
           "meta_for", "move_files", "render_name", "rename_file", "safe_component"]
