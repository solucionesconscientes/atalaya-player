"""Podcast feeds: RSS 2.0 (+ iTunes / Media RSS) and Atom, parsed with ``xml.etree`` (no dependencies).

Only what a subscription needs: the feed title and, per episode, a stable id (``guid`` → enclosure URL → link), the title,
the media URL (``<enclosure>``, ``<media:content>``; Atom ``link rel=enclosure`` or, like YouTube's channel feeds, the
``alternate`` page that yt-dlp can download), its type and size, the publication date and the duration. The document is
read with ``iterparse`` and every finished episode element is cleared, so a 20 MB feed (they exist) stays cheap.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
MEDIA = "http://search.yahoo.com/mrss/"
ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"
MEDIA_MIME = re.compile(r"^(audio|video)/", re.IGNORECASE)
MEDIA_EXT = re.compile(r"\.(mp3|m4a|m4b|aac|ogg|oga|opus|flac|wav|mp4|m4v|mkv|webm|mov)(\?|$)", re.IGNORECASE)
MAX_BYTES = 64 * 1024 * 1024


class FeedError(ValueError):
    pass


@dataclass
class FeedEntry:
    id: str
    title: str
    url: str                       # what gets downloaded (enclosure, or the entry page for yt-dlp)
    mime: str = ""
    size: int = 0
    published: float | None = None  # epoch seconds (UTC)
    duration: float | None = None
    link: str = ""
    direct: bool = True            # url is the media file itself (enclosure) rather than a page

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_video(self) -> bool:
        return self.mime.lower().startswith("video/")


@dataclass
class Feed:
    title: str = ""
    link: str = ""
    language: str = ""
    kind: str = "rss"               # rss | atom
    entries: list[FeedEntry] = field(default_factory=list)

    def newest_first(self) -> list[FeedEntry]:
        """Newest first (by date; undated episodes keep the document order after the dated ones)."""
        dated = [e for e in self.entries if e.published is not None]
        undated = [e for e in self.entries if e.published is None]
        return sorted(dated, key=lambda e: e.published or 0.0, reverse=True) + undated


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def parse_date(text: str | None) -> float | None:
    """RFC 822 (RSS ``pubDate``) or ISO 8601 (Atom ``published``/``updated``) → epoch seconds; None when unreadable.
    A date without a zone is taken as UTC."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        d = email.utils.parsedate_to_datetime(t)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def parse_duration(text: str | None) -> float | None:
    """``itunes:duration``: seconds (``280``), ``MM:SS`` or ``HH:MM:SS``."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        parts = [float(p) for p in t.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def looks_like_feed(head: bytes, content_type: str = "") -> bool:
    """First bytes of a response (and its Content-Type) → is this an RSS/Atom document?"""
    ct = (content_type or "").lower()
    if any(x in ct for x in ("rss", "atom")):
        return True
    text = head[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if not text.startswith(b"<"):
        return False
    return b"<rss" in text or b"<feed" in text or b"<rdf:rdf" in text


def _int(text: str | None) -> int:
    try:
        return max(0, int(str(text or "").strip()))
    except ValueError:
        return 0


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _entry_from_rss(item: ET.Element) -> FeedEntry | None:
    title = guid = link = pub = ""
    url = mime = ""
    size = 0
    duration = None
    media_url = media_mime = ""
    for child in item:
        name, space = local(child.tag), ns(child.tag)
        if name == "title" and not space:
            title = _text(child)
        elif name == "title" and space == ITUNES and not title:
            title = _text(child)
        elif name == "guid":
            guid = _text(child)
        elif name == "link" and not space:
            link = _text(child)
        elif name == "pubDate" or (name == "date" and not pub):
            pub = _text(child)
        elif name == "enclosure" and not url:
            u = (child.get("url") or "").strip()
            if u:
                url, mime, size = u, (child.get("type") or "").strip(), _int(child.get("length"))
        elif name == "duration" and space == ITUNES:
            duration = parse_duration(child.text)
        elif name in ("content", "group") and space == MEDIA and not media_url:
            nodes = [child] if name == "content" else [c for c in child if local(c.tag) == "content"]
            for c in nodes:
                u = (c.get("url") or "").strip()
                m = (c.get("type") or c.get("medium") or "").strip()
                if u and (MEDIA_MIME.match(m) or m in ("audio", "video") or MEDIA_EXT.search(u)):
                    media_url, media_mime = u, m if "/" in m else (f"{m}/*" if m else "")
                    if duration is None and c.get("duration"):
                        duration = parse_duration(c.get("duration"))
                    break
    if not url and media_url:
        url, mime = media_url, media_mime
    direct = bool(url)
    if not url and link:
        url, direct = link, False      # no media file: the page (yt-dlp may know the site)
    if not url:
        return None
    return FeedEntry(id=guid or url or link, title=title or link or url, url=url, mime=mime, size=size,
                     published=parse_date(pub), duration=duration, link=link, direct=direct)


def _entry_from_atom(entry: ET.Element) -> FeedEntry | None:
    title = eid = pub = upd = alternate = ""
    url = mime = ""
    size = 0
    duration = None
    video_id = ""
    for child in entry:
        name, space = local(child.tag), ns(child.tag)
        if name == "title" and space == ATOM:
            title = "".join(child.itertext()).strip()
        elif name == "id" and space == ATOM:
            eid = _text(child)
        elif name == "published":
            pub = _text(child)
        elif name == "updated":
            upd = _text(child)
        elif name == "videoId" and space == YT:
            video_id = _text(child)
        elif name == "link" and space == ATOM:
            rel = child.get("rel") or "alternate"
            href = (child.get("href") or "").strip()
            if rel == "enclosure" and href and not url:
                url, mime, size = href, (child.get("type") or "").strip(), _int(child.get("length"))
            elif rel == "alternate" and href and not alternate:
                alternate = href
        elif name == "duration" and space == ITUNES:
            duration = parse_duration(child.text)
    direct = bool(url)
    if not url:
        url = alternate
    if not url:
        return None
    return FeedEntry(id=video_id or eid or url, title=title or url, url=url, mime=mime, size=size,
                     published=parse_date(pub or upd), duration=duration, link=alternate, direct=direct)


def parse_feed(data: bytes | str, max_entries: int = 500) -> Feed:
    """Parse an RSS or Atom document; raises FeedError when it is neither (or not XML at all)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise FeedError("el feed es demasiado grande")
    feed = Feed()
    root_seen = False
    depth = 0
    try:
        for event, el in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
            name, space = local(el.tag), ns(el.tag)
            if event == "start":
                depth += 1
                if not root_seen:
                    root_seen = True
                    if name == "feed" and space == ATOM:
                        feed.kind = "atom"
                    elif name not in ("rss", "RDF"):
                        raise FeedError(f"no es un feed RSS ni Atom (<{name}>)")
                continue
            depth -= 1
            if name == "item" and feed.kind == "rss":
                e = _entry_from_rss(el)
                if e is not None and len(feed.entries) < max_entries:
                    feed.entries.append(e)
                el.clear()
            elif name == "entry" and space == ATOM:
                e = _entry_from_atom(el)
                if e is not None and len(feed.entries) < max_entries:
                    feed.entries.append(e)
                el.clear()
            elif feed.kind == "rss" and depth == 2 and not space:
                # direct children of <channel> (rss > channel > x)
                if name == "title" and not feed.title:
                    feed.title = _text(el)
                elif name == "link" and not feed.link:
                    feed.link = _text(el)
                elif name == "language":
                    feed.language = _text(el)
            elif feed.kind == "atom" and depth == 1 and space == ATOM:
                if name == "title" and not feed.title:
                    feed.title = "".join(el.itertext()).strip()
                elif name == "link" and (el.get("rel") or "alternate") == "alternate" and not feed.link:
                    feed.link = el.get("href") or ""
    except ET.ParseError as exc:
        raise FeedError(f"XML no válido: {exc}") from exc
    if not root_seen:
        raise FeedError("documento vacío")
    return feed


__all__ = ["Feed", "FeedEntry", "FeedError", "looks_like_feed", "parse_date", "parse_duration", "parse_feed"]
