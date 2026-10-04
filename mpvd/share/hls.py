"""What a guest's browser plays (H25): the direct URL of a web video when a browser can play it, otherwise an HLS
relay made by ffmpeg in the cache (``-c copy`` for H.264/AAC, else H.264 by VA-API — ``h264_vaapi -low_power 1`` on
iGPUs that only expose VAEntrypointEncSliceLP — or libx264 veryfast), and the active text subtitle as WebVTT.

HLS options verified with ``ffmpeg -h muxer=hls`` (ffmpeg 8.0): ``-hls_time``, ``-hls_list_size 0``,
``-hls_playlist_type event`` (every segment stays listed while it grows; ENDLIST at the end), ``-hls_flags
independent_segments+temp_file`` (segments and playlist written to a temporary name and renamed: never served half
written), ``-hls_segment_type mpegts`` and ``-hls_segment_filename``. MPEG-TS segments play in Safari/iOS/Android
natively and in desktop Chrome/Firefox through the vendored hls.js."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from mpvd import priority as prio
from mpvd.convert.presets import HwPlan

log = logging.getLogger("mpvd.share.hls")

SEGMENT_SECONDS = 4
PLAYLIST = "index.m3u8"
SUBS_FILE = "subs.vtt"
MAX_HEIGHT_HW = 1080
MAX_HEIGHT_CPU = 720
VAAPI_QP = 24
X264_CRF = 23
AUDIO_KBPS = 160
NICE = 10
BROWSER_H264_PIX = {"yuv420p", "yuvj420p"}
BAD_H264_PROFILES = {"high 10", "high 4:2:2", "high 4:4:4 predictive", "high 10 intra", "high 4:2:2 intra"}
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
# request headers a browser sends anyway: a format that needs others (Referer, Cookie...) cannot be played directly
PLAIN_HEADERS = {"user-agent", "accept", "accept-language", "sec-fetch-mode", "accept-encoding"}
DIRECT_EXT = {".mp4", ".m4v", ".webm", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav"}


@dataclass
class Input:
    """One ffmpeg input (a file or URL) with the request headers it needs.

    ``start``: H44/C5, seconds to skip before reading (``-ss`` BEFORE ``-i``, which is the fast seek: ffmpeg jumps
    with the demuxer instead of decoding and throwing away). The relay used to start at second 0 of the film even
    when the host was at minute 40, and the guest waited for the packaging to *catch up* with them: measured at
    2,2× real time with VA-API, that is ~18 minutes of «Preparando la retransmisión…». Timestamps come out rebased
    to 0, so whoever plays it has to add this offset back (``media.offset``)."""
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    start: float = 0.0

    def args(self) -> list[str]:
        out: list[str] = []
        if self.start > 0.5:
            out += ["-ss", f"{self.start:.3f}"]
        ua = next((v for k, v in self.headers.items() if k.lower() == "user-agent"), None)
        extra = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items() if k.lower() not in PLAIN_HEADERS)
        if ua and "://" in self.url:
            out += ["-user_agent", ua]
        if extra and "://" in self.url:
            out += ["-headers", extra]
        if "://" in self.url:
            out += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        return out + ["-i", self.url]


# -- probing -------------------------------------------------------------------------------------------------


def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def probe(inp: Input, timeout: float = 30.0) -> dict[str, Any]:
    """``ffprobe -show_streams -show_format`` as a dict (blocking: call it in a thread)."""
    fp = ffprobe_bin()
    if not fp:
        raise RuntimeError("ffprobe no está instalado")
    cmd = [fp, "-v", "error", "-show_streams", "-show_format", "-of", "json"]
    ua = next((v for k, v in inp.headers.items() if k.lower() == "user-agent"), None)
    if ua and "://" in inp.url:
        cmd += ["-user_agent", ua]
    out = subprocess.run([*cmd, inp.url], capture_output=True, text=True, timeout=timeout, check=False)
    if out.returncode != 0:
        raise RuntimeError((out.stderr.strip().splitlines() or [f"ffprobe terminó con {out.returncode}"])[-1])
    return json.loads(out.stdout or "{}")


def video_stream(data: dict[str, Any]) -> dict[str, Any] | None:
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            return s
    return None


def audio_streams(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in data.get("streams") or [] if s.get("codec_type") == "audio"]


def duration_of(data: dict[str, Any]) -> float | None:
    try:
        d = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return None
    return d if d > 0 else None


def fps_of(stream: dict[str, Any] | None) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str((stream or {}).get(key) or "")
        try:
            f = float(Fraction(raw)) if raw and not raw.endswith("/0") else 0.0
        except (ValueError, ZeroDivisionError):
            f = 0.0
        if 1 <= f <= 240:
            return f
    return 25.0


def video_copyable(stream: dict[str, Any] | None) -> bool:
    """H.264 8-bit 4:2:0: every browser with HLS (native or hls.js) decodes it as is."""
    if not stream:
        return False
    if stream.get("codec_name") != "h264":
        return False
    if (stream.get("pix_fmt") or "yuv420p") not in BROWSER_H264_PIX:
        return False
    return str(stream.get("profile") or "").lower() not in BAD_H264_PROFILES


def audio_copyable(stream: dict[str, Any] | None) -> bool:
    return bool(stream) and stream.get("codec_name") == "aac"  # type: ignore[union-attr]


# Containers a browser's <video> opens as they are. Checked with ffprobe on 2026-10-01: the recordings of this house
# are **HEVC + Opus in MP4 with the moov at the end**, which is exactly what a browser handles worst (HEVC only in
# Safari and in Chrome with hardware support, never in Firefox; Opus inside MP4 not in Safari). So «it is an MP4» is
# not enough: the codecs, the container AND where the `moov` sits all have to be checked.
MP4_FORMATS = {"mov,mp4,m4a,3gp,3g2,mj2"}
WEBM_FORMATS = {"matroska,webm", "webm"}


def moov_at_start(path: Path) -> bool:
    """True when an MP4 carries its ``moov`` atom before ``mdat`` (``-movflags +faststart``).

    With it at the end the browser has to download the whole film before the first frame, which over a tunnel is
    unusable; mpv and VLC do not care, because they seek. Read by walking the top-level atoms, which costs two or
    three reads."""
    try:
        with path.open("rb") as fh:
            for _ in range(64):                       # un MP4 normal tiene 3 o 4 átomos de primer nivel
                head = fh.read(8)
                if len(head) < 8:
                    return False
                size = int.from_bytes(head[:4], "big")
                kind = head[4:8]
                if kind == b"moov":
                    return True
                if kind == b"mdat":
                    return False
                if size == 1:                         # tamaño de 64 bits en los 8 bytes siguientes
                    ext = fh.read(8)
                    if len(ext) < 8:
                        return False
                    size = int.from_bytes(ext, "big")
                    if size < 16:
                        return False
                    fh.seek(size - 16, 1)
                elif size < 8:
                    return False
                else:
                    fh.seek(size - 8, 1)
    except OSError:
        return False
    return False


def browser_playable(data: dict[str, Any] | None, path: Path | None = None) -> bool:
    """H44/C4: can a browser's <video> play this file as it is, without repackaging anything?"""
    if not data:
        return False
    fmt = str((data.get("format") or {}).get("format_name") or "")
    v = video_stream(data)
    auds = audio_streams(data)
    if v is None and not auds:
        return False
    vname = str((v or {}).get("codec_name") or "")
    aname = str(auds[0].get("codec_name") or "") if auds else ""
    # OJO: ffprobe llama `matroska,webm` igual a un .webm y a un .mkv, así que el contenedor no basta para decidir.
    # Un Matroska solo lo abre el navegador cuando por dentro es WebM de verdad (VP8/VP9/AV1 + Opus/Vorbis); un
    # .mkv con H.264 + AAC no va en Firefox ni en Safari, aunque Chrome a veces lo aguante.
    if fmt in WEBM_FORMATS:
        return (v is None or vname in ("vp8", "vp9", "av1")) and (not auds or aname in ("opus", "vorbis"))
    if fmt not in MP4_FORMATS:
        return False
    if v is not None and not (video_copyable(v) or vname == "av1"):
        return False
    if auds and not (audio_copyable(auds[0]) or aname == "mp3"):
        return False
    return path is None or moov_at_start(path)


