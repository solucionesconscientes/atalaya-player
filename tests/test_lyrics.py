"""H32 · lyrics and song identification: LRC parser (several times per line, offset, ID tags, enhanced LRC), LRC→SRT,
``lyrics.get`` from a .lrc next to the song / an embedded tag / LRCLIB (off by default; a local fake server stands in
for it), settings with the AcoustID key in a 0600 file, ``songid`` against a fake AcoustID and tag writing with
ffmpeg; mu-lyrics headless adds the «Letra» track and mpv shows cover.jpg by itself."""

from __future__ import annotations

import asyncio
import json
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.lyrics import LyricsService, SongSettings, embedded, parse_lrc, to_cues, to_srt
from mpvd.server import MpvdServer
from mpvd.songid import SongIdError, SongIdService, parse_lookup, write_tags
from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"

LRC = """[ar:Artista]
[ti:Canción]
[al:Disco]
[offset:+500]
[00:01.50]Primera línea
[00:03.00][00:07.00]Estribillo
[00:05.25]<00:05.25>Con <00:05.60>palabras
[00:06.00]
"""


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def tone(path: Path, seconds: float = 10, *extra: str) -> Path:
    ff("-f", "lavfi", "-i", f"sine=f=440:d={seconds}", *extra, str(path))
    return path


def test_parse_lrc_times_offset_tags_and_words():
    lyr = parse_lrc(LRC)
    assert lyr.synced and lyr.meta["ar"] == "Artista" and lyr.meta["ti"] == "Canción" and lyr.meta["al"] == "Disco"
    # offset +500 ms: every line half a second sooner; one line with two times appears twice, in order
    assert lyr.lines == [(1.0, "Primera línea"), (2.5, "Estribillo"), (4.75, "Con palabras"), (5.5, ""),
                         (6.5, "Estribillo")]
    assert parse_lrc("[01:02]a\n[1:03.5]b\n[00:04.123]c\n[00:05:20]d").lines == [
        (4.123, "c"), (5.2, "d"), (62.0, "a"), (63.5, "b")]
    plain = parse_lrc("Una canción\nsin tiempos\n")
    assert not plain.synced and plain.plain == "Una canción\nsin tiempos"


def test_lrc_to_srt():
    lyr = parse_lrc(LRC)
    cues = to_cues(lyr.lines, duration=9.0)
    # a line lasts until the next one; the empty line only ends the previous one; the last lasts until the end
    assert [(c["start"], c["end"], c["text"]) for c in cues] == [
        (1.0, 2.5, "Primera línea"), (2.5, 4.75, "Estribillo"), (4.75, 5.5, "Con palabras"), (6.5, 9.0, "Estribillo")]
    srt = to_srt(lyr.lines)
    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,500\nPrimera línea\n\n")
    assert "4\n00:00:06,500 --> 00:00:12,500\nEstribillo\n" in srt   # unknown length: LAST_CUE seconds
    assert to_srt([]) == ""


def test_embedded_tag_keys():
    assert embedded({"lyrics-eng": "hola"}) == "hola"
    assert embedded({"unsyncedlyrics": "plain", "lyrics": "[00:01.00]synced"}) == "[00:01.00]synced"
    assert embedded({"title": "x"}) is None


