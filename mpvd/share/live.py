"""«Emitir en directo» (H25): send a local file, or the video the player is showing, to an RTMP/RTMPS ingest server
(YouTube Live, Twitch, PeerTube, Owncast...) with ffmpeg.

Off until the user configures it: the server URL and the stream key live in ``<data_dir>/live.json``, written 0600
from creation (``write_private``). The key is never returned by the API, never logged, never shown on the OSD: every
text that could carry it (ffmpeg's stderr, errors) goes through ``redact``. The menu (mu-share) never sees it either:
it asks mpvd to read it from mpv's clipboard (``key_from=clipboard``) over mpvd's own IPC connection. Known limit:
ffmpeg needs the full URL as an argument, so while it runs the key is in its command line (``/proc/<pid>/cmdline``,
readable by other local users on a shared machine). ffmpeg 8 can load *CLI* option values from a file (``-/opt``) but
not protocol AVOptions such as ``-rtmp_playpath`` (checked: «Unrecognized option '/rtmp_playpath'»).

Encoding for a 4-core laptop without a dedicated GPU: H.264 at most 720p and 30 fps, 2500 kb/s CBR-like
(``-b:v``/``-maxrate``/``-bufsize``), a keyframe every 2 s (what YouTube and Twitch ask for), AAC 128 kb/s 44.1 kHz
stereo, FLV (``-flvflags no_duration_filesize``: nothing to rewrite at the end of a live stream). VA-API
(``h264_vaapi``, ``-low_power 1`` on iGPUs that only have EncSliceLP, detected like mpvd/convert) when available,
with a retry on the CPU (libx264 veryfast) when the GPU attempt fails before sending anything. Files and on-demand
URLs are read at their own pace (``-re``) from the host's position; live inputs as they come. A source without video
(radio) gets a black picture; one without audio, silence (the services expect both). Progress comes from
``-progress pipe:1``. «Parar» terminates only this ffmpeg's PID. Options checked with ffmpeg 8.0 (``ffmpeg -h
muxer=flv``, ``-h protocol=rtmp``, ``-h encoder=h264_vaapi``, ``-protocols``: rtmp, rtmps)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from mpvd.control import pick_session
from mpvd.convert import hw as hw_mod
from mpvd.convert.presets import HwPlan
from mpvd.library.settings import write_private
from mpvd.rpc import INVALID_PARAMS, UNAVAILABLE, RpcError
from mpvd.share import hls

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.sessions import Session
    from mpvd.share.service import ShareService

log = logging.getLogger("mpvd.share.live")

SETTINGS_FILE = "live.json"
TARGET = "mu_share"
MAX_HEIGHT = 720
MAX_FPS = 30.0
VIDEO_KBPS = 2500
AUDIO_KBPS = 128
AUDIO_RATE = 44100
KEYFRAME_SECONDS = 2
BLACK = "color=c=black:s=1280x720:r=30"
SILENCE = f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"
MASK = "••••"
KEY_RE = re.compile(r"^[\x21-\x7e]{4,512}$")   # printable ASCII without spaces (YouTube, Twitch, PeerTube, Owncast)
CLIPBOARD_PROP = "clipboard/text"
# ingest servers the menu offers (as published by each service); PeerTube/Owncast: the user's own instance
PRESETS = {
    "youtube": ("YouTube", "rtmp://a.rtmp.youtube.com/live2"),
    "youtube-rtmps": ("YouTube (cifrado)", "rtmps://a.rtmps.youtube.com:443/live2"),
    "twitch": ("Twitch", "rtmp://live.twitch.tv/app"),
}
FINAL = ("done", "failed", "stopped")
LEGAL = ("Emite solo lo que tengas derecho a compartir: lo tuyo, contenido libre o con permiso. "
         "Retransmitir películas, series, fútbol o canales de TV suele estar prohibido.")


# -- settings -------------------------------------------------------------------------------------------------------


def validate_server(raw: Any) -> str:
    """``rtmp://host[:port]/app[/...]`` or ``rtmps://...`` without user, query or fragment; trailing slash removed."""
    url = str(raw or "").strip()
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        raise ValueError("la dirección del servidor no es válida") from None
    if u.scheme not in ("rtmp", "rtmps"):
        raise ValueError("el servidor debe empezar por rtmp:// o rtmps://")
    if not u.hostname or u.username or u.password or u.query or u.fragment or any(c.isspace() for c in url):
        raise ValueError("la dirección del servidor no es válida")
    if not u.path.strip("/"):
        raise ValueError("falta la aplicación del servidor (por ejemplo …/live2 o …/live)")
    del port
    return url.rstrip("/")


