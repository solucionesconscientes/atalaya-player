"""Tolerant M3U / M3U8 channel-list parser.

Handles: BOM, CRLF, blank lines, ``#EXTINF`` with quoted/unquoted attributes and commas inside quotes,
``#EXTVLCOPT:key=value``, ``#KODIPROP:key=value``, ``#EXTGRP:name``, bare URLs (no ``#EXTINF``), two
``#EXTINF`` in a row (first one dropped), duplicates (kept; de-duplication is the model's job) and HLS
media/master playlists (flagged as ``kind='hls'`` and not treated as channel lists).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ATTR_RE = re.compile(r"""([A-Za-z0-9_.\-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([^\s,]*))""")
_HLS_MARKERS = ("#EXT-X-TARGETDURATION", "#EXT-X-STREAM-INF", "#EXT-X-MEDIA-SEQUENCE", "#EXT-X-VERSION")


@dataclass
class M3UEntry:
    name: str
    url: str
    duration: float = -1.0
    attrs: dict[str, str] = field(default_factory=dict)
    vlcopts: dict[str, str] = field(default_factory=dict)
    kodiprops: dict[str, str] = field(default_factory=dict)
    group: str | None = None  # from #EXTGRP (group-title wins when both exist)
    line: int = 0

    @property
    def group_title(self) -> str | None:
        return self.attrs.get("group-title") or self.group


@dataclass
class M3UPlaylist:
    header: dict[str, str] = field(default_factory=dict)  # attributes of the #EXTM3U line
    entries: list[M3UEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    kind: str = "channels"  # or "hls"

    @property
    def epg_url(self) -> str | None:
        return self.header.get("url-tvg") or self.header.get("x-tvg-url")


def parse_attrs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(text):
        key = m.group(1).lower()
        if m.group(2) is not None:
            val = m.group(2).replace('\\"', '"')
        else:
            val = m.group(3) if m.group(3) is not None else m.group(4)
        out[key] = (val or "").strip()
    return out


def _split_extinf(body: str) -> tuple[float, dict[str, str], str]:
    """Split ``<duration> [attrs],<title>`` honouring quotes around the first comma."""
    quote: str | None = None
    skip = False
    for i, ch in enumerate(body):
        if skip:
            skip = False
            continue
        if quote:
            if ch == "\\":
                skip = True
            elif ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == ",":
            head, title = body[:i], body[i + 1:]
            break
    else:
        head, title = body, ""
    head = head.strip()
    m = re.match(r"^(-?\d+(?:\.\d+)?)", head)
    duration = float(m.group(1)) if m else -1.0
    attrs = parse_attrs(head[m.end():] if m else head)
    return duration, attrs, title.strip()


def _is_url(line: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", line)) or line.startswith("/") or line.startswith("\\\\")


def parse_m3u(text: str) -> M3UPlaylist:
    pl = M3UPlaylist()
    if text.startswith("﻿"):
        text = text[1:]
    pending: M3UEntry | None = None
    pending_group: str | None = None
    hls_markers = False
    channel_like = False  # an #EXTINF with attributes or a -1 duration is a channel list, not an HLS segment list
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTM3U"):
            pl.header.update(parse_attrs(line[7:]))
            continue
        if upper.startswith(_HLS_MARKERS):
            hls_markers = True
            continue
        if upper.startswith("#EXTINF:"):
            if pending is not None:
                pl.warnings.append(f"line {pending.line}: #EXTINF without URL (dropped)")
            duration, attrs, title = _split_extinf(line[8:])
            pending = M3UEntry(name=title or attrs.get("tvg-name", ""), url="", duration=duration, attrs=attrs,
                               group=pending_group, line=lineno)
            if attrs or duration < 0:
                channel_like = True
            continue
        if upper.startswith("#EXTVLCOPT:"):
            k, _, v = line[11:].partition("=")
            if pending is not None:
                pending.vlcopts[k.strip().lower()] = v.strip()
            continue
        if upper.startswith("#KODIPROP:"):
            k, _, v = line[10:].partition("=")
            if pending is not None:
                pending.kodiprops[k.strip().lower()] = v.strip()
            continue
        if upper.startswith("#EXTGRP:"):
            pending_group = line[8:].strip() or None
            if pending is not None and pending.group is None:
                pending.group = pending_group
            continue
        if line.startswith("#"):
            continue  # comment or unknown directive
        if not _is_url(line):
            pl.warnings.append(f"line {lineno}: not a URL: {line[:60]!r}")
            continue
        if pending is None:
            name = line.rsplit("/", 1)[-1].split("?", 1)[0] or line
            pl.warnings.append(f"line {lineno}: URL without #EXTINF")
            pending = M3UEntry(name=name, url="", group=pending_group, line=lineno)
        pending.url = line
        if not pending.name:
            pending.name = pending.attrs.get("tvg-id") or line.rsplit("/", 1)[-1]
        pl.entries.append(pending)
        pending = None
    if pending is not None:
        pl.warnings.append(f"line {pending.line}: #EXTINF without URL at end of file (dropped)")
    if hls_markers and not channel_like:
        pl.kind = "hls"
        pl.entries.clear()
    return pl