class FakeLrclib:
    """Local stand-in for https://lrclib.net/api/get (same fields as the real answer)."""

    def __init__(self, songs: dict[tuple[str, str], dict]):
        self.songs, self.hits = songs, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                outer.hits.append(q)
                song = outer.songs.get((q.get("artist_name", ""), q.get("track_name", "")))
                if song and q.get("album_name") not in (None, song["albumName"]):
                    song = None
                body = json.dumps(song if song else {"message": "Failed to find specified track",
                                                     "name": "TrackNotFound", "statusCode": 404}).encode()
                self.send_response(200 if song else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802 - AcoustID lookup
                n = int(self.headers.get("Content-Length") or 0)
                q = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                outer.hits.append(q)
                ok = q.get("client") == "clave-buena" and q.get("fingerprint")
                body = json.dumps(ACOUSTID_OK if ok else {"error": {"code": 4, "message": "invalid API key"},
                                                          "status": "error"}).encode()
                self.send_response(200 if ok else 400)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/get"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# trimmed from a real answer of api.acoustid.org (meta=recordings releasegroups compress)
ACOUSTID_OK = {"status": "ok", "results": [
    {"id": "9ff43b6a-4f16-427c-93c2-92307ca505e0", "score": 0.97, "recordings": [
        {"artists": [{"id": "6d7b7cd4", "name": "M83"}], "duration": 637.333, "id": "cd2e7c47",
         "releasegroups": [{"id": "9e585041", "secondarytypes": ["Compilation", "Soundtrack"], "title": "Donkey Punch",
                            "type": "Album"},
                           {"id": "ddaa2d4d", "title": "Before the Dawn Heals Us", "type": "Album"}],
         "title": "Lower Your Eyelids to Die With the Sun"}]},
    {"id": "be6fa248", "score": 0.5, "recordings": [{"id": "cd2e7c47", "title": "dup"}, {"id": "zz"}]}]}


def service(tmp_path: Path, url: str = "http://127.0.0.1:9/api/get") -> LyricsService:
    server = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"))
    return LyricsService(server, lrclib_url=url)  # type: ignore[arg-type]


def test_get_sidecar_embedded_and_nothing(tmp_path):
    svc = service(tmp_path)
    song = tone(tmp_path / "canción.mp3", 10)
    (tmp_path / "canción.LRC").write_text(LRC, encoding="utf-8")
    got = svc.get(str(song))
    assert got["found"] and got["source"] == "lrc" and got["synced"] and got["sidecar"].endswith("canción.LRC")
    assert got["lines"][0] == {"time": 1.0, "text": "Primera línea"}
    srt = Path(got["srt"]).read_text(encoding="utf-8")
    assert "00:00:06,500 --> 00:00:10,0" in srt   # the last line lasts until the end of the song (ffprobe length)

    tagged = tone(tmp_path / "tag.flac", 5, "-metadata", "LYRICS=[00:01.00]Uno\n[00:02.00]Dos",
                  "-metadata", "artist=A", "-metadata", "title=T")
    got = svc.get("file://" + str(tagged))
    assert got["source"] == "embedded" and [ln["text"] for ln in got["lines"]] == ["Uno", "Dos"]
    plain = tone(tmp_path / "plain.m4a", 3, "-c:a", "aac", "-metadata", "lyrics=Sin tiempos\nOtra")
    got = svc.get(str(plain))
    assert got["found"] and not got["synced"] and got["srt"] is None and got["plain"] == "Sin tiempos\nOtra"
    got = svc.get(str(tone(tmp_path / "nada.mp3", 2)))
    assert got == {**got, "found": False, "srt": None, "online": False}


def test_lrclib_only_when_turned_on_and_cached(tmp_path):
    fake = FakeLrclib({("Artista", "Canción"): {
        "id": 1, "trackName": "Canción", "artistName": "Artista", "albumName": "Disco", "duration": 10.0,
        "instrumental": False, "plainLyrics": "Primera\nSegunda", "syncedLyrics": "[00:01.00]Primera\n[00:02.00]Segunda"}})
    try:
        svc = service(tmp_path, fake.url)
        song = tone(tmp_path / "s.mp3", 4, "-metadata", "artist=Artista", "-metadata", "title=Canción",
                    "-metadata", "album=Otro disco")
        assert svc.get(str(song))["found"] is False and fake.hits == []       # off by default: never contacted
        assert svc.get(str(song), online=True)["found"] is False and fake.hits == []   # the switch rules
        svc.settings.update({"lyrics_online": True})
        got = svc.get(str(song))
        assert got["found"] and got["source"] == "lrclib" and got["synced"] and got["srt"]
        # the album of the file was not found: retried without it
        assert fake.hits[0]["album_name"] == "Otro disco" and "album_name" not in fake.hits[1]
        assert fake.hits[1]["duration"] == "4"
        n = len(fake.hits)
        assert svc.get(str(song))["source"] == "lrclib" and len(fake.hits) == n       # cached
        other = tone(tmp_path / "o.mp3", 2, "-metadata", "artist=X", "-metadata", "title=Y")
        assert svc.get(str(other))["found"] is False
        n = len(fake.hits)
        svc.get(str(other))
        assert len(fake.hits) == n                                                   # misses are cached too
        assert svc.get("", artist="Artista", title="Canción", duration=4)["source"] == "lrclib"   # URLs: tags only
    finally:
        fake.close()


def test_settings_secret_file_is_private(tmp_path):
    s = SongSettings(tmp_path)
    assert s.public() == {"lyrics_online": False, "songid_enabled": False, "has_acoustid_key": False,
                          "songid_active": False}
    s.update({"acoustid_key": "  abc  ", "songid_enabled": True})
    assert stat.S_IMODE((tmp_path / "songs-secrets.json").stat().st_mode) == 0o600
    assert "abc" not in (tmp_path / "songs-settings.json").read_text()
    s2 = SongSettings(tmp_path)
    assert s2.secret("acoustid_key") == "abc" and s2.songid_active and "abc" not in json.dumps(s2.public())
    with pytest.raises(ValueError):
        s2.update({"lyrics_online": "sí"})
    with pytest.raises(ValueError):
        s2.update({"nope": True})
    s2.update({"acoustid_key": ""})
    assert not SongSettings(tmp_path).songid_active


def test_songid_parse_identify_and_tag(tmp_path):
    got = parse_lookup(ACOUSTID_OK)
    assert got[0] == {"score": 0.97, "title": "Lower Your Eyelids to Die With the Sun", "artist": "M83",
                      "album": "Before the Dawn Heals Us", "recording_id": "cd2e7c47", "duration": 637.333}
    assert len(got) == 1          # duplicates and recordings without a title are dropped
    with pytest.raises(SongIdError, match="invalid API key"):
        parse_lookup({"error": {"code": 4, "message": "invalid API key"}, "status": "error"})

    fake = FakeLrclib({})
    try:
        settings = SongSettings(tmp_path / "data")
        svc = SongIdService(SimpleNamespace(), settings, url=fake.url.replace("/api/get", "/v2/lookup"))  # type: ignore
        song = tone(tmp_path / "s.flac", 12)
        with pytest.raises(SongIdError, match="desactivado"):
            svc.identify(str(song))
        settings.update({"songid_enabled": True})
        with pytest.raises(SongIdError, match="clave"):
            svc.identify(str(song))
        assert fake.hits == []
        settings.update({"acoustid_key": "clave-mala"})
        with pytest.raises(SongIdError, match="invalid API key"):
            svc.identify(str(song))
        settings.update({"acoustid_key": "clave-buena"})
        res = svc.identify(str(song))
        assert res["candidates"][0]["artist"] == "M83"
        assert fake.hits[-1]["duration"] == "12" and "recordings" in fake.hits[-1]["meta"]
    finally:
        fake.close()

    # tags written in place with a stream copy (cover and other tags kept)
    cover = tmp_path / "c.png"
    ff("-f", "lavfi", "-i", "color=red:s=32x32:d=1", "-frames:v", "1", str(cover))
    mp3 = tmp_path / "t.mp3"
    ff("-f", "lavfi", "-i", "sine=d=3", "-i", str(cover), "-map", "0", "-map", "1", "-c:v", "png",
       "-disposition:v", "attached_pic", "-metadata", "genre=Rock", str(mp3))
    write_tags(str(mp3), {"title": "Título", "artist": "Artista", "album": "Álbum"})
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
                                       str(mp3)], capture_output=True, text=True, check=True).stdout)
    tags = {k.lower(): v for k, v in probe["format"]["tags"].items()}
    assert tags["title"] == "Título" and tags["artist"] == "Artista" and tags["album"] == "Álbum" and tags["genre"] == "Rock"
    assert any(s.get("disposition", {}).get("attached_pic") for s in probe["streams"])
    assert [p.name for p in tmp_path.iterdir() if "mu-tag" in p.name] == []


