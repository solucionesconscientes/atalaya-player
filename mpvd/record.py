"""«Grabar» helpers for mu-record (H18, ADR-044).

Ranges of local files go through ``study.clip`` (stream copy) and ranges of internet videos through ``ytdl.download``
with ``sections``; live streams are recorded by mpv itself (``stream-record``). What is left here: the default folder
and, for «grabar solo el audio» of a live stream with video, extracting the audio track without re-encoding once the
recording stops (``record.audio``). Completion is pushed as ``mu-event {"event":"record","job":{...}}`` to ``notify``.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import ffmpeg_path
from mpvd.jobs import Job, Priority
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError
from mpvd.study.clips import audio_codec, audio_copy_ext
from mpvd.ytdl.downloads import default_media_dir

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

DEFAULT_NOTIFY = "mu_record"


def default_dir() -> Path:
    """Where recordings go: ``MPV_UOS_RECORD_DIR`` or <Vídeos>/<marca>/Grabaciones.

    The same variable the scheduled recordings honour (H21), so «Grabar» and the TV guide never disagree.
    """
    env = os.environ.get("MPV_UOS_RECORD_DIR")
    return Path(env) if env else default_media_dir("video") / "Grabaciones"


def audio_args(src: Path, out: Path) -> list[str]:
    """``ffmpeg -i SRC -vn -map 0:a:0 -c:a copy OUT`` (the container follows the codec, see AUDIO_COPY_EXT)."""
    return [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-vn",
            "-map", "0:a:0", "-c:a", "copy", str(out)]


async def extract_audio(src: Path, remove: bool = False, timeout: float = 600.0) -> Path:
    codec = await asyncio.to_thread(audio_codec, src)
    if codec is None:
        raise RuntimeError("la grabación no tiene audio")
    out = src.with_suffix(audio_copy_ext(codec))
    if out == src:
        out = src.with_name(src.stem + " (audio)" + src.suffix)
    proc = await asyncio.create_subprocess_exec(*audio_args(src, out), stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        out.unlink(missing_ok=True)
        raise
    if proc.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg: " + err.decode("utf-8", "replace").strip()[-300:])
    if remove:
        src.unlink(missing_ok=True)
    return out


def register(server: MpvdServer) -> None:
    d = server.dispatcher

    def push(item: dict[str, Any], final: bool) -> None:
        for session in server.sessions.all():
            if session.connected:
                session.push_event(item["notify"], "record:" + item["id"], item["status"],
                                   {"event": "record", "job": item}, final=final)

    @d.method("record.defaults")
    async def defaults(ctx: RpcContext) -> dict[str, Any]:
        """Default recordings folder (<Vídeos>/MPV-UOS/Grabaciones)."""
        return {"dir": str(default_dir())}

    @d.method("record.audio")
    async def audio(ctx: RpcContext, file: str, remove: bool = False, notify: str = DEFAULT_NOTIFY) -> dict[str, Any]:
        """Keep only the audio of a recording (stream copy); ``remove`` deletes the original afterwards."""
        src = Path(file).expanduser()
        if not src.is_file():
            raise RpcError(INVALID_PARAMS, t("no existe: %s") % (src,))
        item: dict[str, Any] = {"id": uuid.uuid4().hex[:10], "file": str(src), "status": "queued", "message": "",
                                "notify": notify, "created_at": time.time()}

        async def body(job: Job) -> dict[str, Any]:
            item["status"] = "running"
            push(item, False)
            try:
                out = await extract_audio(src, remove)
            except asyncio.CancelledError:
                item.update({"status": "cancelled", "message": "cancelado"})
                push(item, True)
                raise
            except (RuntimeError, OSError) as exc:
                item.update({"status": "failed", "message": str(exc)})
                push(item, True)
                raise RpcError(UNAVAILABLE, str(exc)) from exc
            item.update({"status": "done", "file": str(out), "message": "listo"})
            push(item, True)
            return item

        job = server.jobs.submit("record.audio", body, priority=Priority.INTERACTIVE, heavy=False, session_id=None,
                                 meta={"file": str(src)})
        item["job"] = job.id
        return item