def validate_key(raw: Any) -> str:
    key = str(raw or "").strip()
    if not KEY_RE.match(key):
        raise ValueError("la clave de emisión no es válida (sin espacios, de 4 a 512 caracteres)")
    return key


def server_host(url: str) -> str:
    """What may be shown of the server: its host (never the path: a pasted full URL would carry the key)."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def service_of(url: str) -> str:
    host = server_host(url)
    if host.endswith("youtube.com"):
        return "YouTube"
    if host.endswith("twitch.tv") or host.endswith("live-video.net"):
        return "Twitch"
    return ""


def target_url(server: str, key: str) -> str:
    return f"{server.rstrip('/')}/{key}"


def redact(text: str, secrets: list[str] | tuple[str, ...]) -> str:
    """``text`` with every secret (and its URL-encoded form) replaced by ••••."""
    out = str(text or "")
    for s in sorted({x for x in secrets if x and len(x) >= 4}, key=len, reverse=True):
        for form in {s, quote(s, safe="")}:
            out = out.replace(form, MASK)
    return out


class LiveSettings:
    """``<data_dir>/live.json`` (0600): {"server": ..., "key": ...}."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / SETTINGS_FILE
        self.server = ""
        self.key = ""
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s: %s", self.path.name, type(exc).__name__)
            return
        if isinstance(data, dict):
            with contextlib.suppress(ValueError):
                self.server = validate_server(data.get("server"))
            with contextlib.suppress(ValueError):
                self.key = validate_key(data.get("key"))
        with contextlib.suppress(OSError):
            if self.path.stat().st_mode & 0o077:
                os.chmod(self.path, 0o600)

    @property
    def configured(self) -> bool:
        return bool(self.server and self.key)

    def public(self) -> dict[str, Any]:
        return {"configured": self.configured, "server_host": server_host(self.server), "has_server": bool(self.server),
                "has_key": bool(self.key), "key_length": len(self.key), "service": service_of(self.server)}

    def save(self, server: str | None = None, key: str | None = None) -> None:
        """Validate and store; ``key=""`` forgets the key. Nothing changes when a value is invalid."""
        new_server = self.server if server is None else validate_server(server)
        new_key = self.key if key is None else ("" if key == "" else validate_key(key))
        write_private(self.path, {"server": new_server, "key": new_key})
        self.server, self.key = new_server, new_key


# -- the ffmpeg command -----------------------------------------------------------------------------------------------


@dataclass
class LivePlan:
    cmd: list[str]
    video: str     # vaapi | cpu
    audio: str     # aac


