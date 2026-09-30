"""Accent-insensitive in-memory search over channels (fast enough for ~20k entries)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable

from mpvd.iptv.model import Channel

_WS = re.compile(r"[\s_\-./,:;|()\[\]]+")


def normalize(text: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return _WS.sub(" ", stripped.casefold()).strip()


class SearchIndex:
    def __init__(self, channels: Iterable[Channel] = ()):
        self._items: list[tuple[str, str, Channel]] = []  # (norm name, norm haystack, channel)
        for ch in channels:
            self.add(ch)

    def add(self, ch: Channel) -> None:
        name = normalize(ch.name)
        hay = " ".join(filter(None, (name, normalize(ch.group or ""), normalize(ch.category or ""),
                                     normalize(ch.country or ""), normalize(ch.tvg_id or ""))))
        self._items.append((name, hay, ch))

    def __len__(self) -> int:
        return len(self._items)

    def search(self, query: str, limit: int = 50, kind: str | None = None, source: str | None = None,
               where: Callable[[Channel], bool] | None = None) -> list[Channel]:
        """Channels whose name/group/category/country/tvg-id contain every word of ``query`` (accents and case
        ignored), best matches first; ``where`` narrows the search to a subset (a country, a group...)."""
        q = normalize(query)
        if not q:
            return []
        tokens = q.split()
        scored: list[tuple[int, int, Channel]] = []
        for pos, (name, hay, ch) in enumerate(self._items):
            if kind and ch.kind != kind:
                continue
            if source and ch.source != source:
                continue
            if where is not None and not where(ch):
                continue
            if not all(t in hay for t in tokens):
                continue
            if name == q:
                score = 0
            elif name.startswith(q):
                score = 1
            elif all(t in name for t in tokens):
                score = 2 if any(w.startswith(tokens[0]) for w in name.split()) else 3
            else:
                score = 4
            scored.append((score, pos, ch))
        scored.sort(key=lambda t: (t[0], len(t[2].name), t[1]))
        return [ch for _, _, ch in scored[:limit]]
