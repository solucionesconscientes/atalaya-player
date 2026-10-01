"""Display labels for TV/radio in Spanish: country names, iptv-org categories, group titles, stream quality
and FAST (ad-supported) copies. Pure functions over the raw channel data; the raw values are never changed
(filters, favourites and the MCP/remote APIs keep using them)."""

from __future__ import annotations

import gettext
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mpvd.iptv.index import normalize

# -- countries ----------------------------------------------------------------------------------

# System iso-codes (Debian/Ubuntu/Fedora package "iso-codes"): names in English + gettext catalogues.
ISO_DIRS = [Path("/usr/share"), Path("/usr/local/share")]
BUNDLED_COUNTRIES = Path(__file__).with_name("data") / "countries_es.json"  # same names, for systems without iso-codes

# Codes used by the sources that are not ISO 3166-1 alpha-2 (iptv-org uses "uk" and "xk").
COUNTRY_ALIASES = {"uk": "gb"}
# Official names that read badly in a menu ("Congo, República Democrática del").
COUNTRY_OVERRIDES = {
    "ax": "Islas Åland",
    "bq": "Caribe Neerlandés",
    "cc": "Islas Cocos",
    "cd": "República Democrática del Congo",
    "ci": "Costa de Marfil",
    "fk": "Islas Malvinas",
    "fm": "Micronesia",
    "km": "Comoras",
    "ps": "Palestina",
    "ru": "Rusia",
    "va": "Ciudad del Vaticano",
    "vg": "Islas Vírgenes Británicas",
    "vi": "Islas Vírgenes de EE. UU.",
    "xk": "Kosovo",
}


def _tidy(name: str) -> str:
    """"Micronesia, Estados Federados de" -> "Estados Federados de Micronesia"; other "A, B" -> "A"."""
    if ", " not in name:
        return name
    head, _, tail = name.partition(", ")
    if tail.endswith((" de", " del")):
        return f"{tail} {head}"
    return head


def system_country_names(iso_dirs: list[Path] | None = None, lang: str = "es") -> dict[str, str]:
    """ISO2 (lower) -> name translated with the system iso-codes; empty when not installed."""
    for base in iso_dirs or ISO_DIRS:
        path = base / "iso-codes" / "json" / "iso_3166-1.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["3166-1"]
            tr = gettext.translation("iso_3166-1", localedir=str(base / "locale"), languages=[lang])
        except (OSError, ValueError, KeyError):
            continue
        out: dict[str, str] = {}
        for r in rows:
            # the short common name ("Bolivia") beats the official one ("Bolivia, Plurinational State of")
            source_name = r.get("common_name") or r.get("name") or ""
            name = tr.gettext(source_name) if source_name else ""
            if r.get("alpha_2") and name:
                out[r["alpha_2"].lower()] = _tidy(name)
        if out:
            return out
    return {}


@lru_cache(maxsize=1)
def country_names() -> dict[str, str]:
    names = system_country_names()
    if not names:
        try:
            names = json.loads(BUNDLED_COUNTRIES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            names = {}
    return {**names, **COUNTRY_OVERRIDES}


def country_code(code: str | None) -> str:
    c = (code or "").strip().lower()
    return COUNTRY_ALIASES.get(c, c)


def country_name(code: str | None, fallback: str | None = None) -> str:
    c = country_code(code)
    return country_names().get(c) or fallback or c.upper()


def flag(code: str | None) -> str:
    """Emoji flag from the regional indicator symbols (works for every alpha-2 code)."""
    c = country_code(code).upper()
    if len(c) != 2 or not c.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in c)


# Language -> country when the locale has no territory ("es" alone): the language's home country.
_LANG_COUNTRY = {"es": "es", "ca": "es", "gl": "es", "eu": "es", "ast": "es", "pt": "pt", "fr": "fr", "de": "de",
                 "it": "it", "en": "gb", "nl": "nl", "pl": "pl", "ro": "ro", "el": "gr", "sv": "se", "da": "dk",
                 "ja": "jp", "zh": "cn", "ko": "kr", "ru": "ru", "uk": "ua", "ar": "sa", "tr": "tr"}


def user_country(env: dict[str, str] | None = None) -> str:
    """Country shown first in the world lists: MPV_UOS_COUNTRY, else the locale's territory, else the
    home country of the locale's language, else Spain (the interface is in Spanish)."""
    env = os.environ if env is None else env
    forced = (env.get("MPV_UOS_COUNTRY") or "").strip().lower()
    if forced:
        return country_code(forced)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = (env.get(var) or "").split(":", 1)[0]
        m = re.match(r"^([a-z]{2,3})(?:_([A-Za-z]{2}))?", value)
        if not m or m.group(1) in ("c", "posix"):
            continue
        if m.group(2):
            return country_code(m.group(2))
        if m.group(1) in _LANG_COUNTRY:
            return _LANG_COUNTRY[m.group(1)]
    return "es"


# -- categories and groups -------------------------------------------------------------------------

