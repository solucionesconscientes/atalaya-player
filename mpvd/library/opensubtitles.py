"""Minimal client for the OpenSubtitles.com REST API v1 (standard library only, synchronous).

Verified against the official docs (opensubtitles.stoplight.io, 2026-09-30):
* every request: ``Api-Key``, ``User-Agent: <app> v<version>`` (403 otherwise), ``Accept: */*`` (406 without it);
* ``POST /login {username, password}`` → ``{user, base_url, token, status}``; later requests go to ``base_url`` (a bare
  host: api. or vip-api.opensubtitles.com) with ``Authorization: Bearer <token>`` (valid 24 h);
* ``GET /subtitles`` with parameters sorted alphabetically, lower-case, spaces as ``+`` (otherwise a redirect):
  ``moviehash`` (16 hex), ``query``, ``languages`` (comma separated, alphabetical), ``season_number``,
  ``episode_number``, ``year``, ``type``; answer ``{total_pages, total_count, per_page, page, data[]}`` with
  ``attributes.files[].file_id`` and ``attributes.moviehash_match`` when searching by hash;
* ``POST /download {file_id}`` (Api-Key + Bearer) → ``{link, file_name, requests, remaining, message, reset_time,
  reset_time_utc}``; 406 when the daily quota is used up; the link expires after 3 h (never cached);
* ``DELETE /logout``. 5 requests/s per IP (429); retry once after 1 s on 5xx.
The movie hash is ``mpvd.hashing.file_hash().opensubtitles`` (same algorithm; only sent for files ≥ 128 KiB, the
smallest size the site accepts).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from mpvd import __version__

log = logging.getLogger("mpvd.library.osub")

API_URL = "https://api.opensubtitles.com/api/v1"
USER_AGENT = f"MPV-UOS v{__version__}"
MIN_HASH_SIZE = 131072


class OpenSubtitlesError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Download:
    content: bytes
    file_name: str
    remaining: int | None
    reset_time: str


def _message(body: bytes, fallback: str) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return fallback
    if isinstance(data, dict):
        for k in ("message", "error", "errors"):
            v = data.get(k)
            if isinstance(v, list):
                v = "; ".join(str(x) for x in v)
            if v:
                return str(v)
    return fallback


def normalize_result(item: dict[str, Any]) -> dict[str, Any] | None:
    """One ``data[]`` entry → the fields the player needs (None when it has no downloadable file)."""
    a = item.get("attributes") or {}
    files = a.get("files") or []
    if not files or not isinstance(files[0], dict) or files[0].get("file_id") is None:
        return None
    fd = a.get("feature_details") or {}
    return {
        "file_id": int(files[0]["file_id"]),
        "file_name": str(files[0].get("file_name") or ""),
        "files": len(files),
        "subtitle_id": str(a.get("subtitle_id") or item.get("id") or ""),
        "language": str(a.get("language") or ""),
        "release": str(a.get("release") or ""),
        "downloads": int(a.get("download_count") or 0),
        "hearing_impaired": bool(a.get("hearing_impaired")),
        "from_trusted": bool(a.get("from_trusted")),
        "ai_translated": bool(a.get("ai_translated")),
        "machine_translated": bool(a.get("machine_translated")),
        "fps": a.get("fps") or 0,
        "hash_match": bool(a.get("moviehash_match")),
        "title": str(fd.get("movie_name") or fd.get("title") or ""),
        "season": fd.get("season_number"),
        "episode": fd.get("episode_number"),
        "year": fd.get("year"),
    }


def rank(results: list[dict[str, Any]], languages: list[str]) -> list[dict[str, Any]]:
    """Hash matches first, then the user's language order, trusted uploaders, not for the hearing impaired, most
    downloaded; machine/AI translations last."""
    order = {lang: i for i, lang in enumerate(languages)}

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        return (not r["hash_match"], order.get(r["language"], len(order)), r["machine_translated"] or r["ai_translated"],
                r["hearing_impaired"], r["files"] > 1, not r["from_trusted"], -r["downloads"])

    return sorted(results, key=key)


class OpenSubtitles:
    def __init__(self, api_key: str, username: str = "", password: str = "", base_url: str = API_URL,
                 user_agent: str = USER_AGENT, timeout: float = 20.0):
        if not api_key:
            raise OpenSubtitlesError(0, "falta la Api-Key de OpenSubtitles")
        self.api_key = api_key
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.token = ""
        self.token_at = 0.0
        self.last_quota: dict[str, Any] = {}

    # -- transport ----------------------------------------------------------------------------------------------

    def _headers(self, auth: bool, body: bool) -> dict[str, str]:
        h = {"Api-Key": self.api_key, "User-Agent": self.user_agent, "Accept": "*/*"}
        if body:
            h["Content-Type"] = "application/json"
        if auth and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    @staticmethod
    def query_string(params: dict[str, Any]) -> str:
        """Alphabetical, lower-case, defaults omitted, ``+`` for spaces (the API's best practice)."""
        items = []
        for k in sorted(params):
            v = params[k]
            if v is None or v == "":
                continue
            items.append((k.lower(), str(v).lower()))
        return urllib.parse.urlencode(items, quote_via=urllib.parse.quote_plus)

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None, auth: bool = False, retry: bool = True) -> dict[str, Any]:
        url = self.base_url + path
        if params:
            qs = self.query_string(params)
            if qs:
                url += "?" + qs
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(auth, data is not None))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - https (or a test server)
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp is not None else b""
            if exc.code >= 500 and retry:
                time.sleep(1.0)
                return self._request(method, path, params, body, auth, retry=False)
            if exc.code == 429 and retry:
                time.sleep(1.0)
                return self._request(method, path, params, body, auth, retry=False)
            raise OpenSubtitlesError(exc.code, _message(raw, f"HTTP {exc.code}")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OpenSubtitlesError(0, f"sin conexión con OpenSubtitles ({reason})") from None
        try:
            out = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise OpenSubtitlesError(0, "respuesta no válida de OpenSubtitles") from None
        return out if isinstance(out, dict) else {"data": out}

    # -- API ----------------------------------------------------------------------------------------------------

    def login(self) -> dict[str, Any]:
        if not self.username or not self.password:
            raise OpenSubtitlesError(401, "falta el usuario o la contraseña de OpenSubtitles")
        res = self._request("POST", "/login", body={"username": self.username, "password": self.password})
        token = res.get("token")
        if not token:
            raise OpenSubtitlesError(401, _message(json.dumps(res).encode(), "no se pudo iniciar sesión"))
        self.token = str(token)
        self.token_at = time.time()
        host = str(res.get("base_url") or "").strip().rstrip("/")
        if host:
            # a bare host (api.… or vip-api.…): keep our scheme and path, switch host only
            parts = urllib.parse.urlsplit(self.base_url)
            if "://" in host:
                new = urllib.parse.urlsplit(host)
                host = new.netloc
            self.base_url = urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))
        user = res.get("user") or {}
        return {"level": user.get("level"), "allowed_downloads": user.get("allowed_downloads"), "vip": user.get("vip")}

    def ensure_login(self) -> None:
        if self.username and self.password and (not self.token or time.time() - self.token_at > 23 * 3600):
            self.login()

    def logout(self) -> None:
        if not self.token:
            return
        try:
            self._request("DELETE", "/logout", auth=True, retry=False)
        except OpenSubtitlesError:
            pass
        self.token = ""

    def search(self, moviehash: str | None = None, query: str | None = None, languages: str = "",
               season: int | None = None, episode: int | None = None, year: int | None = None,
               kind: str | None = None) -> list[dict[str, Any]]:
        langs = ",".join(sorted({x for x in languages.split(",") if x}))
        params: dict[str, Any] = {"languages": langs}
        if moviehash:
            params["moviehash"] = moviehash
        if query:
            params["query"] = query
        if season is not None:
            params["season_number"] = season
        if episode is not None:
            params["episode_number"] = episode
        if year:
            params["year"] = year
        if kind in ("movie", "episode"):
            params["type"] = kind
        res = self._request("GET", "/subtitles", params=params, auth=bool(self.token))
        out = []
        for item in res.get("data") or []:
            r = normalize_result(item) if isinstance(item, dict) else None
            if r is not None:
                out.append(r)
        return out

    def download(self, file_id: int) -> Download:
        self.ensure_login()
        try:
            res = self._request("POST", "/download", body={"file_id": int(file_id)}, auth=True)
        except OpenSubtitlesError as exc:
            if exc.status in (401, 406) and "token" in exc.message.lower() and self.username:
                self.token = ""
                self.login()
                res = self._request("POST", "/download", body={"file_id": int(file_id)}, auth=True)
            elif exc.status == 406 and "download" in exc.message.lower():
                raise OpenSubtitlesError(406, "cupo diario de OpenSubtitles agotado: " + exc.message) from None
            else:
                raise
        link = res.get("link")
        if not link:
            raise OpenSubtitlesError(0, _message(json.dumps(res).encode(), "OpenSubtitles no dio enlace de descarga"))
        self.last_quota = {"remaining": res.get("remaining"), "requests": res.get("requests"),
                           "reset_time": res.get("reset_time"), "reset_time_utc": res.get("reset_time_utc")}
        req = urllib.request.Request(str(link), headers={"User-Agent": self.user_agent, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                content = resp.read(20 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise OpenSubtitlesError(exc.code, f"no se pudo bajar el subtítulo (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenSubtitlesError(0, f"no se pudo bajar el subtítulo ({getattr(exc, 'reason', exc)})") from None
        remaining = res.get("remaining")
        return Download(content=content, file_name=str(res.get("file_name") or ""),
                        remaining=int(remaining) if isinstance(remaining, (int, float)) else None,
                        reset_time=str(res.get("reset_time") or ""))
