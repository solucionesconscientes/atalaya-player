"""Convertir lo descargado: **una pasada de ffmpeg** por trabajo y comprobación con ffprobe.

Por qué ffmpeg y no ``--audio-format``/``--recode-video`` de yt-dlp:

* ``--recode-video`` usa el postprocesador ``FFmpegVideoConvertor``, que empieza con ``-c copy`` y **se salta el
  trabajo cuando la extensión ya es la de destino** (``resolve_mapping`` devuelve «already is target format»): pedir
  H.265 sobre un MP4 no haría nada.
* ``--audio-format alac`` pierde su ``-acodec alac`` por dentro (``FFmpegExtractAudioPP.run`` sustituye los
  ``more_opts`` del códec por los de calidad), así que el fichero saldría AAC con nombre ``.m4a``.
* Ni canales, ni loudness, ni CRF, ni fps, ni dos pasadas, ni hardware se pueden expresar con esas opciones.

Así que el camino es uno solo y explícito: se descarga la pista del origen **sin tocar** y después se convierte.
Los codificadores y las tablas de CRF son los mismos que usa «Convertir» (``mpvd/convert/presets.py``), ya
comprobados contra este ffmpeg; aquí se añaden los dos ajustes que allí no existen (fps y canales/loudness).

Reglas que no se saltan:

* **La salida se comprueba con ffprobe** (códec de vídeo y de audio, duración ±1 s) antes de darla por buena: si no
  cuadra, no se borra el original y el trabajo se cuenta como fallido con el motivo.
* **El original no se borra** hasta que la conversión está verificada; entonces sí, para no dejar dos ficheros.
* **Nada de esto es el camino por defecto**: solo corre cuando el spec lo pide (``DownloadSpec.convertir``).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mpvd.convert.presets import CRF, SVTAV1_PRESET, VAAPI_QP, VP9_SPEED, X264_PRESET, X265_PRESET
from mpvd.i18n import t

# Codificador de cada familia destino. Las claves son las mismas que usa la política (policy._familia_*).
ENC_VIDEO = {"h264": "libx264", "hevc": "libx265", "vp9": "libvpx-vp9", "av1": "libsvtav1"}
ENC_AUDIO = {"opus": "libopus", "aac": "aac", "alac": "alac", "mp3": "libmp3lame", "flac": "flac", "wav": "pcm_s16le"}
VAAPI = {"h264": "h264_vaapi", "hevc": "hevc_vaapi"}          # lo único que codifica VA-API en la práctica

EXT_AUDIO = {"opus": "opus", "aac": "m4a", "alac": "m4a", "mp3": "mp3", "flac": "flac", "wav": "wav"}
# Contenedor de un vídeo recodificado: el que admite el códec con su audio, sin disfrazar nada.
EXT_VIDEO = {("h264", "aac"): "mp4", ("h264", "mp3"): "mp4", ("hevc", "aac"): "mp4", ("hevc", "mp3"): "mp4",
             ("vp9", "opus"): "webm", ("av1", "opus"): "webm"}
SIN_PERDIDA = ("flac", "wav", "alac")
RESOLUCIONES = (2160, 1440, 1080, 720, 480, 360)
CANALES = {"conservar": None, "estereo": 2, "mono": 1}
LOUDNESS = {"ninguna": None, "-18": "-18", "-14": "-14"}

TOLERANCIA = 1.0                      # s de diferencia aceptada entre la duración del origen y la del resultado
FFPROBE_ENTRIES = "format=duration:stream=codec_type,codec_name,width,height,channels,sample_rate,bit_rate"
FFMPEG_TIMEOUT = 6 * 3600.0           # un vídeo largo en un equipo sin GPU: horas, no minutos


@dataclass(frozen=True)
class Conversion:
    """Cómo acabó la conversión. ``avisos`` es lo que hay que contarle al usuario además del resultado."""

    ok: bool
    salida: str
    error: str = ""
    vcodec: str = ""
    acodec: str = ""
    contenedor: str = ""
    bytes: int = 0
    duracion: float = 0.0
    avisos: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ejecutable(nombre: str) -> str | None:
    return shutil.which(nombre)


def sonda(ruta: str | Path) -> dict[str, Any]:
    """Lo que ffprobe dice del fichero: duración, pista de vídeo y pista de audio (o vacíos)."""
    ffprobe = _ejecutable("ffprobe")
    if ffprobe is None:
        raise ValueError(t("hace falta ffprobe para comprobar lo convertido y no está instalado"))
    proc = subprocess.run([ffprobe, "-hide_banner", "-loglevel", "error", "-of", "json",
                           "-show_entries", FFPROBE_ENTRIES, str(ruta)],
                          capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise ValueError(t("ffprobe no pudo leer %s: %s") % (Path(ruta).name, (proc.stderr or "").strip()[-200:]))
    try:
        datos = json.loads(proc.stdout or "{}")
    except ValueError:
        datos = {}
    v = next((s for s in datos.get("streams") or [] if s.get("codec_type") == "video"), {})
    a = next((s for s in datos.get("streams") or [] if s.get("codec_type") == "audio"), {})
    duracion = 0.0
    try:
        duracion = float((datos.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duracion = 0.0
    return {"duracion": duracion, "v": v, "a": a}


def _dimensiones(info_video: dict[str, Any], altura: int | None, ancho: int | None) -> tuple[int, int]:
    """(ancho, alto) del resultado: la altura pedida con la proporción del origen, o la del origen."""
    w = int(info_video.get("width") or 0)
    h = int(info_video.get("height") or 0)
    if not w or not h:
        w, h = ancho or 1920, altura or 1080
    if not altura or altura >= h:                    # no se agranda: recodificar hacia arriba solo gasta
        return w, h
    return max(2, int(round(w * altura / h / 2.0)) * 2), int(altura)


def _filtros(fps: int | None, tam: tuple[int, int], origen: tuple[int, int]) -> str:
    partes: list[str] = []
    if tam != origen:
        partes.append(f"scale={tam[0]}:{tam[1]}")
    if fps:
        partes.append(f"fps={int(fps)}")
    return ",".join(partes)


def args_conversion(origen: str | Path, destino: str | Path, *, vcodec: str | None = None,
                    acodec: str | None = None, crf: int | None = None, video_bitrate: int | None = None,
                    audio_kbps: int | None = None, altura: int | None = None, fps: int | None = None,
                    two_pass: bool = False, hardware: str | None = None, canales: str = "conservar",
                    loudness: str = "ninguna", velocidad: str = "normal") -> tuple[list[list[str]], str]:
    """Las órdenes de ffmpeg (una, o dos si hay dos pasadas) y la extensión del resultado.

    ``hardware`` es el nodo de render que ha dado ``mpvd.convert.hw`` (p. ej. ``/dev/dri/renderD128``): con él la
    codificación va a la tarjeta gráfica. Sin él, al procesador.
    """
    ffmpeg = _ejecutable("ffmpeg")
    if ffmpeg is None:
        raise ValueError(t("hace falta ffmpeg para convertir y no está instalado"))
    origen, destino = Path(origen), Path(destino)
    destino = destino.with_suffix("")            # la extensión la pone el códec destino, no el nombre que llega
    info = sonda(origen)
    if info["duracion"] <= 0:
        raise ValueError(t("no se sabe cuánto dura lo descargado: no se puede convertir a ciegas"))
    if not vcodec and not acodec:
        raise ValueError(t("no se ha dicho a qué convertir"))

    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if hardware and vcodec and str(vcodec) in VAAPI:
        base += ["-vaapi_device", str(hardware)]
    base += ["-i", str(origen)]
    canales_n = CANALES.get(canales, None)
    af = f"loudnorm=I={LOUDNESS[loudness]}:TP=-1.5:LRA=11" if LOUDNESS.get(loudness) else ""
    af_parts = [af] if af else []

    # --- solo audio: se quita la imagen y se codifica el audio
    if not vcodec:
        ext = EXT_AUDIO.get(str(acodec), "mkv")
        cola = ["-vn", "-map", "0:a:0?", "-c:a", ENC_AUDIO.get(str(acodec), str(acodec))]
        if str(acodec) not in SIN_PERDIDA:
            kbps = int(audio_kbps or 128)
            cola += ["-b:a", f"{kbps}k"]
        if canales_n:
            cola += ["-ac", str(canales_n)]
        if af_parts:
            cola += ["-af", ",".join(af_parts)]
        if str(acodec) == "mp3":
            cola += ["-id3v2_version", "3"]
        if ext == "m4a":
            cola += ["-movflags", "+faststart"]
        return [base + cola + [str(destino.with_suffix("." + ext))]], ext

    # --- vídeo: se recodifica la imagen y el audio va con el contenedor
    ext = EXT_VIDEO.get((str(vcodec), str(acodec or "aac")), "mkv")
    origen_tam = (int(info["v"].get("width") or 0), int(info["v"].get("height") or 0))
    tam = _dimensiones(info["v"], altura, None)
    filtro = _filtros(fps, tam, origen_tam)
    usa_hw = bool(hardware) and str(vcodec) in VAAPI
    vf_parts = []
    if usa_hw:
        vf_parts.append("format=nv12")
        if tam != origen_tam:
            vf_parts.append(f"scale_vaapi=w={tam[0]}:h={tam[1]}")
        if fps:
            vf_parts.append(f"fps={int(fps)}")
        vf_parts.append("hwupload")
        video = ["-vf", ",".join(vf_parts), "-c:v", VAAPI[str(vcodec)]]
        video += ["-rc_mode", "CQP", "-qp", str(VAAPI_QP.get(str(vcodec), {}).get("normal", 24))]
    else:
        if str(vcodec) == "h264":
            video = ["-c:v", "libx264", "-preset", X264_PRESET.get(velocidad, "fast")]
        elif str(vcodec) == "hevc":
            video = ["-c:v", "libx265", "-preset", X265_PRESET.get(velocidad, "fast"),
                     "-x265-params", "log-level=error"]
        elif str(vcodec) == "av1":
            video = ["-c:v", "libsvtav1", "-preset", SVTAV1_PRESET.get(velocidad, "8")]
        else:
            video = ["-c:v", "libvpx-vp9", "-row-mt", "1", *VP9_SPEED.get(velocidad, VP9_SPEED["normal"])]
        if video_bitrate:
            video += ["-b:v", f"{int(video_bitrate)}k"]
            if str(vcodec) == "vp9":
                video += ["-minrate", f"{int(video_bitrate)}k", "-maxrate", f"{int(video_bitrate) * 2}k"]
        else:
            valor = int(crf if crf is not None else CRF.get(str(vcodec), {}).get("normal", 23))
            video += ["-crf", str(valor)]
            if str(vcodec) == "vp9":
                video += ["-b:v", "0"]
        if filtro:
            video = ["-vf", filtro, *video]
        video += ["-pix_fmt", "yuv420p"]
        if str(vcodec) == "hevc" and ext == "mp4":
            video += ["-tag:v", "hvc1"]
    audio = ["-c:a", "aac"] if ext == "mp4" else ["-c:a", "libopus"]
    kbps = int(audio_kbps or (128 if ext == "mp4" else 96))
    audio += ["-b:a", f"{kbps}k"]
    if canales_n:
        audio += ["-ac", str(canales_n)]
    if af_parts:
        audio += ["-af", ",".join(af_parts)]
    mapa = ["-map", "0:v:0", "-map", "0:a?"]
    cola = [*mapa, *video, *audio]
    if ext == "mp4":
        cola += ["-movflags", "+faststart"]

    if not two_pass or usa_hw or not video_bitrate:
        return [base + cola + [str(destino.with_suffix("." + ext))]], ext
    # dos pasadas: la primera escribe el log y no fichero (solo tiene sentido con bitrate fijo)
    log = str(destino.with_suffix(".pass"))
    primera = [*base, *mapa, *video, "-pass", "1", "-passlogfile", log, "-an", "-f", "null", "/dev/null"]
    segunda = [*base, *cola, "-pass", "2", "-passlogfile", log, str(destino.with_suffix("." + ext))]
    return [primera, segunda], ext


def convertir(origen: str | Path, destino: str | Path, **ajustes: Any) -> Conversion:
    """Convierte ``origen`` en ``destino`` y **comprueba el resultado**; si no cuadra, no toca el original.

    ``ajustes``: los mismos nombres que ``args_conversion``. El fichero convertido se escribe al lado del original,
    con la extensión del códec destino; si ya existía, se reemplaza (``-y``).
    """
    ordenes, ext = args_conversion(origen, destino, **ajustes)
    destino = Path(destino).with_suffix("")
    salida = destino.with_suffix("." + ext)
    temporal: Path | None = None
    if salida.resolve() == Path(origen).resolve():
        # convertir a la misma extensión del original: nunca se lee y se escribe el mismo fichero (-y lo truncaría)
        temporal = destino.with_name(destino.name + ".convirtiendo").with_suffix("." + ext)
        salida, escrito = temporal, temporal
    else:
        escrito = salida
    for i, cmd in enumerate(ordenes, 1):
        cmd = [str(escrito) if j == len(cmd) - 1 else a for j, a in enumerate(cmd)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT, check=False)
        if proc.returncode != 0:
            ultima = (proc.stderr or "").strip().splitlines()[-1:] or [""]
            for sufijo in ("-0.log", "-0.log.mbtree"):
                Path(f"{destino}.pass{sufijo}").unlink(missing_ok=True)
            escrito.unlink(missing_ok=True)
            paso = t(" (pasada %d de 2)") % i if len(ordenes) > 1 else ""
            return Conversion(False, str(salida), error=t("ffmpeg no pudo convertir%s: %s") % (paso, ultima[0]))
    for sufijo in ("-0.log", "-0.log.mbtree"):
        Path(f"{destino}.pass{sufijo}").unlink(missing_ok=True)
    if not salida.is_file() or salida.stat().st_size == 0:
        return Conversion(False, str(salida), error=t("la conversión no dejó ningún fichero"))
    avisos: list[str] = []
    try:
        antes, despues = sonda(origen), sonda(salida)
    except ValueError as exc:
        return Conversion(False, str(salida), error=str(exc))
    vcodec_ok = str(despues["v"].get("codec_name") or "")
    acodec_ok = str(despues["a"].get("codec_name") or "")
    esperado_v = {"h264": "h264", "hevc": "hevc", "vp9": "vp9", "av1": "av1"}.get(str(ajustes.get("vcodec") or ""), "")
    esperado_a = {"opus": "opus", "aac": "aac", "alac": "alac", "mp3": "mp3", "flac": "flac", "wav": "pcm_s16le"}
    if esperado_v and vcodec_ok != esperado_v:
        return Conversion(False, str(salida), error=t("el resultado no es %s sino %s") % (esperado_v, vcodec_ok or "?"))
    if despues["duracion"] and antes["duracion"] and abs(despues["duracion"] - antes["duracion"]) > TOLERANCIA:
        avisos.append(t("dura %s en vez de %s") % (f"{despues['duracion']:.1f}s", f"{antes['duracion']:.1f}s"))
    if acodec_ok and str(ajustes.get("acodec") or "") and not str(ajustes.get("vcodec") or ""):
        quiero = esperado_a.get(str(ajustes["acodec"]), "")
        if quiero and acodec_ok != quiero:
            escrito.unlink(missing_ok=True)
            return Conversion(False, str(salida), error=t("el audio del resultado no es %s sino %s") % (quiero, acodec_ok))
    if temporal is not None:                     # se convirtió sobre el propio original: se sustituye al final
        origen_p = Path(origen)
        try:
            origen_p.unlink(missing_ok=True)
            temporal.rename(salida)
        except OSError as exc:
            return Conversion(False, str(temporal), error=t("no se pudo sustituir el original: %s") % exc)
    return Conversion(True, str(salida), vcodec=vcodec_ok, acodec=acodec_ok,
                      contenedor=salida.suffix.lstrip("."), bytes=salida.stat().st_size,
                      duracion=float(despues["duracion"] or 0.0), avisos=tuple(avisos))


# ------------------------------------------------------------------ estimación (ficha antes de bajar)

# bits por píxel y fotograma a un CRF de referencia; cada 6 puntos de CRF es más o menos el doble/mitad de bitrate
BPP = {"h264": 0.070, "hevc": 0.048, "vp9": 0.055, "av1": 0.040}
CRF_REF = {"h264": 23, "hevc": 28, "vp9": 35, "av1": 34}
# veces la duración que tarda en codificar (medido a ojo en este equipo de dos núcleos; es una estimación)
FACTOR_CODIFICA = {"h264": 0.7, "hevc": 1.8, "vp9": 1.4, "av1": 2.6}
FACTOR_AUDIO = 0.05
TASA_DESCARGA = 2_500_000.0       # bytes/s (≈20 Mbit/s): una fibra normal en España


def kbps_video(vcodec: str, ancho: int, alto: int, fps: float, crf: int | None = None,
               video_bitrate: int | None = None) -> int:
    """Bitrate que se puede esperar de un vídeo recodificado: el pedido, o el que sale del CRF y el tamaño."""
    if video_bitrate:
        return int(video_bitrate)
    bpp = BPP.get(vcodec, BPP["h264"]) * 2 ** ((CRF_REF.get(vcodec, 23) - int(crf or CRF_REF.get(vcodec, 23))) / 6.0)
    return max(32, int(max(1, ancho * alto) * max(1.0, fps or 25.0) * bpp / 1000.0))


def segundos_codifica(vcodec: str | None, ancho: int, alto: int, duracion: float, two_pass: bool = False) -> float:
    """Tiempo de codificación estimado: proporcional a la duración y a los píxeles, según el códec."""
    if not vcodec:
        return duracion * FACTOR_AUDIO
    escala = max(0.25, (max(1, ancho * alto)) / (1280.0 * 720.0))
    return duracion * FACTOR_CODIFICA.get(vcodec, 1.0) * escala * (2.0 if two_pass else 1.0)


def segundos_descarga(bytes_: int) -> float:
    return bytes_ / TASA_DESCARGA if bytes_ else 0.0


def formato_bytes(n: float | int | None) -> str:
    """«4,2 GB», «117 MB», «480 KB»: lo que se enseña en la ficha."""
    v = float(n or 0)
    if v >= 1e9:
        return f"{v / 1e9:.1f} GB"
    if v >= 1e6:
        return f"{v / 1e6:.0f} MB" if v >= 1e7 else f"{v / 1e6:.1f} MB"
    if v >= 1e3:
        return f"{v / 1e3:.0f} KB"
    return f"{max(0, int(v))} B"


def redondear_segundos(seg: float) -> int:
    """Para la ficha: segundos a la unidad que se entiende («~3 min»), no décimas."""
    return max(1, int(math.ceil(seg)))
