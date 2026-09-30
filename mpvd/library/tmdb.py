"""Optional online metadata from TMDB (API v3) with the user's own key: title, year, overview and poster.

Verified against developer.themoviedb.org (2026-09-30): ``GET /3/search/movie?query=&year=&language=`` and
``GET /3/search/tv?query=&first_air_date_year=&language=`` → ``{page, results[{id, title|name, release_date|
first_air_date, overview, poster_path}], total_pages, total_results}``; images at
``https://image.tmdb.org/t/p/<size><poster_path>`` (poster sizes w92…w780, original). Authentication: the v3 API key as
``api_key`` or the read access token as ``Authorization: Bearer`` — both accepted; a token (a JWT, ``eyJ…``) goes in
the header. Never called unless the user switched it on and gave a key (the service checks that).
The key is never written to disk outside the 0600 secrets file (no HTTP cache here: URLs would carry it).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mpvd.library.opensubtitles import USER_AGENT

API_URL = "https://api.themoviedb.org/3"
IMAGE_URL = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w342"


class TmdbError(RuntimeError):
    pass


class Tmdb:
    def __init__(self, key: str, base_url: str = API_URL, image_url: str = IMAGE_URL, language: str = "es-ES",
                 timeout: float = 15.0):
        if not key:
            raise TmdbError("falta la clave de TMDB")
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.image_url = image_url.rstrip("/")
        self.language = language
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        q = {k: v for k, v in params.items() if v not in (None, "")}
        if self.key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            q["api_key"] = self.key
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TmdbError(f"TMDB respondió HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise TmdbError(f"sin conexión con TMDB ({type(exc).__name__})") from None

    def search(self, kind: str, title: str, year: int | None = None) -> dict[str, Any] | None:
        """Best match for a movie (``kind='movie'``) or a series (``'episode'``/``'tv'``), or None."""
        tv = kind in ("episode", "tv")
        params: dict[str, Any] = {"query": title, "language": self.language, "include_adult": "false"}
        if year:
            params["first_air_date_year" if tv else "year"] = year
        res = self._get("/search/tv" if tv else "/search/movie", params)
        results = res.get("results") or []
        if not results and year:
            params.pop("first_air_date_year" if tv else "year", None)
            results = self._get("/search/tv" if tv else "/search/movie", params).get("results") or []
        if not results:
            return None
        r = results[0]
        date = str(r.get("first_air_date" if tv else "release_date") or "")
        return {"id": r.get("id"), "title": r.get("name" if tv else "title") or title,
                "original_title": r.get("original_name" if tv else "original_title") or "",
                "year": int(date[:4]) if date[:4].isdigit() else None, "overview": r.get("overview") or "",
                "poster_path": r.get("poster_path") or "", "vote_average": r.get("vote_average"),
                "type": "tv" if tv else "movie"}

    def download_poster(self, poster_path: str, dest: Path, size: str = POSTER_SIZE) -> Path:
        if not poster_path:
            raise TmdbError("sin carátula")
        url = f"{self.image_url}/{size}{poster_path if poster_path.startswith('/') else '/' + poster_path}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                data = resp.read(10 * 1024 * 1024)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TmdbError(f"no se pudo bajar la carátula ({type(exc).__name__})") from None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest
