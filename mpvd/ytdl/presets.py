"""Download specifications → exact yt-dlp argument lists.

Every option below was verified against ``yt-dlp --help`` of the vendored release (docs/YTDLP.md §5/§9).
"""

from __future__ import annotations

import re

from dataclasses import asdict, dataclass, field
from typing import Any

from mpvd.convert.presets import CRF as CRF_VIDEO, available_encoders
from mpvd.i18n import t
from mpvd.ytdl import chapters, policy, sections, transcode

CONTAINERS = ("mp4", "mkv", "webm")
AUDIO_FORMATS = ("mp3", "opus", "m4a", "flac", "wav")   # los de yt-dlp (--audio-format); ALAC va por transcode
AUDIO_BITRATES = (96, 128, 160, 192, 256, 320)
SPONSORBLOCK_MODES = ("none", "mark", "remove")
KINDS = ("video", "exact", "audio_original", "audio_convert")
# Vocabulario de la conversión explícita (F3). Los nombres son los de `transcode`, que es quien los ejecuta.
CANALES = ("conservar", "estereo", "mono")
LOUDNESS = ("ninguna", "-18", "-14")
FPS = (None, 24, 25, 30, 50, 60)
KBPS_POR_DEFECTO = {"opus": 96, "aac": 128, "mp3": 192}          # lo que se propone al elegir el códec

# Perfiles de la política única (mpvd/ytdl/policy.py). La expresión de formato sólo tiene que cubrir el caso sin
# ``-J``: cuando la app tiene el análisis del origen, lo que viaja son los dos ``format_id`` que elige ``policy.plan``
# y estos textos no se usan. Por eso el Opus de aquí es «el del origen» y no distingue voz de música (el perfil sí).
POLICY_FORMAT = {
    "opus_origen": "ba[acodec^=opus]/ba/b",
    "video_720": ("bv*[vcodec^=vp9][height<=?720]+ba[acodec^=opus]/bv*[vcodec^=vp9][height<=?720]+ba/"
                  "b[height<=?720]/bv*+ba/b"),
    "compatible": ("bv*[vcodec^=avc1][height<=?1080]+ba[acodec^=mp4a]/b[ext=mp4][height<=?1080]/"
                   "bv*[height<=?1080]+ba/b"),
    "pistas": "bv*+ba/b",
    "convertir": "bv*+ba/b",
}
# Orden de elección (-S) de cada perfil. A igual altura manda el bitrate MÁS BAJO («+tbr» ascendente): para guardar
# interesa el fichero pequeño, no el que decodifica mejor este equipo. En «opus_origen» no hay -S: el Opus va en la
# expresión y quién decide el bitrate es policy.plan (voz → el más bajo, música → el más alto).
POLICY_SORT = {
    "opus_origen": "",
    "video_720": "vcodec:vp9,res,+tbr,acodec:opus",
    "compatible": "vcodec:h264,res,acodec:aac",
    "pistas": "res,+tbr,acodec:opus",
    "convertir": "",
}

# Container policy. Merging (video+audio) takes a preference list: yt-dlp picks the first container compatible with the
# codecs (get_compatible_ext) and falls back to mkv. Remuxing a single-file format fails when the codecs do not fit the
# target ("If the target container does not support the video/audio codec, remuxing will fail"), so only safe pairs
# are remuxed; mkv accepts anything.
# H31 (ADR-053): mp4 is forced (not "mp4/mkv"): yt-dlp's compatibility table leaves Opus out of mp4 and would fall back
# to .mkv, while ffmpeg muxes AV1/VP9/HEVC/H.264 + Opus/AAC into mp4 fine (verified 2026-09-30: 299+251 → .mp4). A merge
# that still fails (exotic codecs) is retried once as .mkv by the download manager.
MERGE_PREFERENCES = {"mp4": "mp4", "mkv": "mkv", "webm": "webm/mkv"}
REMUX_RULES = {"mp4": "mov>mp4/m4v>mp4/flv>mp4/3gp>mp4", "mkv": "mkv", "webm": ""}
# Format sort (-S) for the resolution presets. yt-dlp's default order ranks AV1/VP9 + Opus first; that pair does not
# fit mp4, so the merge silently fell back to .mkv. "vcodec:h264" = best codec no better than H.264 (H.264 wins over
# AV1/VP9; older codecs such as Theora still qualify when nothing else exists); "res" right after it keeps the highest
# resolution among those (otherwise a combined 360p H.264+AAC format beats a 1080p video-only one on "acodec").
# mkv takes any codec: no -S. Checked with the vendored yt-dlp on the recorded YouTube -J: 360p mp4 → 18 (.mp4),
# best mp4 → 299+140 (1080p .mp4), 360p webm → 243+251 (.webm); archive.org keeps its best ≤360p file.
# H31: mp4 and mkv prefer the best codec this machine decodes in hardware ("vcodec:X", X from mpvd.hwdecode:
# h265 on an Intel iHD with HEVC but no VP9/AV1, h264 when unknown) and the original Opus audio; {vcodec} is filled in.
FORMAT_SORT = {"mp4": "vcodec:{vcodec},res,acodec:opus", "mkv": "vcodec:{vcodec},res,acodec:opus",
               "webm": "vcodec:vp9,res,acodec:opus"}
