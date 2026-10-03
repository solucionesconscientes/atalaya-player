"""The app's language in mpvd (H49, ADR-087).

The Spanish string is the key, like in ``mu/i18n.lua``: ``t("No se pudo descargar")``. Spanish needs no catalogue
(it is the identity) and anything missing falls back to it, so the worst case is "it shows in Spanish" — never a raw
key or an empty gap.

Two different questions, answered in two different places:

* **The player's language** is the machine's, and ``bin/mpv-uos`` already resolved it from the environment. mpvd
  works it out the same way and, on Windows, where the POSIX variables are usually absent, asks ``locale``.
* **The language of the pages mpvd serves** (the room, the phone remote, the downloads panel) is the *visitor's*,
  taken from their ``Accept-Language``: someone else opens them, possibly on a device in another language. That is
  ``from_accept_language``.
"""

from __future__ import annotations

import json
import locale
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from mpvd.config import project_root

SUPPORTED = ("es", "en", "fr")
DEFAULT = "en"          # Ser's rule: Spanish or French → that one; English or anything else → English
_TAG = re.compile(r"\s*([A-Za-z]{2})")


def from_locale(value: str | None) -> str:
    """A locale as the environment writes it (``fr_CA.UTF-8``, ``es``, ``en_GB``) → the language we serve."""
    m = _TAG.match(value or "")
    code = m.group(1).lower() if m else ""
    return code if code in ("es", "fr") else DEFAULT


def system_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value and value not in ("C", "POSIX"):
            return from_locale(value)
    # Windows and macOS do not always set those; the C library knows anyway
    try:
        loc = locale.getlocale()[0] or ""
    except (ValueError, TypeError):           # pragma: no cover - a broken locale must not stop the daemon
        loc = ""
    return from_locale(loc)


def from_accept_language(header: str | None) -> str:
    """The best language for a visitor's ``Accept-Language``, honouring the q values.

    Only our three count: a browser asking for ``de`` gets English, which is the rule."""
    best, best_q = DEFAULT, -1.0
    for part in (header or "").split(","):
        tag, _, params = part.strip().partition(";")
        m = _TAG.match(tag)
        if not m:
            continue
        code = m.group(1).lower()
        if code not in SUPPORTED:
            continue
        q = 1.0
        if "q=" in params:
            try:
                q = float(params.split("q=", 1)[1])
            except ValueError:
                q = 0.0
        if q > best_q:
            best, best_q = code, q
    return best


@lru_cache(maxsize=4)
def catalogue(lang: str) -> dict[str, str]:
    if lang == "es":
        return {}
    root = project_root()
    try:
        data: Any = json.loads((root / "locales" / f"{lang}.json").read_text(encoding="utf-8")) if root else {}
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v} \
        if isinstance(data, dict) else {}


def t(text: str, lang: str | None = None) -> str:
    """The translation, or the Spanish string when there is none —including when the entry exists but is empty,
    which is how the extractor leaves a string it has just collected (same rule as `mu/i18n.lua`)."""
    return catalogue(lang or system_language()).get(text) or text

# -- las páginas servidas (H49/G6) ------------------------------------------------------------------------------
# La sala, el mando y el panel de descargas los abre OTRA PERSONA en SU navegador, así que su idioma no es el de
# este equipo sino el que pide el navegador (`Accept-Language`). El servidor le mete en la página solo las cadenas
# que esa página usa —no el catálogo entero, que son 63 KB— y el `t()` del JavaScript las busca ahí.
PAGE_T = re.compile(r"\bt\(\s*'((?:[^'\\]|\\.)*)'")
# y el texto que ya está escrito en el HTML: el de cada etiqueta y los atributos que una persona lee
PAGE_SKIP = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
PAGE_TEXT = re.compile(r">([^<>]+)<")
PAGE_ATTR = re.compile(r"\b(?:placeholder|title|aria-label|alt)\s*=\s*\"([^\"]+)\"", re.I)
PAGE_WORD = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{2}")
PAGE_RUTA = re.compile(r"^\S*(?://|/|\.[a-z])\S*$")   # https://…, tools/install.sh, mpv-uos:// → no son frases


def html_keys(src: str) -> set[str]:
    """El texto traducible de una página: lo que hay entre etiquetas y los atributos que se leen.

    El mismo criterio lo usan el extractor (`tools/i18n_extract_web.py`) y el servidor, así que lo que se recoge
    para traducir y lo que se le inyecta a la página son exactamente lo mismo."""
    limpio = PAGE_SKIP.sub(" ", src)
    out: set[str] = set()
    for m in list(PAGE_TEXT.finditer(limpio)) + list(PAGE_ATTR.finditer(limpio)):
        text = " ".join(m.group(1).split())   # la indentación del HTML no debe cambiar la clave
        if text and PAGE_WORD.search(text) and not PAGE_RUTA.match(text) and "&" not in text:
            out.add(text)
    return out


@lru_cache(maxsize=8)
def page_keys(www: str) -> frozenset[str]:
    """Las cadenas de una carpeta de páginas: los `t('…')` de sus .js y el texto de sus .html."""
    out: set[str] = set()
    carpeta = Path(www)
    for f in sorted(carpeta.glob("*.js")):
        if f.name.endswith(".min.js"):
            continue
        try:
            src = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in PAGE_T.finditer(src):
            out.add(m.group(1).replace("\\'", "'").replace("\\\\", "\\"))
    for f in sorted(carpeta.glob("*.html")):
        try:
            out |= html_keys(f.read_text(encoding="utf-8"))
        except OSError:
            continue
    return frozenset(out)


def page_catalogue(www: str | Path, header: str | None) -> str:
    """El JSON que se le da a la página, para el idioma que pide su navegador («{}» en castellano)."""
    lang = from_accept_language(header)
    if lang == "es":
        return "{}"
    cat = catalogue(lang)
    usadas = {k: cat[k] for k in page_keys(str(www)) if k in cat}
    return json.dumps(usadas, ensure_ascii=False, separators=(",", ":"))


def page_script(www: str | Path, header: str | None) -> bytes:
    """El `/i18n.js` que carga la página: su catálogo y, detrás, el `t()` y el paso que traduce el HTML.

    Es un fichero aparte y no un `<script>` dentro del HTML porque la sala se sirve con `script-src 'self'`, que
    bloquea lo que va en línea **sin avisar** (no salta ni `window.onerror`): la página se quedaba muda y sin
    traducir. Lo caro —leer el .js de la plantilla— lo hace el sistema de ficheros, que lo tiene en caché."""
    from mpvd.brand import app_name     # noqa: PLC0415 - evita un import circular al cargar el módulo

    plantilla = (Path(__file__).resolve().parent / "i18n_page.js").read_text(encoding="utf-8")
    texto = f"window.MU_T = {page_catalogue(www, header)};\n{plantilla}"
    if app_name() != "MPV-UOS":
        texto = texto.replace("MPV-UOS", app_name())
    return texto.encode("utf-8")
