"""File hashing: OpenSubtitles-compatible hash and the blake2b cache key."""

import os
import struct

import pytest

from mpvd.hashing import CHUNK, content_key, file_hash, url_key


def reference_opensubtitles(path) -> str:
    """Independent implementation of the published algorithm."""
    size = os.path.getsize(path)
    h = size
    with open(path, "rb") as f:
        head = f.read(CHUNK)
        f.seek(max(0, size - CHUNK))
        tail = f.read(CHUNK)
    for data in (head, tail):
        fmt = "<%dQ" % (len(data) // 8)
        for v in struct.unpack(fmt, data[: len(data) // 8 * 8]):
            h += v
    return "%016x" % (h & 0xFFFFFFFFFFFFFFFF)


def test_matches_reference_for_big_file(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(os.urandom(3 * CHUNK + 123 * 8))
    fh = file_hash(p)
    assert fh.size == p.stat().st_size
    assert fh.opensubtitles == reference_opensubtitles(p)
    assert len(fh.mu) == 32 and fh.key == f"mu:{fh.mu}"


def test_depends_on_head_tail_and_size_only(tmp_path):
    data = bytearray(os.urandom(4 * CHUNK))
    p = tmp_path / "a.bin"
    p.write_bytes(data)
    base = file_hash(p)
    data[2 * CHUNK] ^= 0xFF  # middle byte: outside the hashed windows
    p.write_bytes(data)
    assert file_hash(p) == base
    data[10] ^= 0xFF  # head
    p.write_bytes(data)
    assert file_hash(p).mu != base.mu
    data[10] ^= 0xFF
    data[-10] ^= 0xFF  # tail
    p.write_bytes(data)
    assert file_hash(p).mu != base.mu
    data[-10] ^= 0xFF
    p.write_bytes(data + b"x")  # size
    assert file_hash(p).mu != base.mu


def test_small_files_and_path_independence(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "sub" / "renamed.txt"
    b.parent.mkdir()
    a.write_bytes(b"hello world" * 100)
    b.write_bytes(b"hello world" * 100)
    assert file_hash(a) == file_hash(b)
    assert file_hash(a).opensubtitles == reference_opensubtitles(a)
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert file_hash(empty).size == 0


def test_content_key_for_urls_and_files(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    assert content_key(str(f)).startswith("mu:")
    assert content_key(f"file://{f}") == content_key(str(f))
    k = content_key("https://Example.com/watch?v=1#t=10")
    assert k.startswith("url:")
    assert k == url_key("https://example.com/watch?v=1")
    assert k != url_key("https://example.com/watch?v=2")


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        file_hash(tmp_path / "nope")