VCODEC_SORTS = ("av01", "vp9", "h265", "h264")

PROGRESS_PREFIX = "MU_PROGRESS"
POSTPROCESS_PREFIX = "MU_PP"
DONE_PREFIX = "MU_DONE"
SUBS_PREFIX = "MU_SUBS"   # after_video: paths of the subtitle files written (also with --skip-download)
DEFAULT_TEMPLATE = "%(title).120B [%(id)s].%(ext)s"
DEFAULT_SUB_LANGS = "orig,es.*,en.*"   # «orig» = the video's own language (resolved by mpvd, see resolve_sub_langs)
SUBS_MODES = ("embed", "file", "only")  # inside the video · .srt next to it · only the .srt (no video)
SUB_LANG_CHOICES = [  # menu order (H19)
    {"value": "orig,es.*,en.*", "label": "originales + es + en"},
    {"value": "orig", "label": "solo el original"},
    {"value": "es.*", "label": "español"},
    {"value": "en.*", "label": "inglés"},
    {"value": "all,-live_chat", "label": "todos"},
]


@dataclass
class DownloadSpec:
    url: str
    kind: str = "video"                 # video | exact | audio_original | audio_convert
    perfil: str | None = None           # política: opus_origen | video_720 | compatible | pistas | convertir
    video_id: str | None = None         # perfil «pistas»: format_id exacto del vídeo (p. ej. "303")
    audio_id: str | None = None         # perfil «pistas»/«opus_origen»: format_id exacto del audio (p. ej. "251")
    video_codec: str | None = None      # códec de esa pista, para validar el contenedor antes de lanzar
    audio_codec: str | None = None
    height: int | None = None           # video: cap (None = best)
    format: str | None = None           # exact: yt-dlp format expression ("137+140", "251", "bv*[height<=720]+ba")
    container: str = "mp4"              # video/exact: mp4 | mkv | webm (merge + remux target)
    audio_format: str = "mp3"           # audio_convert: mp3 | opus | m4a | flac | wav
    audio_bitrate: int | None = 192     # audio_convert (lossy): constant bitrate in kbps; None = VBR
    audio_vbr: int = 0                  # audio_convert with audio_bitrate=None: 0 (best) … 10
    # --- conversión explícita (F3). Con cualquiera de estos puestos la descarga NO recodifica: baja la pista del
    # origen tal cual y `mpvd.ytdl.transcode.convertir()` hace el trabajo y lo comprueba con ffprobe. No es el
    # camino por defecto: solo existe cuando el usuario lo elige en «Convertir al bajar…».
    vcod_destino: str | None = None     # h264 | hevc | vp9 | av1
    acod_destino: str | None = None     # opus | aac | alac | mp3 | flac | wav
    crf: int | None = None              # calidad constante del vídeo (None = la de la familia destino)
    video_bitrate: int | None = None    # kbps; si está, manda sobre el CRF y permite dos pasadas
    fps: int | None = None              # None = conservar; 24 | 25 | 30 | 50 | 60
    two_pass: bool = False              # dos pasadas (solo con video_bitrate)
    hardware: bool = False              # codificar en la tarjeta gráfica, si esta máquina puede
    audio_canales: str = "conservar"    # conservar | estereo | mono
    loudness: str = "ninguna"           # ninguna | -18 (voz) | -14 (música)
    subtitles: bool = False
    sub_langs: str = DEFAULT_SUB_LANGS
    subs_mode: str = "embed"            # embed | file (.srt next to the video) | only (just the .srt)
    thumbnail: bool = False
    metadata: bool = False
    sponsorblock: str = "none"          # none | mark | remove
    playlist: bool = False              # whole playlist vs only the referenced item
    playlist_items: str | None = None   # e.g. "1:5"
    # Un trozo, o varios: "00:10-02:30", "*12.5-20", "3:00-4:00,10:00-11:00" (o la lista ya partida). El `*` de la
    # forma de yt-dlp sobra; se acepta igual. Ver mpvd/ytdl/sections.py.
    sections: str | list[str] | None = None
    sections_margin: float = 0.0        # s de cola que se suman al final de cada rango (Fragmentos: 0,3)
    sections_join: bool = False         # unir los trozos al terminar, sin recodificar (mpvd.ytdl.chapters.unir)
    # Capítulos: True = marcas de capítulo dentro del fichero (lo de siempre); una lista de números, una expresión
    # ("3-5", "/entrevista/") o la lista de capítulos ya resuelta = entregarlos (ver mpvd/ytdl/chapters.py).
    chapters: bool | str | list[Any] = False
    chapters_mode: str = "separado"     # separado (un fichero por capítulo) | unido (un solo fichero)
    archive: bool = False               # skip what the download archive already has (lists, channels, batches)
    list_folder: bool = False           # a playlist goes to its own folder with numbered files
    title: str | None = None            # display only
    extra: dict[str, Any] = field(default_factory=dict)  # display-only info (preset id...)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DownloadSpec:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        spec = cls(**known)
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not self.url or not isinstance(self.url, str):
            raise ValueError("url required")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.perfil is not None and self.perfil not in policy.PERFILES:
            raise ValueError(f"perfil must be one of {policy.PERFILES}")
        if self.kind == "exact" and not self.format:
            raise ValueError("exact download needs a format")
        if self.container not in CONTAINERS:
            raise ValueError(f"container must be one of {CONTAINERS}")
        if self.audio_format not in AUDIO_FORMATS:
            raise ValueError(f"audio_format must be one of {AUDIO_FORMATS}")
        if self.audio_bitrate is not None and not (8 <= int(self.audio_bitrate) <= 512):
            raise ValueError("audio_bitrate out of range")
        if not (0 <= int(self.audio_vbr) <= 10):
            raise ValueError("audio_vbr must be 0..10")
        if self.sponsorblock not in SPONSORBLOCK_MODES:
            raise ValueError(f"sponsorblock must be one of {SPONSORBLOCK_MODES}")
        if self.height is not None and int(self.height) <= 0:
            raise ValueError("height must be positive")
        if self.subs_mode not in SUBS_MODES:
            raise ValueError(f"subs_mode must be one of {SUBS_MODES}")
        if self.sections is not None:
            # un rango imposible se rechaza al elegir, con el motivo, no con un trozo de cero segundos
            sections.parsear(self.sections, float(self.sections_margin))
        if self.chapters_mode not in chapters.MODOS:
            raise ValueError(f"chapters_mode must be one of {chapters.MODOS}")
        if self.vcod_destino is not None and self.vcod_destino not in policy.VIDEO_DESTINO:
            raise ValueError(f"vcod_destino must be one of {policy.VIDEO_DESTINO}")
        if self.acod_destino is not None and self.acod_destino not in policy.AUDIO_DESTINO:
            raise ValueError(f"acod_destino must be one of {policy.AUDIO_DESTINO}")
        if self.crf is not None and not (0 <= int(self.crf) <= 63):
            raise ValueError("crf must be 0..63")
        if self.video_bitrate is not None and not (50 <= int(self.video_bitrate) <= 50000):
            raise ValueError("video_bitrate out of range (50–50000 kbps)")
        if self.fps is not None and int(self.fps) not in [f for f in FPS if f]:
            raise ValueError(f"fps must be one of {[f for f in FPS if f]}")
        if self.two_pass and not self.video_bitrate:
            raise ValueError(t("las dos pasadas necesitan un bitrate fijo: con CRF no hay nada que repartir"))
        if self.hardware and (self.vcod_destino or "") not in ("h264", "hevc"):
            raise ValueError(t("solo H.264 y H.265 se codifican en la tarjeta gráfica"))
        if self.audio_canales not in CANALES:
            raise ValueError(f"audio_canales must be one of {CANALES}")
        if self.loudness not in LOUDNESS:
            raise ValueError(f"loudness must be one of {LOUDNESS}")
        if isinstance(self.chapters, str) and not self.chapters.strip():
            raise ValueError(t("no has elegido ningún capítulo"))
        if isinstance(self.chapters, (list, tuple)) and not self.chapters:
            raise ValueError(t("no has elegido ningún capítulo"))

    @property
    def is_audio(self) -> bool:
        return self.kind in ("audio_original", "audio_convert")

    @property
    def convertir(self) -> bool:
        """¿Este trabajo convierte lo que baja? Solo si se ha dicho a qué códec (nunca por defecto)."""
        return bool(self.vcod_destino or self.acod_destino)

    @property
    def destino(self) -> dict[str, Any] | None:
        """Lo que necesita ``policy.plan`` para la conversión: los códecs y la altura pedida."""
        if not self.convertir:
            return None
        return {"vcod": self.vcod_destino, "acod": self.acod_destino, "altura": self.height}

    def format_expression(self) -> str:
        if self.kind == "exact":
            return str(self.format)
        if self.sections or seleccion_capitulos(self):
            # sections are cut by ffmpeg with input seeking + stream copy: with YouTube's webm/opus formats the range
            # comes out wrong (16 s instead of 6, verified 2026-09-30), with H.264/AAC in mp4/m4a it is right (±0.03 s)
            if self.is_audio:
                return "ba[ext=m4a]/ba/b"
            h = f"[height<=?{int(self.height)}]" if self.height else ""
            return f"bv*[vcodec^=avc1]{h}+ba[ext=m4a]/b[ext=mp4]{h}/bv*{h}+ba/b"
        if self.kind == "video":
            if self.height:
                h = int(self.height)
                # best video ≤ h + best audio; else best combined ≤ h; else anything (audio-only sites, odd extractors)
                return f"bv*[height<=?{h}]+ba/b[height<=?{h}]/bv*+ba/b"
            return "bv*+ba/b"
        return "ba/b"  # audio: best audio-only, else best anything (-x extracts the audio)

    def describe(self) -> str:
        if self.convertir:
            return f"{_descripcion_conversion(self)} → {policy.contenedor_conversion(self.vcod_destino, self.acod_destino)}"
        if self.kind == "exact":
            return f"formato {self.format} → {self.container}"
        if self.perfil == "opus_origen":
            return "audio Opus del origen (sin recodificar)"
        if self.perfil == "video_720":
            return f"vídeo 720p VP9 + Opus (sin recodificar, {self.container})"
        if self.perfil == "compatible":
            return f"vídeo H.264 + AAC ({self.container})"
        if self.perfil == "pistas":
            return "pistas del origen, copiadas tal cual"
        if self.video_id or self.audio_id:
            return f"pistas {self.video_id or '—'} + {self.audio_id or '—'} → {self.container}"
        if self.kind == "video":
            return (f"vídeo {self.height}p" if self.height else "vídeo mejor calidad") + f" ({self.container})"
        if self.kind == "audio_original":
            return "audio original (sin recodificar)"
        q = f"{self.audio_bitrate} kbps" if self.audio_bitrate else f"VBR q{self.audio_vbr}"
        if self.audio_format in ("flac", "wav"):
            q = "sin pérdida"
        return f"audio {self.audio_format} {q}"