def build_command(inputs: list[hls.Input], probes: list[dict[str, Any]], target: str, *, hw: HwPlan | None = None,
                  start: float = 0.0, live: bool = False, audio_index: int | None = None, ffmpeg: str | None = None,
                  video_kbps: int = VIDEO_KBPS, max_height: int = MAX_HEIGHT) -> LivePlan:
    """ffmpeg arguments that send ``inputs`` to ``target`` (``rtmp[s]://server/app/key``) as H.264 + AAC in FLV.

    ``start``: seconds to begin at (files and on-demand URLs). ``live``: the input is itself live (no ``-re``, no
    seeking). ``audio_index``: ffprobe index of the audio the host is listening to. ``hw``: VA-API plan or None."""
    ff = ffmpeg or hls.ffmpeg_bin() or "ffmpeg"
    cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    if hw is not None:
        cmd += ["-vaapi_device", hw.device]
    video: tuple[int, dict[str, Any]] | None = None
    audio: tuple[int, dict[str, Any]] | None = None
    for i, data in enumerate(probes):
        vs = hls.video_stream(data)
        if video is None and vs is not None:
            video = (i, vs)
        streams = hls.audio_streams(data)
        if audio is None and streams:
            chosen = next((s for s in streams if audio_index is not None and s.get("index") == audio_index), streams[0])
            audio = (i, chosen)
    if video is None and audio is None:
        raise ValueError("no hay vídeo ni audio que emitir")
    pre = [] if live else ["-re"]
    if start > 0.5 and not live:
        pre += ["-ss", f"{start:.3f}"]
    for inp in inputs:
        cmd += pre + inp.args()
    n = len(inputs)
    synthetic = False
    if video is None:
        cmd += ["-re", "-f", "lavfi", "-i", BLACK]
        video = (n, {"index": 0, "height": 720, "avg_frame_rate": "30/1"})
        n += 1
        synthetic = True
    if audio is None:
        cmd += ["-re", "-f", "lavfi", "-i", SILENCE]
        audio = (n, {"index": 0})
        synthetic = True
    vi, vs = video
    ai, ast = audio
    cmd += ["-map", f"{vi}:{vs.get('index', 0)}", "-map", f"{ai}:{ast.get('index', 0)}"]
    fps = hls.fps_of(vs)
    out_fps = min(fps, MAX_FPS)
    gop = max(12, round(out_fps * KEYFRAME_SECONDS))
    filters = []
    if fps > MAX_FPS + 0.5:
        filters.append(f"fps={MAX_FPS:g}")
    height = int(vs.get("height") or 0)
    if height > max_height:
        filters.append(f"scale=-2:{max_height}")
    rate = ["-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k"]
    if hw is not None:
        vmode = "vaapi"
        filters += ["format=nv12", "hwupload"]
        vargs = ["-vf", ",".join(filters), "-c:v", "h264_vaapi"]
        if hw.low_power:
            vargs += ["-low_power", "1"]
        vargs += ["-rc_mode", "CBR", *rate, "-g", str(gop)]
    else:
        vmode = "cpu"
        filters.append("format=yuv420p")
        vargs = ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", *rate,
                 "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"]
    cmd += vargs + ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ar", str(AUDIO_RATE), "-ac", "2", "-sn", "-dn"]
    if synthetic:
        cmd += ["-shortest"]
    cmd += ["-f", "flv", "-flvflags", "no_duration_filesize", target]
    return LivePlan(cmd, vmode, "aac")


def plans_for(inputs: list[hls.Input], probes: list[dict[str, Any]], target: str, hw: HwPlan | None = None,
              **kw: Any) -> list[LivePlan]:
    """The plan to try first and, when it uses the GPU, the CPU retry."""
    first = build_command(inputs, probes, target, hw=hw, **kw)
    if first.video == "vaapi":
        return [first, build_command(inputs, probes, target, hw=None, **kw)]
    return [first]


def friendly_error(text: str) -> str:
    low = text.lower()
    if "connection refused" in low:
        return "el servidor de emisión rechaza la conexión"
    if "timed out" in low or "timeout" in low:
        return "el servidor de emisión no responde"
    if "broken pipe" in low or "end of file" in low or "connection reset" in low:
        return "el servidor ha cortado la emisión (¿clave incorrecta o emisión no preparada?)"
    if "name or service not known" in low or "failed to resolve" in low or "resolve" in low:
        return "no se encuentra el servidor (¿dirección mal escrita o sin internet?)"
    return text


# -- a running emission --------------------------------------------------------------------------------------------------