# -- the ffmpeg command ---------------------------------------------------------------------------------------


@dataclass
class HlsPlan:
    cmd: list[str]
    video: str   # copy | vaapi | cpu | none
    audio: str   # copy | aac | none

    @property
    def mode(self) -> str:
        if self.video in ("vaapi", "cpu"):
            return self.video
        return "copy" if self.audio in ("copy", "none") else "audio"


def _scale(height: int, max_height: int) -> str | None:
    if height and height > max_height:
        return f"scale=-2:{max_height}"
    return None


def build_command(inputs: list[Input], out_dir: Path, video: tuple[int, dict[str, Any]] | None,
                  audio: tuple[int, dict[str, Any]] | None, hw: HwPlan | None = None, ffmpeg: str | None = None,
                  force_transcode: bool = False, segment: int = SEGMENT_SECONDS) -> HlsPlan:
    """ffmpeg arguments that turn ``inputs`` into ``out_dir/index.m3u8`` + ``seg_NNNNN.ts``.

    ``video``/``audio``: (input index, ffprobe stream) to map, or None. ``hw``: VA-API plan for h264 (None = CPU)."""
    ff = ffmpeg or ffmpeg_bin() or "ffmpeg"
    cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    vmode, amode = "none", "none"
    vargs: list[str] = []
    if video is not None:
        vi, vs = video
        if video_copyable(vs) and not force_transcode:
            vmode = "copy"
            vargs = ["-c:v", "copy"]
        else:
            gop = max(12, round(fps_of(vs) * 2))
            height = int(vs.get("height") or 0)
            if hw is not None:
                vmode = "vaapi"
                cmd += ["-vaapi_device", hw.device]
                vf = ",".join(f for f in (_scale(height, MAX_HEIGHT_HW), "format=nv12", "hwupload") if f)
                vargs = ["-vf", vf, "-c:v", "h264_vaapi"]
                if hw.low_power:
                    vargs += ["-low_power", "1"]
                vargs += ["-rc_mode", "CQP", "-qp", str(VAAPI_QP), "-g", str(gop)]
            else:
                vmode = "cpu"
                scale = _scale(height, MAX_HEIGHT_CPU)
                vargs = (["-vf", scale] if scale else []) + [
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", str(X264_CRF), "-pix_fmt", "yuv420p",
                    "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"]
    for inp in inputs:
        cmd += inp.args()
    if video is not None:
        cmd += ["-map", f"{video[0]}:{video[1].get('index', 0)}"]
    if audio is not None:
        ai, ast = audio
        cmd += ["-map", f"{ai}:{ast.get('index', 0)}"]
        if audio_copyable(ast):
            amode = "copy"
            aargs = ["-c:a", "copy"]
        else:
            amode = "aac"
            aargs = ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2"]
    else:
        aargs = []
    cmd += vargs + aargs + ["-sn", "-dn",
                            "-f", "hls", "-hls_time", str(segment), "-hls_list_size", "0",
                            "-hls_playlist_type", "event", "-hls_flags", "independent_segments+temp_file",
                            "-hls_segment_type", "mpegts",
                            "-hls_segment_filename", str(out_dir / "seg_%05d.ts"), str(out_dir / PLAYLIST)]
    return HlsPlan(cmd, vmode, amode)


