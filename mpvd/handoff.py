"""«Enviar a MPV-UOS» from the browser (H23): ``mpv-uos://download?url=<url>[&preset=<id>]`` links.

A bookmarklet in the browser opens that link; the desktop entry hands it to bin/mpv-uos, which runs
``python -m mpvd link <link>`` instead of opening a player: the daemon is started if needed, the URL is queued with
``ytdl.download.batch`` and a desktop notification says so. Playing a page (``mpv-uos://open?path=<url>``) is the
«Mis notas» link that bin/mpv-uos already opens (mpvd/notes.py).

Only http(s) URLs are accepted, one per link: a web page can fire the scheme (the browser asks first), so nothing
else — local paths, other schemes, options — may come through it."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from mpvd.notes import SCHEME

DEFAULT_PRESET = "video_best"
MAX_URL = 4096


def download_link(url: str, preset: str | None = None) -> str:
    """``mpv-uos://download?url=…[&preset=…]``."""
    link = f"{SCHEME}://download?url={quote(url, safe='')}"
    return link + (f"&preset={quote(preset, safe='')}" if preset else "")


def parse(link: str) -> dict[str, Any] | None:
    """``{"action": "download", "url", "preset"}`` for a valid download link, else None."""
    u = urlparse(link.strip())
    if u.scheme != SCHEME or u.netloc != "download":
        return None
    qs = parse_qs(u.query)
    url = (qs.get("url") or [""])[0].strip()
    if not url or len(url) > MAX_URL or any(c in url for c in "\r\n\t "):
        return None
    target = urlparse(url)
    if target.scheme not in ("http", "https") or not target.netloc:
        return None
    preset = (qs.get("preset") or [""])[0].strip() or None
    if preset is not None and not preset.replace("_", "").isalnum():
        return None
    return {"action": "download", "url": url, "preset": preset}


def handle(socket_path: str, links: list[str], timeout: float = 30.0) -> dict[str, Any]:
    """Queue every valid link on the running daemon (the caller makes sure it runs). Links are grouped by preset."""
    from mpvd.client import rpc_call  # noqa: PLC0415

    parsed = [p for p in (parse(link) for link in links) if p is not None]
    out: dict[str, Any] = {"ok": False, "count": 0, "rejected": len(links) - len(parsed), "urls": []}
    if not parsed:
        out["error"] = "enlace no válido (se esperaba mpv-uos://download?url=https://…)"
        return out
    by_preset: dict[str, list[str]] = {}
    for p in parsed:
        by_preset.setdefault(p["preset"] or DEFAULT_PRESET, []).append(p["url"])
    for preset, urls in by_preset.items():
        res = rpc_call(socket_path, "ytdl.download.batch", {"urls": urls, "preset": preset}, timeout=timeout)
        out["count"] += int(res.get("count") or 0)
        out["urls"] += urls
    out["ok"] = out["count"] > 0
    return out


def notify(result: dict[str, Any]) -> None:
    """Desktop notification with what was queued (or why not); silent without a helper or with MPV_UOS_NO_NOTIFY."""
    from mpvd.brand import app_name  # noqa: PLC0415
    from mpvd.iptv.schedule import desktop_notify  # noqa: PLC0415

    if result.get("ok"):
        n = result["count"]
        title = f"{app_name()}: {n} descarga{'s' if n != 1 else ''} en cola"
        body = "\n".join(result.get("urls") or [])[:300]
    else:
        title = f"{app_name()}: no se pudo descargar"
        body = str(result.get("error") or "error")[:300]
    asyncio.run(desktop_notify(title, body))
