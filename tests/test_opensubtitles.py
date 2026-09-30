"""H22 · Subtitles from OpenSubtitles.com (REST v1) against a local fake that imitates the real API (headers, login with
base_url, /subtitles by hash and by name, /download with quota, links to the file): off by default, search by the
file's hash first and by name otherwise, ranking, cache (no second download), quota errors, account login, the
OpenSubtitles hash checked against the official reference code, and the resync decision of ADR-050."""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.hashing import file_hash
from mpvd.library.opensubtitles import OpenSubtitles, OpenSubtitlesError, rank
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer

FIX = Path(__file__).parent / "fixtures" / "opensubtitles"
SRT = "1\n00:00:01,000 --> 00:00:02,500\nHola {id}\n\n2\n00:00:03,000 --> 00:00:04,000\nAdiós\n"


def reference_hash(name: str) -> str:
    """The Python reference published by OpenSubtitles ("Hash Source Codes"), unchanged but for style."""
    longlongformat = "<q"
    bytesize = struct.calcsize(longlongformat)
    with open(name, "rb") as f:
        filesize = os.path.getsize(name)
        h = filesize
        if filesize < 65536 * 2:
            return "SizeError"
        for _ in range(65536 // bytesize):
            (value,) = struct.unpack(longlongformat, f.read(bytesize))
            h = (h + value) & 0xFFFFFFFFFFFFFFFF
        f.seek(max(0, filesize - 65536), 0)
        for _ in range(65536 // bytesize):
            (value,) = struct.unpack(longlongformat, f.read(bytesize))
            h = (h + value) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def test_hash_matches_the_official_reference(tmp_path, media_dir):
    rnd = random.Random(7)
    files = [media_dir / "video30.mkv", media_dir / "serie" / "ep01.mkv"]
    for size in (131072, 200_003, 1_000_000):
        p = tmp_path / f"r{size}.bin"
        p.write_bytes(bytes(rnd.getrandbits(8) for _ in range(size)))
        files.append(p)
    for f in files:
        assert file_hash(f).opensubtitles == reference_hash(str(f)), f


class FakeApi(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *args):  # noqa: D102
        pass

    def _send(self, code: int, body: dict | bytes | None) -> None:
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
        self.send_response(code)
        self.send_header("Content-Type", "application/json" if not isinstance(body, bytes) else "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _check(self) -> bool:
        st = FakeApi.state
        st["requests"].append((self.command, self.path, dict(self.headers)))
        if self.path.startswith("/files/"):
            return True
        if self.headers.get("Api-Key") != "clave-os":
            self._send(403, {"message": "You cannot consume this service"})
            return False
        ua = self.headers.get("User-Agent", "")
        if not ua or " v" not in ua:
            self._send(403, {"message": "User-Agent header is wrong; set it to App name with version eg: MyApp v1.2.3"})
            return False
        if self.headers.get("Accept") != "*/*":
            self._send(406, None)
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):  # noqa: N802
        if not self._check():
            return
        st = FakeApi.state
        u = urlsplit(self.path)
        if u.path.startswith("/files/"):
            fid = u.path.rsplit("/", 1)[-1].split(".")[0]
            self._send(200, SRT.format(id=fid).encode())
            return
        if u.path == "/api/v1/subtitles":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            st["searches"].append((u.query, q))
            data = json.loads((FIX / "search.json").read_text())
            items = data["data"]
            if "moviehash" in q:
                if q["moviehash"] != st.get("hash"):
                    items = []
                else:
                    items = [i for i in items if i["attributes"]["files"][0]["file_id"] in (2000002, 1348525)]
                    for i in items:
                        i["attributes"]["moviehash_match"] = i["attributes"]["files"][0]["file_id"] == 2000002
            langs = q.get("languages", "").split(",")
            items = [i for i in items if i["attributes"]["language"] in langs]
            data.update({"data": items, "total_count": len(items)})
            self._send(200, data)
            return
        self._send(404, {"message": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._check():
            return
        st = FakeApi.state
        body = self._body()
        port = self.server.server_address[1]
        if self.path == "/api/v1/login":
            if body != {"username": "ana", "password": "secreto"}:
                self._send(401, {"message": "Error, invalid username/password", "status": 401})
                return
            data = json.loads((FIX / "login.json").read_text())
            data["base_url"] = f"127.0.0.1:{port}"      # the real one: "api.opensubtitles.com" / "vip-api…"
            st["logins"] += 1
            self._send(200, data)
            return
        if self.path == "/api/v1/download":
            auth = self.headers.get("Authorization", "")
            st["downloads"].append((body, auth))
            if st.get("quota"):
                self._send(406, json.loads((FIX / "quota.json").read_text()))
                return
            data = json.loads((FIX / "download.json").read_text())
            data["link"] = f"http://127.0.0.1:{port}/files/{body['file_id']}.srt"
            self._send(200, data)
            return
        self._send(404, {"message": "not found"})

    def do_DELETE(self):  # noqa: N802
        if not self._check():
            return
        self._send(200, {"message": "token successfully destroyed", "status": 200})


@pytest.fixture
def fake_api():
    FakeApi.state = {"requests": [], "searches": [], "downloads": [], "logins": 0}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/v1", FakeApi.state
    srv.shutdown()
    srv.server_close()


def test_client_headers_query_rules_login_and_errors(fake_api):
    base, st = fake_api
    with pytest.raises(OpenSubtitlesError):
        OpenSubtitles("", base_url=base)
    bad = OpenSubtitles("otra", base_url=base)
    with pytest.raises(OpenSubtitlesError) as e:
        bad.search(query="x", languages="es")
    assert e.value.status == 403 and "consume" in e.value.message
    c = OpenSubtitles("clave-os", "ana", "secreto", base_url=base)
    res = c.search(query="Mi Serie", languages="es,en", season=1, episode=2, kind="episode")
    raw, q = st["searches"][-1]
    # alphabetical, lower-case, "+" for spaces, languages sorted
    assert raw == "episode_number=2&languages=en%2Ces&query=mi+serie&season_number=1&type=episode"
    method, _path, headers = st["requests"][-1]
    assert headers["Api-Key"] == "clave-os" and headers["Accept"] == "*/*" and headers["User-Agent"].startswith("MPV-UOS v")
    assert "Authorization" not in headers
    assert {r["file_id"] for r in res} == {1348525, 2000001, 2000002}
    ranked = rank(res, ["es", "en"])
    assert [r["file_id"] for r in ranked] == [2000002, 2000001, 1348525]   # es first, not SDH first, then en
    info = c.login()
    assert info["allowed_downloads"] == 100 and c.token.startswith("eyJ") and c.base_url == base
    dl = c.download(2000002)
    assert dl.content.decode().startswith("1\n") and "Hola 2000002" in dl.content.decode() and dl.remaining == 97
    assert st["downloads"][-1] == ({"file_id": 2000002}, "Bearer " + c.token)
    wrong = OpenSubtitles("clave-os", "ana", "mal", base_url=base)
    with pytest.raises(OpenSubtitlesError) as e:
        wrong.login()
    assert e.value.status == 401
    st["quota"] = True
    with pytest.raises(OpenSubtitlesError) as e:
        c.download(2000002)
    assert e.value.status == 406 and "cupo" in e.value.message
    c.logout()
    assert st["requests"][-1][0] == "DELETE" and c.token == ""


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


def test_library_subs_off_by_default_hash_then_name_cache_and_resync(tmp_path, media_dir, fake_api):
    base, st = fake_api
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=2)
    lib = tmp_path / "lib" / "Mi Serie" / "Temporada 1"
    lib.mkdir(parents=True)
    ep = lib / "Mi.Serie.S01E02.720p.mkv"
    shutil.copyfile(media_dir / "serie" / "ep02.mkv", ep)
    other = tmp_path / "Mi.Serie.S01E02.otra.version.mkv"
    shutil.copyfile(media_dir / "serie" / "ep03.mkv", other)
    st["hash"] = file_hash(ep).opensubtitles
    resyncs: list[tuple] = []

    async def fake_resync(path, srt, language, model, notify, session_id):
        resyncs.append((path, srt, language))
        out = Path(srt).with_name(Path(srt).stem + ".resync.srt")
        out.write_text(Path(srt).read_text(encoding="utf-8"), encoding="utf-8")
        return {"status": "done", "srt": str(out), "stats": {"ok": True, "matched": 10}}

    def setup(server):
        server.library.osub_url = base
        server.library.resync_fn = fake_resync

    async def go(c, server):
        # off by default: an error and not a single request to the API
        with pytest.raises(RpcError) as e:
            await c.call("library.subs.search", {"path": str(ep)})
        assert "desactivad" in e.value.message and st["requests"] == []
        await c.call("library.settings.set", {"osub_enabled": True})
        with pytest.raises(RpcError) as e:
            await c.call("library.subs.search", {"path": str(ep)})
        assert "Api-Key" in e.value.message and st["requests"] == []
        await c.call("library.settings.set", {"osub_api_key": "clave-os", "osub_languages": "es,en"})

        # by hash: the exact file → hash match first; the name is not needed
        found = await c.call("library.subs.search", {"path": str(ep)})
        assert found["hash"] == st["hash"] and found["hash_matches"] == 1 and found["query"] == ""
        assert [r["file_id"] for r in found["results"]] == [2000002, 1348525]
        assert found["results"][0]["hash_match"] is True and found["results"][0]["language"] == "es"
        assert len(st["searches"]) == 1 and st["searches"][0][1]["moviehash"] == st["hash"]
        # cached search: no new request
        await c.call("library.subs.search", {"path": str(ep)})
        assert len(st["searches"]) == 1

        # download the best one: anonymous (no account), into the cache; a hash match is not resynced
        got = await c.call("library.subs.download", {"path": str(ep)})
        assert got["file_id"] == 2000002 and got["language"] == "es" and got["hash_match"] is True
        assert got["resync"] == "skipped" and got["cached"] is False and got["remaining"] == 97
        assert Path(got["srt"]).read_text(encoding="utf-8").startswith("1\n") and str(settings.cache_dir) in got["srt"]
        assert st["downloads"][-1] == ({"file_id": 2000002}, "")
        again = await c.call("library.subs.download", {"path": str(ep), "file_id": 2000002})
        assert again["cached"] is True and len(st["downloads"]) == 1       # never downloaded twice
        assert resyncs == []

        # another release of the same episode: no hash match → search by name, resync (Whisper) automatically
        found = await c.call("library.subs.search", {"path": str(other)})
        assert found["hash_matches"] == 0 and found["query"] == "Mi Serie"
        q = st["searches"][-1][1]
        assert (q["query"], q["season_number"], q["episode_number"], q["type"]) == ("mi serie", "1", "2", "episode")
        got = await c.call("library.subs.download", {"path": str(other), "audio_lang": "spa"})
        assert got["hash_match"] is False and got["resync"] == "done" and got["srt"].endswith(".resync.srt")
        assert got["original"] != got["srt"] and resyncs[-1][0] == str(other) and resyncs[-1][2] == "es"
        # an English subtitle for Spanish audio is never aligned against the voice
        got = await c.call("library.subs.download", {"path": str(other), "file_id": 1348525, "audio_lang": "spa"})
        assert got["language"] == "en" and got["resync"] == "skipped" and "idioma" in got["resync_reason"]
        # the setting: never
        await c.call("library.settings.set", {"osub_resync": "never"})
        got = await c.call("library.subs.download", {"path": str(other), "file_id": 2000002})
        assert got["resync"] == "skipped"
        n = len(resyncs)
        # "always" also resyncs hash matches
        await c.call("library.settings.set", {"osub_resync": "always"})
        got = await c.call("library.subs.download", {"path": str(ep)})
        assert got["resync"] == "done" and len(resyncs) == n + 1

        # with an account: login first, Bearer on /download; quota errors are readable
        await c.call("library.settings.set", {"osub_username": "ana", "osub_password": "secreto", "osub_resync": "never"})
        got = await c.call("library.subs.download", {"path": str(other), "file_id": 2000001})
        assert st["logins"] == 1 and st["downloads"][-1][1].startswith("Bearer eyJ")
        st["quota"] = True
        with pytest.raises(RpcError) as e:
            await c.call("library.subs.download", {"path": str(ep), "file_id": 1348525})
        assert "cupo" in e.value.message
        # the password never ends up in the cache or the public settings
        for f in settings.cache_dir.rglob("*"):
            if f.is_file():
                assert b"secreto" not in f.read_bytes(), f
        assert "secreto" not in json.dumps(await c.call("library.settings.get"))
        return True

    assert run(settings, go, setup)
