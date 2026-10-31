"""Una sola verdad para decidir **qué pistas** se le piden a yt-dlp.

Portado de la política ya probada del bot (``~/.local/share/ytdl/ytdlbot/formats.py``: ``plan()``,
``elegir_audio()``, ``es_musica()``, ``_orden_video()``). Reglas que no se saltan:

* **El defecto no recodifica.** Convertir es una acción aparte (perfil ``convertir``), con su nombre.
* **A igual altura gana el bitrate más bajo**: para guardar, un 720p a 1,5 Mbps es mejor que uno a 2,5.
* **Voz y música no se tratan igual**: en voz el Opus más bajo del origen es indistinguible y ahorra la mitad;
  en música el más alto es el único que conserva detalle.
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
CONTENEDOR_AUDIO = {"opus": "opus", "aac": "m4a", "vorbis": "webm", "mp3": "mp4", "flac": "mkv"}
CON_VIDEO = (("h264", "aac", "mp4"), ("hevc", "aac", "mp4"), ("h264", "opus", "mkv"), ("hevc", "opus", "mkv"),
             ("vp9", "aac", "mkv"), ("vp9", "opus", "mkv"), ("av1", "aac", "mkv"), ("av1", "opus", "mkv"))


@dataclass(frozen=True)
class Decision:
    """Lo que se va a pedir: dos pistas (o una) y un contenedor, sin recodificar salvo que se diga."""

    video_id: str | None
    audio_id: str | None
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
        "altura": _int(f.get("height")), "fps": _num(f.get("fps")),
        "tbr": _num(f.get("tbr")), "abr": _num(f.get("abr")),
        "bytes": _int(f.get("filesize")) or _int(f.get("filesize_approx")),
        "idioma": str(f.get("language") or f.get("idioma") or ""),
        "ext": str(f.get("ext") or ""),
        "canales": _int(f.get("audio_channels") or f.get("channels")),
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


# ------------------------------------------------------------------ clasificación y pistas

def clasificar(info: dict[str, Any]) -> str:
    """``musica`` si el origen lo dice (categoría con «music», o ``track``/``artist``); si no, ``voz``.

    La misma regla que ``es_musica()`` del bot: no se adivina por el título ni por la duración.
    """
    if not isinstance(info, dict):
        return "voz"
    cats = info.get("categories")
    if cats is None:
        cats = info.get("categorias")
    texto = " ".join(str(c) for c in cats) if isinstance(cats, (list, tuple)) else str(cats or "")
    if "music" in texto.lower():
        return "musica"
    if info.get("track") or info.get("artist"):
        return "musica"
    return "voz"


def pistas(info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Lo que ofrece el origen, listo para pintar: vídeo por altura y audio por bitrate.

    Cada fila: ``id``, ``codec``, ``familia``, ``altura``/``fps`` (solo vídeo), ``kbps``, ``bytes``, ``idioma``,
    ``ext`` y ``canales`` (solo audio). Los bytes son los que declara el origen; no se estiman con la duración
    porque quien pinta la ficha ya la conoce.
    """
    orden_v = {"vp9": 0, "h264": 1, "av1": 2, "hevc": 3, "otro": 4}
    vids = _separadas(info) + _combinadas(info)
    vid = sorted(vids, key=lambda f: (-f["altura"], orden_v.get(f["vfam"], 9), -f["fps"], -f["bytes"], f["tbr"]))
    aud = sorted(_audios(info), key=lambda f: ({"opus": 0, "aac": 1}.get(f["afam"], 9), -_kbps(f), f["idioma"]))
    return {
        "video": [{"id": f["id"], "codec": NOMBRE.get(f["vfam"], f["vfam"]), "familia": f["vfam"],
                   "altura": f["altura"], "fps": int(f["fps"]), "kbps": int(f["tbr"]), "bytes": f["bytes"],
                   "idioma": f["idioma"], "ext": f["ext"], "con_audio": bool(f["afam"])} for f in vid],
        "audio": [{"id": f["id"], "codec": NOMBRE.get(f["afam"], f["afam"]), "familia": f["afam"],
                   "kbps": _kbps(f), "canales": f["canales"], "bytes": f["bytes"],
                   "idioma": f["idioma"], "ext": f["ext"]} for f in aud],
    }