def test_rpc(tmp_path):
    song = tone(tmp_path / "a.mp3", 4, "-metadata", "lyrics=[00:01.00]Hola\n[00:02.00]Adiós")

    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=1)
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                got = await c.call("lyrics.get", {"path": str(song)})
                assert got["source"] == "embedded" and got["srt"]
                s = await c.call("lyrics.settings.set", {"acoustid_key": "k"})
                assert s["has_acoustid_key"] and not s["songid_active"]
                with pytest.raises(Exception, match="desactivado"):
                    await c.call("songid.identify", {"path": str(song)})
                with pytest.raises(Exception, match="locales"):
                    await c.call("songid.tag", {"path": "https://x/y.mp3", "title": "t"})
                with pytest.raises(Exception, match="required"):
                    await c.call("lyrics.get", {})
                caps = await c.call("capabilities")
                assert caps["services"]["lyrics"] and caps["services"]["songid"] and caps["services"]["books"]
        finally:
            await server.stop()

    asyncio.run(go())


@pytest.mark.network
def test_lrclib_real(tmp_path):
    """The real LRCLIB answers the documented fields for a well-known song."""
    svc = service(tmp_path, "https://lrclib.net/api/get")
    svc.settings.update({"lyrics_online": True})
    got = svc.get("", artist="Borislav Slavov", title="I Want to Live",
                  album="Baldur's Gate 3 (Original Game Soundtrack)", duration=233)
    assert got["found"] and got["source"] == "lrclib" and got["synced"] and len(got["lines"]) > 10