SECTIONS_RE = re.compile(r"^\*\d+(\.\d+)?-(\d+(\.\d+)?|inf)$")


# ------------------------------------------------------------------ conversión explícita (F3)

def _kbps_sin_perdida(codec: str, pista: dict[str, Any] | None) -> int:
    """Bitrate que se puede esperar de WAV/FLAC/ALAC: sale de la frecuencia y los canales del origen."""
    pista = pista or {}
    asr = int(pista.get("asr") or 0) or 44100
    canales = int(pista.get("canales") or 0) or 2
    pcm = asr * canales * 16 / 1000.0                 # WAV: 16 bits por muestra
    factor = {"wav": 1.0, "flac": 0.6, "alac": 0.65}.get(codec, 0.6)
    return max(64, int(pcm * factor))


def _descripcion_conversion(spec: DownloadSpec) -> str:
    """Una línea con lo que se va a crear: «H.265 CRF 28 · 720p 30 fps · AAC 128k»."""
    partes: list[str] = []
    if spec.vcod_destino:
        nombre = policy.NOMBRE_CODEC.get(spec.vcod_destino, spec.vcod_destino.upper())
        calidad = (f"{int(spec.video_bitrate)} kbps" if spec.video_bitrate
                   else f"CRF {int(spec.crf) if spec.crf is not None else CRF_VIDEO[spec.vcod_destino]['normal']}")
        partes.append(f"{nombre} {calidad}")
        if spec.two_pass:
            partes.append(t("2 pasadas"))
        if spec.hardware:
            partes.append(t("por la tarjeta gráfica"))
        if spec.height:
            partes.append(f"{int(spec.height)}p")
        if spec.fps:
            partes.append(f"{int(spec.fps)} fps")
    audio = spec.acod_destino or ("aac" if spec.vcod_destino in ("h264", "hevc") else "opus")
    nombre_a = policy.NOMBRE_CODEC.get(audio, audio.upper())
    if audio in ("flac", "wav", "alac"):
        partes.append(nombre_a)
    else:
        partes.append(f"{nombre_a} {int(spec.audio_bitrate or KBPS_POR_DEFECTO.get(audio, 128))}k")
    if spec.audio_canales == "mono":
        partes.append(t("mono"))
    elif spec.audio_canales == "estereo":
        partes.append(t("estéreo"))
    if spec.loudness != "ninguna":
        partes.append(f"{spec.loudness} LUFS")
    return " · ".join(partes)