# ------------------------------------------------------------------ contenedor por pareja

def contenedor_valido(vcodec: str, acodec: str, pedido: str | None = None) -> tuple[str | None, str | None]:
    """``(contenedor, None)`` si la pareja se puede copiar tal cual; ``(None, motivo)`` si no.

    Sin ``pedido`` devuelve el contenedor honesto de la pareja. Con ``pedido``, ese si lo admite y, si no, se
    rechaza con el motivo en castellano: el contenedor nunca se cambia en silencio.
    """
    vfam, afam = _familia_video(vcodec), _familia_audio(acodec)
    pedido = (pedido or "").strip().lower() or None

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


def _mejor_opus(audios: list[dict[str, Any]], musica: bool) -> dict[str, Any] | None:
    """Voz → el Opus más bajo del origen; música → el más alto. A igualdad, el estéreo."""
    opus = [f for f in audios if f["afam"] == "opus"]
    if not opus:
        return None
    clave = lambda f: (_kbps(f), -f["canales"])     # noqa: E731 - una línea
    return (max if musica else min)(opus, key=clave)


def _audio_general(audios: list[dict[str, Any]], musica: bool) -> dict[str, Any] | None:
    """Sin Opus en el origen: se coge lo que haya y se dice en la nota (no se disimula)."""
    if not audios:
        return None
    return sorted(audios, key=lambda f: (_kbps(f), f["canales"]), reverse=musica)[0]


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


def _nota_audio(f: dict[str, Any] | None, tipo: str, elegido: bool = False, aac_objetivo: bool = False) -> str:
    if f is None:
        return "sin audio"
    nombre = NOMBRE.get(f["afam"], f["afam"])
    if elegido:
        return f"{nombre} {_kbps(f)}k del origen, copiado tal cual"
    if f["afam"] == "opus":
        return (f"Opus del origen, el mejor bitrate (música): {_kbps(f)}k" if tipo == "musica"
                else f"Opus del origen, el bitrate más bajo (voz): {_kbps(f)}k")
    if aac_objetivo and f["afam"] == "aac":
        return f"{nombre} del origen, el más cercano a {AAC_OBJETIVO}k: {_kbps(f)}k"
    return f"el origen no trae Opus: se entrega {nombre} {_kbps(f)}k"


def _nota_video(f: dict[str, Any] | None) -> str:
    if f is None:
        return "sin vídeo"
    nombre = NOMBRE.get(f["vfam"], f["vfam"])
    return f"{nombre} {f['altura']}p {int(f['tbr'])}k" if f["altura"] else f"{nombre} {int(f['tbr'])}k"


def _completa(f: dict[str, Any], tipo: str) -> Decision:
    """Una pista progresiva: el audio viaja dentro y no se puede sustituir (archive.org y compañía)."""
    contenedor = f["ext"] if f["ext"] in ("mp4", "webm", "mkv") else "mkv"
    return Decision(f["id"], None, contenedor, False,
                    f"{_nota_video(f)} con el audio dentro ({NOMBRE.get(f['afam'], f['afam'])}), copiada tal cual",
                    _bytes(f) or None)


# ------------------------------------------------------------------ el plan

