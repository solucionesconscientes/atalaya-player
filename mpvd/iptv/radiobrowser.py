"""Radio Browser (https://api.radio-browser.info) client: world radio directory, community maintained.

Rules of the service: send an identifying User-Agent, pick a server from the DNS round-robin
``all.api.radio-browser.info`` (reverse-resolve the IPs to host names), be gentle (we cache responses).
Endpoints used are the documented ``/json/...`` ones; see docs/FUENTES_IPTV.md.
"""

from __future__ import annotations

import json
import logging
import random
import socket
from typing import Any
from urllib.parse import quote, urlencode

from mpvd.iptv.model import Channel, channel_id
from mpvd.net import FetchError, HttpCache

log = logging.getLogger("mpvd.radiobrowser")

SOURCE_ID = "radio_browser"
DISCOVERY_HOST = "all.api.radio-browser.info"
FALLBACK_SERVERS = ["https://de1.api.radio-browser.info", "https://de2.api.radio-browser.info",
                    "https://fi1.api.radio-browser.info"]
TTL_DIRECTORY = 24 * 3600.0  # countries/tags
TTL_STATIONS = 6 * 3600.0


def discover_servers(timeout: float = 3.0) -> list[str]:
    """Host names behind the round-robin DNS entry, shuffled; falls back to a fixed list."""
    hosts: set[str] = set()
    try:
        socket.setdefaulttimeout(timeout)
        for info in socket.getaddrinfo(DISCOVERY_HOST, 443, proto=socket.IPPROTO_TCP):
            ip = info[4][0]
            try:
                hosts.add(socket.gethostbyaddr(ip)[0])
            except OSError:
                continue
    except OSError:
        pass
    finally:
        socket.setdefaulttimeout(None)
    servers = [f"https://{h}" for h in sorted(hosts)] or list(FALLBACK_SERVERS)
    random.shuffle(servers)
    return servers


class RadioBrowser:
    def __init__(self, http: HttpCache, base_url: str | None = None):
        self.http = http
        self._servers = [base_url] if base_url else []
        self.stations: dict[str, Channel] = {}  # channel id -> last seen station (for play/favourites)

    # -- transport ----------------------------------------------------------------------------

    def _base(self) -> str:
        if not self._servers:
            self._servers = discover_servers()
        return self._servers[0]

    def _rotate(self) -> None:
        if len(self._servers) > 1:
            self._servers.append(self._servers.pop(0))

    def _get(self, path: str, params: dict[str, Any] | None = None, ttl: float = TTL_STATIONS) -> Any:
        query = ("?" + urlencode({k: v for k, v in (params or {}).items() if v is not None})) if params else ""
        last: Exception | None = None
        for _ in range(max(1, len(self._servers) or 3)):
            url = f"{self._base()}{path}{query}"
            try:
                res = self.http.fetch(url, ttl=ttl, timeout=20)
                return json.loads(res.read_text())
            except (FetchError, ValueError) as exc:
                last = exc
                log.warning("radio-browser %s failed: %s", url, exc)
                self._rotate()
        raise FetchError(f"radio-browser unavailable: {last}")

    # -- directory ----------------------------------------------------------------------------

    def countries(self) -> list[dict[str, Any]]:
        rows = self._get("/json/countries", {"hidebroken": "true"}, ttl=TTL_DIRECTORY)
        out = [{"name": r.get("name", ""), "code": (r.get("iso_3166_1") or "").lower(), "count": int(r.get("stationcount", 0))}
               for r in rows if r.get("iso_3166_1")]
        return sorted(out, key=lambda r: (-r["count"], r["name"]))

    def tags(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._get("/json/tags", {"order": "stationcount", "reverse": "true", "limit": limit, "hidebroken": "true"},
                         ttl=TTL_DIRECTORY)
        return [{"name": r.get("name", ""), "count": int(r.get("stationcount", 0))} for r in rows if r.get("name")]

    def _stations(self, path: str, params: dict[str, Any]) -> list[Channel]:
        rows = self._get(path, {"hidebroken": "true", "order": "votes", "reverse": "true", **params})
        chans = [self.to_channel(r) for r in rows if r.get("url")]
        for ch in chans:
            self.stations[ch.id] = ch
        return chans

    def by_country(self, code: str, limit: int = 500) -> list[Channel]:
        return self._stations(f"/json/stations/bycountrycodeexact/{quote(code.upper())}", {"limit": limit})

    def by_tag(self, tag: str, limit: int = 200) -> list[Channel]:
        return self._stations(f"/json/stations/bytagexact/{quote(tag)}", {"limit": limit})

    def search(self, name: str, limit: int = 50, country: str | None = None) -> list[Channel]:
        params: dict[str, Any] = {"name": name, "limit": limit}
        if country:
            params["countrycode"] = country.upper()
        return self._stations("/json/stations/search", params)

    def top(self, limit: int = 100) -> list[Channel]:
        return self._stations(f"/json/stations/topvote/{int(limit)}", {})

    def click(self, station_uuid: str) -> None:
        """Tell the directory a station was played (counts votes/clicks). Best effort."""
        try:
            self.http.fetch(f"{self._base()}/json/url/{quote(station_uuid)}", ttl=0, timeout=5)
        except FetchError:
            pass

    # -- mapping ------------------------------------------------------------------------------

    @staticmethod
    def to_channel(st: dict[str, Any]) -> Channel:
        url = (st.get("url_resolved") or st.get("url") or "").strip()
        tags = [t.strip() for t in (st.get("tags") or "").split(",") if t.strip()]
        country_code = (st.get("countrycode") or "").lower() or None
        extra = {k: str(st[k]) for k in ("stationuuid", "codec", "bitrate", "votes", "homepage", "hls", "lastcheckok")
                 if st.get(k) not in (None, "")}
        return Channel(
            id=channel_id(SOURCE_ID, url),
            name=(st.get("name") or url).strip(),
            url=url,
            kind="radio",
            source=SOURCE_ID,
            group=st.get("country") or None,
            country=country_code,
            language=(st.get("language") or None),
            category=tags[0] if tags else None,
            logo=(st.get("favicon") or None),
            headers={},
            extra=extra,
        )
