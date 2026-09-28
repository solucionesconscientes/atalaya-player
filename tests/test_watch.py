"""Watch history keyed by content: store semantics and the watch.* methods (in-process server)."""

import asyncio
import shutil
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.watch import WatchStore, content_key, is_finished


def test_store_update_recents_search_and_limits(tmp_path):
    st = WatchStore(tmp_path / "w.sqlite3")
    a = st.update("mu:a", "/v/Película Ñandú.mkv", "Película Ñandú", duration=600, position=10)
    assert a["resume"] is False and a["finished"] is False and a["plays"] == 1  # too early to resume
    a = st.update("mu:a", "/v/Película Ñandú.mkv", "Película Ñandú", duration=600, position=120, new_play=True)
    assert a["resume"] is True and a["plays"] == 2
    b = st.update("url:b", "https://x/y", "Vídeo web", duration=100, position=96)
    assert b["finished"] is True and b["resume"] is False and b["kind"] == "url"
    st.update("mu:c", "/v/c.mkv", "Corto", duration=0, position=500)  # unknown duration → never finished
    assert [r["key"] for r in st.recents()] == ["mu:c", "url:b", "mu:a"]
    assert [r["key"] for r in st.recents(unfinished_only=True)] == ["mu:c", "mu:a"]
    assert [r["key"] for r in st.recents(kind="url")] == ["url:b"]
    assert [r["key"] for r in st.search("nandu")] == ["mu:a"]  # accents ignored, file name matched
    assert [r["key"] for r in st.search("pelicula nandu")] == ["mu:a"]
    assert [r["key"] for r in st.search("")] == ["mu:c", "url:b", "mu:a"]
    assert st.remove("url:b") and not st.remove("url:b") and st.count() == 2
    for i in range(600):
        st.update(f"mu:{i}", f"/v/{i}.mkv", duration=1, position=0)
    assert st.count() == 500
    assert st.clear() == 500 and st.count() == 0
    assert is_finished(575, 600) and is_finished(570, 600) and not is_finished(500, 600) and not is_finished(5, 0)
    st.close()


def test_content_key_moves_with_the_file(tmp_path, media_dir):
    src = media_dir / "video30.mkv"
    copy = tmp_path / "otro nombre.mkv"
    shutil.copyfile(src, copy)
    assert content_key(str(src)) == content_key(str(copy)) and content_key(str(src)).startswith("mu:")
    assert content_key("https://example.com/v?x=1#t") == content_key("https://EXAMPLE.com/v?x=1")
    assert content_key(str(tmp_path / "missing.mkv")).startswith("url:")  # unreadable → path-based fallback


def test_watch_methods(tmp_path, media_dir):
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=2)
    video = str(media_dir / "video30.mkv")

    async def go():
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                assert (await c.call("capabilities"))["services"]["watch"] is True
                empty = await c.call("watch.get", {"path": video})
                assert empty["resume"] is False and empty["key"].startswith("mu:")
                e = await c.call("watch.update", {"path": video, "position": 25, "duration": 30, "title": "Vídeo 30"})
                assert e["resume"] is True and e["key"] == empty["key"]
                copy = tmp_path / "renombrado.mkv"
                shutil.copyfile(video, copy)
                same = await c.call("watch.get", {"path": str(copy)})
                assert same["key"] == e["key"] and same["position"] == 25 and same["resume"] is True
                await c.call("watch.update", {"path": "https://example.com/a", "position": 5, "duration": 10, "title": "Web"})
                assert [r["title"] for r in await c.call("watch.recents")] == ["Web", "Vídeo 30"]
                assert [r["title"] for r in await c.call("watch.search", {"q": "video"})] == ["Vídeo 30"]
                with pytest.raises(RpcError):
                    await c.call("watch.remove", {"key": "mu:nope"})
                assert (await c.call("watch.remove", {"path": video}))["removed"] is True
                assert (await c.call("watch.clear"))["removed"] == 1
                with pytest.raises(RpcError):
                    await c.call("watch.get", {})
        finally:
            await server.stop()

    asyncio.run(go())