def estimar(spec: DownloadSpec, info: dict[str, Any]) -> dict[str, Any]:
    """Cuánto va a ocupar y cuánto va a tardar, **antes** de empezar. Es una estimación y se dice.

    ``bytes`` es el tamaño del resultado; ``bytes_origen`` lo que hay que descargar para conseguirlo (en una copia
    son el mismo). Para vídeo con CRF se estima con píxeles × fotogramas × un factor por códec y calidad (la tabla
    de ``transcode.BPP``); con bitrate fijo, el bitrate mandado. La duración del trabajo sale de la duración del
    vídeo por un factor de codificación, más lo que tarda la descarga a una tasa razonable.
    """
    info = info or {}
    dur = float(info.get("duration") or info.get("duracion") or 0.0) or 0.0
    p = policy.pistas(info)
    if not spec.convertir:
        plan = policy.plan(info, spec.perfil or "video_720", spec.video_id, spec.audio_id)
        bytes_ = int(plan.bytes_estimados or 0)
        return {"bytes": bytes_, "bytes_origen": bytes_, "kbps": int(bytes_ * 8 / 1000 / dur) if dur and bytes_ else 0,
                "segundos": transcode.redondear_segundos(transcode.segundos_descarga(bytes_)),
                "descripcion": plan.nota, "contenedor": plan.contenedor, "recodificar": False}

    plan = policy.plan(info, "convertir", spec.video_id, spec.audio_id, destino=spec.destino)
    origen_v = next((r for r in p["video"] if str(r["id"]) == str(plan.video_id)), {})
    origen_a = next((r for r in p["audio"] if str(r["id"]) == str(plan.audio_id)), {})
    audio = spec.acod_destino or ("aac" if spec.vcod_destino in ("h264", "hevc") else "opus")
    if audio in ("flac", "wav", "alac"):
        kbps_a = _kbps_sin_perdida(audio, origen_a)
    else:
        kbps_a = int(spec.audio_bitrate or KBPS_POR_DEFECTO.get(audio, 128))

    if spec.vcod_destino:
        alto = int(origen_v.get("altura") or 0)
        ancho = int(origen_v.get("ancho") or 0) or (int(alto * 16 / 9) if alto else 1280)
        alto = alto or 720
        if spec.height and spec.height < alto:                 # se reduce de tamaño
            ancho, alto = max(2, int(round(ancho * spec.height / alto / 2)) * 2), int(spec.height)
        fps = float(spec.fps or origen_v.get("fps") or 25.0)
        kbps_v = transcode.kbps_video(spec.vcod_destino, ancho, alto, fps, spec.crf, spec.video_bitrate)
        kbps = kbps_v + kbps_a
        segundos = transcode.segundos_codifica(spec.vcod_destino, ancho, alto, dur, spec.two_pass)
    else:
        kbps = kbps_a
        segundos = transcode.segundos_codifica(None, 0, 0, dur)

    bytes_origen = int(plan.bytes_estimados or 0)
    bytes_ = int(kbps * 1000 / 8 * dur) if dur else bytes_origen
    return {"bytes": bytes_, "bytes_origen": bytes_origen, "kbps": int(kbps),
            "segundos": transcode.redondear_segundos(segundos + transcode.segundos_descarga(bytes_origen)),
            "descripcion": _descripcion_conversion(spec), "contenedor": plan.contenedor, "recodificar": True,
            "nota": plan.nota}