def plans_for(inputs: list[Input], probes: list[dict[str, Any]], out_dir: Path, audio_index: int | None = None,
              hw: HwPlan | None = None, ffmpeg: str | None = None) -> list[HlsPlan]:
    """The plan to try first and, when it uses VA-API, the CPU fallback (drivers fail in surprising ways).

    ``audio_index``: ffprobe stream index of the audio the host is listening to (in the first input carrying audio)."""
    video: tuple[int, dict[str, Any]] | None = None
    audio: tuple[int, dict[str, Any]] | None = None
    for i, data in enumerate(probes):
        vs = video_stream(data)
        if video is None and vs is not None:
            video = (i, vs)
        streams = audio_streams(data)
        if audio is None and streams:
            chosen = next((s for s in streams if audio_index is not None and s.get("index") == audio_index), streams[0])
            audio = (i, chosen)
    if video is None and audio is None:
        raise ValueError("no hay vídeo ni audio que retransmitir")
    first = build_command(inputs, out_dir, video, audio, hw=hw, ffmpeg=ffmpeg)
    if first.video == "vaapi":
        return [first, build_command(inputs, out_dir, video, audio, hw=None, ffmpeg=ffmpeg)]
    return [first]


# -- playlist -------------------------------------------------------------------------------------------------

_EXTINF = re.compile(r"^#EXTINF:([0-9.]+)", re.MULTILINE)