# iptv-org categories (https://iptv-org.github.io/api/categories.json), by lower-case English name.
CATEGORIES_ES = {
    "animation": "Animación", "auto": "Motor", "business": "Negocios", "classic": "Clásicos", "comedy": "Comedia",
    "cooking": "Cocina", "culture": "Cultura", "documentary": "Documentales", "education": "Educación",
    "entertainment": "Entretenimiento", "family": "Familia", "general": "General", "interactive": "Interactivos",
    "kids": "Infantil", "legislative": "Parlamentos", "lifestyle": "Estilo de vida", "movies": "Cine",
    "music": "Música", "news": "Noticias", "outdoor": "Aire libre", "public": "Público", "relax": "Relax",
    "religious": "Religión", "science": "Ciencia", "series": "Series", "shop": "Teletienda", "sports": "Deportes",
    "travel": "Viajes", "weather": "El tiempo", "xxx": "Adultos", "undefined": "Sin categoría",
}


def category_label(category: str | None) -> str | None:
    if not category:
        return None
    c = category.strip()
    return CATEGORIES_ES.get(c.lower(), c)


def group_label(group: str | None) -> str | None:
    """"General;Public" -> "General · Público"; "Radio_C. Valenciana" -> "Radio C. Valenciana"."""
    if not group:
        return None
    parts = [category_label(p.replace("_", " ").strip()) for p in group.split(";")]
    return " · ".join(p for p in parts if p) or group


# -- stream quality ----------------------------------------------------------------------------------

LOW_BITRATE_HD = 1_600_000  # bit/s: an HD (≥720p) variant below this looks worse than good SD


def _mbps(bps: float) -> str:
    return f"{bps / 1_000_000:.1f}".replace(".", ",") + " Mb"


def quality_label(q: dict[str, Any] | None) -> str | None:
    """{"height": 720, "fps": 50, "bandwidth": 2_700_000} -> "720p50 · 2,7 Mb"."""
    if not q:
        return None
    parts = []
    height = q.get("height")
    if height:
        fps = q.get("fps")
        parts.append(f"{int(height)}p{round(fps) if fps else ''}")
    bandwidth = q.get("bandwidth")
    if bandwidth:
        parts.append(_mbps(bandwidth) if height or bandwidth >= 1_000_000 else f"{round(bandwidth / 1000)} kb")
    return " · ".join(parts) or None


def low_bitrate(q: dict[str, Any] | None) -> bool:
    if not q:
        return False
    return bool((q.get("height") or 0) >= 720 and q.get("bandwidth") and q["bandwidth"] < LOW_BITRATE_HD)


# -- FAST copies (free ad-supported TV: the channel re-streamed with ad breaks inserted) --------------

FAST_HOST_MARKERS = ("ottera", "amagi", "samsungtv.plus", "rakuten", "getpublica", "pluto.tv", "jmp2.uk", "wurl.")


def has_ads(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if any(m in host for m in FAST_HOST_MARKERS):
        return True
    # AWS MediaTailor ad insertion: https://<id>.cloudfront.net/v1/master/<hash>/<config>/<stream>.m3u8
    return "/v1/master/" in parts.path and (host.endswith("cloudfront.net") or "mediatailor" in host)


# -- duplicates --------------------------------------------------------------------------------------


def duplicate_key(ch: Any) -> tuple[str, str, str, str]:
    """Same channel listed several times (mirrors, FAST copies): same source, kind, normalized name and group."""
    return (ch.source, ch.kind, normalize(ch.name), ch.group or "")


# -- por qué un canal no responde (H39/E2) ----------------------------------------------------------

# El detalle que guarda la comprobación es la salida de ffprobe, que no se le puede poner delante a nadie. Esto la
# traduce a algo que se entienda, y lo que no se reconoce se queda en «no se pudo abrir» (nunca inventar una causa).
HEALTH_REASONS: tuple[tuple[str, str], ...] = (
    ("timeout", "no responde"),
    ("403", "prohibido: suele ser geobloqueo"),
    ("401", "pide identificarse"),
    ("404", "ya no existe"),
    ("410", "ya no existe"),
    ("5xx", "el servidor falla"),
    ("503", "el servidor falla"),
    ("502", "el servidor falla"),
    ("500", "el servidor falla"),
    ("connection refused", "no acepta la conexión"),
    ("name or service not known", "no se encuentra el servidor"),
    ("temporary failure in name resolution", "no se encuentra el servidor"),
    ("no route to host", "no se llega al servidor"),
    ("ffprobe not found", "falta ffprobe"),
    ("invalid data found", "responde, pero no es un vídeo"),
    ("end of file", "corta en seguida"),
    ("ssl", "problema de certificado"),
    ("tls", "problema de certificado"),
)


def health_reason(detail: str) -> str:
    low = (detail or "").lower()
    for needle, text in HEALTH_REASONS:
        if needle in low:
            return text
    return "no se pudo abrir"