def catalogo(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lo que este equipo puede convertir, para la pantalla «Convertir al bajar…».

    Solo se ofrecen los códec cuya **codificadora existe** en este ffmpeg (``available_encoders``): ofrecer AV1 sin
    SVT-AV1 o ALAC sin ALAC es prometer un fichero que luego no sale. Sin etiquetas: los nombres visibles los pone el
    menú, que es quien traduce.
    """
    encs = available_encoders()
    audio: list[dict[str, Any]] = []
    for cod in policy.AUDIO_DESTINO:
        nombre = transcode.ENC_AUDIO.get(cod)
        if nombre and nombre not in encs:
            continue
        audio.append({"id": cod, "bitrates": list(policy.CADA_CODEC_AUDIO.get(cod, ())),
                      "contenedor": policy.CONTENEDOR_DESTINO_AUDIO.get(cod, "mkv"),
                      "sin_perdida": cod in transcode.SIN_PERDIDA})
    hw_codecs = list((hw or {}).get("codecs") or []) if (hw or {}).get("available") else []
    video: list[dict[str, Any]] = []
    for cod in policy.VIDEO_DESTINO:
        nombre = transcode.ENC_VIDEO.get(cod)
        if nombre not in encs:
            continue
        video.append({"id": cod, "crf": dict(CRF_VIDEO.get(cod, {})),
                      "contenedor": policy.contenedor_conversion(cod, "aac"),
                      "hardware": cod in hw_codecs})
    return {"audio": audio, "video": video, "canales": list(CANALES), "loudness": list(LOUDNESS),
            "resoluciones": list(transcode.RESOLUCIONES), "fps": [f for f in FPS if f],
            "hardware": {"disponible": bool(hw_codecs), "codecs": hw_codecs}}


def spec_conversion(url: str, *, acod: str | None = None, vcod: str | None = None, altura: int | None = None,
                    audio_bitrate: int | None = None, crf: int | None = None, video_bitrate: int | None = None,
                    fps: int | None = None, two_pass: bool = False, hardware: bool = False,
                    audio_canales: str = "conservar", loudness: str = "ninguna",
                    options: dict[str, Any] | None = None) -> DownloadSpec:
    """El spec de una conversión explícita: baja el original tal cual y lo convierte después.

    ``kind`` es ``video`` si hay códec de vídeo y ``audio_original`` si solo hay audio: lo que decide es qué hay que
    bajarse del origen, no lo que se va a crear.
    """
    opciones = dict(options or {})
    d: dict[str, Any] = {"url": url, "kind": "video" if vcod else "audio_original",
                         "vcod_destino": vcod, "acod_destino": acod, "audio_canales": audio_canales,
                         "loudness": loudness, "two_pass": bool(two_pass), "hardware": bool(hardware),
                         # None = el de por defecto del códec (KBPS_POR_DEFECTO): 192 solo era del MP3 de siempre
                         "audio_bitrate": int(audio_bitrate) if audio_bitrate else None}
    if altura:
        d["height"] = int(altura)
    if crf is not None:
        d["crf"] = int(crf)
    if video_bitrate:
        d["video_bitrate"] = int(video_bitrate)
    if fps:
        d["fps"] = int(fps)
    for k, v in opciones.items():
        if k in DownloadSpec.__dataclass_fields__ and k not in d:
            d[k] = v
    d["extra"] = {"preset": "convertir", "catalogo": True}
    return DownloadSpec.from_dict(d)


def seleccion_capitulos(spec: DownloadSpec) -> list[dict[str, Any]] | None:
    """Los capítulos pedidos, ya resueltos a filas (número, título, inicio, fin), o ``None``.

    ``spec.chapters`` llega de la interfaz como números o como expresión (``"3-5"``, ``"/entrevista/"``) y quien lo
    convierte en filas es ``YtdlService.resolve_chapters`` con el ``-J`` del vídeo; aquí solo se acepta lo ya
    resuelto. Una lista de números sin el análisis no se puede convertir en rangos, y suponer que son «todos» sería
    bajar capítulos que nadie ha pedido.
    """
    c = spec.chapters
    if not c or isinstance(c, bool):
        return None
    if isinstance(c, str) or (isinstance(c, (list, tuple)) and c and not isinstance(c[0], dict)):
        raise ValueError(t("para elegir capítulos por número hace falta el análisis del vídeo: usa ytdl.chapters"))
    filas = [dict(x) for x in c if isinstance(x, dict)]
    if not filas:
        raise ValueError(t("no has elegido ningún capítulo"))
    return sorted(filas, key=lambda x: int(x.get("numero") or 0))


def section_template(template: str) -> str:
    """The same name with the range, so that two ranges of one video never collide (``--no-overwrites``)."""
    tail = ".%(ext)s"
    base = template[: -len(tail)] if template.endswith(tail) else template
    return base + " [%(section_start)d-%(section_end)d s]" + tail


def output_args(out_dir: str, template: str = DEFAULT_TEMPLATE) -> list[str]:
    return ["-P", out_dir, "-o", template]


def progress_args() -> list[str]:
    return [
        "--newline", "--progress-delta", "0.2",
        "--progress-template", f"download:{PROGRESS_PREFIX} %(progress)j",
        "--progress-template", f"postprocess:{POSTPROCESS_PREFIX} %(progress)j",
        "--print", f"after_move:{DONE_PREFIX} %(.{{id,title,ext,filepath,format_id,duration,upload_date,uploader,channel}})j",
        "--no-simulate",
    ]


def resolve_sub_langs(langs: str, language: str | None) -> str:
    """``orig`` → the video's language (``<lang>.*``) plus YouTube's original-language auto captions (``.*-orig``)."""
    out: list[str] = []
    for tok in [t.strip() for t in langs.split(",") if t.strip()]:
        if tok == "orig":
            if language:
                out.append(f"{language.split('-')[0]}.*")
            out.append(".*-orig")
        else:
            out.append(tok)
    return ",".join(dict.fromkeys(out))


def list_template(template: str) -> str:
    """``<list title>/<NNN> - <name>``: a folder per playlist or channel, files numbered in the list's order."""
    return "%(playlist_title,playlist_id|Lista)s/%(playlist_index)03d - " + template


RATE_RE = re.compile(r"^\d+(\.\d+)?[KMG]?$")


def build_args(spec: DownloadSpec, out_dir: str, template: str = DEFAULT_TEMPLATE, rate_limit: str = "",
               archive_file: str | None = None, vcodec: str = "h264") -> list[str]:
    """Arguments after the binary (+ its base args); the URL is last. ``rate_limit``: yt-dlp ``-r`` (``2M``, ``500K``);
    ``archive_file``: the download archive used when ``spec.archive``; ``vcodec``: preferred codec limit for ``-S``
    (the best one decoded in hardware, see mpvd.hwdecode.sort_codec).

    Con ``video_id``/``audio_id`` (política «pistas») la orden pide esas dos pistas exactas y el contenedor del plan,
    y la pareja se valida con ``policy.contenedor_valido`` **antes** de construir nada: si no cabe, el error dice el
    motivo y no se lanza ffmpeg. No se recodifica: el merge de yt-dlp copia los flujos (no existe un ``-c copy`` que
    pasarle — ``-c`` en yt-dlp es ``--continue`` — lo que garantiza la copia es ``--merge-output-format``/
    ``--remux-video`` sin ``-x`` ni ``--audio-format``)."""
    spec.validate()
    cap_sel = seleccion_capitulos(spec)                       # None, o las filas de capítulos ya resueltas
    rangos = sections.parsear(spec.sections, float(spec.sections_margin)) if spec.sections else []
    if vcodec not in VCODEC_SORTS:
        vcodec = "h264"
    perfil = spec.perfil or ""
    explicito = bool(spec.video_id or spec.audio_id)
    solo_pista_audio = bool(spec.audio_id) and not spec.video_id
    es_audio = spec.is_audio or solo_pista_audio
    only_subs = spec.subs_mode == "only"
    if spec.video_codec and not only_subs:
        # una pareja imposible se rechaza al elegir, con el motivo, no al terminar con el fichero a medias
        contenedor, motivo = policy.contenedor_valido(spec.video_codec, spec.audio_codec or "none", spec.container)
        if contenedor is None:
            raise ValueError(motivo or "esa pareja no se puede copiar en el contenedor pedido")
    args: list[str] = ["--no-overwrites", "--continue", "--ignore-errors", "--retries", "5", "--socket-timeout", "30"]
    if rate_limit and RATE_RE.match(rate_limit):
        args += ["-r", rate_limit]
    if spec.archive and archive_file:
        args += ["--download-archive", archive_file]
    if not only_subs:
        if explicito:
            expresion = ("+".join(x for x in (spec.video_id, spec.audio_id) if x))
        elif perfil in POLICY_FORMAT and not spec.sections and not cap_sel:
            expresion = POLICY_FORMAT[perfil]
        else:
            expresion = spec.format_expression()
        args += ["-f", expresion]
    orden = ""
    if not only_subs and not explicito:
        if spec.convertir:
            # se recodifica de todos modos: interesa la resolución más alta, no el códec que este equipo decodifica
            orden = "res"
        elif perfil:
            orden = POLICY_SORT.get(perfil, "")
        elif spec.kind == "video" and FORMAT_SORT[spec.container]:
            orden = FORMAT_SORT[spec.container].format(vcodec=vcodec)
        elif spec.kind == "audio_original":
            # H31: the original Opus when there is one (YouTube: 251 over the AAC 140)
            orden = "acodec:opus"
    if orden.strip():
        args += ["-S", orden]
    if only_subs:
        pass
    elif es_audio:
        args += ["-x"]
        if spec.kind == "audio_convert":
            args += ["--audio-format", spec.audio_format]
            if spec.audio_format not in ("flac", "wav"):
                if spec.audio_bitrate is not None:
                    args += ["--audio-quality", f"{int(spec.audio_bitrate)}K"]
                else:
                    args += ["--audio-quality", str(int(spec.audio_vbr))]
        # audio sin --audio-format: FFmpegExtractAudio «best» copia el códec de origen (Opus sigue siendo Opus)
    elif spec.convertir:
        # el intermedio va en MKV, que admite cualquier códec: la conversión la hace `transcode` después, sobre el
        # fichero tal y como lo sirve el origen (nada de --recode-video ni --audio-format, que se saltarían el trabajo)
        args += ["--merge-output-format", "mkv"]
    else:
        args += ["--merge-output-format", MERGE_PREFERENCES[spec.container]]
        if REMUX_RULES[spec.container]:
            args += ["--remux-video", REMUX_RULES[spec.container]]
    langs = spec.sub_langs.replace("orig,", "").replace(",orig", "") if "orig" in spec.sub_langs.split(",") else \
        spec.sub_langs  # an unresolved «orig» (no info): the other languages
    langs = langs if langs != "orig" else ".*-orig"
    # .srt files that stay on disk: their paths come from an after_video print (after_move never runs without a
    # download); embedded subtitles are deleted once inside the video, so they are not reported
    subs_print = ["--print", f"after_video:{SUBS_PREFIX} %(requested_subtitles.:.filepath)j"]
    if spec.subs_mode == "only":
        args += ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs, "--convert-subs", "srt",
                 *subs_print]
    elif spec.subtitles and spec.subs_mode == "file":
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", langs, "--convert-subs", "srt", *subs_print]
    elif spec.subtitles and not spec.is_audio:
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", langs, "--embed-subs"]
    if spec.chapters is True:
        args += ["--embed-chapters"]
    if spec.thumbnail:
        args += ["--embed-thumbnail"]
    if spec.metadata:
        args += ["--embed-metadata"]
    if spec.sponsorblock == "mark":
        args += ["--sponsorblock-mark", "all"]
    elif spec.sponsorblock == "remove":
        args += ["--sponsorblock-remove", "default"]
    if spec.playlist:
        args += ["--yes-playlist"]
        if spec.playlist_items:
            args += ["--playlist-items", spec.playlist_items]
        if spec.list_folder:
            template = list_template(template)
    else:
        args += ["--no-playlist"]
    if rangos:
        # corte en el fotograma clave, sin recodificar (como «Grabar»); necesita ffmpeg, que ya es obligatorio.
        # Un --download-sections por rango: yt-dlp los acepta repetidos y escribe un fichero por rango.
        args += sections.args_rangos(rangos)
        template = section_template(template)
    if cap_sel:
        # --split-chapters con su plantilla «chapter:» cuando van todos; trozo por capítulo cuando van algunos
        cap_args = chapters.args_capitulos(cap_sel, spec.chapters_mode, None, todos=spec.extra.get("chapters_all"))
        args += cap_args
        if "--download-sections" in cap_args:
            template = section_template(template)
    args += output_args(out_dir, template)
    args += progress_args()
    args += ["--", spec.url]
    return args


