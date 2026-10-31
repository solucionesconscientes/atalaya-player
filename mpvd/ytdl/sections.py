"""Fragmentos de tiempo: descargar solo lo que interesa y unirlo.

Tres cosas, y una que hay que respetar aunque no guste:

* ``parsear(expr)`` — uno o varios rangos a la vez: ``00:10-02:30``, ``1:00:00-1:05:00``, abierto por delante
  (``00:10-``) o por detrás (``-02:30``), y negativos contados desde el final (``-00:30-inf`` = los últimos 30 s).
  Lleva **margen de cola** (``MARGEN_COLA``, 0,3 s por defecto) para no cortar la última sílaba: se suma al final del
  rango, nunca al principio.
* ``args_rangos(rangos)`` — un ``--download-sections`` por rango: yt-dlp los acepta repetidos en una sola pasada y
  escribe un fichero por rango.
* ``precision(vcodec, acodec)`` — el aviso que hay que enseñar antes de cortar. Medido en esta máquina: con VP9/Opus
  el corte por copia se va (16 s donde se pidieron 6); con H.264+AAC va a ±0,03 s. Por eso los fragmentos usan por
  defecto H.264+AAC (perfil ``compatible``, ``PERFIL_FRAGMENTOS``) salvo que el usuario pida otra cosa.

La unión de los trozos está en ``mpvd.ytdl.chapters.unir``: el concat demuxer y la comprobación con ffprobe son los
mismos para capítulos y para fragmentos, y tenerlo en un solo sitio evita dos maneras de unir que se desvíen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from mpvd.i18n import t

MARGEN_COLA = 0.3                 # s de cola: la última sílaba se queda dentro
PERFIL_FRAGMENTOS = "compatible"  # H.264 + AAC: el corte por copia es exacto con esta pareja
INF = float("inf")

# Un rango: `A-B`, con A y B en segundos, mm:ss o hh:mm:ss, negativos desde el final, o `inf` al final.
_NUM = r"(?:\d+(?::\d+){0,2}(?:\.\d+)?)"
_TOKEN = re.compile(rf"^(?P<a>-?{_NUM})?-(?P<b>-?{_NUM}|inf)?$", re.IGNORECASE)


@dataclass(frozen=True)
class Rango:
    """Un fragmento pedido: ``inicio`` y ``fin`` en segundos (``inf`` = hasta el final, negativo = desde el final)."""

    inicio: float
    fin: float
    cola: float = MARGEN_COLA
    crudo: str = ""            # el rango tal cual lo escribió quien lo pidió (sin margen se pasa igual)

    def fin_con_cola(self) -> float:
        return self.fin if self.fin == INF else self.fin + self.cola

    def texto(self) -> str:
        """Lo que se le pasa a ``--download-sections``: ``*inicio-fin`` con el margen ya sumado.

        Sin margen se devuelve el rango tal cual se escribió: así quien ya lo tenía calculado (la grabación de lo que
        se está viendo) recibe exactamente lo que pidió y no un número reescrito por el camino.
        """
        if self.cola <= 0 and self.crudo:
            return "*" + self.crudo.lstrip("*")
        return "*%s-%s" % (_tiempo(self.inicio), _tiempo(self.fin_con_cola()))

    def absoluto(self, duracion: float) -> tuple[float, float]:
        """Inicio y fin reales del vídeo (los negativos se cuentan desde el final; ``inf`` es la duración)."""
        ini = duracion + self.inicio if self.inicio < 0 else self.inicio
        fin = duracion if self.fin == INF else (duracion + self.fin if self.fin < 0 else self.fin)
        return max(0.0, ini), max(0.0, fin)

    def duracion(self, duracion_video: float | None = None) -> float:
        """Lo que dura el fragmento. Sin la duración del vídeo no se puede resolver un ``inf``: devuelve 0."""
        if self.fin == INF:
            if duracion_video is None:
                return 0.0
            ini, fin = self.absoluto(duracion_video)
            return max(0.0, fin - ini) + self.cola
        return max(0.0, self.fin - self.inicio) + self.cola

    def to_dict(self, duracion_video: float | None = None) -> dict[str, Any]:
        return {"inicio": self.inicio, "fin": self.fin, "cola": self.cola, "texto": self.texto(),
                "duracion": round(self.duracion(duracion_video), 3)}


def _tiempo(seg: float) -> str:
    if seg == INF:
        return "inf"
    return f"{seg:.3f}"


def _segundos(texto: str) -> float:
    """``12``, ``12.5``, ``01:30``, ``1:00:05.250`` o ``-00:30`` → segundos (sin perder el signo)."""
    s = texto.strip().lower()
    negativo = s.startswith("-")
    if negativo:
        s = s[1:]
    if s == "inf":
        return INF
    if ":" in s:
        partes = s.split(":")
        if len(partes) > 3:
            raise ValueError(t("%s no es un tiempo: usa mm:ss o hh:mm:ss") % texto)
        total = 0.0
        for p in partes:
            total = total * 60 + float(p)
    else:
        total = float(s)
    return -total if negativo else total


def parsear(expr: str | Sequence[str] | None, cola: float = MARGEN_COLA) -> list[Rango]:
    """Los rangos de ``expr``, ya normalizados y con el margen de cola.

    ``expr`` puede ser un texto con uno o varios rangos separados por comas o saltos de línea, o la lista de trozos
    ya partida. Falla con el motivo si un rango está al revés o mezcla «desde el principio» con «desde el final»:
    un rango imposible descargado tal cual es un fichero de 0 s que parece un fallo de la red.
    """
    if expr is None:
        return []
    trozos: list[str] = []
    for parte in ([expr] if isinstance(expr, str) else list(expr)):
        trozos += [p for p in re.split(r"[,;\n]+", str(parte)) if p.strip()]
    if not trozos:
        return []
    rangos: list[Rango] = []
    for crudo in trozos:
        token = crudo.strip().lstrip("*").strip()
        m = _TOKEN.match(token)
        if not m or (not m.group("a") and not m.group("b")):
            raise ValueError(t("%s no es un rango de tiempo: usa 00:10-02:30") % crudo.strip())
        inicio = _segundos(m.group("a")) if m.group("a") else 0.0
        fin = _segundos(m.group("b")) if m.group("b") else INF
        if fin != INF and ((fin < 0 and inicio >= 0) or (inicio < 0 and fin >= 0)):
            raise ValueError(t("%s mezcla el principio y el final del vídeo en un mismo rango") % crudo.strip())
        if fin != INF and fin <= inicio:
            raise ValueError(t("en %s el final va antes que el principio") % crudo.strip())
        rangos.append(Rango(round(inicio, 3), fin if fin == INF else round(fin, 3), float(cola), token))
    return rangos


def args_rangos(rangos: Iterable[Rango]) -> list[str]:
    """Un ``--download-sections`` por rango (yt-dlp los acepta repetidos y escribe un fichero por rango)."""
    args: list[str] = []
    for r in rangos:
        args += ["--download-sections", r.texto()]
    return args


def total(rangos: Sequence[Rango], duracion_video: float | None = None) -> float:
    """Cuánto suman los fragmentos, en segundos (para la ficha previa y para estimar el tamaño)."""
    return round(sum(r.duracion(duracion_video) for r in rangos), 3)


# ------------------------------------------------------------------ precisión del corte

_VP9 = ("vp9", "vp09")
_AV1 = ("av01", "av1")
_H264 = ("avc1", "avc3", "h264")
_AAC = ("mp4a", "aac")
_OPUS = ("opus",)


def _plano(codec: str | None) -> str:
    c = str(codec or "").strip().lower()
    return "" if c in ("none", "null", "na") else c.replace(".", "").replace("-", "")


def es_exacto(vcodec: str | None, acodec: str | None) -> bool:
    """¿El corte por copia cae donde se pidió? Solo con H.264 (o H.265) + AAC.

    Con VP9/AV1/Opus el corte va por fotograma clave al principio y arrastra cola: medido 16 s donde se pidieron 6.
    """
    v, a = _plano(vcodec), _plano(acodec)
    video_ok = not v or v.startswith(_H264) or v.startswith(("hevc", "h265", "hev1", "hvc1"))
    audio_ok = not a or a.startswith(_AAC) or a.startswith("ac3") or a.startswith("eac3")
    return bool(video_ok and audio_ok)


def precision(vcodec: str | None, acodec: str | None) -> str:
    """El aviso, en palabras llanas, de lo exacto que va a salir el corte con esos códecs.

    Se enseña **antes** de descargar: con VP9/Opus el usuario tiene que saber que el trozo puede salir bastante más
    largo, o lo tomará por un fallo.
    """
    v, a = _plano(vcodec), _plano(acodec)
    if es_exacto(vcodec, acodec):
        return t("el corte es exacto (±0,03 s): H.264 + AAC se copian al fotograma clave de cada corte")
    if v.startswith(_VP9) or v.startswith(_AV1) or a.startswith(_OPUS):
        return t("con VP9/Opus el corte por copia se va: el trozo puede salir hasta 10 s más largo de lo pedido (medido: 16 s donde se pidieron 6). Para fragmentos exactos, H.264 + AAC («compatible»)")
    return t("el corte va al fotograma clave más cercano: puede sobrar hasta un segundo o dos de imagen")
