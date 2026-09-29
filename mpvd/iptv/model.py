"""Normalized channel model and translation of playlist hints into mpv per-file options."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from mpvd.iptv.m3u import M3UEntry

HEADER_CANON = {
    "user-agent": "User-Agent",
    "useragent": "User-Agent",
    "referer": "Referer",
    "referrer": "Referer",
    "origin": "Origin",
    "cookie": "Cookie",
    "authorization": "Authorization",
    "x-forwarded-for": "X-Forwarded-For",
}

# Sent when the list gives no User-Agent: some CDNs answer 403 to mpv's default "libmpv" (Canal Sur, verified
# 2026-09-30). A current desktop Chrome (reduced UA string, as Chrome itself sends it); MPV_UOS_USER_AGENT overrides.
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/152.0.0.0 Safari/537.36")

# FFmpeg hls demuxer options for every HLS channel: a fresh connection per request (servers that drop kept-alive
# connections made the playlist reload fail and the picture froze: 101TV Málaga after ~12 s) and a few retries per
# segment. Both listed by `ffmpeg -h demuxer=hls` (FFmpeg 8.0).
HLS_LAVF_DEFAULTS = {"http_persistent": "0", "seg_max_retry": "3"}

# mpv options a list may carry itself (#EXTVLCOPT:demuxer-lavf-o=...): passed through, never replaced.
MPV_PASSTHROUGH = ("demuxer-lavf-o", "stream-lavf-o")

_HLS_PATH_RE = re.compile(r"\.m3u8s?(?:;|$)")


def default_user_agent() -> str:
    return os.environ.get("MPV_UOS_USER_AGENT") or BROWSER_USER_AGENT


def is_http(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def is_hls(url: str, extra: dict[str, str] | None = None) -> bool:
    """HLS by URL (".m3u8", "format=m3u8") or by what the source says (Radio Browser ``hls``, KODIPROP)."""
    if not is_http(url):
        return False
    extra = extra or {}
    if extra.get("hls") == "1" or extra.get("inputstream.adaptive.manifest_type", "").lower() == "hls":
        return True
    parts = urlsplit(url)
    return bool(_HLS_PATH_RE.search(parts.path.lower())) or "format=m3u8" in parts.query.lower()


def split_kv_list(value: str) -> list[tuple[str, str]]:
    """mpv key-value list ('a=1,b=[x,y],c="z,w"') -> [(key, value)], values kept as written."""
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    quoted = False
    for ch in value:
        if ch == '"' and depth == 0:
            quoted = not quoted
        elif ch == "[" and not quoted:
            depth += 1
        elif ch == "]" and not quoted and depth:
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            items.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    items.append("".join(buf))
    out = []
    for item in items:
        if item.strip():
            key, _, val = item.partition("=")
            out.append((key.strip(), val))
    return out


def merge_kv_list(existing: str | None, defaults: dict[str, str]) -> str:
    """Add ``defaults`` to an mpv key-value list without overriding the keys it already sets."""
    pairs = split_kv_list(existing or "")
    have = {k for k, _ in pairs}
    pairs += [(k, v) for k, v in defaults.items() if k not in have]
    return ",".join(f"{k}={v}" for k, v in pairs)


@dataclass
class Channel:
    id: str
    name: str
    url: str
    kind: str  # "tv" | "radio"
    source: str
    group: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2, lower-case, when known
    language: str | None = None
    category: str | None = None
    logo: str | None = None
    tvg_id: str | None = None
    chno: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    drm: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "url": self.url, "kind": self.kind, "source": self.source,
            "group": self.group, "country": self.country, "language": self.language, "category": self.category,
            "logo": self.logo, "tvg_id": self.tvg_id, "chno": self.chno, "headers": self.headers, "drm": self.drm,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Channel:
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})  # type: ignore[arg-type]

    def request_headers(self) -> dict[str, str]:
        """HTTP headers as the player sends them (the list's, plus a browser User-Agent when it gives none)."""
        headers = dict(self.headers)
        if "User-Agent" not in headers and is_http(self.url):
            headers["User-Agent"] = default_user_agent()
        return headers

    def is_hls(self) -> bool:
        return is_hls(self.url, self.extra)

    def mpv_options(self) -> dict[str, str]:
        """Per-file options for ``loadfile <url> replace -1 <options>`` (values must be strings)."""
        # A live channel has no position to resume: never write watch_later for it (zapping or quitting).
        opts: dict[str, str] = {"force-media-title": self.name, "save-position-on-quit": "no"}
        others: list[str] = []
        for key, value in self.request_headers().items():
            if key == "User-Agent":
                opts["user-agent"] = value
            elif key == "Referer":
                opts["referrer"] = value
            else:
                others.append(f"{key}: {value}")
        if others:
            # http-header-fields is a string list; escape commas inside values with the %n% length syntax.
            opts["http-header-fields"] = ",".join(_escape_list_item(x) for x in others)
        for key in MPV_PASSTHROUGH:
            if self.extra.get("mpv:" + key):
                opts[key] = self.extra["mpv:" + key]
        if self.is_hls():
            opts["demuxer-lavf-o"] = merge_kv_list(opts.get("demuxer-lavf-o"), HLS_LAVF_DEFAULTS)
        return opts


def _escape_list_item(item: str) -> str:
    if "," not in item:
        return item
    encoded = item.encode("utf-8")
    return f"%{len(encoded)}%{item}"


def channel_id(source: str, url: str) -> str:
    return hashlib.blake2b(f"{source}|{url}".encode("utf-8"), digest_size=8).hexdigest()


def _parse_header_blob(blob: str) -> dict[str, str]:
    """``User-Agent=foo&Referer=bar`` (Kodi/pipe style, URL-encoded) -> canonical headers."""
    out: dict[str, str] = {}
    for k, v in parse_qsl(blob, keep_blank_values=True):
        canon = HEADER_CANON.get(k.strip().lower(), k.strip())
        out[canon] = unquote(v)
    return out


def split_pipe_headers(url: str) -> tuple[str, dict[str, str]]:
    """``http://x/y.m3u8|User-Agent=..&Referer=..`` -> (url, headers)."""
    if "|" not in url:
        return url, {}
    base, _, blob = url.partition("|")
    return base, _parse_header_blob(blob)


def _country_from(entry: M3UEntry, from_tvg_id: bool) -> str | None:
    for key in ("tvg-country", "country"):
        v = entry.attrs.get(key)
        if v and len(v) == 2:
            return v.lower()
    if from_tvg_id:
        # iptv-org: tvg-id="Name.cc@Feed" (TDTChannels uses "Name.TV"/"Name.Radio", so this is opt-in)
        tvg = (entry.attrs.get("tvg-id") or "").split("@", 1)[0]
        m = re.search(r"\.([a-z]{2})$", tvg.lower())
        return m.group(1) if m else None
    return None


_QUALITY_RE = re.compile(r"\s*\((\d{3,4}p|[0-9.]+p?)\)\s*$")
_FLAG_RE = re.compile(r"\s*\[(Not 24/7|Geo-blocked)\]\s*", re.IGNORECASE)


def clean_title(title: str) -> tuple[str, dict[str, str]]:
    """Strip iptv-org style suffixes: "Name (1080p) [Not 24/7] [Geo-blocked]" -> ("Name", flags)."""
    flags: dict[str, str] = {}
    name = title
    for m in _FLAG_RE.finditer(name):
        flag = m.group(1).lower()
        flags["geo_blocked" if flag == "geo-blocked" else "not_24_7"] = "1"
    name = _FLAG_RE.sub(" ", name).strip()
    m = _QUALITY_RE.search(name)
    if m:
        flags["quality"] = m.group(1)
        name = name[: m.start()].strip()
    return name or title.strip(), flags


def entry_to_channel(
    entry: M3UEntry, source: str, default_kind: str = "tv", default_country: str | None = None,
    country_from_tvg_id: bool = False,
) -> Channel:
    url, headers = split_pipe_headers(entry.url.strip())
    attrs = entry.attrs
    # iptv-org repeats the hints as #EXTINF attributes (http-user-agent="..", http-referrer="..")
    for k, v in list(attrs.items()) + list(entry.vlcopts.items()):
        if k.startswith("http-") and v:
            canon = HEADER_CANON.get(k[5:], None)
            if canon:
                headers[canon] = v
    drm = False
    extra: dict[str, str] = {f"mpv:{k}": v for k, v in entry.vlcopts.items() if k in MPV_PASSTHROUGH and v}
    for k, v in entry.kodiprops.items():
        if k in ("inputstream.adaptive.stream_headers", "inputstream.adaptive.manifest_headers"):
            headers.update(_parse_header_blob(v))
        elif k.startswith("inputstream.adaptive.license") or k == "inputstreamaddon" and "drm" in v.lower():
            drm = True
            extra[k] = v
        elif k.startswith("inputstream.adaptive.license_key"):
            drm = True
            extra[k] = v
        else:
            extra[k] = v
    if any("license" in k for k in entry.kodiprops):
        drm = True
    kind = default_kind
    if attrs.get("radio", "").lower() == "true":
        kind = "radio"
    group = entry.group_title
    if kind == "tv" and group and "radio" in group.lower() and default_kind != "tv":
        kind = "radio"
    chno_raw = attrs.get("tvg-chno") or attrs.get("channel-number")
    try:
        chno = int(chno_raw) if chno_raw else None
    except ValueError:
        chno = None
    raw_name = (entry.name or attrs.get("tvg-name") or attrs.get("tvg-id") or url).strip()
    name, flags = clean_title(raw_name)
    extra.update(flags)
    category = (attrs.get("group-title") or group or "").split(";", 1)[0].strip() or None
    return Channel(
        id=channel_id(source, url),
        name=name,
        url=url,
        kind=kind,
        source=source,
        group=group,
        country=_country_from(entry, country_from_tvg_id) or default_country,
        language=(attrs.get("tvg-language") or attrs.get("language") or None),
        category=category,
        logo=attrs.get("tvg-logo") or None,
        tvg_id=attrs.get("tvg-id") or None,
        chno=chno,
        headers=headers,
        drm=drm,
        extra=extra,
    )


def dedupe(channels: list[Channel]) -> list[Channel]:
    """Drop exact duplicates (same source + url); keep the first occurrence."""
    seen: set[str] = set()
    out: list[Channel] = []
    for ch in channels:
        if ch.id in seen:
            continue
        seen.add(ch.id)
        out.append(ch)
    return out


# -- HLS master playlists (quality shown in the menus) ---------------------------------------------------

_HLS_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_hls_attrs(text: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in _HLS_ATTR_RE.findall(text)}


def hls_variants(text: str) -> list[dict[str, Any]]:
    """#EXT-X-STREAM-INF entries of a master playlist -> [{width, height, fps, bandwidth, codecs}]."""
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.upper().startswith("#EXT-X-STREAM-INF:"):
            continue
        a = parse_hls_attrs(line.split(":", 1)[1])
        v: dict[str, Any] = {"bandwidth": None, "width": None, "height": None, "fps": None, "codecs": a.get("CODECS")}
        try:
            v["bandwidth"] = int(a.get("BANDWIDTH") or a.get("AVERAGE-BANDWIDTH") or 0) or None
        except ValueError:
            pass
        m = re.match(r"^(\d+)x(\d+)$", a.get("RESOLUTION", "").strip())
        if m:
            v["width"], v["height"] = int(m.group(1)), int(m.group(2))
        try:
            v["fps"] = float(a["FRAME-RATE"]) if a.get("FRAME-RATE") else None
        except ValueError:
            pass
        out.append(v)
    return out


def best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The variant mpv plays by default (hls-bitrate=max): highest resolution, then frame rate, then bitrate."""
    if not variants:
        return None
    return max(variants, key=lambda v: (v.get("height") or 0, v.get("fps") or 0, v.get("bandwidth") or 0))