class LiveRun:
    """ffmpeg sending one source (plans tried in order); status, progress, stop (its own PID only)."""

    def __init__(self, plans: list[LivePlan], secrets: list[str], title: str = "", source: str = "",
                 on_change: Any = None):
        self.plans = plans
        self.secrets = secrets
        self.title = title
        self.source = source
        self.on_change = on_change
        self.status = "connecting"      # connecting | live | done | failed | stopped
        self.mode = plans[0].video if plans else ""
        self.error = ""
        self.detail = ""
        self.warnings: list[str] = []
        self.started_at = time.time()
        self.live_at: float | None = None
        self.progress: dict[str, str] = {}
        self.pid: int | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None
        self.stderr: deque[str] = deque(maxlen=20)

    @property
    def active(self) -> bool:
        return self.status not in FINAL

    def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="share-live")

    def _set(self, status: str) -> None:
        if status == self.status:
            return
        self.status = status
        if status == "live" and self.live_at is None:
            self.live_at = time.time()
        if self.on_change is not None:
            with contextlib.suppress(Exception):
                self.on_change(self)

    async def _read_progress(self, stream: asyncio.StreamReader) -> None:
        block: dict[str, str] = {}
        while True:
            line = await stream.readline()
            if not line:
                return
            k, _, v = line.decode("utf-8", "replace").strip().partition("=")
            if not k:
                continue
            block[k] = v.strip()
            if k == "progress":
                self.progress = block
                block = {}
                sent = _int(self.progress.get("total_size")) > 0 or _int(self.progress.get("out_time_us")) > 0
                if sent and self.status == "connecting":
                    self._set("live")

    async def _read_errors(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = redact(line.decode("utf-8", "replace").strip(), self.secrets)
            if text:
                self.stderr.append(text)

    async def _run(self) -> None:
        for n, plan in enumerate(self.plans):
            self.mode = plan.video
            self.progress = {}
            self.stderr.clear()
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *plan.cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
            except OSError as exc:
                self.error = f"no se pudo lanzar ffmpeg: {exc.strerror or type(exc).__name__}"
                self._set("failed")
                return
            self.pid = self.proc.pid
            assert self.proc.stdout is not None and self.proc.stderr is not None
            await asyncio.gather(self._read_progress(self.proc.stdout), self._read_errors(self.proc.stderr))
            code = await self.proc.wait()
            self.proc = None
            if self.status == "stopped":
                return
            if code == 0:
                self._set("done")
                return
            last = next((ln for ln in reversed(self.stderr) if ln), f"ffmpeg terminó con {code}")
            self.detail = re.sub(r"^\[[^\]]+ @ 0x[0-9a-f]+\]\s*", "", last)
            self.error = friendly_error(self.detail)
            log.warning("live (%s) failed: %s", plan.video, self.detail)
            if self.live_at is None and n + 1 < len(self.plans):
                self.warnings.append("la tarjeta gráfica falló: se emite con la CPU")
                continue
            self._set("failed")
            return

    async def stop(self) -> None:
        stopping = self.active
        if stopping:
            self.status = "stopped"      # announced once ffmpeg is really gone (below)
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        if self.task is not None and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 3)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self.task.cancel()
        if stopping and self.on_change is not None:
            with contextlib.suppress(Exception):
                self.on_change(self)

    def info(self) -> dict[str, Any]:
        p = self.progress
        return {"status": self.status, "mode": self.mode, "error": self.error, "detail": self.detail,
                "warnings": list(self.warnings), "title": self.title, "source": self.source,
                "started_at": round(self.started_at, 1), "live_at": round(self.live_at, 1) if self.live_at else None,
                "seconds": round(_int(p.get("out_time_us")) / 1e6, 1), "fps": _float(p.get("fps")),
                "kbps": _float((p.get("bitrate") or "").replace("kbits/s", "")),
                "speed": _float((p.get("speed") or "").rstrip("x")), "bytes": _int(p.get("total_size")),
                "running": self.active, "pid": self.pid if self.active else None}


def _int(v: Any) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0
    return round(f, 2) if f == f else 0.0  # NaN → 0


# -- the service ---------------------------------------------------------------------------------------------------------


@dataclass
class _Source:
    inputs: list[hls.Input]
    title: str
    kind: str                  # local | url | web
    start: float = 0.0
    live: bool = False
    audio_index: int | None = None
    probes: list[dict[str, Any]] = field(default_factory=list)


