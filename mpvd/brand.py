"""The app's identity (H33): name, id, folder name and colours, read from ``<project>/brand.json`` — the one place to
change when the name is decided. Falls back to the built-in values if the file is missing or broken."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from mpvd.config import project_root

DEFAULTS: dict[str, Any] = {
    "name": "MPV-UOS",
    "id": "mpv-uos",
    "folder": "MPV-UOS",
    "site": "https://solucionesconscientes.es/atalaya",
    "colors": {"ink": "#0D1320", "slate": "#24324D", "signal": "#3D7BFF", "amber": "#FFB020", "mist": "#E9EEF6"},
}


@lru_cache(maxsize=1)
def brand() -> dict[str, Any]:
    out = {**DEFAULTS, "colors": dict(DEFAULTS["colors"])}
    root = project_root()
    try:
        data = json.loads((root / "brand.json").read_text(encoding="utf-8")) if root else {}
    except (OSError, ValueError):
        data = {}
    for key in ("name", "id", "folder", "site"):
        if isinstance(data.get(key), str) and data[key].strip():
            out[key] = data[key].strip()
    if isinstance(data.get("colors"), dict):
        out["colors"].update({k: v for k, v in data["colors"].items() if isinstance(v, str)})
    return out


def app_name() -> str:
    return str(brand()["name"])


def app_id() -> str:
    return str(brand()["id"])


def app_site() -> str:
    """Where the project lives: the page with the documentation and the downloads."""
    return str(brand()["site"])


def folder_name() -> str:
    """Name of the app's folder inside the user's Videos/Music (downloads, recordings, subtitles...)."""
    return str(brand()["folder"])