# -- presets shown in the "Descargar" menu ------------------------------------------------------------

PRESETS: list[dict[str, Any]] = [
    # --- la política única (docs/DISENO-YTDL-POLITICAS.md §3). El defecto no recodifica y el contenedor es el que
    # admiten los códecs del origen: Opus se queda en .opus/.mkv, nunca dentro de un MP4.
    {"id": "audio_opus_origen", "title": "Audio · Opus del origen (recomendado)", "group": "audio",
     "spec": {"kind": "audio_original", "perfil": "opus_origen"}},
    {"id": "video_720", "title": "Vídeo · 720p VP9 + Opus (recomendado)", "group": "video",
     "spec": {"kind": "video", "perfil": "video_720", "height": 720, "container": "mkv"}},
    {"id": "compatible", "title": "Vídeo · compatible (H.264 + AAC, MP4)", "group": "video",
     "spec": {"kind": "video", "perfil": "compatible", "height": 1080, "container": "mp4"}},
    {"id": "pistas", "title": "Elegir pistas del origen…", "group": "video",
     "spec": {"kind": "video", "perfil": "pistas", "container": "mkv"}},
    # --- los de siempre: mismos ids (prefs.json, mu-feeds y mu-record los nombran) y mismo comportamiento
    {"id": "video_best", "title": "Vídeo · mejor calidad", "group": "video", "spec": {"kind": "video", "height": None}},
    {"id": "video_1080", "title": "Vídeo · hasta 1080p (recomendado)", "group": "video",
     "spec": {"kind": "video", "height": 1080}},
    {"id": "video_480", "title": "Vídeo · 480p", "group": "video", "spec": {"kind": "video", "height": 480}},
    {"id": "video_360", "title": "Vídeo · 360p", "group": "video", "spec": {"kind": "video", "height": 360}},
    {"id": "audio_original", "title": "Audio · original (sin recodificar, recomendado)", "group": "audio",
     "spec": {"kind": "audio_original"}},
    {"id": "subs_only", "title": "Solo subtítulos (SRT)", "group": "subs",
     "spec": {"kind": "video", "subs_mode": "only", "subtitles": True}},
    # --- convertir: recodifican, así que viven aparte y con su nombre (nunca el defecto)
    {"id": "audio_mp3_128", "title": "Audio · MP3 128 kbps", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": 128}},
    {"id": "audio_mp3_192", "title": "Audio · MP3 192 kbps", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": 192}},
    {"id": "audio_mp3_320", "title": "Audio · MP3 320 kbps", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": 320}},
    {"id": "audio_mp3_vbr", "title": "Audio · MP3 VBR (máxima)", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "mp3", "audio_bitrate": None, "audio_vbr": 0}},
    {"id": "audio_opus_128", "title": "Audio · Opus 128 kbps", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "opus", "audio_bitrate": 128}},
    {"id": "audio_m4a_192", "title": "Audio · M4A (AAC) 192 kbps", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "m4a", "audio_bitrate": 192}},
    {"id": "audio_flac", "title": "Audio · FLAC (sin pérdida)", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "flac", "audio_bitrate": None}},
    {"id": "audio_wav", "title": "Audio · WAV", "group": "convertir",
     "spec": {"kind": "audio_convert", "audio_format": "wav", "audio_bitrate": None}},
]


