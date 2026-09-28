"""Normalized channel model and translation of playlist hints into mpv per-file options."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote

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

    def mpv_options(self) -> dict[str, str]:
        """Per-file options for ``loadfile <url> replace -1 <options>`` (values must be strings)."""
        opts: dict[str, str] = {"force-media-title": self.name}
        others: list[str] = []
        for key, value in self.headers.items():
            if key == "User-Agent":
                opts["user-agent"] = value
            elif key == "Referer":
                opts["referrer"] = value
            else:
                others.append(f"{key}: {value}")
        if others:
            # http-header-fields is a string list; escape commas inside values with the %n% length syntax.
            opts["http-header-fields"] = ",".join(_escape_list_item(x) for x in others)
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
    extra: dict[str, str] = {}
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
