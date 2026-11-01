"""Una sola verdad para decidir **qué pistas** se le piden a yt-dlp.

Portado de la política ya probada del bot (``~/.local/share/ytdl/ytdlbot/formats.py``: ``plan()``,
``elegir_audio()``, ``_orden_video()``). Reglas que no se saltan:

* **El defecto no recodifica.** Convertir es una acción aparte (perfil ``convertir``), con su nombre.
* **A igual altura gana el bitrate más bajo**: para guardar, un 720p a 1,5 Mbps es mejor que uno a 2,5.
* **El audio por defecto es el original y el pequeño**: de los Opus del origen, el de menor bitrate; sin Opus, el
  AAC de menor bitrate, y se dice. Sin ninguno de los dos, la mejor pista que haya y el aviso de que para
  ``.opus``/``.m4a`` habría que convertir (otra pantalla). Nunca se recodifica en el camino de descarga.
* **Un contenedor que no admite el códec se rechaza con el motivo**, nunca se descubre a mitad de la descarga.

Este módulo no ejecuta nada ni conoce yt-dlp: recibe el ``-J`` (crudo o ya analizado por ``info.py``) y devuelve
una ``Decision``. ``presets.py`` la convierte en argumentos.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

Perfil = Literal["opus_origen", "video_720", "compatible", "pistas", "convertir"]
PERFILES: tuple[str, ...] = ("opus_origen", "video_720", "compatible", "pistas", "convertir")

VIDEO_720 = 720          # altura del perfil por defecto
COMPATIBLE_MAX = 1080    # altura máxima de «compatible»: por encima, ni el origen suele ofrecer H.264
AAC_OBJETIVO = 140       # kbps del AAC que se pide en «compatible» (el 140 de YouTube)

# Conversión explícita (F3): las familias a las que se puede convertir y el contenedor de cada resultado. No es el
# camino por defecto: solo se usa cuando el spec lo pide, con su nombre y su estimación delante.
AUDIO_DESTINO: tuple[str, ...] = ("opus", "aac", "alac", "mp3", "flac", "wav")
VIDEO_DESTINO: tuple[str, ...] = ("h264", "hevc", "vp9", "av1")
CADA_CODEC_AUDIO = {"opus": (32, 48, 64, 96, 128, 160), "aac": (96, 128, 192, 256), "mp3": (128, 192, 256, 320),
                    "flac": (), "wav": (), "alac": ()}      # kbps ofrecidos; sin pérdida, ninguno
CONTENEDOR_DESTINO_AUDIO = {"opus": "opus", "aac": "m4a", "alac": "m4a", "mp3": "mp3", "flac": "flac", "wav": "wav"}
NOMBRE_CODEC = {"h264": "H.264", "hevc": "H.265", "vp9": "VP9", "av1": "AV1", "opus": "Opus", "aac": "AAC",
                "alac": "ALAC (sin pérdida)", "mp3": "MP3", "flac": "FLAC (sin pérdida)", "wav": "WAV (sin comprimir)"}


def contenedor_conversion(vcodec: str | None, acodec: str | None) -> str:
    """La extensión honesta del resultado: la que el códec admite, sin disfrazarlo.

    Solo audio → la del códec (``.opus``, ``.m4a``, ``.mp3``…). Con vídeo → MP4 para H.264/H.265 con AAC, WebM para
    VP9/AV1 con Opus, y MKV para cualquier otra pareja (MKV lo admite todo, así que nunca se rechaza una conversión).
    """
    v = _familia_video(vcodec) if vcodec else ""
    a = _familia_audio(acodec) if acodec else ""
    if not v:
        return CONTENEDOR_DESTINO_AUDIO.get(a or (acodec or "").lower(), "mkv")
    if not a:                                        # el audio que acompaña lo pone el contenedor destino
        a = "aac" if v in ("h264", "hevc") else "opus"
    if v in ("h264", "hevc") and a in ("aac", "mp3"):
        return "mp4"
    if v in ("vp9", "av1") and a in ("opus", "vorbis"):
        return "webm"
    return "mkv"

# Familias de códec. Se comparan sin puntos ni guiones: «mp4a.40.2» → «mp4a402», «h.264» → «h264».
VP9 = ("vp9", "vp09")
H264 = ("avc1", "avc3", "h264")
HEVC = ("hevc", "h265", "hev1", "hvc1")
AV1 = ("av01", "av1")
OPUS = ("opus",)
AAC = ("mp4a", "aac")
VORBIS = ("vorbis",)
MP3 = ("mp3",)

NOMBRE = {"vp9": "VP9", "h264": "H.264", "hevc": "H.265", "av1": "AV1", "vp8": "VP8", "otro": "códec raro",
          "opus": "Opus", "aac": "AAC", "vorbis": "Vorbis", "mp3": "MP3", "ac3": "AC-3", "eac3": "E-AC-3",
          "flac": "FLAC", "": ""}

# Contenedor honesto de cada códec de audio suelto, y de cada pareja copiada tal cual (DISENO-YTDL-POLITICAS §3.2).
# Un Opus suelto va en Ogg con extensión .opus, y un AAC suelto en .m4a: es lo que el códec admite sin tocarlo.
CONTENEDOR_AUDIO = {"opus": "opus", "aac": "m4a", "vorbis": "webm", "mp3": "mp4", "flac": "mkv"}
CON_VIDEO = (("h264", "aac", "mp4"), ("hevc", "aac", "mp4"), ("h264", "opus", "mkv"), ("hevc", "opus", "mkv"),
             ("vp9", "aac", "mkv"), ("vp9", "opus", "mkv"), ("av1", "aac", "mkv"), ("av1", "opus", "mkv"))

# «Extensión compatible»: el mismo fichero Ogg con Opus dentro, entregado como .ogg porque hay programas que no
# reconocen la extensión .opus. Es un renombrado al terminar, nunca una conversión.
EXT_COMPATIBLE = {"opus": "ogg"}

# Los avisos de la ficha cuando el origen no trae lo que se querría. Son texto para el usuario: salen tal cual.
AVISO_SIN_OPUS = "el origen no trae Opus"
AVISO_SIN_OPUS_NI_AAC = ("el origen no trae ni Opus ni AAC: para .opus o .m4a habría que convertirlo, "
                         "y eso es otra pantalla")


def lista_ids(ident: Any) -> list[str]:
    """Uno o varios ``format_id`` → siempre una lista. ``None``, ``""`` y las listas vacías dan ``[]``.

    La elección a mano admite **varias pistas** (dos audios, o audio y vídeo de doblajes distintos): se muxean tal
    cual en un MKV. Por eso ``video_id``/``audio_id`` viajan como id suelto (el caso de siempre) o como lista.
    """
    if ident is None or ident == "":
        return []
    if isinstance(ident, (list, tuple)):
        return [str(x) for x in ident if x not in (None, "")]
    return [str(ident)]


def _uno_o_varios(ids: list[str]) -> str | list[str] | None:
    """Lo que se devuelve en la ``Decision``: el id suelto si es uno (lo que esperan el menú y la estimación)."""
    if not ids:
        return None
    return ids[0] if len(ids) == 1 else ids


@dataclass(frozen=True)
class Decision:
    """Lo que se va a pedir: las pistas elegidas y un contenedor, sin recodificar salvo que se diga."""

    video_id: str | list[str] | None
    audio_id: str | list[str] | None
    contenedor: str            # "mkv" | "mp4" | "opus" | "m4a" | "webm"
    recodificar: bool          # siempre False salvo perfil «convertir»
    nota: str                  # «Opus del origen, el más bajo (voz)»
    bytes_estimados: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ lectura del -J

def _num(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int:
    return int(_num(v))


def _plano(codec: Any) -> str:
    """Un códec de yt-dlp sin puntos ni guiones, para comparar familias. ``none``/``null`` → vacío."""
    c = str(codec or "").strip().lower()
    if c in ("none", "null", "na"):
        return ""
    return c.replace(".", "").replace("-", "")


def _familia_video(codec: Any) -> str:
    c = _plano(codec)
    if not c:
        return ""
    for nombre, prefijos in (("vp9", VP9), ("h264", H264), ("hevc", HEVC), ("av1", AV1)):
        if c.startswith(prefijos):
            return nombre
    return "otro"


def _familia_audio(codec: Any) -> str:
    c = _plano(codec)
    if not c:
        return ""
    for nombre, prefijos in (("opus", OPUS), ("aac", AAC), ("vorbis", VORBIS), ("mp3", MP3)):
        if c.startswith(prefijos):
            return nombre
    if c in ("ac3",):
        return "ac3"
    if c in ("eac3", "ec3"):
        return "eac3"
    if c.startswith("flac"):
        return "flac"
    return "otro"


def _norm(f: dict[str, Any]) -> dict[str, Any]:
    """Una fila del ``-J`` (cruda) o de ``info.analyze()`` (agrupada) → la misma forma interna."""
    vcod = f.get("vcodec") if f.get("vcodec") not in (None, "") else f.get("vcodec_name")
    acod = f.get("acodec") if f.get("acodec") not in (None, "") else f.get("acodec_name")
    return {
        "id": str(f.get("format_id") or f.get("id") or ""),
        "vcod": str(vcod or "none"), "acod": str(acod or "none"),
        "vfam": _familia_video(vcod), "afam": _familia_audio(acod),
        "altura": _int(f.get("height")), "ancho": _int(f.get("width")), "fps": _num(f.get("fps")),
        "tbr": _num(f.get("tbr")), "abr": _num(f.get("abr")),
        "bytes": _int(f.get("filesize")) or _int(f.get("filesize_approx")),
        "idioma": str(f.get("language") or f.get("idioma") or ""),
        "ext": str(f.get("ext") or ""),
        "canales": _int(f.get("audio_channels") or f.get("channels")),
        "asr": _int(f.get("asr")),
        "drm": bool(f.get("has_drm")),
        "protocolo": str(f.get("protocol") or ""),
        "storyboard": str(f.get("protocol") or "") == "mhtml" or _plano(vcod) == "images",
    }


def _filas(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Todas las pistas del origen, venga el dict crudo del ``-J`` o el ya agrupado por ``info.analyze()``."""
    if not isinstance(info, dict):
        return []
    crudo = info.get("formats")
    if isinstance(crudo, dict):                       # analyze(): {"video": [...], "combined": [...], "audio": [...]}
        salida: list[dict[str, Any]] = []
        for grupo in ("video", "combined", "audio"):
            salida += [f for f in (crudo.get(grupo) or []) if isinstance(f, dict)]
        return [_norm(f) for f in salida]
    if isinstance(crudo, list):
        return [_norm(f) for f in crudo if isinstance(f, dict)]
    otro = info.get("formatos")                   # la clave en castellano que usa el bot
    if isinstance(otro, list):
        return [_norm(f) for f in otro if isinstance(f, dict)]
    return []