def plan(info: dict[str, Any], perfil: str, video_id: str | None = None,
         audio_id: str | None = None) -> Decision:
    """El plan de descarga: qué dos pistas y en qué contenedor, sin recodificar salvo ``convertir``.

    ``video_id``/``audio_id`` mandan sobre el perfil (es lo que el usuario marcó a mano en «Pistas del
    origen»). Si la pareja no cabe en ningún contenedor honesto, se lanza ``ValueError`` con el motivo: no se
    cambia el contenedor ni el códec en silencio.
    """
    if perfil not in PERFILES:
        raise ValueError(f"perfil de descarga desconocido: {perfil}")
    tipo = clasificar(info)
    musica = tipo == "musica"
    duracion = _duracion(info)
    audios = _audios(info)

    # --- un solo fichero de audio, el del origen, sin tocar
    if perfil == "opus_origen":
        audio = (_pista(info, audio_id, "audio") if audio_id
                 else _mejor_opus(audios, musica) or _audio_general(audios, musica))
        if audio is None:
            raise ValueError("este enlace no ofrece ninguna pista de audio utilizable")
        if audio["vfam"]:
            raise ValueError(f"la pista {audio['id']} es un vídeo: elige una pista de audio suelta")
        contenedor = CONTENEDOR_AUDIO.get(audio["afam"], "mkv")
        nota = _nota_audio(audio, tipo, elegido=bool(audio_id))
        if audio["afam"] != "opus" and not audio_id:
            nota += f" · no hay Opus: el fichero va en .{contenedor}, sin recodificar"
        return Decision(None, audio["id"], contenedor, False, nota, _bytes(audio, duracion) or None)

    # --- pistas elegidas a mano: se copian tal cual, contenedor por pareja
    if perfil == "pistas":
        v = _pista(info, video_id, "vídeo") if video_id else None
        a = _pista(info, audio_id, "audio") if audio_id else None
        if v is not None and not v["vfam"]:
            raise ValueError(f"la pista {v['id']} es de audio, no de vídeo")
        if a is not None and (a["vfam"] or not a["afam"]):
            raise ValueError(f"la pista {a['id']} no es una pista de audio suelta")
        if v is None and a is None:
            raise ValueError("«pistas» necesita al menos una pista de vídeo o de audio")
        if v is None:
            contenedor = CONTENEDOR_AUDIO.get(a["afam"], "mkv")            # type: ignore[index]
            return Decision(None, a["id"], contenedor, False,                # type: ignore[index]
                            _nota_audio(a, tipo, elegido=True), _bytes(a, duracion) or None)  # type: ignore[arg-type]
        if v["afam"]:                              # pista progresiva: ya trae su audio
            if a is not None:
                raise ValueError(f"la pista {v['id']} ya trae su propio audio: elige una de vídeo sin audio "
                                 "para combinar, o quita la de audio")
            return _completa(v, tipo)
        if a is None:
            a = _mejor_opus(audios, musica) or _audio_general(audios, musica)
        if a is None:
            raise ValueError("el origen no ofrece ninguna pista de audio para acompañar al vídeo")
        contenedor, motivo = contenedor_valido(v["vcod"], a["acod"])
        if contenedor is None:
            raise ValueError(motivo or "esa pareja de pistas no se puede copiar en un solo contenedor")
        nota = f"{_nota_video(v)} + {_nota_audio(a, tipo, elegido=True)} → {contenedor.upper()}"
        return Decision(v["id"], a["id"], contenedor, False, nota,
                        (_bytes(v, duracion) + _bytes(a, duracion)) or None)

    # --- perfiles de vídeo: el perfil elige y dice lo que ha elegido
    if perfil == "video_720":
        v = (_mejor_video(_separadas(info), ("vp9",), VIDEO_720)
             or _mejor_video(_separadas(info), ("h264",), VIDEO_720)
             or _mejor_video(_separadas(info), ("av1", "hevc", "otro"), VIDEO_720))
        a = _mejor_opus(audios, musica) or _audio_general(audios, musica)
        pedido, aac_obj = "mkv", False
    elif perfil == "compatible":
        v = (_mejor_video(_separadas(info), ("h264",), COMPATIBLE_MAX)
             or _mejor_video(_separadas(info), ("hevc", "vp9", "av1", "otro"), COMPATIBLE_MAX))
        a = _aac_cerca(audios) or _mejor_opus(audios, musica) or _audio_general(audios, musica)
        pedido, aac_obj = "mp4", True
    else:                                          # convertir: la conversión la hace la fase siguiente
        v = _mejor_video(_separadas(info), ("vp9", "h264", "av1", "hevc", "otro"), None)
        a = _mejor_opus(audios, musica) or _audio_general(audios, musica)
        pedido, aac_obj = "mkv", False

    if v is None:
        # orígenes sin pistas separadas (archive.org): se entrega la progresiva, con su audio dentro
        completa = _mejor_video(_combinadas(info), ("h264", "vp9", "av1", "hevc", "otro"),
                                VIDEO_720 if perfil == "video_720" else COMPATIBLE_MAX)
        if completa is None:
            raise ValueError("este enlace no ofrece ninguna pista de vídeo utilizable")
        return _completa(completa, tipo)

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
        nota = f"{_nota_video(v)} + {_nota_audio(a, tipo, aac_objetivo=aac_obj)} → {contenedor.upper()}"
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