def preset_rows() -> list[dict[str, Any]]:
    """Los presets con el nombre en el idioma de quien mira. H49/G8: la tabla está en castellano porque la cadena
    castellana ES la clave; se traduce aquí, que es por donde salen hacia el menú y el panel."""
    out = []
    for p in PRESETS:
        fila = dict(p)
        for campo in ("title", "label", "hint"):
            if fila.get(campo):
                fila[campo] = t(str(fila[campo]))
        out.append(fila)
    return out


def sub_lang_rows() -> list[dict[str, Any]]:
    return [{**r, "label": t(str(r["label"]))} for r in SUB_LANG_CHOICES]


def preset(preset_id: str) -> dict[str, Any]:
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    raise KeyError(preset_id)


def spec_from_preset(preset_id: str, url: str, options: dict[str, Any] | None = None) -> DownloadSpec:
    """Preset + user options (container, sub_langs, toggles) → spec."""
    p = preset(preset_id)
    d: dict[str, Any] = {"url": url, **p["spec"]}
    for k, v in (options or {}).items():
        if k in DownloadSpec.__dataclass_fields__ and k not in p["spec"] and k != "url":
            d[k] = v
    d["extra"] = {"preset": preset_id, "preset_title": p["title"]}
    return DownloadSpec.from_dict(d)
