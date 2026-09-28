"""Artifact cache: keys, inline data vs blobs, invalidation, stats and LRU pruning."""

import time

import pytest

from mpvd.cache import ArtifactCache, canonical_params


@pytest.fixture
def cache(tmp_path):
    c = ArtifactCache(tmp_path / "cache")
    yield c
    c.close()


def test_put_get_inline_json(cache):
    assert cache.get("mu:1", "subs") is None
    e = cache.put("mu:1", "subs", model="whisper-small", version="1", params={"lang": "es"}, data={"segments": [1, 2]})
    got = cache.get("mu:1", "subs", model="whisper-small", version="1", params={"lang": "es"})
    assert got is not None and got.id == e.id and got.data == {"segments": [1, 2]}
    assert cache.get("mu:1", "subs", model="whisper-small", version="1", params={"lang": "en"}) is None
    assert cache.get("mu:1", "subs", model="whisper-small", version="2", params={"lang": "es"}) is None
    assert got.last_access >= e.last_access


def test_params_are_canonical():
    assert canonical_params({"b": 1, "a": [1, 2]}) == canonical_params({"a": [1, 2], "b": 1})
    assert canonical_params(None) == "{}"


def test_blob_bytes_and_file_move(cache, tmp_path):
    e = cache.put("mu:2", "audio", blob=b"\x00\x01\x02", blob_suffix=".pcm")
    assert e.blob_path is not None and e.blob_path.read_bytes() == b"\x00\x01\x02" and e.size == 3
    src = tmp_path / "out.srt"
    src.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n", encoding="utf-8")
    e2 = cache.put("mu:2", "srt", blob=src, data={"lang": "es"})
    assert not src.exists()  # moved into the cache
    assert e2.blob_path is not None and e2.blob_path.suffix == ".srt" and e2.data == {"lang": "es"}
    got = cache.get("mu:2", "srt")
    assert got is not None and got.blob_path == e2.blob_path
    e2.blob_path.unlink()  # blob vanished on disk -> entry is dropped on read
    assert cache.get("mu:2", "srt") is None


def test_put_replaces_and_deletes_old_blob(cache):
    e1 = cache.put("mu:3", "x", blob=b"a")
    e2 = cache.put("mu:3", "x", blob=b"bb")
    assert e1.blob_path is not None and not e1.blob_path.exists()
    assert e2.blob_path is not None and e2.blob_path.read_bytes() == b"bb"
    assert cache.stats()["entries"] == 1


def test_invalidate_by_fields(cache):
    cache.put("mu:a", "subs", model="m1", data=1)
    cache.put("mu:a", "subs", model="m2", data=2)
    cache.put("mu:a", "thumbs", data=3)
    cache.put("mu:b", "subs", model="m1", data=4)
    assert cache.invalidate(file_hash="mu:a", artifact="subs", model="m1") == 1
    assert cache.invalidate(artifact="subs") == 2
    assert [e.artifact for e in cache.list()] == ["thumbs"]
    assert cache.invalidate() == 1
    assert cache.stats()["entries"] == 0


def test_stats_and_prune_lru(cache):
    for i in range(5):
        cache.put(f"mu:{i}", "blob", blob=b"x" * 100)
        time.sleep(0.01)
    cache.get("mu:0", "blob")  # touch the oldest so it becomes most recent
    st = cache.stats()
    assert st["entries"] == 5 and st["bytes"] == 500 and st["by_artifact"]["blob"]["entries"] == 5
    removed = cache.prune(max_bytes=250)
    assert removed == 3
    left = {e.file_hash for e in cache.list()}
    assert left == {"mu:0", "mu:4"}
    assert cache.prune(max_bytes=10_000) == 0


def test_put_requires_payload(cache):
    with pytest.raises(ValueError):
        cache.put("mu:z", "x")