class LiveService:
    def __init__(self, server: MpvdServer, share: ShareService):
        self.server = server
        self.share = share
        self.settings = LiveSettings(server.settings.data_dir)
        self.run: LiveRun | None = None
        self.session_id: str | None = None
        self.clipboard_prop = os.environ.get("MPVD_LIVE_CLIPBOARD_PROP") or CLIPBOARD_PROP  # tests: a user-data prop
        self._starting = False
        self._tasks: set[asyncio.Task[Any]] = set()
        server.session_listeners.append(self._session_event)

    # -- state -------------------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        run = self.run
        return {**self.settings.public(), "ffmpeg": hls.ffmpeg_bin() is not None, "legal": LEGAL,
                "active": run is not None and run.active, "run": run.info() if run is not None else None,
                "presets": [{"id": k, "name": v[0], "server": v[1]} for k, v in PRESETS.items()]}

    def _secrets(self) -> list[str]:
        s = self.settings
        return [s.key, target_url(s.server, s.key)] if s.key else []

    def _push(self, run: LiveRun) -> None:
        s = self.server.sessions.get(self.session_id) if self.session_id else None
        if s is None or not s.connected:
            return
        text = {"live": "Emisión en directo en marcha", "done": "La emisión ha terminado: se acabó el vídeo",
                "stopped": "Emisión parada", "failed": f"La emisión se ha cortado: {run.error}"}.get(run.status, "")
        s.push_event(TARGET, "live", run.status, {"event": "live", "kind": run.status, "text": text,
                                                  "status": self.status()})

    def _session_event(self, kind: str, session: Session) -> None:
        if kind == "closed" and self.session_id == session.id and self.run is not None and self.run.active:
            t = asyncio.create_task(self.stop())  # the player that started it has closed: no orphan ffmpeg
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    # -- configuration -------------------------------------------------------------------------------------------

    async def configure(self, ctx_session: Session | None, server: str | None = None, key: str | None = None,
                        preset: str | None = None, key_from: str | None = None) -> dict[str, Any]:
        if preset:
            if preset not in PRESETS:
                raise RpcError(INVALID_PARAMS, f"servicio desconocido: {preset}")
            server = PRESETS[preset][1]
        if key_from is not None:
            if key_from != "clipboard":
                raise RpcError(INVALID_PARAMS, "key_from solo admite 'clipboard'")
            try:
                session = pick_session(self.server, None, ctx_session)
                value = await session.client.get_property(self.clipboard_prop, timeout=5)
            except Exception:  # noqa: BLE001 - never echo what the clipboard had
                raise RpcError(UNAVAILABLE, "no se pudo leer el portapapeles") from None
            if not isinstance(value, str) or not value.strip():
                raise RpcError(INVALID_PARAMS, "el portapapeles está vacío: copia antes la clave de emisión")
            key = value.strip()
        try:
            self.settings.save(server=server, key=key)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
        except OSError as exc:
            raise RpcError(UNAVAILABLE, f"no se pudo guardar la configuración: {exc.strerror}") from None
        log.info("live settings saved (server %s, key %s)", server_host(self.settings.server) or "-",
                 "set" if self.settings.key else "unset")
        return self.status()

    # -- source ---------------------------------------------------------------------------------------------------

    async def _source(self, session: Session | None, source: str, from_start: bool) -> _Source:
        share = self.share
        if source != "current":
            path = source
            title = Path(source).name if not hls.is_url(source) else source
            pos = 0.0
            audio_index = None
        else:
            if session is None:
                raise RpcError(UNAVAILABLE, "abre el reproductor antes de emitir")
            path = await share._prop(session, "path")
            if not path:
                raise RpcError(UNAVAILABLE, "no se está reproduciendo nada")
            path = str(path)
            title = str(await share._prop(session, "media-title") or "")
            pos = 0.0 if from_start else float(await share._prop(session, "time-pos") or 0.0)
            audio_index = None
        if not hls.is_url(path):
            local = await share._local_path(session, path) if source == "current" else Path(path).expanduser()
            if not local.is_file():
                raise RpcError(INVALID_PARAMS, "solo se emiten archivos de este equipo o vídeos de internet")
            if source == "current" and session is not None:
                audio_index = await share._audio_ff_index(session)
            src = _Source([hls.Input(str(local))], title or local.name, "local", pos, False, audio_index)
        elif hls.plain_direct(path):
            src = _Source([hls.Input(path)], title, "url", pos)
        else:
            info: dict[str, Any] | None = None
            seed = None
            if source == "current" and session is not None:
                res = await share._prop(session, "user-data/mpv/ytdl/json-subprocess-result")
                if isinstance(res, dict) and res.get("status") == 0 and isinstance(res.get("stdout"), str):
                    seed = res["stdout"]
            with contextlib.suppress(Exception):
                info = await self.server.ytdl.raw_info(path, seed=seed)
            if info and (info.get("formats") or info.get("url")):
                src = _Source(hls.pick_relay(info), title or str(info.get("title") or ""), "web", pos,
                              bool(info.get("is_live")))
            else:
                src = _Source([hls.Input(path)], title, "url", pos)  # IPTV and other streams ffmpeg reads directly
        try:
            src.probes = [await asyncio.to_thread(hls.probe, i) for i in src.inputs]
        except Exception as exc:  # noqa: BLE001
            raise RpcError(UNAVAILABLE, f"no se puede abrir la fuente: {redact(str(exc), self._secrets())}") from None
        if hls.duration_of(src.probes[0]) is None:
            src.live = True     # no duration: a live stream (no -re, no seeking)
        if src.live:
            src.start = 0.0
        return src

    # -- start / stop ------------------------------------------------------------------------------------------

    async def start(self, ctx_session: Session | None, source: str = "current", from_start: bool = False,
                    session_id: str | None = None) -> dict[str, Any]:
        if not self.settings.configured:
            raise RpcError(UNAVAILABLE, "configura antes el servidor y la clave de emisión")
        if self.run is not None and self.run.active:
            raise RpcError(UNAVAILABLE, "ya hay una emisión en marcha: párala antes")
        if self._starting:
            raise RpcError(UNAVAILABLE, "la emisión ya está arrancando")
        if not hls.ffmpeg_bin():
            raise RpcError(UNAVAILABLE, "ffmpeg no está instalado")
        self._starting = True
        try:
            session: Session | None = None
            with contextlib.suppress(RpcError):
                session = pick_session(self.server, session_id, ctx_session)
            src = await self._source(session, str(source or "current"), bool(from_start))
            caps = await self.server.convert.hw_caps()
            hw_plan = hw_mod.plan_for(caps, "h264")
            target = target_url(self.settings.server, self.settings.key)
            try:
                plans = plans_for(src.inputs, src.probes, target, hw=hw_plan, start=src.start, live=src.live,
                                  audio_index=src.audio_index)
            except ValueError as exc:
                raise RpcError(INVALID_PARAMS, str(exc)) from None
            self.session_id = session.id if session is not None else None
            run = LiveRun(plans, self._secrets(), title=src.title, source=src.kind, on_change=self._push)
            self.run = run
            run.start()
            log.info("live: sending %s (%s, from %.0f s) to %s with %s", src.kind, "live" if src.live else "file",
                     src.start, server_host(self.settings.server), plans[0].video)
        finally:
            self._starting = False
        return self.status()

    async def stop(self) -> dict[str, Any]:
        run = self.run
        if run is not None:
            await run.stop()
        return self.status()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.stop()