def _duracion(info: dict[str, Any]) -> float:
    return _num(info.get("duration") if info.get("duration") is not None else info.get("duracion"))


def _util(f: dict[str, Any]) -> bool:
    return bool(f["id"]) and not f["drm"] and not f["storyboard"]


def _sin_drc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """YouTube sirve la misma pista dos veces (``251`` y ``251-drc``). Es la misma calidad: se queda la normal."""
    normales = {f["id"] for f in rows if not f["id"].endswith("-drc")}
    return [f for f in rows if not (f["id"].endswith("-drc") and f["id"][:-4] in normales)]


def _separadas(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Pistas de vídeo sin audio dentro: las únicas que se pueden emparejar con un audio elegido."""
    return _sin_drc([f for f in _filas(info) if _util(f) and f["vfam"] and not f["afam"]])


def _combinadas(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Pistas progresivas (vídeo con su audio dentro): algunos orígenes, como archive.org, solo tienen esto."""
    return _sin_drc([f for f in _filas(info) if _util(f) and f["vfam"] and f["afam"]])


def _audios(info: dict[str, Any]) -> list[dict[str, Any]]:
    return _sin_drc([f for f in _filas(info) if _util(f) and f["afam"] and not f["vfam"]])


def _bytes(f: dict[str, Any] | None, duracion: float = 0.0) -> int:
    if not f:
        return 0
    if f["bytes"]:
        return int(f["bytes"])
    kbps = f["abr"] or f["tbr"]
    return int(kbps * 1000 / 8 * duracion) if (kbps and duracion) else 0


def _kbps(f: dict[str, Any] | None) -> int:
    if not f:
        return 0
    return int(f["abr"] or f["tbr"])


# ------------------------------------------------------------------ las pistas del origen

def pistas(info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """**Todos** los flujos reales del origen, listos para pintar y para marcar varios: vídeo y audio.

    Cada fila: ``id``, ``codec``, ``familia``, ``altura``/``ancho``/``fps`` (solo vídeo), ``kbps``, ``bytes``,
    ``idioma``, ``ext`` y ``canales``/``asr`` (solo audio). ``bytes`` son los que declara el origen y, cuando no los
    declara, los que salen del bitrate por la duración (``estimado`` lo dice): sin eso, media lista sale sin tamaño.
    """
    orden_v = {"vp9": 0, "h264": 1, "av1": 2, "hevc": 3, "otro": 4}
    dur = _duracion(info)
    vids = _separadas(info) + _combinadas(info)
    vid = sorted(vids, key=lambda f: (-f["altura"], orden_v.get(f["vfam"], 9), -f["fps"], -f["bytes"], f["tbr"]))
    aud = sorted(_audios(info), key=lambda f: ({"opus": 0, "aac": 1}.get(f["afam"], 9), -_kbps(f), f["idioma"]))
    return {
        "video": [{"id": f["id"], "codec": NOMBRE.get(f["vfam"], f["vfam"]), "familia": f["vfam"],
                   "altura": f["altura"], "ancho": f["ancho"], "fps": int(f["fps"]), "kbps": int(f["tbr"]),
                   "bytes": f["bytes"] or _bytes(f, dur), "estimado": not f["bytes"],
                   "idioma": f["idioma"], "ext": f["ext"],
                   "con_audio": bool(f["afam"])} for f in vid],
        "audio": [{"id": f["id"], "codec": NOMBRE.get(f["afam"], f["afam"]), "familia": f["afam"],
                   "kbps": _kbps(f), "canales": f["canales"], "asr": f["asr"],
                   "bytes": f["bytes"] or _bytes(f, dur), "estimado": not f["bytes"],
                   "idioma": f["idioma"], "ext": f["ext"]} for f in aud],
    }


# ------------------------------------------------------------------ contenedor por pareja

def contenedor_valido(vcodec: str, acodec: str, pedido: str | None = None, *,
                      varias: bool = False, capitulos: bool = False) -> tuple[str | None, str | None]:
    """``(contenedor, None)`` si la pareja se puede copiar tal cual; ``(None, motivo)`` si no.

    Sin ``pedido`` devuelve el contenedor honesto de la pareja. Con ``pedido``, ese si lo admite y, si no, se
    rechaza con el motivo en castellano: el contenedor nunca se cambia en silencio.

    ``varias`` (más de dos pistas marcadas) y ``capitulos`` (marcas de capítulo dentro del fichero) fuerzan MKV
    aunque la pareja cupiera en MP4: es el único contenedor que admite varias pistas y capítulos sin recodificar.
    """
    vfam, afam = _familia_video(vcodec), _familia_audio(acodec)
    pedido = (pedido or "").strip().lower() or None
    if vfam and (varias or capitulos):
        if not pedido or pedido == "mkv":
            return "mkv", None
        return None, ("MKV es el único contenedor que lleva varias pistas o capítulos sin recodificar: "
                      f"{pedido.upper()} no")

    if not vfam:                                   # solo audio: el contenedor lo pone el códec
        if not afam:
            return None, "no hay ninguna pista que copiar: el origen no declara códec"
        natural = CONTENEDOR_AUDIO.get(afam, "mkv")
        if not pedido or pedido == natural:
            return natural, None
        if pedido == "mkv":
            return "mkv", None
        if afam == "opus" and pedido == "mp4":
            return None, "Opus no es estándar en MP4 (muchos televisores y móviles no lo abren): usa .opus o MKV"
        if afam == "aac" and pedido == "webm":
            return None, "WebM no admite AAC: usa MP4 o MKV"
        return None, f"{pedido.upper()} no lleva {NOMBRE.get(afam, afam)} tal cual: usa {natural.upper()} o MKV"

    if not afam:                                   # vídeo sin audio: MKV lo admite todo, MP4 el H.264/HEVC
        natural = "mp4" if vfam in ("h264", "hevc") else "mkv"
    else:
        natural = next((c for v, a, c in CON_VIDEO if v == vfam and a == afam), "mkv")
    if not pedido or pedido == natural:
        return natural, None
    if pedido == "mkv":
        return "mkv", None
    if pedido == "mp4" and afam == "opus":
        return None, "Opus no es estándar en MP4 (muchos televisores y móviles no lo abren): usa MKV"
    if pedido == "mp4" and vfam in ("vp9", "av1"):
        return None, f"MP4 con {NOMBRE.get(vfam, vfam)} no se reproduce en televisiones: usa MKV"
    if pedido == "webm" and afam == "aac":
        return None, "WebM no admite AAC: usa MP4 o MKV"
    if pedido == "webm" and vfam in ("h264", "hevc"):
        return None, f"WebM solo admite VP9 o AV1 de vídeo, no {NOMBRE.get(vfam, vfam)}: usa MP4 o MKV"
    return None, (f"{pedido.upper()} no lleva {NOMBRE.get(vfam, vfam)} + {NOMBRE.get(afam, afam)} tal cual: "
                  f"usa {natural.upper()}")


# ------------------------------------------------------------------ elección de pistas

def _mejor_video(rows: list[dict[str, Any]], familias: tuple[str, ...],
                 tope: int | None) -> dict[str, Any] | None:
    """La pista de esas familias a la altura más alta ≤ ``tope`` y, a igual altura, la de menor ``tbr``."""
    cands = [f for f in rows if f["vfam"] in familias and f["altura"] > 0]
    if not cands:
        return None
    debajo = [f for f in cands if tope is None or f["altura"] <= tope]
    pool = debajo or cands                          # si el origen no baja de ahí, se dice en la nota
    return sorted(pool, key=lambda f: (-f["altura"], 0 if f["bytes"] else 1, f["tbr"] or 1e9, -f["fps"]))[0]


def _audio_defecto(audios: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """El audio del origen que se entrega por defecto, y el aviso que lleva la ficha.

    Opus → el de menor bitrate (es el que se quiere: el original y el pequeño). Sin Opus → el AAC de menor
    bitrate, y la ficha dice que el origen no trae Opus. Sin Opus ni AAC → la mejor pista que haya, y se avisa de
    que para ``.opus``/``.m4a`` habría que convertir. En ningún caso se recodifica aquí.
    """
    for familia, aviso in (("opus", ""), ("aac", AVISO_SIN_OPUS)):
        iguales = [f for f in audios if f["afam"] == familia]
        if iguales:
            # un bitrate que el origen no declara (0) no puede ganar un «el más bajo»: se deja para el final
            return min(iguales, key=lambda f: (_kbps(f) or 1 << 30, -f["canales"])), aviso
    if not audios:
        return None, ""
    return max(audios, key=lambda f: (_kbps(f), f["canales"])), AVISO_SIN_OPUS_NI_AAC


def _aac_cerca(audios: list[dict[str, Any]], objetivo: int = AAC_OBJETIVO) -> dict[str, Any] | None:
    aac = [f for f in audios if f["afam"] == "aac"]
    if not aac:
        return None
    # a igual distancia manda no pasarse del objetivo (un 128k está más cerca de 140 que un 192k)
    return sorted(aac, key=lambda f: (abs(_kbps(f) - objetivo), 0 if _kbps(f) <= objetivo else 1, _kbps(f)))[0]


def _pista(info: dict[str, Any], ident: str | None, que: str) -> dict[str, Any] | None:
    """La pista exacta con ese ``format_id``, o ``ValueError`` si el origen ya no la sirve."""
    if not ident:
        return None
    for f in _filas(info):
        if f["id"] == str(ident) and _util(f):
            return f
    raise ValueError(f"la pista de {que} {ident} ya no está en el origen")


def _nota_audio(f: dict[str, Any] | None, elegido: bool = False, aac_objetivo: bool = False,
                aviso: str = "") -> str:
    if f is None:
        return "sin audio"
    nombre = NOMBRE.get(f["afam"], f["afam"])
    if elegido:
        return f"{nombre} {_kbps(f)}k del origen, copiado tal cual"
    if aac_objetivo and f["afam"] == "aac":
        return f"{nombre} del origen, el más cercano a {AAC_OBJETIVO}k: {_kbps(f)}k"
    cual = "el de menor bitrate" if f["afam"] in ("opus", "aac") else "la mejor pista del origen"
    nota = f"{nombre} del origen, {cual}: {_kbps(f)}k"
    return f"{nota} · {aviso}" if aviso else nota


def _nota_video(f: dict[str, Any] | None) -> str:
    if f is None:
        return "sin vídeo"
    nombre = NOMBRE.get(f["vfam"], f["vfam"])
    return f"{nombre} {f['altura']}p {int(f['tbr'])}k" if f["altura"] else f"{nombre} {int(f['tbr'])}k"


def _completa(f: dict[str, Any]) -> Decision:
    """Una pista progresiva: el audio viaja dentro y no se puede sustituir (archive.org y compañía)."""
    contenedor = f["ext"] if f["ext"] in ("mp4", "webm", "mkv") else "mkv"
    return Decision(f["id"], None, contenedor, False,
                    f"{_nota_video(f)} con el audio dentro ({NOMBRE.get(f['afam'], f['afam'])}), copiada tal cual",
                    _bytes(f) or None)


# ------------------------------------------------------------------ el plan

# Calidad de la fuente de audio para recodificar: primero lo que no pierde, y entre lo que pierde, el códec que
# mejor suena a igual bitrate (Opus > Vorbis > AAC > MP3). Recodificar de AAC a AAC es perder dos veces.
ORDEN_AUDIO_FUENTE = {"flac": 0, "alac": 0, "wav": 0, "opus": 1, "vorbis": 2, "aac": 3, "mp3": 4}


def _audio_para_recodificar(audios: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La pista de audio que conviene convertir: sin pérdida si la hay, luego la de mejor códec y más bitrate."""
    if not audios:
        return None
    return sorted(audios, key=lambda f: (ORDEN_AUDIO_FUENTE.get(f["afam"], 5), -_kbps(f), -f["canales"]))[0]


def _plan_conversion(info: dict[str, Any], destino: dict[str, Any], audios: list[dict[str, Any]],
                     duracion: float) -> Decision:
    """La conversión explícita: qué pista del origen se baja para convertirla y en qué se va a convertir.

    ``bytes_estimados`` es lo que se **descarga** (la pista del origen); el tamaño del resultado lo calcula
    ``presets.estimar``. Nunca se recodifica en el mismo paso que la descarga: baja el original tal cual y después
    ``transcode.convertir`` lo comprueba con ffprobe.
    """
    vcod = str(destino.get("vcod") or "").lower() or None
    acod = str(destino.get("acod") or "").lower() or None
    altura = _int(destino.get("altura")) or None
    if vcod and vcod not in VIDEO_DESTINO:
        raise ValueError(f"no se puede convertir a {vcod}: los códecs de vídeo del catálogo son "
                         f"{', '.join(NOMBRE_CODEC[c] for c in VIDEO_DESTINO)}")
    if acod and acod not in AUDIO_DESTINO:
        raise ValueError(f"no se puede convertir a {acod}: los códecs de audio del catálogo son "
                         f"{', '.join(NOMBRE_CODEC[c] for c in AUDIO_DESTINO)}")
    if not vcod and not acod:
        raise ValueError("la conversión necesita decir a qué códec: audio, vídeo, o los dos")
    if not acod:                                     # el audio que acompaña al vídeo lo pone el contenedor
        acod = "aac" if vcod in ("h264", "hevc") else "opus"
    contenedor = contenedor_conversion(vcod, acod)

    if not vcod:                                     # solo audio
        a = _audio_para_recodificar(audios)
        if a is None:
            raise ValueError("este enlace no ofrece ninguna pista de audio que convertir")
        nota = (f"{NOMBRE_CODEC[acod]} a partir de {NOMBRE_CODEC.get(a['afam'], a['afam'])} "
                f"{_kbps(a)}k del origen → {contenedor.upper()} · se recodifica")
        return Decision(None, a["id"], contenedor, True, nota, _bytes(a, duracion) or None)

    # el códec del origen da igual (se va a recodificar): se baja la resolución pedida, que es lo que ahorra datos
    v = _mejor_video(_separadas(info), ("vp9", "h264", "av1", "hevc", "otro"), altura)
    if v is None:
        completa = _mejor_video(_combinadas(info), ("vp9", "h264", "av1", "hevc", "otro"), altura)
        if completa is None:
            raise ValueError("este enlace no ofrece ninguna pista de vídeo que convertir")
        v = completa
    a = _audio_para_recodificar(audios) if v["afam"] == "" else None
    nota = (f"{NOMBRE_CODEC[vcod]} + {NOMBRE_CODEC[acod]} a partir de {_nota_video(v)} → "
            f"{contenedor.upper()} · se recodifica")
    if v["altura"] and altura and v["altura"] > altura:
        nota += f" · se baja a {altura}p"
    if v["afam"]:
        nota += " · el audio viaja dentro del vídeo"
    return Decision(v["id"], a["id"] if a else None, contenedor, True, nota,
                    (_bytes(v, duracion) + _bytes(a, duracion)) or None)


def _pistas_a_mano(info: dict[str, Any], video_id: Any, audio_id: Any, duracion: float,
                   capitulos: bool) -> Decision:
    """Las pistas que ha marcado el usuario, **una o varias**, muxeadas tal cual (nunca se recodifica).

    Con más de dos pistas el contenedor es MKV, que es el único que las lleva todas sin tocarlas; con una pareja
    manda la tabla de ``contenedor_valido``, y si no cabe se dice cuál sí en vez de fallar al final de la descarga.
    """
    vids = [f for f in (_pista(info, i, "vídeo") for i in lista_ids(video_id)) if f]
    auds = [f for f in (_pista(info, i, "audio") for i in lista_ids(audio_id)) if f]
    for v in vids:
        if not v["vfam"]:
            raise ValueError(f"la pista {v['id']} es de audio, no de vídeo")
    for a in auds:
        if a["vfam"] or not a["afam"]:
            raise ValueError(f"la pista {a['id']} no es una pista de audio suelta")
    if not vids and not auds:
        raise ValueError("«pistas» necesita al menos una pista de vídeo o de audio")
    progresivas = [v for v in vids if v["afam"]]
    if progresivas and (len(vids) > 1 or auds):
        raise ValueError(f"la pista {progresivas[0]['id']} ya trae su propio audio: elige una de vídeo sin audio "
                         "para combinar, o quita las de audio")
    bytes_ = sum(_bytes(f, duracion) for f in vids + auds) or None
    varias = len(vids) + len(auds) > 2

    if not vids:                                   # audio suelto: el contenedor lo pone el códec (.opus, .m4a…)
        contenedor = "mkv" if len(auds) > 1 else CONTENEDOR_AUDIO.get(auds[0]["afam"], "mkv")
        nota = " + ".join(_nota_audio(a, elegido=True) for a in auds) + f" → {contenedor.upper()}"
        return Decision(None, _uno_o_varios([a["id"] for a in auds]), contenedor, False, nota, bytes_)
    if progresivas:                                # pista progresiva: ya trae su audio dentro
        return _completa(vids[0])
    if not auds:
        elegido, aviso = _audio_defecto(_audios(info))
        if elegido is None:
            raise ValueError("el origen no ofrece ninguna pista de audio para acompañar al vídeo")
        auds, bytes_ = [elegido], (bytes_ or 0) + _bytes(elegido, duracion) or None
    contenedor, motivo = contenedor_valido(vids[0]["vcod"], auds[0]["acod"], varias=varias, capitulos=capitulos)
    if contenedor is None:
        raise ValueError(motivo or "esas pistas no se pueden copiar en un solo contenedor")
    nota = " + ".join([_nota_video(v) for v in vids] + [_nota_audio(a, elegido=True) for a in auds])
    return Decision(_uno_o_varios([v["id"] for v in vids]), _uno_o_varios([a["id"] for a in auds]),
                    contenedor, False, f"{nota} → {contenedor.upper()}", bytes_)


def plan(info: dict[str, Any], perfil: str, video_id: Any = None, audio_id: Any = None,
         destino: dict[str, Any] | None = None, capitulos: bool = False) -> Decision:
    """El plan de descarga: qué pistas y en qué contenedor, sin recodificar salvo ``convertir``.

    ``video_id``/``audio_id`` mandan sobre el perfil (es lo que el usuario marcó a mano en «Pistas del origen») y
    admiten **varias** pistas, como id suelto o como lista. Si no caben en ningún contenedor honesto, se lanza
    ``ValueError`` con el motivo: no se cambia el contenedor ni el códec en silencio.

    ``capitulos`` (marcas de capítulo dentro del fichero) fuerza MKV con las pistas elegidas a mano: MP4 no las
    lleva sin recodificar.

    ``destino`` solo tiene sentido con el perfil ``convertir``: ``{"vcod": "hevc", "acod": "aac", "altura": 720}``.
    Lo que se convierte **después** de bajar (``mpvd/ytdl/transcode.py``); sin él, ``convertir`` se queda como
    estaba (pistas del origen, recodificar a MKV) y la decisión fina la pone el catálogo del spec.
    """
    if perfil not in PERFILES:
        raise ValueError(f"perfil de descarga desconocido: {perfil}")
    duracion = _duracion(info)
    audios = _audios(info)

    # --- un solo fichero de audio, el del origen, sin tocar
    if perfil == "opus_origen":
        if audio_id:
            audio, aviso = _pista(info, lista_ids(audio_id)[0], "audio"), ""
        else:
            audio, aviso = _audio_defecto(audios)
        if audio is None:
            raise ValueError("este enlace no ofrece ninguna pista de audio utilizable")
        if audio["vfam"]:
            raise ValueError(f"la pista {audio['id']} es un vídeo: elige una pista de audio suelta")
        contenedor = CONTENEDOR_AUDIO.get(audio["afam"], "mkv")
        nota = _nota_audio(audio, elegido=bool(audio_id), aviso=aviso)
        if aviso:
            nota += f" · el fichero va en .{contenedor}, sin recodificar"
        return Decision(None, audio["id"], contenedor, False, nota, _bytes(audio, duracion) or None)

    # --- pistas elegidas a mano: se copian tal cual, contenedor por lo que se haya marcado
    if perfil == "pistas":
        return _pistas_a_mano(info, video_id, audio_id, duracion, capitulos)

    # --- perfiles de vídeo: el perfil elige y dice lo que ha elegido
    aviso = ""
    if perfil == "video_720":
        v = (_mejor_video(_separadas(info), ("vp9",), VIDEO_720)
             or _mejor_video(_separadas(info), ("h264",), VIDEO_720)
             or _mejor_video(_separadas(info), ("av1", "hevc", "otro"), VIDEO_720))
        a, aviso = _audio_defecto(audios)
        pedido, aac_obj = "mkv", False
    elif perfil == "compatible":
        v = (_mejor_video(_separadas(info), ("h264",), COMPATIBLE_MAX)
             or _mejor_video(_separadas(info), ("hevc", "vp9", "av1", "otro"), COMPATIBLE_MAX))
        a = _aac_cerca(audios)
        aac_obj = True
        if a is None:
            a, aviso = _audio_defecto(audios)
        pedido = "mp4"
    else:                                          # convertir: la conversión va después de bajar
        if destino:
            return _plan_conversion(info, destino, audios, duracion)
        v = _mejor_video(_separadas(info), ("vp9", "h264", "av1", "hevc", "otro"), None)
        a, aviso = _audio_defecto(audios)
        pedido, aac_obj = "mkv", False

    if v is None:
        # orígenes sin pistas separadas (archive.org): se entrega la progresiva, con su audio dentro
        completa = _mejor_video(_combinadas(info), ("h264", "vp9", "av1", "hevc", "otro"),
                                VIDEO_720 if perfil == "video_720" else COMPATIBLE_MAX)
        if completa is None:
            raise ValueError("este enlace no ofrece ninguna pista de vídeo utilizable")
        return _completa(completa)

    contenedor, motivo = contenedor_valido(v["vcod"], (a or {}).get("acod", "none"), pedido)
    recodificar = perfil == "convertir"
    if contenedor is None and perfil == "compatible":
        # el MP4 prometido no cabe en esta pareja: se entrega el contenedor honesto y se dice por qué. No se
        # recodifica para cuadrarlo ni se disfraza la extensión.
        contenedor = contenedor_valido(v["vcod"], (a or {}).get("acod", "none"))[0] or "mkv"
    if contenedor is None:
        if not recodificar:
            raise ValueError(motivo or "esa pareja no se puede copiar en el contenedor pedido")
        contenedor = "mkv"                          # convertir puede con todo: recodifica
    if a is None:
        nota = f"{_nota_video(v)} (sin pista de audio suelta) → {contenedor.upper()}"
    else:
        nota = f"{_nota_video(v)} + {_nota_audio(a, aac_objetivo=aac_obj, aviso=aviso)} → {contenedor.upper()}"
    if perfil == "compatible" and v["vfam"] != "h264":
        nota += " · el origen no ofrece H.264: no es el MP4 «compatible» de siempre"
    if perfil == "video_720" and v["altura"] > VIDEO_720:
        nota += f" · el origen no baja de {v['altura']}p"
    if motivo:
        nota += f" · {motivo}"
    if recodificar:
        nota += " · se recodifica"
    return Decision(v["id"], a["id"] if a else None, contenedor, recodificar, nota,
                    (_bytes(v, duracion) + _bytes(a, duracion)) or None)
