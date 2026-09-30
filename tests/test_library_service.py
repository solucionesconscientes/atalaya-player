"""H22 · library.* end to end on an in-process mpvd: folders, background scan (incremental), movies / series → seasons
→ episodes with progress from watch.*, «seguir viendo» + «siguiente episodio», library.next, search, local posters and
ffmpeg frames, settings with secrets in a 0600 file, and TMDB metadata against a local fake server (never contacted
while switched off)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def build_tree(root: Path, media: Path) -> dict[str, Path]:
    films = root / "Películas"
    shows = root / "Series" / "Mi Serie"
    p = {
        "jaws": films / "Tiburón (1975)" / "Tiburón (1975) [BDRip 1080p].mkv",
        "casablanca": films / "Casablanca.1942.1080p.BluRay.x264.mkv",
        "e1": shows / "Temporada 1" / "Mi.Serie.S01E01.Piloto.720p.mkv",
        "e2": shows / "Temporada 1" / "Mi.Serie.S01E02.720p.mkv",
        "e3": shows / "Temporada 1" / "Mi.Serie.S01E03.720p.mkv",
        "s2e1": shows / "Temporada 2" / "01 - Vuelta.mkv",
    }
    src = {"jaws": "video30.mkv", "casablanca": "chapters.mkv", "e1": "serie/ep01.mkv", "e2": "serie/ep02.mkv",
           "e3": "serie/ep03.mkv", "s2e1": "voz_es_en.mkv"}
    for k, dest in p.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(media / src[k], dest)
    (films / "Tiburón (1975)" / "poster.jpg").write_bytes(JPEG)
    (shows / "folder.jpg").write_bytes(JPEG)
    (films / "Casablanca-sample.mkv").write_bytes(b"x" * 10)     # samples are skipped
    (root / ".oculta").mkdir()
    shutil.copyfile(media / "video30.mkv", root / ".oculta" / "no.mkv")  # hidden folders too
    return p


def run(settings: Settings, fn, setup=None):
    async def go():
        server = MpvdServer(settings)
        if setup is not None:
            setup(server)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                return await fn(c, server)
        finally:
            await server.stop()

    return asyncio.run(go())


@pytest.fixture
def settings(tmp_path):
    return Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                    idle_timeout=0, workers=2)


def test_scan_lists_progress_continue_next_search(tmp_path, media_dir, settings):
    lib = tmp_path / "lib"
    p = build_tree(lib, media_dir)
    outside = tmp_path / "suelta"
    outside.mkdir()
    shutil.copyfile(media_dir / "serie/ep01.mkv", outside / "Otra 1x01.mkv")
    shutil.copyfile(media_dir / "serie/ep02.mkv", outside / "Otra 1x02.mkv")
    for f in outside.iterdir():      # other content than the library copies (found by hash otherwise)
        with f.open("ab") as fh:
            fh.write(b"\0" * 512)

    async def go(c, server):
        caps = await c.call("capabilities")
        assert caps["services"]["library"] is True
        assert {"library.continue", "library.next", "library.scan", "library.subs.search"} <= {
            m["name"] for m in caps["methods"]}
        assert await c.call("library.folders.list") == []
        res = await c.call("library.folders.add", {"path": str(lib), "scan": False})
        assert res["added"] is True and res["folders"][0]["path"] == str(lib)
        with pytest.raises(RpcError):
            await c.call("library.folders.add", {"path": str(tmp_path / "no-existe")})
        scan = await c.call("library.scan", {"wait": True})
        r = scan["result"]
        assert (r["added"], r["unchanged"], r["removed"]) == (6, 0, 0), r
        job = server.jobs.get(scan["job"])
        assert job.priority.name == "INDEX"
        status = await c.call("library.status")
        assert (status["movies"], status["shows"], status["episodes"], status["files"]) == (2, 1, 4, 6)
        assert status["settings"]["tmdb_enabled"] is False and status["settings"]["osub_enabled"] is False

        movies = await c.call("library.list", {"kind": "movies"})
        assert [m["title"] for m in movies] == ["Casablanca", "Tiburón"]
        jaws = movies[1]
        assert jaws["year"] == 1975 and jaws["label"] == "Tiburón (1975)"
        assert jaws["poster"] == str(p["jaws"].parent / "poster.jpg")           # local poster wins
        casa = movies[0]
        assert casa["poster"].startswith(str(settings.cache_dir)) and Path(casa["poster"]).stat().st_size > 1000  # ffmpeg frame

        shows = await c.call("library.list", {"kind": "shows"})
        assert len(shows) == 1
        show = shows[0]
        assert (show["title"], show["seasons"], show["episodes"], show["watched"]) == ("Mi Serie", 2, 4, 0)
        assert show["poster"].endswith("folder.jpg")
        seasons = await c.call("library.list", {"show": show["key"]})
        assert [(s["season"], s["label"], s["episodes"]) for s in seasons["seasons"]] == [
            (1, "Temporada 1", 3), (2, "Temporada 2", 1)]
        eps = (await c.call("library.list", {"show": show["key"], "season": 1}))["episodes"]
        assert [e["label"] for e in eps] == ["1x01 · Piloto", "1x02", "1x03"]
        assert eps[0]["full_title"] == "Mi Serie · 1x01 · Piloto" and eps[0]["progress"] == 0
        s2 = (await c.call("library.list", {"show": show["key"], "season": 2}))["episodes"]
        assert [e["label"] for e in s2] == ["2x01 · Vuelta"]

        # nothing watched yet → nothing to continue
        assert await c.call("library.continue") == []

        # episode 1 finished → «siguiente episodio»: 1x02
        await c.call("watch.update", {"path": str(p["e1"]), "position": 38, "duration": 38, "finished": True})
        rows = await c.call("library.continue")
        assert [(r["row"], r["label"]) for r in rows] == [("next", "1x02")]
        # the movie half watched → «seguir viendo» on top (most recent first)
        await asyncio.sleep(0.01)
        await c.call("watch.update", {"path": str(p["jaws"]), "position": 21, "duration": 30})
        rows = await c.call("library.continue")
        assert [(r["row"], r["title"]) for r in rows] == [("continue", "Tiburón"), ("next", "Mi Serie")]
        assert rows[0]["progress"] == pytest.approx(0.7)
        # episode 2 started → the series row becomes «seguir viendo» 1x02
        await asyncio.sleep(0.01)
        await c.call("watch.update", {"path": str(p["e2"]), "position": 25, "duration": 38})
        rows = await c.call("library.continue")
        assert [(r["row"], r.get("label")) for r in rows] == [("continue", "1x02"), ("continue", "Tiburón (1975)")]
        eps = (await c.call("library.list", {"show": show["key"], "season": 1}))["episodes"]
        assert [e["finished"] for e in eps] == [True, False, False] and eps[1]["progress"] == pytest.approx(25 / 38, 1e-3)
        shows = await c.call("library.list", {"kind": "shows"})
        assert shows[0]["watched"] == 1 and shows[0]["next"] == "1x02"
        # 1x02 and 1x03 finished → next is the new season
        await c.call("watch.update", {"path": str(p["e2"]), "position": 38, "duration": 38, "finished": True})
        await asyncio.sleep(0.01)
        await c.call("watch.update", {"path": str(p["e3"]), "position": 38, "duration": 38, "finished": True})
        rows = await c.call("library.continue")
        assert rows[0]["row"] == "next" and rows[0]["label"] == "2x01 · Vuelta" and rows[0]["path"] == str(p["s2e1"])
        assert await c.call("library.continue", {"limit": 1}) == rows[:1]

        # library.next: the following episode, across seasons; none after the last; folder fallback outside
        nxt = await c.call("library.next", {"path": str(p["e1"])})
        assert nxt["next"]["path"] == str(p["e2"]) and nxt["next"]["source"] == "library"
        assert nxt["current"]["label"] == "1x01 · Piloto"
        assert (await c.call("library.next", {"path": str(p["e3"])}))["next"]["path"] == str(p["s2e1"])
        assert (await c.call("library.next", {"path": str(p["s2e1"])}))["next"] is None
        assert (await c.call("library.next", {"path": str(p["jaws"])}))["next"] is None
        moved = tmp_path / "copia de ep1.mkv"      # same content elsewhere: found by its hash
        shutil.copyfile(p["e1"], moved)
        assert (await c.call("library.next", {"path": str(moved)}))["next"]["path"] == str(p["e2"])
        out = await c.call("library.next", {"path": str(outside / "Otra 1x01.mkv")})
        assert out["next"]["source"] == "folder" and out["next"]["path"] == str(outside / "Otra 1x02.mkv")

        # search: accents ignored, shows / movies / episodes
        found = await c.call("library.search", {"q": "tiburon"})
        assert [m["title"] for m in found["movies"]] == ["Tiburón"] and found["shows"] == []
        found = await c.call("library.search", {"q": "mi serie"})
        assert [s["title"] for s in found["shows"]] == ["Mi Serie"] and len(found["episodes"]) == 4
        assert (await c.call("library.search", {"q": "piloto"}))["episodes"][0]["path"] == str(p["e1"])

        # posters on demand
        assert (await c.call("library.poster", {"path": str(p["jaws"])}))["poster"].endswith("poster.jpg")

        # incremental: unchanged files are not hashed again; new / changed / deleted ones are
        r = (await c.call("library.scan", {"wait": True}))["result"]
        assert (r["added"], r["updated"], r["unchanged"], r["removed"]) == (0, 0, 6, 0)
        shutil.copyfile(media_dir / "video30.mkv", lib / "Películas" / "Nueva (2020).mkv")
        with p["casablanca"].open("ab") as f:
            f.write(b"\0" * 1024)
        p["e3"].unlink()
        r = (await c.call("library.scan", {"wait": True}))["result"]
        assert (r["added"], r["updated"], r["unchanged"], r["removed"]) == (1, 1, 4, 1), r
        assert (await c.call("library.status"))["episodes"] == 3

        # a folder inside another one: its files are not hashed again nor counted twice
        await c.call("library.folders.add", {"path": str(lib / "Series"), "scan": False})
        r = (await c.call("library.scan", {"wait": True}))["result"]
        assert (r["added"], r["updated"], r["removed"], r["seen"]) == (0, 0, 0, 6), r
        with pytest.raises(RpcError):
            await c.call("library.scan", {"path": str(tmp_path)})       # only folders of the library
        assert (await c.call("library.folders.remove", {"path": str(lib / "Series")}))["removed"] == 0
        assert (await c.call("library.status"))["files"] == 6

        # removing the folder forgets its files (the files stay on disk)
        with pytest.raises(RpcError):
            await c.call("library.folders.remove", {"path": str(tmp_path)})
        res = await c.call("library.folders.remove", {"path": str(lib)})
        assert res["removed"] == 6 and res["folders"] == []
        assert (await c.call("library.status"))["files"] == 0 and p["e1"].exists()
        return True

    assert run(settings, go)


class FakeTmdb(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *args):  # noqa: D102 - silence
        pass

    def do_GET(self):  # noqa: N802
        FakeTmdb.hits.append(self.path)
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path.startswith("/img/"):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(JPEG)
            return
        if q.get("api_key") != ["clave-tmdb"]:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"status_code":7,"status_message":"Invalid API key"}')
            return
        if u.path == "/3/search/movie":
            body = {"page": 1, "total_pages": 1, "total_results": 1, "results": [{
                "adult": False, "backdrop_path": None, "genre_ids": [18], "id": 578, "original_language": "en",
                "original_title": "Jaws", "overview": "Un tiburón aterroriza Amity.", "popularity": 50.1,
                "poster_path": "/tiburon.jpg", "release_date": "1975-06-20", "title": "Tiburón", "video": False,
                "vote_average": 7.7, "vote_count": 10000}]}
        elif u.path == "/3/search/tv":
            body = {"page": 1, "total_pages": 1, "total_results": 1, "results": [{
                "adult": False, "backdrop_path": None, "genre_ids": [35], "id": 99, "origin_country": ["ES"],
                "original_language": "es", "original_name": "Mi Serie", "overview": "Una serie de prueba.",
                "popularity": 1.0, "poster_path": "/serie.jpg", "first_air_date": "2020-01-01", "name": "Mi Serie",
                "vote_average": 6.0, "vote_count": 3}]}
        else:
            body = {"page": 1, "results": [], "total_pages": 0, "total_results": 0}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_tmdb():
    FakeTmdb.hits = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeTmdb)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_settings_secrets_and_tmdb_only_when_enabled(tmp_path, media_dir, settings, fake_tmdb):
    lib = tmp_path / "lib"
    p = build_tree(lib, media_dir)
    (p["jaws"].parent / "poster.jpg").unlink()

    def setup(server):
        server.library.tmdb_url = fake_tmdb + "/3"
        server.library.tmdb_image_url = fake_tmdb + "/img"

    async def go(c, server):
        s = await c.call("library.settings.get")
        assert s["tmdb_enabled"] is False and s["has_tmdb_key"] is False and s["osub_resync"] == "auto"
        # the key alone does not switch anything on
        s = await c.call("library.settings.set", {"tmdb_key": "clave-tmdb", "osub_api_key": "k", "osub_username": "ana",
                                                  "osub_password": "secreto"})
        assert s["has_tmdb_key"] and s["tmdb_active"] is False and "tmdb_key" not in s and "osub_password" not in s
        assert s["osub_username"] == "ana" and s["has_osub_password"] is True
        secrets = settings.data_dir / "library-secrets.json"
        assert stat.S_IMODE(os.stat(secrets).st_mode) == 0o600
        assert json.loads(secrets.read_text())["osub_password"] == "secreto"
        assert "secreto" not in (settings.data_dir / "library-settings.json").read_text() if (
            settings.data_dir / "library-settings.json").exists() else True
        with pytest.raises(RpcError):
            await c.call("library.settings.set", {"osub_resync": "a veces"})
        with pytest.raises(RpcError):
            await c.call("library.settings.set", {"no_existe": True})
        s = await c.call("library.settings.set", {"osub_languages": "EN, es,es"})
        assert s["osub_languages"] == "en,es"

        await c.call("library.folders.add", {"path": str(lib), "scan": False})
        await c.call("library.scan", {"wait": True})
        assert FakeTmdb.hits == []                     # switched off: never contacted
        movies = await c.call("library.list", {"kind": "movies"})
        assert "overview" not in movies[1]

        s = await c.call("library.settings.set", {"tmdb_enabled": True})
        assert s["tmdb_active"] is True
        await c.call("library.scan", {"wait": True})
        assert any(h.startswith("/3/search/movie") for h in FakeTmdb.hits)
        assert any(h.startswith("/3/search/tv") for h in FakeTmdb.hits)
        movies = await c.call("library.list", {"kind": "movies"})
        jaws = next(m for m in movies if m["title"] == "Tiburón")
        assert jaws["overview"] == "Un tiburón aterroriza Amity."
        assert jaws["poster"].endswith("movie-578.jpg") and Path(jaws["poster"]).read_bytes() == JPEG
        show = (await c.call("library.list", {"kind": "shows"}))[0]
        assert show["poster"].endswith("folder.jpg")    # local poster still wins over TMDB
        assert show["overview"] == "Una serie de prueba."
        first = [h for h in FakeTmdb.hits if "/search/movie" in h and "Tib" in h]
        assert first and "year=1975" in first[0]
        n = len(FakeTmdb.hits)
        await c.call("library.scan", {"wait": True})
        assert len(FakeTmdb.hits) == n                 # already known: no new requests
        # forgetting the key switches it off again
        s = await c.call("library.settings.set", {"tmdb_key": ""})
        assert s["has_tmdb_key"] is False and s["tmdb_active"] is False
        return True

    assert run(settings, go, setup)
    # settings survive a restart
    async def again(c, server):
        return await c.call("library.settings.get")
    s = run(settings, again)
    assert s["tmdb_enabled"] is True and s["osub_languages"] == "en,es" and s["osub_username"] == "ana"


def test_removing_a_folder_keeps_the_files_of_a_subfolder_still_in_the_library(tmp_path, media_dir):
    """H34 · las filas llevan la carpeta donde se vieron primero: quitar la de arriba se llevaba las de la subcarpeta."""
    from mpvd.library.store import LibraryStore

    root = tmp_path / "Vídeos"
    (root / "Series").mkdir(parents=True)
    shutil.copyfile(media_dir / "video30.mkv", root / "Una peli (2020).mkv")
    shutil.copyfile(media_dir / "serie/ep01.mkv", root / "Series" / "Mi.Serie.S01E01.mkv")

    store = LibraryStore(tmp_path / "lib.sqlite3")
    try:
        store.add_folder(str(root))
        store.add_folder(str(root / "Series"))
        store.scan([str(root), str(root / "Series")])
        assert len(store.items()) == 2

        removed = store.remove_folder(str(root))
        rows = store.items()
        assert removed == 1 and len(rows) == 1
        assert rows[0]["path"].endswith("Mi.Serie.S01E01.mkv") and rows[0]["folder"] == str(root / "Series")
    finally:
        store.close()