def register(server: MpvdServer, service: LiveService) -> None:
    d = server.dispatcher
    server.services["live"] = True

    @d.method("live.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """Configuration (never the key: only whether it is set) and the running emission, if any."""
        return service.status()

    @d.method("live.configure")
    async def configure(ctx: RpcContext, server: str | None = None, key: str | None = None, preset: str | None = None,
                        key_from: str | None = None) -> dict[str, Any]:
        """Save the ingest server (``server`` or ``preset``: youtube, youtube-rtmps, twitch) and/or the stream key
        (``key``; ``key_from="clipboard"`` reads it from the player's clipboard; ``key=""`` forgets it)."""
        return await service.configure(ctx.session, server=server, key=key, preset=preset, key_from=key_from)

    @d.method("live.start")
    async def start(ctx: RpcContext, source: str = "current", from_start: bool = False,
                    session: str | None = None) -> dict[str, Any]:
        """Start the emission: ``source="current"`` (what the player shows, from its position unless from_start)
        or a local file / URL."""
        return await service.start(ctx.session, source=source, from_start=from_start, session_id=session)

    @d.method("live.stop")
    async def stop(ctx: RpcContext) -> dict[str, Any]:
        """Stop the emission (terminates only its own ffmpeg)."""
        return await service.stop()


__all__ = ["LEGAL", "PRESETS", "LivePlan", "LiveRun", "LiveService", "LiveSettings", "build_command", "plans_for",
           "redact", "register", "target_url", "validate_key", "validate_server"]
