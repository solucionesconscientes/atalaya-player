"""Conversion presets → exact ffmpeg argument lists (H20).

Every option below was checked against the installed FFmpeg 8.0 (``ffmpeg -h encoder=libx264|libx265|libvpx-vp9|
h264_vaapi|libopus``) and by converting the test media (tests/test_convert_presets.py, tests/test_convert_service.py).

Presets: «MP4 compatible» (H.264 + AAC, faststart), «Más pequeño» (H.265 + AAC in mp4 with the ``hvc1`` tag that Apple
players need, or + Opus in mkv), «Web» (VP9 + Opus in WebM, ADR-048), audio only (MP3, M4A/AAC, Opus, FLAC, WAV) and
GIF (two passes: palettegen → paletteuse, limited fps and width). Options: resolution cap (short side, so a vertical
video keeps its orientation), quality (→ CRF/QP/bitrate), a time range, keep subtitles, and VA-API for H.264/H.265 when
the machine offers it (see mpvd/convert/hw.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from mpvd.i18n import t
from pathlib import Path
from typing import Any

PRESETS: list[dict[str, Any]] = [
    {"id": "mp4", "label": "MP4 compatible", "hint": "H.264 + AAC · se abre en cualquier sitio", "kind": "video",
     "ext": ".mp4", "vcodec": "h264"},
    {"id": "small", "label": "Más pequeño (H.265)", "hint": "ocupa la mitad · tarda más", "kind": "video",
     "ext": ".mp4", "vcodec": "hevc"},
    {"id": "web", "label": "Web (WebM)", "hint": "VP9 + Opus para páginas web", "kind": "video", "ext": ".webm",
     "vcodec": "vp9"},
    # H55 · AV1: lo más nuevo, y para el mismo aspecto ocupa bastante menos que H.265. A cambio tarda más en una
    # CPU modesta, por eso no es el que viene puesto. Solo se ofrece si este ffmpeg trae SVT-AV1.
    {"id": "av1", "label": "AV1 (el que menos ocupa)", "hint": "lo más nuevo · tarda más de codificar",
     "kind": "video", "ext": ".mp4", "vcodec": "av1"},
    # Y el contrario: no tocar nada. Corta por fotograma clave (puede empezar un poco antes), es instantáneo y no
    # pierde ni un bit. MKV porque acepta cualquier códec que venga.
    {"id": "copy", "label": "Sin recodificar (rapidísimo)", "hint": "calidad intacta · corta por fotograma clave",
     "kind": "video", "ext": ".mkv"},
    {"id": "mp3", "label": "Solo audio · MP3", "hint": "el más compatible", "kind": "audio", "ext": ".mp3"},
    {"id": "m4a", "label": "Solo audio · M4A (AAC)", "hint": "móviles y Apple", "kind": "audio", "ext": ".m4a"},
    {"id": "opus", "label": "Solo audio · Opus", "hint": "el más pequeño", "kind": "audio", "ext": ".opus"},
    {"id": "flac", "label": "Solo audio · FLAC", "hint": "sin pérdida", "kind": "audio", "ext": ".flac"},
    {"id": "wav", "label": "Solo audio · WAV", "hint": "sin comprimir", "kind": "audio", "ext": ".wav"},
    {"id": "gif", "label": "GIF animado", "hint": "sin sonido · máx. 60 s", "kind": "gif", "ext": ".gif"},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

# Codificadores que no están en todos los ffmpeg. Se mira una vez y se recuerda: ofrecer un formato que esta
# máquina no puede hacer es peor que no ofrecerlo (H55).
NEEDS_ENCODER = {"av1": "libsvtav1"}
_encoders: set[str] | None = None


def available_encoders(ffmpeg: str | None = None) -> set[str]:
    global _encoders  # noqa: PLW0603
    if _encoders is None:
        try:
            binario = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
            out = subprocess.run([binario, "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=20, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _encoders = {line.split()[1] for line in out.splitlines()
                     if line.startswith(" ") and len(line.split()) > 1}
    return _encoders


def usable_presets(ffmpeg: str | None = None) -> list[dict[str, Any]]:
    """Los formatos que ESTA máquina puede hacer de verdad, con el nombre y la pista en el idioma de quien mira.

    H49/G8 · la tabla de arriba está en castellano porque la cadena castellana ES la clave; traducirla donde se
    define no valdría (el idioma se decide al servir, no al importar el módulo), así que se traduce aquí, que es
    por donde salen hacia el menú."""
    have = available_encoders(ffmpeg)
    out = []
    for p in PRESETS:
        if NEEDS_ENCODER.get(str(p["id"]), "") not in ("", *have):
            continue
        fila = dict(p)
        for campo in ("label", "hint"):
            if fila.get(campo):
                fila[campo] = t(str(fila[campo]))
        out.append(fila)
    return out

HEIGHTS = (0, 1080, 720, 480)                 # 0 = original
QUALITIES = ("high", "normal", "small")
# Las tablas de nombres se escriben con `label`, que es la forma que el extractor sabe recoger (H49/G8)
QUALITY_ROWS = ({"id": "high", "label": "alta"}, {"id": "normal", "label": "normal"},
                {"id": "small", "label": "pequeña"})
QUALITY_LABELS = {str(r["id"]): str(r["label"]) for r in QUALITY_ROWS}


def quality_labels() -> dict[str, str]:
    """Los nombres de las calidades en el idioma de quien mira (la clave sigue siendo la castellana)."""
    return {str(r["id"]): t(str(r["label"])) for r in QUALITY_ROWS}
HW_MODES = ("auto", "cpu", "vaapi")
SPEEDS = ("normal", "fast")                   # fast = tests (and impatient users): fastest encoder settings
SMALL_CONTAINERS = ("mp4", "mkv")
AUDIO_BITRATE_CHOICES = (64, 96, 128, 160, 192, 256, 320)
GIF_WIDTHS = (320, 480, 640)
GIF_FPS = (10, 12, 15)
GIF_MAX_SECONDS = 60.0
MAX_RANGES = 50                               # H52 · tramos que se pueden unir de una vez

# video quality → CRF (x264/x265/VP9) or constant QP (VA-API): «normal» is each encoder's usual default
CRF = {"h264": {"high": 20, "normal": 23, "small": 28},
       "hevc": {"high": 24, "normal": 28, "small": 32},
       "vp9": {"high": 31, "normal": 35, "small": 40},
       "av1": {"high": 28, "normal": 34, "small": 42}}      # SVT-AV1 usa una escala más alta que x264/x265
VAAPI_QP = {"h264": {"high": 20, "normal": 24, "small": 29}, "hevc": {"high": 22, "normal": 26, "small": 31}}
# audio bitrate (kbps) by quality: audio-only presets and the audio of video presets
AUDIO_KBPS = {"mp3": {"high": 256, "normal": 192, "small": 128},
              "m4a": {"high": 256, "normal": 192, "small": 128},
              "opus": {"high": 160, "normal": 128, "small": 64},
              "aac": {"high": 192, "normal": 160, "small": 128},     # audio of mp4 video
              "libopus": {"high": 160, "normal": 128, "small": 96}}  # audio of webm/mkv video
SVTAV1_PRESET = {"normal": "8", "fast": "12"}   # 0 = lentísimo y pequeñísimo, 13 = lo más rápido
X264_PRESET = {"normal": "fast", "fast": "ultrafast"}
X265_PRESET = {"normal": "fast", "fast": "ultrafast"}
VP9_SPEED = {"normal": ["-deadline", "good", "-cpu-used", "4"], "fast": ["-deadline", "realtime", "-cpu-used", "8"]}

# subtitles that are text (convertible to mov_text / WebVTT / SRT); anything else (PGS, VobSub, DVB) is a picture
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer", "subviewer1",
             "jacosub", "realtext", "sami", "stl", "mpl2", "vplayer", "pjs"}
# mkv carries every subtitle codec except mp4's mov_text (rewritten as SRT)
MKV_REWRITE = {"mov_text": "srt"}

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".wmv", ".flv", ".ts", ".m2ts", ".mts", ".mpg",
              ".mpeg", ".3gp", ".ogv", ".vob", ".divx", ".asf"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".wma", ".alac", ".aiff", ".aif",
              ".ape", ".wv", ".mka", ".ac3", ".dts"}

_RANGE_NAME = re.compile(r"[/\\:*?\"<>|\x00-\x1f]")


class ConvertError(ValueError):
    pass


@dataclass
class ConvertSpec:
    preset: str
    height: int = 0                 # resolution cap on the short side (0 = original)
    quality: str = "normal"         # high | normal | small
    start: float | None = None      # range in seconds (None = from the beginning / to the end)
    end: float | None = None
    subtitles: bool = True          # keep subtitle tracks (converted when the container needs it)
    container: str = ""             # «small»: mp4 (default) | mkv
    audio_bitrate: int | None = None  # kbps override for lossy audio
    audio_track: int | None = None  # audio presets: which audio track (0-based among audio streams; None = first)
    gif_width: int = 480
    gif_fps: int = 12
    hw: str = "auto"                # auto (VA-API when available) | cpu | vaapi
    speed: str = "normal"           # normal | fast (fastest encoder settings, used by the tests)
    # H52 · «juntar los tramos que quieras» en UN archivo: [[inicio, fin], …] en segundos. Con uno solo es lo
    # mismo que start/end; con varios se corta y se pega en una sola pasada de ffmpeg (trim + concat).
    ranges: list[list[float]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConvertSpec:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("height", "gif_width", "gif_fps"):
            if k in known and known[k] is not None:
                known[k] = int(known[k])
        for k in ("audio_bitrate", "audio_track"):
            if known.get(k) is not None:
                known[k] = int(known[k])
        for k in ("start", "end"):
            if known.get(k) is not None:
                known[k] = float(known[k])
        if known.get("ranges"):
            known["ranges"] = [[float(r[0]), float(r[1])] for r in known["ranges"] if len(r) >= 2]
            # un solo tramo es exactamente start/end, y por ahí va mucho más rápido (-ss/-t en vez de un filtro)
            if len(known["ranges"]) == 1 and known.get("start") is None and known.get("end") is None:
                known["start"], known["end"] = known["ranges"][0]
        if "subtitles" in known:
            known["subtitles"] = bool(known["subtitles"])
        spec = cls(**known)
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.preset not in PRESET_BY_ID:
            raise ConvertError(f"preset desconocido: {self.preset}")
        if self.height not in HEIGHTS:
            raise ConvertError(f"height must be one of {HEIGHTS}")
        if self.quality not in QUALITIES:
            raise ConvertError(f"quality must be one of {QUALITIES}")
        if self.hw not in HW_MODES:
            raise ConvertError(f"hw must be one of {HW_MODES}")
        if self.speed not in SPEEDS:
            raise ConvertError(f"speed must be one of {SPEEDS}")
        if self.container and (self.preset != "small" or self.container not in SMALL_CONTAINERS):
            raise ConvertError("container only applies to «small» (mp4 | mkv)")
        if self.audio_bitrate is not None and not (32 <= self.audio_bitrate <= 512):
            raise ConvertError("audio_bitrate out of range (32–512 kbps)")
        if self.audio_track is not None and self.audio_track < 0:
            raise ConvertError("audio_track must be >= 0")
        if self.gif_width not in GIF_WIDTHS:
            raise ConvertError(f"gif_width must be one of {GIF_WIDTHS}")
        if self.gif_fps not in GIF_FPS:
            raise ConvertError(f"gif_fps must be one of {GIF_FPS}")
        if self.start is not None and self.start < 0:
            raise ConvertError("start must be >= 0")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ConvertError("el final del tramo debe ser mayor que el inicio")
        if self.ranges:
            if self.preset == "gif" and len(self.ranges) > 1:
                raise ConvertError("un GIF se hace de un solo tramo")
            if self.preset == "copy" and len(self.ranges) > 1:
                raise ConvertError("«Sin recodificar» no puede unir tramos: para pegarlos hay que recodificar")
            if len(self.ranges) > MAX_RANGES:
                raise ConvertError(f"como mucho {MAX_RANGES} tramos de una vez")
            # H55 · NO se exige que vayan en orden: «juntar 2 o más en el orden deseado» es justo poder darle la
            # vuelta a un trozo o repetirlo. `trim`+`concat` los pega en el orden en que llegan.
            for a, b in self.ranges:
                if a < 0 or b <= a:
                    raise ConvertError("cada tramo tiene que acabar después de empezar")

    @property
    def meta(self) -> dict[str, Any]:
        return PRESET_BY_ID[self.preset]

    @property
    def kind(self) -> str:
        return str(self.meta["kind"])

    @property
    def vcodec(self) -> str | None:
        return self.meta.get("vcodec")

    @property
    def ext(self) -> str:
        if self.preset == "small" and self.container == "mkv":
            return ".mkv"
        return str(self.meta["ext"])

    def describe(self) -> str:
        """Short Spanish summary for lists: «MP4 compatible · 720p · calidad alta · 0:10–0:25»."""
        parts = [str(self.meta["label"])]
        if self.kind == "video":
            if self.height:
                parts.append(f"{self.height}p")
            if self.preset == "small" and self.container == "mkv":
                parts.append("MKV")
        if self.kind == "gif":
            parts.append(f"{self.gif_width} px · {self.gif_fps} fps")
        elif self.preset not in ("flac", "wav"):
            parts.append("calidad " + QUALITY_LABELS[self.quality])
        if self.audio_bitrate and self.preset in ("mp3", "m4a", "opus"):
            parts[-1] = f"{self.audio_bitrate} kbps"
        if self.start is not None or self.end is not None:
            parts.append(f"{clock(self.start or 0)}–{clock(self.end) if self.end is not None else 'final'}")
        return " · ".join(parts)


def clock(t: float | None) -> str:
    s = int(max(0.0, t or 0.0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def hms_name(t: float) -> str:
    s = int(max(0.0, t))
    return f"{s // 3600:02d}.{s % 3600 // 60:02d}.{s % 60:02d}"


# -- source ---------------------------------------------------------------------------------------


@dataclass
class SourceInfo:
    duration: float = 0.0
    video_index: int | None = None          # absolute stream index of the first real video (not cover art)
    width: int = 0
    height: int = 0
    audio: list[int] = field(default_factory=list)                 # absolute indexes of the audio streams
    subs: list[tuple[int, str]] = field(default_factory=list)      # (absolute index, codec_name)

    @classmethod
    def from_ffprobe(cls, data: dict[str, Any]) -> SourceInfo:
        info = cls()
        try:
            info.duration = float((data.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            info.duration = 0.0
        for s in data.get("streams") or []:
            idx = int(s.get("index", 0))
            kind = s.get("codec_type")
            if kind == "video" and info.video_index is None and not (s.get("disposition") or {}).get("attached_pic"):
                info.video_index = idx
                info.width, info.height = int(s.get("width") or 0), int(s.get("height") or 0)
            elif kind == "audio":
                info.audio.append(idx)
            elif kind == "subtitle":
                info.subs.append((idx, str(s.get("codec_name") or "")))
        return info


def probe(src: Path, timeout: float = 30.0) -> SourceInfo:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ConvertError("ffprobe no está instalado")
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                              "format=duration:stream=index,codec_type,codec_name,width,height:stream_disposition="
                              "attached_pic", "-of", "json", str(src)],
                             capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConvertError(f"ffprobe: {exc}") from exc
    if out.returncode != 0:
        raise ConvertError("no se puede leer el archivo: " + (out.stderr.strip().splitlines() or ["?"])[-1])
    try:
        return SourceInfo.from_ffprobe(json.loads(out.stdout or "{}"))
    except ValueError as exc:
        raise ConvertError(f"ffprobe: {exc}") from exc


# -- hardware plan --------------------------------------------------------------------------------


@dataclass
class HwPlan:
    """VA-API encode of ``codec`` on ``device`` (``low_power`` when the driver only offers VAEntrypointEncSliceLP)."""

    device: str
    codec: str          # h264 | hevc
    low_power: bool = False


# -- argv -----------------------------------------------------------------------------------------


@dataclass
class Plan:
    commands: list[list[str]]
    weights: list[float]            # share of the progress bar of each command
    duration: float                 # seconds of media each command processes (progress = out_time / duration)
    warnings: list[str] = field(default_factory=list)
    hw: str = "cpu"                 # cpu | vaapi


def effective_range(spec: ConvertSpec, duration: float) -> tuple[float, float | None]:
    """(start, length or None) clipped to the file; raises when the range is empty."""
    a = max(0.0, spec.start or 0.0)
    b = spec.end
    if duration > 0:
        if a >= duration:
            raise ConvertError("el tramo empieza después del final del archivo")
        if b is not None and b > duration:
            b = None
    if b is not None and b <= a:
        raise ConvertError("el final del tramo debe ser mayor que el inicio")
    return a, (b - a) if b is not None else None


def scale_filter(height: int) -> str | None:
    """Cap the short side at ``height`` without upscaling; even sizes (4:2:0) and the aspect ratio kept (-2)."""
    if not height:
        return None
    return (f"scale='if(gte(iw,ih),-2,trunc(min(iw,{height})/2)*2)':"
            f"'if(gte(iw,ih),trunc(min(ih,{height})/2)*2,-2)'")


def subtitle_args(ext: str, subs: list[tuple[int, str]]) -> tuple[list[str], list[str], list[str]]:
    """(maps, codec args, warnings) to keep the subtitle tracks in a ``ext`` file: text → mov_text (mp4) or WebVTT
    (webm); mkv copies everything (mov_text rewritten as SRT). Picture subtitles only fit in mkv."""
    maps: list[str] = []
    codecs: list[str] = []
    dropped = 0
    for idx, codec in subs:
        if ext == ".mkv":
            target = MKV_REWRITE.get(codec, "copy")
        elif codec in TEXT_SUBS:
            target = "mov_text" if ext in (".mp4", ".m4v", ".mov") else "webvtt"
        else:
            dropped += 1
            continue
        codecs += [f"-c:s:{len(maps) // 2}", target]
        maps += ["-map", f"0:{idx}"]
    warnings = []
    if dropped:
        warnings.append(f"{dropped} subtítulo(s) de imagen no caben en {ext.lstrip('.').upper()}: elige MKV para "
                        "conservarlos")
    return maps, codecs, warnings


def base_args(ffmpeg: str, overwrite: bool = False) -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
            "-y" if overwrite else "-n"]


def input_args(src: Path, start: float, length: float | None) -> list[str]:
    # -ss before -i: fast seek to the keyframe before A, then exact (decoded frames before A are dropped); text
    # subtitles are shifted with it (one already on screen at A is lost)
    args: list[str] = []
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    return [*args, "-i", str(src)]


def _audio_kbps(spec: ConvertSpec, key: str) -> int:
    if spec.audio_bitrate and spec.kind == "audio":
        return int(spec.audio_bitrate)
    return AUDIO_KBPS[key][spec.quality]


def _audio_track(spec: ConvertSpec, info: SourceInfo) -> int:
    track = spec.audio_track or 0
    return track if track < len(info.audio) else 0


def audio_only_args(spec: ConvertSpec) -> list[str]:
    """Encoder of an audio-only preset."""
    return {
        "mp3": ["-c:a", "libmp3lame", "-b:a", f"{_audio_kbps(spec, 'mp3')}k", "-id3v2_version", "3"],
        "m4a": ["-c:a", "aac", "-b:a", f"{_audio_kbps(spec, 'm4a')}k", "-movflags", "+faststart"],
        "opus": ["-c:a", "libopus", "-b:a", f"{_audio_kbps(spec, 'opus')}k"],
        "flac": ["-c:a", "flac"],
        "wav": ["-c:a", "pcm_s16le"],
    }[spec.preset]


def cpu_video_args(spec: ConvertSpec, vcodec: str) -> list[str]:
    """Video encoder on the CPU (no scaling: the caller puts the filter where it belongs)."""
    if vcodec == "h264":
        args = ["-c:v", "libx264", "-preset", X264_PRESET[spec.speed], "-crf", str(CRF["h264"][spec.quality])]
    elif vcodec == "hevc":
        args = ["-c:v", "libx265", "-preset", X265_PRESET[spec.speed], "-crf", str(CRF["hevc"][spec.quality]),
                "-x265-params", "log-level=error"]
    elif vcodec == "av1":
        args = ["-c:v", "libsvtav1", "-preset", SVTAV1_PRESET[spec.speed], "-crf", str(CRF["av1"][spec.quality])]
    else:  # vp9: constant quality (-b:v 0), row multithreading
        args = ["-c:v", "libvpx-vp9", "-crf", str(CRF["vp9"][spec.quality]), "-b:v", "0", "-row-mt", "1",
                *VP9_SPEED[spec.speed]]
    return [*args, "-pix_fmt", "yuv420p"]


def video_audio_args(spec: ConvertSpec, ext: str) -> list[str]:
    """Audio encoder of a video preset, by container."""
    if ext == ".mp4":
        return ["-c:a", "aac", "-b:a", f"{_audio_kbps(spec, 'aac')}k"]
    return ["-c:a", "libopus", "-b:a", f"{_audio_kbps(spec, 'libopus')}k"]


def copy_plan(spec: ConvertSpec, src: Path, out: Path, info: SourceInfo, ffmpeg: str = "ffmpeg") -> Plan:
    """H55 · «Sin recodificar»: se copian los flujos tal cual a un MKV. Instantáneo y sin perder nada.

    El precio, que se dice en la propia etiqueta: un corte empieza en el fotograma clave anterior, porque sin
    recodificar no se puede partir por en medio de un grupo de imágenes; pueden entrar unos segundos de más."""
    start, length = effective_range(spec, info.duration)
    span = length if length is not None else max(0.0, info.duration - start)
    cmd = [*base_args(ffmpeg), *input_args(src, start, length), "-map", "0", "-c", "copy",
           "-map_metadata", "0", str(out)]
    avisos = []
    if start > 0 or length is not None:
        avisos.append("sin recodificar el corte empieza en el fotograma clave anterior: pueden entrar segundos de más")
    return Plan([cmd], [1.0], span, avisos, "copy")


def join_plan(spec: ConvertSpec, src: Path, out: Path, info: SourceInfo, ffmpeg: str = "ffmpeg") -> Plan:
    """H52 · «juntar los que quieras»: varios tramos del mismo archivo en UNO, en una sola pasada de ffmpeg.

    `trim`/`atrim` cortan cada tramo y `concat` los pega. Se recodifica a la fuerza —un filtro no puede ir con
    `-c copy`— y por eso va siempre por CPU: montar VA-API dentro de un filter_complex por tramo es pedir
    problemas al controlador para ganar en un caso raro. Los subtítulos se quedan fuera: no hay forma de pegarlos
    con el filtro, y colarlos a medias sería peor que decirlo."""
    ranges = [(float(a), float(b)) for a, b in spec.ranges]
    if info.duration > 0:
        ranges = [(a, min(b, info.duration)) for a, b in ranges if a < info.duration]
    if not ranges:
        raise ConvertError("ningún tramo cae dentro del archivo")
    span = sum(b - a for a, b in ranges)
    warnings: list[str] = []
    kind = spec.kind
    if kind == "video" and info.video_index is None:
        raise ConvertError("el archivo no tiene vídeo")
    if kind == "audio" and not info.audio:
        raise ConvertError("el archivo no tiene audio")

    chains: list[str] = []
    labels: list[str] = []
    want_video = kind == "video"
    aidx = info.audio[_audio_track(spec, info)] if info.audio else None
    if want_video and aidx is None:
        warnings.append("el archivo no tiene audio: los tramos se unen sin sonido")
    for i, (a, b) in enumerate(ranges):
        if want_video:
            chains.append(f"[0:{info.video_index}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
            labels.append(f"[v{i}]")
        if aidx is not None:
            chains.append(f"[0:{aidx}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    n = len(ranges)
    has_audio = aidx is not None
    outs = ("[v]" if want_video else "") + ("[a]" if has_audio else "")
    chains.append(f"{''.join(labels)}concat=n={n}:v={1 if want_video else 0}:a={1 if has_audio else 0}{outs}")

    vlabel = "[v]"
    if want_video:
        scale = scale_filter(spec.height)
        if scale:
            chains.append(f"[v]{scale}[vout]")
            vlabel = "[vout]"
    maps: list[str] = []
    if want_video:
        maps += ["-map", vlabel]
    if has_audio:
        maps += ["-map", "[a]"]

    if want_video:
        vcodec = spec.vcodec or "h264"
        codec = cpu_video_args(spec, vcodec)
        if vcodec == "hevc" and spec.ext == ".mp4":
            codec += ["-tag:v", "hvc1"]
        codec += video_audio_args(spec, spec.ext) if has_audio else ["-an"]
        tail = ["-movflags", "+faststart"] if spec.ext == ".mp4" else []
        if spec.subtitles and info.subs:
            warnings.append("al unir tramos no se pueden llevar los subtítulos incrustados")
    else:
        codec = ["-vn", *audio_only_args(spec)]
        tail = []

    cmd = [*base_args(ffmpeg), "-i", str(src), "-filter_complex", ";".join(chains), *maps, *codec, *tail, str(out)]
    return Plan([cmd], [1.0], span, warnings, "cpu")


def build_plan(spec: ConvertSpec, src: Path, out: Path, info: SourceInfo, hw: HwPlan | None = None,
               ffmpeg: str = "ffmpeg", palette: Path | None = None) -> Plan:
    """Exact ffmpeg command(s) converting ``src`` into ``out`` (never overwrites: ``-n``)."""
    spec.validate()
    if spec.preset == "copy":
        return copy_plan(spec, src, out, info, ffmpeg)
    if len(spec.ranges) > 1:
        return join_plan(spec, src, out, info, ffmpeg)
    start, length = effective_range(spec, info.duration)
    span = length if length is not None else max(0.0, info.duration - start)
    kind = spec.kind
    if kind in ("video", "gif") and info.video_index is None:
        raise ConvertError("el archivo no tiene vídeo")
    if kind == "audio" and not info.audio:
        raise ConvertError("el archivo no tiene audio")
    inp = input_args(src, start, length)

    if kind == "gif":
        if span > GIF_MAX_SECONDS:
            raise ConvertError(f"un GIF puede durar como mucho {int(GIF_MAX_SECONDS)} s: elige un tramo")
        if palette is None:
            raise ConvertError("GIF needs a palette path")
        chain = f"fps={spec.gif_fps},scale='min({spec.gif_width},iw)':-1:flags=lanczos"
        pass1 = [*base_args(ffmpeg, overwrite=True), *inp, "-map", f"0:{info.video_index}",
                 "-vf", f"{chain},palettegen=stats_mode=diff", "-frames:v", "1", "-update", "1", str(palette)]
        pass2 = [*base_args(ffmpeg), *inp, "-i", str(palette), "-lavfi",
                 f"[0:{info.video_index}]{chain}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                 "-loop", "0", "-an", str(out)]
        return Plan([pass1, pass2], [0.4, 0.6], span)

    if kind == "audio":
        amap = ["-map", f"0:{info.audio[_audio_track(spec, info)]}"]
        return Plan([[*base_args(ffmpeg), *inp, *amap, "-vn", *audio_only_args(spec), str(out)]], [1.0], span)

    # video
    ext = spec.ext
    vcodec = spec.vcodec or "h264"
    use_hw = hw is not None and hw.codec == vcodec and vcodec in VAAPI_QP
    maps = ["-map", f"0:{info.video_index}", "-map", "0:a?"]
    warnings: list[str] = []
    sub_codecs: list[str] = []
    if spec.subtitles and info.subs:
        smaps, sub_codecs, warnings = subtitle_args(ext, info.subs)
        maps += smaps
    scale = scale_filter(spec.height)
    head = base_args(ffmpeg)
    if use_hw:
        assert hw is not None
        head += ["-vaapi_device", hw.device]
        vf = ",".join(f for f in (scale, "format=nv12", "hwupload") if f)
        video = ["-vf", vf, "-c:v", f"{vcodec}_vaapi"]
        if hw.low_power:
            video += ["-low_power", "1"]
        video += ["-rc_mode", "CQP", "-qp", str(VAAPI_QP[vcodec][spec.quality])]
    else:
        video = (["-vf", scale] if scale else []) + cpu_video_args(spec, vcodec)
    if vcodec == "hevc" and ext == ".mp4":
        video += ["-tag:v", "hvc1"]    # QuickTime/iOS only play HEVC in mp4 tagged hvc1 (ffmpeg's default is hev1)
    audio = video_audio_args(spec, ext)
    tail = ["-movflags", "+faststart"] if ext == ".mp4" else []
    if length is not None and sub_codecs:
        # the input -t does not stop subtitle packets (a cue after the range reached the mp4/webm, verified with
        # FFmpeg 8): the same limit on the output drops them
        tail = ["-t", f"{length:.3f}", *tail]
    cmd = [*head, *inp, *maps, *video, *audio, *sub_codecs, *tail, str(out)]
    return Plan([cmd], [1.0], span, warnings, "vaapi" if use_hw else "cpu")


# -- files ----------------------------------------------------------------------------------------


def output_path(src: Path, folder: Path, spec: ConvertSpec, reserved: set[str] | None = None) -> Path:
    """``<folder>/<stem>[ [A-B]].<ext>``; `` (2)``, `` (3)``… when the name (or its .part) is taken or reserved."""
    stem = src.stem
    if len(spec.ranges) > 1:
        stem += f" [{len(spec.ranges)} tramos]"
    elif spec.start is not None or spec.end is not None:
        a = spec.start or 0.0
        stem += f" [{hms_name(a)}-{hms_name(spec.end) if spec.end is not None else 'fin'}]"
    stem = _RANGE_NAME.sub(" ", stem).strip() or "convertido"
    ext = spec.ext
    reserved = reserved or set()
    n = 1
    while True:
        name = f"{stem}{ext}" if n == 1 else f"{stem} ({n}){ext}"
        out = folder / name
        if not out.exists() and not partial_path(out).exists() and str(out) not in reserved \
                and out.resolve() != src.resolve():
            return out
        n += 1


def partial_path(out: Path) -> Path:
    """Where ffmpeg writes while converting (renamed to ``out`` at the end): ``name.part.ext`` keeps the extension
    ffmpeg needs to pick the format."""
    return out.with_name(out.stem + ".part" + out.suffix)


def media_files(folder: Path, kind: str, recursive: bool = False, skip: Path | None = None) -> list[Path]:
    """Files a preset of ``kind`` can convert in ``folder`` (video presets: videos; audio: videos and audio), in
    natural order."""
    exts = set(VIDEO_EXTS) if kind in ("video", "gif") else VIDEO_EXTS | AUDIO_EXTS
    it = folder.rglob("*") if recursive else folder.iterdir()
    skip_r = skip.resolve() if skip is not None else None
    found = []
    for p in it:
        if not p.is_file() or p.suffix.lower() not in exts or p.name.startswith("."):
            continue
        if ".part." in p.name:
            continue
        if skip_r is not None and (skip_r == p.parent.resolve() or skip_r in p.resolve().parents):
            continue
        found.append(p)
    return sorted(found, key=natural_key)


def natural_key(p: Path) -> list[Any]:
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", str(p))]


__all__ = ["AUDIO_BITRATE_CHOICES", "ConvertError", "ConvertSpec", "GIF_FPS", "GIF_WIDTHS", "HEIGHTS", "HwPlan",
           "PRESETS", "Plan", "QUALITIES", "SourceInfo", "build_plan", "media_files", "output_path", "partial_path",
           "probe"]
