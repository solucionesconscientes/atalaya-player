"""What kind of subscription a URL is: a YouTube channel or list (by its shape), a podcast feed (by its first bytes)
or, as a last resort, any list yt-dlp can read flat."""

from __future__ import annotations

import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from mpvd.net import DEFAULT_USER_AGENT
from mpvd.subscriptions.rss import looks_like_feed

KINDS = ("channel", "playlist", "rss")
KIND_LABELS = {"channel": "canal", "playlist": "lista", "rss": "podcast"}

YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com")
# /@handle, /channel/UC…, /c/name, /user/name, optionally followed by a tab
YT_CHANNEL = re.compile(r"^/(@[^/]+|channel/UC[\w-]{10,}|c/[^/]+|user/[^/]+)(?:/([a-z]+))?/?$", re.IGNORECASE)
YT_TABS_OK = ("videos", "streams", "shorts", "podcasts")
FEED_HINT = re.compile(r"(\.(xml|rss|atom)$|/(feed|rss|atom)(/|$)|^feeds?\.|podcast)", re.IGNORECASE)
SNIFF_BYTES = 65536


def classify_url(url: str) -> tuple[str | None, str]:
    """(kind or None when the shape says nothing, normalised URL). A channel always points at a tab with videos
    (``/videos`` unless the URL names ``streams``/``shorts``/``podcasts``): the bare channel page lists its tabs."""
    u = (url or "").strip()
    parts = urllib.parse.urlsplit(u)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("no es una URL http(s)")
    host = parts.netloc.lower().split(":")[0]
    if host in YT_HOSTS:
        q = urllib.parse.parse_qs(parts.query)
        if parts.path.rstrip("/") in ("/playlist",) and q.get("list"):
            return "playlist", f"https://www.youtube.com/playlist?list={q['list'][0]}"
        if parts.path.startswith("/feeds/videos.xml"):
            return "rss", u
        m = YT_CHANNEL.match(parts.path)
        if m:
            tab = (m.group(2) or "").lower()
            tab = tab if tab in YT_TABS_OK else "videos"
            return "channel", f"https://www.youtube.com/{m.group(1)}/{tab}"
        if q.get("list") and not q.get("v"):
            return "playlist", f"https://www.youtube.com/playlist?list={q['list'][0]}"
        return None, u
    if FEED_HINT.search(parts.path) or FEED_HINT.search(host):
        return "rss?", u
    return None, u


def sniff(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """GET the first 64 KiB (Range, and never more than that is read): (is an RSS/Atom feed, content type).
    Network errors propagate as OSError/URLError."""
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Range": f"bytes=0-{SNIFF_BYTES - 1}",
                                              "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                                                        "text/xml;q=0.9, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the user's URL
            ctype = resp.headers.get("Content-Type", "") or ""
            head = resp.read(SNIFF_BYTES)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise OSError(f"no se puede abrir {url}: {exc}") from exc
    return looks_like_feed(head, ctype), ctype


__all__ = ["KINDS", "KIND_LABELS", "classify_url", "sniff"]
