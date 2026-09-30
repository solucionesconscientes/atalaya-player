"""HTTP fetching with an on-disk cache: conditional requests (ETag / Last-Modified), TTL and offline fallback.

Standard library only (urllib). Synchronous; call from asyncio with ``asyncio.to_thread``.
Layout: ``<dir>/<key>.data`` (body) + ``<dir>/<key>.json`` (metadata).
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mpvd import __version__
from mpvd.brand import app_id
from mpvd.hashing import url_key

log = logging.getLogger("mpvd.net")

DEFAULT_USER_AGENT = f"{app_id().upper()}/{__version__} (+https://github.com/mpv-uos) mpvd"
DEFAULT_TTL = 12 * 3600.0


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    path: Path
    status: int  # HTTP status of the last network exchange (0 = not contacted)
    from_cache: bool
    stale: bool  # served from cache although the TTL expired (offline / server error)
    fetched_at: float
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    size: int = 0

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.path.read_bytes().decode(encoding, errors="replace")

    @property
    def age(self) -> float:
        return time.time() - self.fetched_at


class HttpCache:
    def __init__(self, directory: str | Path, user_agent: str = DEFAULT_USER_AGENT, offline: bool = False):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.offline = offline  # never touch the network (serve cache or fail)

    # -- storage -----------------------------------------------------------------------------

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = url_key(url)[4:]
        return self.dir / f"{key}.data", self.dir / f"{key}.json"

    def _load_meta(self, meta_path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def cached(self, url: str) -> FetchResult | None:
        data_path, meta_path = self._paths(url)
        meta = self._load_meta(meta_path)
        if meta is None or not data_path.exists():
            return None
        return FetchResult(
            url=url, path=data_path, status=int(meta.get("status", 0)), from_cache=True, stale=False,
            fetched_at=float(meta.get("fetched_at", 0)), etag=meta.get("etag"),
            last_modified=meta.get("last_modified"), content_type=meta.get("content_type"),
            size=data_path.stat().st_size,
        )

    def _store(self, url: str, body: bytes | None, headers: dict[str, str], status: int) -> FetchResult:
        data_path, meta_path = self._paths(url)
        if body is not None:
            tmp = data_path.with_suffix(".part")
            tmp.write_bytes(body)
            os.replace(tmp, data_path)
        old = self._load_meta(meta_path) or {}
        meta = {
            "url": url,
            "status": status,
            "fetched_at": time.time(),
            "etag": headers.get("etag", old.get("etag")),
            "last_modified": headers.get("last-modified", old.get("last_modified")),
            "content_type": headers.get("content-type", old.get("content_type")),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return FetchResult(
            url=url, path=data_path, status=status, from_cache=body is None, stale=False,
            fetched_at=meta["fetched_at"], etag=meta["etag"], last_modified=meta["last_modified"],
            content_type=meta["content_type"], size=data_path.stat().st_size,
        )

    def invalidate(self, url: str) -> None:
        for p in self._paths(url):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    # -- fetching ----------------------------------------------------------------------------

    def fetch(
        self, url: str, ttl: float = DEFAULT_TTL, force: bool = False, timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """Return the body of ``url`` from cache (fresh within ``ttl``) or from the network.

        ``force`` skips the TTL but still sends a conditional request. When the network fails and a
        cached copy exists it is returned with ``stale=True``; otherwise ``FetchError`` is raised.
        """
        cached = self.cached(url)
        if cached is not None and not force and cached.age < ttl:
            return cached
        if self.offline:
            if cached is not None:
                cached.stale = True
                return cached
            raise FetchError(f"offline and not cached: {url}")

        req_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "identity", **(headers or {})}
        if cached is not None:
            if cached.etag:
                req_headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                req_headers["If-Modified-Since"] = cached.last_modified
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URLs come from config
                body = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return self._store(url, body, hdrs, resp.status)
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached is not None:
                hdrs = {k.lower(): v for k, v in exc.headers.items()}
                return self._store(url, None, hdrs, 304)
            if cached is not None:
                log.warning("%s -> HTTP %s; serving stale cache", url, exc.code)
                cached.stale = True
                return cached
            raise FetchError(f"HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            if cached is not None:
                log.warning("%s unreachable (%s); serving stale cache", url, exc)
                cached.stale = True
                return cached
            raise FetchError(f"cannot fetch {url}: {exc}") from exc


def http_date(ts: float | None = None) -> str:
    return email.utils.formatdate(ts if ts is not None else time.time(), usegmt=True)
