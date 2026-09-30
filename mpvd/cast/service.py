"""``cast.*``: «Enviar a la tele» (H27, ADR-063). A DLNA renderer on the local network (most smart TVs, some
speakers and Kodi) plays what mpv was playing, from the same position; mpvd serves it and drives the TV.

What the TV gets:
* a local file the TV can decode (H.264/HEVC/MPEG-2 video, AAC/MP3/AC-3 audio) → the file itself over HTTP with byte
  ranges (the TV seeks by itself);
* anything else, and web videos (yt-dlp) without a single progressive MP4 → an MPEG-TS relay made by ffmpeg
  (video copied when it is H.264, otherwise H.264 by CPU; audio AAC); seeking restarts the relay at that time;
* a web video with a progressive MP4 (``share.hls.pick_direct``) → that URL directly.

The media server listens on the LAN (port 8792) only while something is being cast, under a random token that
changes with every item; nothing else is reachable through it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
import re
import secrets
import shutil
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from mpvd.cast import dlna
from mpvd.remote.http import HttpError, HttpServer, Request, Response
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.cast")

DEFAULT_PORT = 8792
CHUNK = 256 * 1024
TV_VIDEO = {"h264", "hevc", "mpeg2video", "mpeg4"}
TV_AUDIO = {"aac", "mp3", "ac3", "eac3", "mp2"}
MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".mov": "video/quicktime",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
        ".wav": "audio/wav"}


@dataclass
class Media:
    token: str
    title: str
    mime: str
    seekable: bool
    path: str = ""                        # a local file served as is
    url: str = ""                         # a direct web URL handed to the TV
    relay: list[dict[str, Any]] = field(default_factory=list)   # ffmpeg inputs [{url, headers}]
    relay_video_copy: bool = True
    audio_only: bool = False
    duration: float | None = None
    offset: float = 0.0                   # a relay starts here (its own clock starts at 0)


def mime_for(path: str, audio_only: bool = False) -> str:
    ext = Path(path).suffix.lower()
    if ext in MIME:
        return MIME[ext]
    guess = mimetypes.guess_type(path)[0] or ""
    return guess or ("audio/mpeg" if audio_only else "video/mp4")


def tv_can_play(probe: dict[str, Any]) -> tuple[bool, bool]:
    """(the file as is, its video stream can be copied into the relay)."""
    streams = probe.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    vname, aname = (v or {}).get("codec_name", ""), (a or {}).get("codec_name", "")
    fmt = str((probe.get("format") or {}).get("format_name", ""))
    container_ok = any(x in fmt for x in ("mp4", "matroska", "mpegts", "avi", "mp3", "flac", "wav", "aac"))
    ok_v = v is None or vname in TV_VIDEO
    ok_a = a is None or aname in TV_AUDIO or (v is None and aname in ("flac", "pcm_s16le"))
    return (container_ok and ok_v and ok_a and (v is not None or a is not None)), vname == "h264"


def relay_argv(media: Media, start: float, ffmpeg: str) -> list[str]:
    from mpvd.share.hls import Input  # noqa: PLC0415

    argv = [ffmpeg, "-v", "error", "-nostdin"]
    for inp in media.relay:
        args = Input(inp["url"], dict(inp.get("headers") or {})).args()
        argv += args[:-2] + (["-ss", f"{start:.3f}"] if start > 0 else []) + args[-2:]
    if media.audio_only:
        # speakers and TVs alike take MP3 over HTTP
        return argv + ["-map", f"{len(media.relay) - 1}:a:0", "-vn", "-sn", "-dn", "-c:a", "libmp3lame", "-b:a", "256k",
                       "-f", "mp3", "pipe:1"]
    argv += ["-map", "0:v:0", "-map", "1:a:0"] if len(media.relay) > 1 else ["-map", "0:v:0?", "-map", "0:a:0?"]
    if media.relay_video_copy:
        argv += ["-c:v", "copy"]
    else:
        argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                 "-vf", "scale='min(1920,iw)':-2"]
    return argv + ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-sn", "-dn", "-f", "mpegts", "pipe:1"]


class CastService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.devices: dict[str, dlna.Renderer] = {}
        self.http: HttpServer | None = None
        self.port = int(os.environ.get("MPV_UOS_CAST_PORT", DEFAULT_PORT))
        self.ip = ""
        self.media: Media | None = None
        self.device: dlna.Renderer | None = None
        self.ctrl: dlna.Controller | None = None
        self._relays: set[asyncio.subprocess.Process] = set()
        self._lock = asyncio.Lock()
        server.services["cast"] = True

    # -- media server -------------------------------------------------------------------------------------------

    async def _ensure_http(self, towards: str) -> None:
        from mpvd.remote.service import lan_ip  # noqa: PLC0415

        self.ip = _route_ip(towards) or lan_ip()
        if self.http is not None:
            return
        self.http = HttpServer(self._handle, name="mpv-uos-cast")
        try:
            await self.http.start("0.0.0.0", self.port)  # noqa: S104 - the TV is on the LAN
        except OSError:
            await self.http.start("0.0.0.0", 0)  # noqa: S104
        self.port = self.http.port

    def media_url(self, media: Media) -> str:
        name = ("stream.mp3" if media.audio_only else "stream.ts") if media.relay else urllib.parse.quote(Path(media.path).name or "media")
        return f"http://{self.ip}:{self.port}/c/{media.token}/{name}"

    async def _handle(self, req: Request) -> Response:
        m = re.match(r"^/c/([A-Za-z0-9_-]{16,})/", req.path)
        media = self.media
        if req.method not in ("GET", "HEAD") or not m or media is None or not secrets.compare_digest(m.group(1), media.token):
            raise HttpError(404)
        dlna_headers = {"transferMode.dlna.org": "Streaming", "contentFeatures.dlna.org":
                        "DLNA.ORG_OP=" + ("01" if media.seekable else "00") + ";DLNA.ORG_CI=0"}
        if media.relay:
            if req.method == "HEAD":
                return Response(200, {"Content-Type": media.mime, **dlna_headers})
            return Response(200, {"Content-Type": media.mime, **dlna_headers}, b"", stream=self._relay(media))
        return self._file(Path(media.path), media.mime, req, dlna_headers)

    @staticmethod
    def _file(path: Path, mime: str, req: Request, extra: dict[str, str]) -> Response:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise HttpError(404) from exc
        start, end, status = 0, size - 1, 200
        headers = {"Content-Type": mime, "Accept-Ranges": "bytes", **extra}
        m = re.match(r"^bytes=(\d*)-(\d*)$", req.headers.get("range", "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return Response(416, {"Content-Range": f"bytes */{size}"}, b"")
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)

        async def body() -> AsyncIterator[bytes]:
            f = await asyncio.to_thread(open, path, "rb")
            try:
                await asyncio.to_thread(f.seek, start)
                left = end - start + 1
                while left > 0:
                    chunk = await asyncio.to_thread(f.read, min(CHUNK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    yield chunk
            finally:
                f.close()
        return Response(status, headers, b"", stream=body() if req.method != "HEAD" else None)

    async def _relay(self, media: Media) -> AsyncIterator[bytes]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return
        proc = await asyncio.create_subprocess_exec(*relay_argv(media, media.offset, ffmpeg), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self._relays.add(proc)
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            await self._kill(proc)

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        """A relay has nothing worth finishing: kill it and close its pipe. (``wait()`` alone can hang: asyncio only
        reports the exit once stdout reaches EOF, and nobody reads a relay the TV stopped fetching.)"""
        self._relays.discard(proc)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), 3)

    # -- devices ------------------------------------------------------------------------------------------------

    async def discover(self, timeout: float = 3.0) -> list[dict[str, Any]]:
        found = await dlna.discover(timeout)
        for r in found:
            self.devices[r.id] = r
        return [r.to_dict() for r in found]

    async def _device(self, device_id: str) -> dlna.Renderer:
        r = self.devices.get(device_id)
        if r is None:
            await self.discover()
            r = self.devices.get(device_id)
        if r is None:
            raise RpcError(NOT_FOUND, "no encuentro esa tele en la red")
        return r

    # -- what to send -------------------------------------------------------------------------------------------

    async def _prepare(self, path: str, title: str, mode: str, audio_only: bool) -> Media:
        from mpvd.share import hls  # noqa: PLC0415

        token = secrets.token_urlsafe(18)
        if hls.is_url(path):
            if hls.plain_direct(path) and mode != "relay":
                return Media(token, title, mime_for(path.split("?")[0], audio_only), True, url=path)
            ytdl = getattr(self.server, "ytdl", None)
            if ytdl is None:
                raise RpcError(UNAVAILABLE, "yt-dlp no está disponible")
            info = await ytdl.raw_info(path)
            title = title or str(info.get("title") or "")
            direct = hls.pick_direct(info) if mode != "relay" and not audio_only else None
            if direct:
                return Media(token, title, "video/mp4", True, url=direct["url"], duration=info.get("duration"))
            try:
                inputs = hls.pick_relay(info)
            except ValueError as exc:
                raise RpcError(UNAVAILABLE, str(exc)) from exc
            copy = all(str(f.get("vcodec") or "").startswith("avc1") for f in info.get("formats") or []
                       if f.get("url") == inputs[0].url) if not audio_only else False
            return Media(token, title, "audio/mpeg" if audio_only else "video/mp2t", False,
                         relay=[{"url": i.url, "headers": i.headers} for i in inputs],
                         relay_video_copy=copy, audio_only=audio_only, duration=info.get("duration"))
        p = Path(path)
        if not p.is_file():
            raise RpcError(NOT_FOUND, f"no existe: {path}")
        probe = await asyncio.to_thread(hls.probe, hls.Input(str(p)))
        as_is, v_copy = tv_can_play(probe)
        duration = hls.duration_of(probe)
        title = title or p.stem
        if (as_is and mode != "relay") or mode == "direct":
            return Media(token, title, mime_for(str(p), audio_only), True, path=str(p), duration=duration)
        video = hls.video_stream(probe)
        return Media(token, title, "audio/mpeg" if video is None else "video/mp2t", False,
                     relay=[{"url": str(p), "headers": {}}], relay_video_copy=v_copy, audio_only=video is None,
                     duration=duration)

    # -- control ------------------------------------------------------------------------------------------------

    async def play(self, device_id: str, path: str, title: str = "", start: float = 0.0, mode: str = "auto",
                   audio_only: bool = False) -> dict[str, Any]:
        if mode not in ("auto", "direct", "relay"):
            raise RpcError(INVALID_PARAMS, "mode: auto, direct o relay")
        async with self._lock:
            device = await self._device(device_id)
            media = await self._prepare(path, title, mode, audio_only)
            media.offset = max(0.0, float(start)) if media.relay else 0.0
            if not media.url:
                await self._ensure_http(urllib.parse.urlparse(device.location).hostname or "")
            await self._stop_relays()
            self.media, self.device, self.ctrl = media, device, dlna.Controller(device)
            url = media.url or self.media_url(media)
            try:
                await self.ctrl.load(url, media.title, media.mime, media.seekable, media.duration)
                await self.ctrl.play()
                if start > 1 and not media.relay:
                    await self._seek_when_ready(start)
            except dlna.DlnaError as exc:
                raise RpcError(UNAVAILABLE, str(exc)) from exc
        return await self.status()

    async def _seek_when_ready(self, seconds: float) -> None:
        """TVs refuse Seek until they are PLAYING (a few seconds after Play)."""
        assert self.ctrl is not None
        for _ in range(20):
            with contextlib.suppress(dlna.DlnaError):
                st = await self.ctrl.position()
                if st.get("state") in ("PLAYING", "PAUSED_PLAYBACK"):
                    await self.ctrl.seek(seconds)
                    return
            await asyncio.sleep(0.5)

    async def control(self, action: str, value: float | None = None) -> dict[str, Any]:
        if self.ctrl is None or self.media is None:
            raise RpcError(NOT_FOUND, "no se está enviando nada a la tele")
        try:
            if action == "pause":
                await self.ctrl.pause()
            elif action == "play":
                await self.ctrl.play()
            elif action == "seek":
                target = max(0.0, float(value or 0))
                if self.media.relay:     # a live relay cannot seek: start it again from there
                    self.media.offset = target
                    self.media.token = secrets.token_urlsafe(18)
                    await self._stop_relays()
                    await self.ctrl.load(self.media_url(self.media), self.media.title, self.media.mime, False,
                                         self.media.duration)
                    await self.ctrl.play()
                else:
                    await self.ctrl.seek(target)
            elif action == "volume":
                await self.ctrl.set_volume(int(value or 0))
            elif action == "volume_add":
                return {"ok": True, "volume": await self.ctrl.add_volume(int(value or 0))}
            elif action == "stop":
                return await self.stop()
            else:
                raise RpcError(INVALID_PARAMS, f"acción desconocida: {action}")
        except dlna.DlnaError as exc:
            raise RpcError(UNAVAILABLE, str(exc)) from exc
        return await self.status()

    async def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"casting": self.media is not None, "devices": [r.to_dict() for r in self.devices.values()]}
        if self.media is None or self.ctrl is None or self.device is None:
            return out
        out.update(device=self.device.to_dict(), title=self.media.title, mode="relay" if self.media.relay else
                   ("url" if self.media.url else "file"), duration=self.media.duration)
        try:
            pos = await self.ctrl.position()
            if self.media.relay and pos.get("position") is not None:
                pos["position"] += self.media.offset      # the relay's clock starts at its offset
            out.update(pos)
        except dlna.DlnaError as exc:
            out["error"] = str(exc)
        with contextlib.suppress(dlna.DlnaError):
            out["volume"] = await self.ctrl.volume()
        if self.http is not None and not self.media.url:
            from mpvd.remote.service import firewall_hint  # noqa: PLC0415
            out["firewall"] = await asyncio.to_thread(firewall_hint, self.port, self.ip, "tele")
        return out

    async def stop(self) -> dict[str, Any]:
        if self.ctrl is not None:
            with contextlib.suppress(dlna.DlnaError):
                await self.ctrl.stop()
        self.media, self.ctrl, self.device = None, None, None
        await self._stop_relays()
        if self.http is not None:
            await self.http.stop()
            self.http = None
        return await self.status()

    async def _stop_relays(self) -> None:
        for proc in list(self._relays):
            await self._kill(proc)

    async def close(self) -> None:
        await self.stop()


def _route_ip(host: str) -> str:
    """Our address on the interface that reaches ``host`` (UDP connect sends nothing)."""
    import socket  # noqa: PLC0415

    if not host:
        return ""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return str(s.getsockname()[0])
    except OSError:
        return ""
    finally:
        s.close()


def register(server: MpvdServer, service: CastService) -> None:
    d = server.dispatcher

    @d.method("cast.discover")
    async def cast_discover(ctx: RpcContext, timeout: float = 3.0) -> dict[str, Any]:
        """Search the local network for TVs and players (DLNA renderers) for ``timeout`` seconds."""
        return {"devices": await service.discover(max(0.5, min(10.0, float(timeout))))}

    @d.method("cast.play")
    async def cast_play(ctx: RpcContext, device: str, path: str, title: str = "", start: float = 0.0,
                        mode: str = "auto", audio_only: bool = False) -> dict[str, Any]:
        """Send a local file or a URL to ``device`` from ``start`` seconds (``mode``: auto | direct | relay)."""
        return await service.play(device, path, title, start, mode, audio_only)

    @d.method("cast.control")
    async def cast_control(ctx: RpcContext, action: str, value: float | None = None) -> dict[str, Any]:
        """play | pause | seek (seconds) | volume (0–100) | volume_add (±n from the TV's own volume) | stop."""
        return await service.control(action, value)

    @d.method("cast.status")
    async def cast_status(ctx: RpcContext) -> dict[str, Any]:
        """What is being cast, where, and the TV's position and state."""
        return await service.status()

    @d.method("cast.stop")
    async def cast_stop(ctx: RpcContext) -> dict[str, Any]:
        """Stop the TV and the media server."""
        return await service.stop()
