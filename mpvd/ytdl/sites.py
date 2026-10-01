"""Qué formas de URL de redes sociales sabe abrir el yt-dlp instalado, y qué decir cuando no (H37/D4).

El problema real: Ser pegó `https://www.instagram.com/<perfil>/saved/all-posts/` esperando bajarse sus guardados.
Ningún extractor de yt-dlp acepta esa URL, así que lo único honesto es decirlo antes de intentarlo, con la alternativa,
en vez de fallar con un error de yt-dlp. Y de paso avisar de los extractores que yt-dlp marca como rotos («CURRENTLY
BROKEN» en su propio `--list-extractors`), que es información del binario instalado, no una suposición nuestra.

Las formas se reconocen con expresiones propias (barato: ni red ni importar yt_dlp), y `tests/test_ytdl_sitios.py`
comprueba contra el binario de verdad —preguntando a cada extractor con `suitable()`— que lo que decimos aquí sigue
siendo cierto en la versión instalada. Si yt-dlp añade el extractor que falta, el test avisa.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mpvd.ytdl.binary import YtdlpBinary

BROKEN_RE = re.compile(r"^(?P<name>[^\s]+)\s+\(CURRENTLY BROKEN\)\s*$")


@dataclass(frozen=True)
class Shape:
    id: str
    pattern: re.Pattern[str]
    kind: str                 # saved | profile | collection
    extractor: str | None     # el que debería aceptarla (None = no existe ninguno)
    message: str              # qué se le dice a quien la pegó
    alternative: str = ""


SHAPES: tuple[Shape, ...] = (
    Shape("instagram_saved",
          re.compile(r"^https?://(www\.)?instagram\.com/[^/]+/saved(/|$)", re.I), "saved", None,
          "yt-dlp no sabe abrir los guardados de Instagram: no existe ningún extractor para esa página.",
          "Abre un guardado, copia su enlace y pégalo aquí: los vídeos sueltos sí funcionan."),
    Shape("tiktok_favorites",
          re.compile(r"^https?://(www\.)?tiktok\.com/(favorite|@[^/]+/favorite)(/|$)", re.I), "saved", None,
          "yt-dlp no sabe abrir los favoritos de TikTok: no existe ningún extractor para esa página.",
          "Pega el enlace de cada vídeo, o el del perfil, o el de una colección pública."),
    Shape("instagram_profile",
          re.compile(r"^https?://(www\.)?instagram\.com/[^/]+/?$", re.I), "profile", "instagram:user",
          "Un perfil entero de Instagram.",
          "Si falla, pega los enlaces de los vídeos: no es culpa tuya."),
    Shape("tiktok_collection",
          re.compile(r"^https?://(www\.)?tiktok\.com/@[^/]+/collection/", re.I), "collection", "tiktok:collection",
          "Una colección de TikTok.", ""),
    Shape("tiktok_profile",
          re.compile(r"^https?://(www\.)?tiktok\.com/@[^/]+/?$", re.I), "profile", "tiktok:user",
          "Un perfil entero de TikTok.", ""),
)

PRIVATE_HINT = "Para lo que sea privado hace falta «Usar mi sesión del navegador» (cookies), que está apagado."


def shape_for(url: str) -> Shape | None:
    for shape in SHAPES:
        if shape.pattern.match(url.strip()):
            return shape
    return None


def broken_extractors(binary: YtdlpBinary, timeout: float = 30.0) -> set[str]:
    """Los que el propio yt-dlp instalado marca «CURRENTLY BROKEN» en `--list-extractors`."""
    try:
        out = subprocess.run([*binary.argv, "--list-extractors"], capture_output=True, text=True,
                             timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {m.group("name") for m in (BROKEN_RE.match(line) for line in out.splitlines()) if m}


def describe(url: str, broken: set[str] | None = None) -> dict[str, object]:
    """Qué se puede hacer con esa URL: ``{supported, kind, extractor, message, alternative, warning, private}``.

    ``supported`` False solo cuando NO hay extractor: un extractor roto se ofrece igual (puede funcionar, y yt-dlp se
    actualiza solo), pero con el aviso delante.
    """
    shape = shape_for(url)
    if shape is None:
        return {"supported": True, "kind": "video", "extractor": None, "message": "", "alternative": "",
                "warning": "", "private": False}
    roto = bool(shape.extractor and (broken or set()) and shape.extractor in (broken or set()))
    return {
        "supported": shape.extractor is not None,
        "kind": shape.kind,
        "extractor": shape.extractor,
        "message": shape.message,
        "alternative": shape.alternative,
        "warning": (f"yt-dlp marca «{shape.extractor}» como roto en esta versión: puede fallar." if roto else ""),
        "private": shape.kind in ("profile", "collection"),
    }