# -- mu-lyrics + mpv ---------------------------------------------------------------------------------------------------

@pytest.fixture
def lyrics_mpv(daemon_env):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h
    finally:
        h.stop()


def test_mu_lyrics_adds_the_track_and_cover_art_is_mpvs(lyrics_mpv, tmp_path):
    h = lyrics_mpv
    album = tmp_path / "disco"
    album.mkdir()
    ff("-f", "lavfi", "-i", "color=blue:s=64x64:d=1", "-frames:v", "1", str(album / "cover.jpg"))
    song = tone(album / "tema.flac", 12, "-metadata", "LYRICS=[00:01.00]Uno\n[00:03.00]Dos\n[00:05.00]Tres",
                "-metadata", "title=Tema")
    h.command("loadfile", str(song))
    st = h.wait_property("user-data/mu/lyrics", lambda v: bool(v) and v.get("status") == "found", timeout=30)
    assert st["source"] == "embedded" and st["synced"] and st["lines"] == 3
    tracks = h.get("track-list")
    subs = [t for t in tracks if t["type"] == "sub"]
    assert len(subs) == 1 and subs[0]["title"] == "Letra" and subs[0]["selected"] and subs[0]["id"] == st["sid"]
    # cover.jpg next to the song: loaded by mpv itself as cover art (no config needed)
    covers = [t for t in tracks if t["type"] == "video"]
    assert covers and covers[0]["albumart"] and covers[0]["external-filename"].endswith("cover.jpg")
    h.command("seek", "3.5", "absolute+exact")
    h.wait_property("sub-text", lambda v: v == "Dos", timeout=10)

    # the menu lists the lines; Enter on one jumps there
    h.command("script-binding", "mu_lyrics/lyrics-menu")
    st = h.wait_property("user-data/mu/lyrics", lambda v: bool(v) and v.get("view") == "root" and
                         any(i["title"] == "Tres" for i in v.get("items") or []), timeout=10)
    item = next(i for i in st["items"] if i["title"] == "Tres")
    assert item["hint"] == "0:05"
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_lyrics", "mu-lyrics-event",
              json.dumps({**base, "type": "activate", "index": 3, "value": item["value"]}))
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 5.0) < 0.5, timeout=10)

    # a .lrc next to the song is loaded by mpv (sub-auto): not added twice
    song2 = tone(album / "otro.mp3", 6)
    (album / "otro.lrc").write_text("[00:01.00]Lrc uno\n[00:02.00]Lrc dos\n", encoding="utf-8")
    h.command("loadfile", str(song2))
    st = h.wait_property("user-data/mu/lyrics", lambda v: bool(v) and v.get("status") == "found" and
                         v.get("source") == "lrc", timeout=30)
    subs = [t for t in h.get("track-list") if t["type"] == "sub"]
    assert len(subs) == 1 and subs[0]["external-filename"].endswith("otro.lrc") and st["sid"] == subs[0]["id"]

    # no lyrics at all: nothing added, the menu says so
    h.command("loadfile", str(tone(album / "sin.mp3", 4)))
    h.wait_property("user-data/mu/lyrics", lambda v: bool(v) and v.get("status") == "none", timeout=30)
    assert [t for t in h.get("track-list") if t["type"] == "sub"] == []
    assert h.script_errors() == [], h.script_errors()