def playlist_info(text: str) -> dict[str, Any]:
    durations = [float(x) for x in _EXTINF.findall(text or "")]
    m = re.search(r"#EXT-X-PLAYLIST-TYPE:(\w+)", text or "")
    return {"segments": len(durations), "seconds": round(sum(durations), 3),
            "complete": "#EXT-X-ENDLIST" in (text or ""), "type": m.group(1) if m else ""}


# -- subtitles -------------------------------------------------------------------------------------------------


def vtt_command(source: str, out: Path, stream_index: int | None = None, ffmpeg: str | None = None) -> list[str]:
    """Text subtitle (a stream of ``source``, or a whole .srt/.ass/.vtt file) → WebVTT."""
    ff = ffmpeg or ffmpeg_bin() or "ffmpeg"
    cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", source]
    cmd += ["-map", f"0:{stream_index}"] if stream_index is not None else ["-map", "0:s:0"]
    return cmd + ["-c:s", "webvtt", "-f", "webvtt", str(out)]


# -- web videos -----------------------------------------------------------------------------------------------


def _codec(v: Any) -> str:
    return "" if v in (None, "none") else str(v).lower()


def _needs_headers(f: dict[str, Any]) -> bool:
    return any(k.lower() not in PLAIN_HEADERS for k in (f.get("http_headers") or {}))


def pick_direct(info: dict[str, Any], max_height: int = 1080) -> dict[str, Any] | None:
    """A yt-dlp format a ``<video>`` element plays by itself: one progressive http(s) file with video and audio in
    MP4 (H.264/AAC, or unknown codecs in an .mp4) and no special request headers. None (YouTube today: no combined
    formats) → the relay."""
    formats = list(info.get("formats") or [])
    if not formats and info.get("url"):
        formats = [info]
    best: tuple[tuple[float, ...], dict[str, Any]] | None = None
    for f in formats:
        url = f.get("url")
        if not url or str(f.get("protocol") or "https") not in ("https", "http"):
            continue
        if _needs_headers(f):
            continue
        vc, ac = _codec(f.get("vcodec")), _codec(f.get("acodec"))
        ext = str(f.get("ext") or "").lower()
        if f.get("vcodec") == "none" or f.get("acodec") == "none":
            continue  # video-only or audio-only
        if ext != "mp4":
            continue
        if vc and not (vc.startswith("avc1") or vc.startswith("h264")):
            continue
        if ac and not (ac.startswith("mp4a") or ac == "aac"):
            continue
        height = float(f.get("height") or 0)
        if height > max_height:
            continue
        known = 1.0 if vc and ac else 0.0
        key = (known, height, float(f.get("tbr") or 0))
        if best is None or key > best[0]:
            best = (key, f)
    if best is None:
        return None
    f = best[1]
    return {"url": f["url"], "format_id": f.get("format_id"), "height": f.get("height"), "ext": f.get("ext")}


