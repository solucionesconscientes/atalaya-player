"""Content identity for local files and URLs.

* ``opensubtitles``: the classic OpenSubtitles hash (size + sum of the first and last 64 KiB read as
  little-endian uint64s, modulo 2**64) — useful for subtitle lookups.
* ``mu``: blake2b-128 over (size, first 64 KiB, last 64 KiB). Same inputs, collision-resistant; this is
  the cache key used across mpvd ("hash de archivo" in the backlog).
Both are path-independent and cost two 64 KiB reads whatever the file size.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CHUNK = 64 * 1024


@dataclass(frozen=True)
class FileHash:
    size: int
    opensubtitles: str  # 16 hex chars
    mu: str  # 32 hex chars

    @property
    def key(self) -> str:
        return f"mu:{self.mu}"


def _head_tail(path: Path) -> tuple[int, bytes, bytes]:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(CHUNK)
        if size > CHUNK:
            f.seek(max(size - CHUNK, 0))
            tail = f.read(CHUNK)
        else:
            tail = head
    return size, head, tail


def _os_sum(data: bytes) -> int:
    """Sum of little-endian uint64 words; a trailing partial word (files < 128 KiB) is ignored."""
    total = 0
    full = len(data) - (len(data) % 8)
    for (v,) in struct.iter_unpack("<Q", data[:full]):
        total += v
    return total


def file_hash(path: str | Path) -> FileHash:
    p = Path(path)
    size, head, tail = _head_tail(p)
    os_hash = (size + _os_sum(head) + _os_sum(tail)) & 0xFFFFFFFFFFFFFFFF
    h = hashlib.blake2b(digest_size=16)
    h.update(struct.pack("<Q", size))
    h.update(head)
    h.update(tail)
    return FileHash(size=size, opensubtitles=f"{os_hash:016x}", mu=h.hexdigest())


def url_key(url: str) -> str:
    """Stable key for a URL: scheme/host lower-cased, fragment dropped."""
    parts = urlsplit(url.strip())
    norm = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
    return "url:" + hashlib.blake2b(norm.encode("utf-8"), digest_size=16).hexdigest()


def content_key(path_or_url: str) -> str:
    """``mu:<hash>`` for local files, ``url:<hash>`` for anything with a scheme."""
    parts = urlsplit(path_or_url)
    if parts.scheme and parts.scheme not in ("file", "") and len(parts.scheme) > 1:
        return url_key(path_or_url)
    p = path_or_url[7:] if path_or_url.startswith("file://") else path_or_url
    return file_hash(p).key
