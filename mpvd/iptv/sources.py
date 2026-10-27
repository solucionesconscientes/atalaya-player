"""Channel sources: built-in lists (TDTChannels, iptv-org) plus user M3U URLs, loaded through the HTTP cache."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.iptv.m3u import parse_m3u
from mpvd.iptv.model import Channel, dedupe, entry_to_channel
from mpvd.net import DEFAULT_TTL, FetchError, HttpCache


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    kind: str = "tv"  # default kind of its channels
    region: str = "world"  # "es" | "world"
    ttl: float = DEFAULT_TTL
    builtin: bool = True
    country: str | None = None  # all channels belong to this country (ISO2) when set
    country_from_tvg_id: bool = False  # iptv-org style "Name.cc@Feed" ids
    exclude_countries: tuple[str, ...] = ()  # países que no deben aparecer en esta lista

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "url": self.url, "kind": self.kind, "region": self.region,
                "ttl": self.ttl, "builtin": self.builtin, "exclude_countries": list(self.exclude_countries)}


# Verified 2026-09-28 (see docs/FUENTES_IPTV.md); tdt_radio is skipped automatically if the URL 404s.
# Mirrors used only when the main URL fails (Cloudflare/TLS/network): the first one that returns a real
# playlist wins, and a copy bundled with the app is the last resort so TV is never an empty menu.
FALLBACK_URLS: dict[str, list[str]] = {
    "tdt_tv": [
        # Solo TDTChannels: si su lista principal falla, se usa esta otra URL del mismo proyecto y, en último
        # caso, la copia incluida en la app. No se cae a la lista española de iptv-org: España es TDTChannels.
        "https://www.tdtchannels.com/lists/tv.m3u",
    ],
    "tdt_radio": [
        "https://www.tdtchannels.com/lists/radio.m3u",
    ],
}

BUILTIN_SOURCES: list[Source] = [
    Source("tdt_tv", "España · TV (TDTChannels)", "https://www.tdtchannels.com/lists/tv.m3u8", "tv", "es", country="es"),
    Source("tdt_radio", "España · Radio (TDTChannels)", "https://www.tdtchannels.com/lists/radio.m3u8", "radio", "es",
           country="es"),
    Source("iptv_org", "Mundo · TV (iptv-org)", "https://iptv-org.github.io/iptv/index.m3u", "tv", "world",
           country_from_tvg_id=True, exclude_countries=("es",)),
]


@dataclass
class SourceState:
    source: Source
    channels: list[Channel] = field(default_factory=list)
    loaded_at: float = 0.0
    fetched_at: float = 0.0
    stale: bool = False
    from_cache: bool = False
    error: str | None = None
    epg_url: str | None = None
    warnings: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.source.to_dict() | {
            "channels": len(self.channels), "loaded_at": self.loaded_at, "fetched_at": self.fetched_at,
            "stale": self.stale, "from_cache": self.from_cache, "error": self.error, "epg_url": self.epg_url,
            "warnings": self.warnings,
        }


def _bundled(source_id: str) -> Path | None:
    path = Path(__file__).with_name("data") / f"{source_id}.m3u8"
    return path if path.is_file() else None


def _from_text(source: Source, text: str, *, fetched_at: float = 0.0, stale: bool = False,
               from_cache: bool = False, error: str | None = None) -> SourceState:
    state = SourceState(source=source)
    pl = parse_m3u(text)
    if pl.kind == "hls":
        state.error = "esa dirección es un vídeo en directo, no una lista de canales"
        return state
    channels = [entry_to_channel(e, source.id, source.kind, source.country, source.country_from_tvg_id)
                for e in pl.entries]
    if source.exclude_countries:
        excluidos = {c.lower() for c in source.exclude_countries}
        channels = [c for c in channels if (c.country or "").lower() not in excluidos]
    state.channels = dedupe(channels)
    state.loaded_at = time.time()
    state.fetched_at = fetched_at
    state.stale = stale
    state.from_cache = from_cache
    state.epg_url = pl.epg_url
    state.warnings = len(pl.warnings)
    state.error = error
    return state


def load_source(http: HttpCache, source: Source, force: bool = False, timeout: float = 60.0) -> SourceState:
    """Fetch (cached) + parse + normalize one source. Never raises: errors land in ``state.error``.

    If the main URL fails (Cloudflare, TLS, proxy, no network), the mirrors in FALLBACK_URLS are tried and,
    as a last resort, the playlist bundled in ``mpvd/iptv/data`` is used. A channel list that works offline
    is better than an empty TV menu; ``state.error`` records why the main URL failed.
    """
    errors: list[str] = []
    official = any(s.id == source.id and s.url == source.url for s in BUILTIN_SOURCES)
    urls = [source.url, *(FALLBACK_URLS.get(source.id, []) if official else [])]
    for index, url in enumerate(urls):
        try:
            res = http.fetch(url, ttl=source.ttl, force=force or index > 0, timeout=timeout)
        except FetchError as exc:
            errors.append(f"{url}: {exc}")
            continue
        state = _from_text(source, res.read_text(), fetched_at=res.fetched_at, stale=res.stale,
                           from_cache=res.from_cache)
        if state.channels:
            if index > 0 or errors:
                state.error = "; ".join(errors) or None
            return state
        errors.append(f"{url}: lista vacía")
    bundled = _bundled(source.id) if official else None
    if bundled is not None:
        try:
            state = _from_text(source, bundled.read_text(encoding="utf-8"), from_cache=True)
        except OSError as exc:
            errors.append(f"lista incluida: {exc}")
        else:
            if state.channels:
                state.error = "; ".join(errors) or None
                return state
    state = SourceState(source=source)
    state.error = "; ".join(errors) or "no se pudo cargar la lista"
    return state