def pick_relay(info: dict[str, Any], max_height: int = 720) -> list[Input]:
    """Inputs for ffmpeg: one combined format, or the best H.264 (≤ max_height) video + AAC audio, falling back to
    any video/audio when there is no H.264 (then the relay transcodes)."""
    formats = [f for f in info.get("formats") or [] if f.get("url") and str(f.get("protocol") or "")
               not in ("mhtml",) and not str(f.get("format_id") or "").startswith("sb")]
    if not formats and info.get("url"):
        return [Input(str(info["url"]), dict(info.get("http_headers") or {}))]

    def score_v(f: dict[str, Any]) -> tuple[float, ...]:
        h = float(f.get("height") or 0)
        vc = _codec(f.get("vcodec"))
        return (1.0 if h <= max_height else 0.0, 1.0 if vc.startswith("avc1") else 0.0,
                1.0 if f.get("protocol") in ("https", "http") else 0.0, h if h <= max_height else -h,
                float(f.get("tbr") or 0))

    def score_a(f: dict[str, Any]) -> tuple[float, ...]:
        ac = _codec(f.get("acodec"))
        return (1.0 if ac.startswith("mp4a") else 0.0, 0.0 if "drc" in str(f.get("format_id")) else 1.0,
                float(f.get("abr") or f.get("tbr") or 0))

    combined = [f for f in formats if _codec(f.get("vcodec")) and _codec(f.get("acodec"))]
    unknown = [f for f in formats if f.get("vcodec") is None and f.get("acodec") is None]
    videos = [f for f in formats if _codec(f.get("vcodec")) and f.get("acodec") == "none"]
    audios = [f for f in formats if _codec(f.get("acodec")) and f.get("vcodec") == "none"]
    if videos and audios:
        v = max(videos, key=score_v)
        a = max(audios, key=score_a)
        if not combined or float(v.get("height") or 0) > float(max(combined, key=score_v).get("height") or 0):
            return [Input(v["url"], dict(v.get("http_headers") or {})), Input(a["url"], dict(a.get("http_headers") or {}))]
    pool = combined or unknown or videos or audios
    if not pool:
        raise ValueError("yt-dlp no da ningún formato retransmitible")
    f = max(pool, key=score_v)
    return [Input(f["url"], dict(f.get("http_headers") or {}))]


def is_url(path: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path or "")) and not path.startswith("file://")


def plain_direct(url: str) -> bool:
    """An http(s) link straight to a media file a browser plays (no yt-dlp needed)."""
    if not url.startswith(("http://", "https://")):
        return False
    tail = url.split("?", 1)[0].split("#", 1)[0].lower()
    return any(tail.endswith(ext) for ext in DIRECT_EXT)


# -- a running relay --------------------------------------------------------------------------------------------




class HlsStream:
    """One relay: ffmpeg (plans tried in order) writing into ``out_dir``; status, produced seconds, stop."""

    def __init__(self, sid: str, out_dir: Path, plans: list[HlsPlan], duration: float | None = None, label: str = "",
                 offset: float = 0.0):
        self.id = sid
        self.dir = out_dir
        self.plans = plans
        self.duration = duration
        self.offset = offset           # H44/C5: second of the original the first segment corresponds to
        self.label = label
        self.status = "preparing"      # preparing | running | done | failed | stopped
        self.mode = plans[0].mode if plans else ""
        self.error = ""
        self.started_at = time.time()
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None

    @property
    def playlist(self) -> Path:
        return self.dir / PLAYLIST

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.task = asyncio.create_task(self._run(), name=f"share-hls-{self.id}")

    async def _run(self) -> None:
        for n, plan in enumerate(self.plans):
            self.mode = plan.mode
            self.status = "running"
            kwargs: dict[str, Any] = {}
            if os.name == "posix":
                kwargs["preexec_fn"] = prio.lower
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *plan.cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE, **kwargs)
            except OSError as exc:
                self.status, self.error = "failed", f"no se pudo lanzar ffmpeg: {exc}"
                return
            _, err = await self.proc.communicate()
            code = self.proc.returncode
            self.proc = None
            if self.status == "stopped":
                return
            if code == 0:
                self.status = "done"
                return
            self.error = (err.decode("utf-8", "replace").strip().splitlines() or [f"ffmpeg terminó con {code}"])[-1]
            produced = self.info()["segments"]
            log.warning("hls %s (%s) failed: %s", self.id, plan.mode, self.error)
            if produced == 0 and n + 1 < len(self.plans):
                for p in self.dir.glob("*"):
                    with contextlib.suppress(OSError):
                        p.unlink()
                continue
            self.status = "failed"
            return

    def info(self) -> dict[str, Any]:
        try:
            text = self.playlist.read_text(encoding="utf-8")
        except OSError:
            text = ""
        pi = playlist_info(text)
        return {"id": self.id, "status": self.status, "mode": self.mode, "error": self.error,
                "ready": pi["seconds"], "segments": pi["segments"], "complete": pi["complete"],
                "duration": self.duration, "offset": self.offset}

    async def stop(self) -> None:
        if self.status in ("running", "preparing"):
            self.status = "stopped"
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task

    def remove_files(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
