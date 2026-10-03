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
