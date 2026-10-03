"""SponsorBlock al reproducir (H39/E3): los tramos que la gente ha marcado en un vídeo de YouTube.

**Privacidad**: no se le dice a nadie qué estás viendo. La API pública permite pedir los tramos por un **prefijo de 4
caracteres del sha256 del id** del vídeo (`GET /api/skipSegments/<prefijo>`), que devuelve los de todos los vídeos cuyo
hash empieza igual —en la prueba del 2026-10-01, 106 vídeos para un prefijo— y el filtrado por vídeo se hace aquí. O
sea: lo que sale de este equipo son 4 caracteres hexadecimales, que valen para 1 de cada 65 536 vídeos.

Verificado contra el servicio real el 2026-10-01: respuesta 200 con una lista de `{videoID, segments:[{category,
actionType, segment:[inicio,fin], UUID, videoDuration, locked, votes, description}]}`. Un vídeo sin tramos simplemente
no aparece en la lista (no es un error).

Las categorías son las mismas que ya usa la descarga (`--sponsorblock-remove`), así que lo que se salta al ver y lo que
se corta al descargar se llaman igual.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import urllib.parse
from typing import TYPE_CHECKING, Any

from mpvd.net import FetchError, HttpCache
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer

log = logging.getLogger("mpvd.sponsorblock")

API_URL = "https://sponsor.ajay.app/api/skipSegments"
TTL = 6 * 3600.0          # los tramos cambian poco; 6 h de caché evita pedir lo mismo al volver a ver algo
PREFIX_LEN = 4            # 4 caracteres hex = 1 de cada 65 536 vídeos: el equipo no dice qué está viendo

# Qué es cada categoría, en español y con lo que la gente espera que pase.
CATEGORIES: dict[str, str] = {
    "sponsor": "patrocinio",
    "selfpromo": "autopromoción",
    "interaction": "«suscríbete»",
    "intro": "cabecera",
    "outro": "despedida",
    "preview": "resumen de lo anterior",
    "music_offtopic": "parte sin música",
    "filler": "relleno",
    "poi_highlight": "lo importante",
}
# Lo que se salta por defecto: lo que nadie quiere ver. La cabecera y la despedida NO (hay quien las quiere).
DEFAULT_CATEGORIES = ("sponsor", "selfpromo", "interaction", "music_offtopic")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_PATTERNS = (
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})"),
)


def video_id(url_or_id: str) -> str | None:
    """El id de 11 caracteres de un vídeo de YouTube, o ``None`` si eso no es un vídeo de YouTube."""
    text = (url_or_id or "").strip()
    if _ID_RE.match(text):
        return text
    for pattern in _URL_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def hash_prefix(vid: str, length: int = PREFIX_LEN) -> str:
    return hashlib.sha256(vid.encode("utf-8")).hexdigest()[:length]


def clean_categories(categories: Any) -> list[str]:
    if categories is None:
        return list(DEFAULT_CATEGORIES)
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(",")]
    if not isinstance(categories, (list, tuple)):
        raise RpcError(INVALID_PARAMS, t("categories: una lista de nombres de categoría"))
    out = [c for c in categories if c in CATEGORIES]
    if not out:
        raise RpcError(INVALID_PARAMS, t("ninguna categoría válida: %s") % (", ".join(CATEGORIES),))
    return out


def parse(payload: Any, vid: str, categories: list[str]) -> list[dict[str, Any]]:
    """La respuesta de la API → los tramos de ESTE vídeo, ordenados y sin solapes absurdos."""
    rows = payload if isinstance(payload, list) else []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("videoID") != vid:
            continue
        for seg in row.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            cat = seg.get("category")
            pair = seg.get("segment")
            if cat not in categories or not isinstance(pair, list) or len(pair) != 2:
                continue
            if seg.get("actionType") not in (None, "skip"):
                continue       # «mute» y «poi» no se saltan: no se inventa otro comportamiento
            try:
                start, end = float(pair[0]), float(pair[1])
            except (TypeError, ValueError):
                continue
            if end - start < 0.5:
                continue
            out.append({"start": round(start, 3), "end": round(end, 3), "category": cat,
                        "label": CATEGORIES.get(cat, cat), "votes": int(seg.get("votes") or 0),
                        "locked": bool(seg.get("locked")), "uuid": seg.get("UUID") or ""})
    out.sort(key=lambda s: s["start"])
    merged: list[dict[str, Any]] = []
    for seg in out:
        if merged and seg["start"] <= merged[-1]["end"] + 0.1 and seg["category"] == merged[-1]["category"]:
            merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
        else:
            merged.append(seg)
    return merged


class SponsorBlockService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.base_url = os.environ.get("MPV_UOS_SPONSORBLOCK_URL") or API_URL
        self.cache = HttpCache(server.settings.cache_dir / "sponsorblock", user_agent="MPV-UOS v1.0")

    async def segments(self, url: str, categories: Any = None) -> dict[str, Any]:
        vid = video_id(url)
        cats = clean_categories(categories)
        if vid is None:
            return {"video_id": "", "segments": [], "supported": False,
                    "reason": "SponsorBlock solo tiene tramos de vídeos de YouTube"}
        query = urllib.parse.urlencode({"categories": json.dumps(cats)})
        full = f"{self.base_url}/{hash_prefix(vid)}?{query}"
        try:
            res = await asyncio.to_thread(self.cache.fetch, full, TTL)
            payload = json.loads(res.path.read_bytes() or b"[]")
        except (FetchError, OSError, ValueError) as exc:
            log.info("sponsorblock: %s", exc)
            return {"video_id": vid, "segments": [], "supported": True, "reason": "no se pudo preguntar a SponsorBlock"}
        segs = parse(payload, vid, cats)
        return {"video_id": vid, "segments": segs, "supported": True, "reason": "",
                "categories": cats, "prefix": hash_prefix(vid)}


def register(server: MpvdServer, service: SponsorBlockService) -> None:
    d = server.dispatcher

    @d.method("sponsorblock.segments")
    async def segments(ctx: Any, url: str, categories: Any = None) -> dict[str, Any]:
        """Tramos marcados de un vídeo de YouTube, pidiéndolos por un prefijo del hash de su id (no se dice qué vídeo
        es). ``categories`` por defecto: patrocinio, autopromoción, «suscríbete» y partes sin música."""
        if not url:
            raise RpcError(INVALID_PARAMS, "url required")
        return await service.segments(url, categories)

    @d.method("sponsorblock.categories")
    async def categories_(ctx: Any) -> dict[str, Any]:
        """Las categorías con su nombre en español y las que se saltan por defecto."""
        return {"categories": [{"id": k, "label": v} for k, v in CATEGORIES.items()],
                "default": list(DEFAULT_CATEGORIES)}
