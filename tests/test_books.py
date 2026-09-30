"""H32 · audiobooks and podcasts: what counts as a book (m4b, genre, > 1 h, a folder of chapters but not a music
album, podcasts, the user's marks), position/speed per book (folder books: track + time), bookmarks, «Seguir
escuchando»; mu-books headless with an m4b with chapters made by ffmpeg (ffmetadata): resume, file-local speed,
bookmarks, ±30 s, sleep timer with short injected minutes (fade + pause, «al terminar el capítulo») and a folder book
that resumes in another track."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from mpvd.books import BooksStore
from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.server import MpvdServer
from tests.conftest import start_mpv

MU_OPTS = ("--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
           "mu-books-save_seconds=1,mu-books-fade_seconds=1,mu-books-minute_seconds=0.5,mu-books-resume_min=1")


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make_m4b(path: Path, chapters: int = 3, seconds: int = 6, genre: str | None = None) -> Path:
    meta = path.with_suffix(".ffmeta")
    lines = [";FFMETADATA1", "title=El libro de prueba", "artist=Autora", "album=El libro de prueba"]
    if genre:
        lines.append(f"genre={genre}")
    for i in range(chapters):
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={i * seconds * 1000}", f"END={(i + 1) * seconds * 1000}",
                  f"title=Capítulo {i + 1}"]
    meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp = path.with_suffix(".m4a")
    ff("-f", "lavfi", "-i", f"sine=f=330:d={chapters * seconds}", "-i", str(meta), "-map_metadata", "1",
       "-map_chapters", "1", "-c:a", "aac", "-b:a", "32k", str(tmp))
    tmp.rename(path)
    return path


def tone(path: Path, seconds: float, *extra: str) -> Path:
    ff("-f", "lavfi", "-i", f"sine=f=440:d={seconds}", *extra, str(path))
    return path


class FakeProbe:
    """Stands in for ffprobe: {file name: (duration, tags, video)}."""

    def __init__(self, table: dict[str, tuple[float, dict[str, str], bool]]):
        self.table = table

    def __call__(self, p: Path) -> dict:
        dur, tags, video = self.table.get(p.name, (0.0, {}, False))
        return {"duration": dur, "tags": tags, "chapters": [], "video": video}


def touch(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        (folder / n).write_bytes(n.encode() * 50)
        out.append(folder / n)
    return out


def test_detect_single_files(tmp_path):
    table = {"libro.m4b": (600.0, {}, False), "genero.mp3": (300.0, {"genre": "Audiobook"}, False),
             "largo.mp3": (4000.0, {}, False), "corto.mp3": (200.0, {}, False), "pod.mp3": (1800.0,
             {"genre": "Podcast", "album": "Mi programa"}, False), "peli.mkv": (5000.0, {}, True),
             "hoerbuch.ogg": (100.0, {"genre": "Hörbuch"}, False)}
    s = BooksStore(tmp_path / "data", prober=FakeProbe(table))
    files = {p.name: p for p in touch(tmp_path / "sueltos", *table)}
    assert s.detect(str(files["libro.m4b"]))["reason"] == "m4b"
    assert s.detect(str(files["genero.mp3"]))["reason"] == "genre"
    assert s.detect(str(files["hoerbuch.ogg"]))["reason"] == "genre"
    assert s.detect(str(files["largo.mp3"]))["reason"] == "long"
    assert s.detect(str(files["corto.mp3"]))["is_book"] is False
    assert s.detect(str(files["peli.mkv"]))["is_book"] is False        # real video never counts
    pod = s.detect("file://" + str(files["pod.mp3"]))
    assert pod["kind"] == "podcast" and pod["show"] == "Mi programa" and pod["id"].startswith("mu:")
    assert s.detect("https://x/ep.mp3", meta={"genre": "Podcast", "album": "Show"})["kind"] == "podcast"
    assert s.detect("https://x/song.mp3", meta={"genre": "Rock"})["is_book"] is False
    # the user decides: never a book / always a book
    s.mark(str(files["largo.mp3"]), False)
    assert s.detect(str(files["largo.mp3"]))["is_book"] is False
    s.mark(str(files["corto.mp3"]), True)
    assert s.detect(str(files["corto.mp3"]))["reason"] == "mark"
    s.mark(str(files["largo.mp3"]), None)
    assert s.detect(str(files["largo.mp3"]))["is_book"] is True


def test_detect_folders(tmp_path):
    chapters = {f"{i:02d} Parte.mp3": (20 * 60.0, {"album": "Novela", "artist": "Autor", "track": str(4 - i)}, False)
                for i in range(1, 4)}
    songs = {f"{i:02d} canción.mp3": (5 * 60.0, {"album": "Disco largo", "track": str(i)}, False) for i in range(1, 16)}
    cd_rip = {f"Capítulo {i}.mp3": (4 * 60.0, {"album": "Libro en CD"}, False) for i in range(1, 20)}
    pods = {f"ep{i}.mp3": (40 * 60.0, {"album": "Show", "genre": "Podcast"}, False) for i in range(1, 4)}
    s = BooksStore(tmp_path / "data", prober=FakeProbe({**chapters, **songs, **cd_rip, **pods}))
    novel = touch(tmp_path / "novela", *chapters)
    got = s.detect(str(novel[0]))
    assert got["is_book"] and got["reason"] == "folder" and got["id"] == "dir:" + str(novel[0].parent)
    # numbered by their track tags (3, 2, 1), not by name
    assert [t["file"] for t in got["tracks"]] == ["03 Parte.mp3", "02 Parte.mp3", "01 Parte.mp3"]
    assert got["track_index"] == 2 and got["title"] == "Novela" and got["author"] == "Autor"
    # a 75-minute music album is not a book...
    album = touch(tmp_path / "disco", *songs)
    assert s.detect(str(album[0]))["is_book"] is False
    # ...unless the user says so for the whole folder
    s.mark(str(album[0]), True, folder=True)
    assert s.detect(str(album[3]))["reason"] == "folder"
    # short tracks but «Capítulo» in the names
    rip = touch(tmp_path / "cd", *cd_rip)
    assert s.detect(str(rip[0]))["reason"] == "folder"
    # podcast episodes in a folder are separate items
    eps = touch(tmp_path / "pods", *pods)
    assert s.detect(str(eps[1]))["kind"] == "podcast" and s.detect(str(eps[1]))["id"].startswith("mu:")


def test_position_speed_bookmarks_and_list(tmp_path):
    table = {f"{i}.mp3": (30 * 60.0, {"album": "Saga"}, False) for i in range(1, 4)}
    table.update({"solo.m4b": (7200.0, {"title": "Solo"}, False),
                  "e1.mp3": (100.0, {"genre": "Podcast", "album": "P"}, False),
                  "e2.mp3": (100.0, {"genre": "Podcast", "album": "P"}, False)})
    data = tmp_path / "data"
    s = BooksStore(data, prober=FakeProbe(table))
    saga = touch(tmp_path / "saga", "1.mp3", "2.mp3", "3.mp3")
    solo, e1, e2 = touch(tmp_path / "otros", "solo.m4b", "e1.mp3", "e2.mp3")

    first = s.open(str(saga[0]))
    assert first["is_book"] and first["position"] is None and first["speed"] is None and not first["known"]
    s.save(first["id"], track=str(saga[1]), time_pos=123.45, speed=1.5, title=first["title"], kind="book",
           path=first["path"])
    s.save(s.open(str(solo))["id"], time_pos=3000, speed=1.25, title="Solo", kind="book", path=str(solo))
    marks = s.bookmark_add(first["id"], track=str(saga[2]), time_pos=10, note="  giro   final ")
    marks = s.bookmark_add(first["id"], track=str(saga[0]), time_pos=50, note="")
    assert [(m["track_index"], m["time"], m["note"]) for m in marks] == [(0, 50.0, ""), (2, 10.0, "giro final")]

    s2 = BooksStore(data, prober=FakeProbe(table))        # persisted
    again = s2.open(str(saga[2]))
    assert again["position"] == {"track": "2.mp3", "time": 123.5, "index": 1} and again["speed"] == 1.5
    assert again["track_index"] == 2 and len(again["bookmarks"]) == 2
    solo_again = s2.open(str(solo))
    assert solo_again["position"]["time"] == 3000 and solo_again["speed"] == 1.25 and solo_again["title"] == "Solo"
    left = s2.bookmark_delete(again["id"], next(m["index"] for m in again["bookmarks"] if m["note"]))
    assert [m["note"] for m in left] == [""]
    with pytest.raises(KeyError):
        s2.bookmark_delete(again["id"], 9)
    # speeds are clamped; a finished book starts again from the start
    assert s2.save(solo_again["id"], time_pos=7190, speed=9, finished=True)["speed"] == 3.0
    assert s2.open(str(solo))["position"] is None and s2.open(str(solo))["finished"]

    # podcasts: a new episode starts at the speed last used for the same show
    p1 = s2.open(str(e1))
    s2.save(p1["id"], time_pos=20, speed=1.75, kind="podcast", show=p1["show"], title="e1")
    p2 = s2.open(str(e2))
    assert p2["speed"] == 1.75 and p2["position"] is None

    lst = s2.list()
    assert [b["finished"] for b in lst] == [False, False, True]
    saga_row = next(b for b in lst if b["id"] == again["id"])
    assert saga_row["open"] == str(saga[1]) and saga_row["bookmarks"] == 1
    assert s2.forget(again["id"]) and not s2.forget(again["id"])
    # nothing like a history: no list of plays, only one entry per book
    stored = json.loads((data / "books.json").read_text())
    assert set(stored) == {"version", "books", "shows", "marks"}
    assert all("history" not in b and "plays" not in b for b in stored["books"].values())


def test_rpc_with_real_m4b(tmp_path):
    book = make_m4b(tmp_path / "libro.m4b")

    async def go():
        settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                            idle_timeout=0, workers=1)
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                got = await c.call("books.open", {"path": str(book)})
                assert got["is_book"] and got["reason"] == "m4b" and got["title"] == "El libro de prueba"
                assert [ch["title"] for ch in got["chapters"]] == ["Capítulo 1", "Capítulo 2", "Capítulo 3"]
                await c.call("books.save", {"id": got["id"], "time": 7.5, "speed": 1.2, "title": got["title"]})
                await c.call("books.bookmark.add", {"id": got["id"], "time": 3, "note": "aquí"})
                assert (await c.call("books.open", {"path": str(book)}))["position"]["time"] == 7.5
                assert (await c.call("books.bookmarks", {"id": got["id"]}))[0]["note"] == "aquí"
                assert (await c.call("books.list"))[0]["speed"] == 1.2
                with pytest.raises(Exception, match="not found"):
                    await c.call("books.bookmark.delete", {"id": got["id"], "index": 5})
                assert (await c.call("books.open", {"path": str(tmp_path / "nada.mp3")}))["is_book"] is False
        finally:
            await server.stop()

    asyncio.run(go())


# -- mu-books + mpv ----------------------------------------------------------------------------------------------------

@pytest.fixture
def books_mpv(daemon_env):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def is_num(v) -> bool:
    return isinstance(v, (int, float))


def test_mu_books_resume_speed_bookmarks_and_skips(books_mpv, tmp_path, media_dir):
    h, d = books_mpv
    book = make_m4b(tmp_path / "libro.m4b", chapters=3, seconds=6)
    h.command("loadfile", str(book))
    st = h.wait_property("user-data/mu/books", lambda v: bool(v) and bool(v.get("book")), timeout=30)
    assert st["book"]["reason"] == "m4b" and st["book"]["title"] == "El libro de prueba"

    # speed of this book: file-local (the next file goes back to normal) and saved
    h.command("script-message-to", "mu_books", "mu-books-speed", "1.5")
    h.wait_property("speed", lambda v: v == 1.5, timeout=10)
    h.command("seek", "8", "absolute+exact")
    h.wait_property("user-data/mu/books", lambda v: bool(v) and (v.get("last_saved") or {}).get("reason") == "seek"
                    and abs(v["last_saved"]["time"] - 8) < 0.5 and v["last_saved"]["speed"] == 1.5, timeout=10)
    h.command("script-message-to", "mu_books", "mu-books-bookmark", "Me gusta esta parte")
    h.wait_property("user-data/mu/books", lambda v: bool(v) and v.get("bookmarks") == 1, timeout=10)
    # ±30 s: back from 8 clamps at the start
    h.command("script-message-to", "mu_books", "mu-books-skip", "-30")
    h.wait_property("time-pos", lambda v: is_num(v) and v < 0.5, timeout=10)
    h.command("seek", "10", "absolute+exact")
    h.wait_property("user-data/mu/books", lambda v: (v.get("last_saved") or {}).get("reason") == "seek"
                    and abs(v["last_saved"]["time"] - 10) < 0.5, timeout=10)

    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("user-data/mu/books", lambda v: bool(v) and v.get("book") is None, timeout=20)
    h.wait_property("duration", lambda v: is_num(v) and v > 20, timeout=20)
    assert h.get("speed") == 1.0   # the book's speed stayed with the book

    h.command("loadfile", str(book))
    h.wait_property("user-data/mu/books", lambda v: bool(v) and bool(v.get("book")), timeout=30)
    h.wait_property("time-pos", lambda v: is_num(v) and abs(v - 10) < 0.6, timeout=10)
    assert h.get("speed") == 1.5
    saved = d.call("books.list")
    assert saved[0]["title"] == "El libro de prueba" and saved[0]["bookmarks"] == 1

    # menu: chapters of the file, bookmarks, Enter on a bookmark jumps
    h.command("script-binding", "mu_books/books-menu")
    st = h.wait_property("user-data/mu/books", lambda v: v.get("view") == "root" and len(v.get("items") or []) > 3,
                         timeout=10)
    titles = [i["title"] for i in st["items"]]
    assert "Capítulos" in titles and "Marcadores" in titles and "Temporizador de apagado" in titles
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}

    def event(value, action=None):
        ev = {**base, "type": "activate", "index": 1, "value": value}
        if action:
            ev["action"] = action
        h.command("script-message-to", "mu_books", "mu-books-event", json.dumps(ev))

    event({"view": "chapters"})
    st = h.wait_property("user-data/mu/books", lambda v: v.get("view") == "chapters", timeout=10)
    assert [i["title"] for i in st["items"]] == ["Capítulo 1", "Capítulo 2", "Capítulo 3"]
    assert [i["hint"] for i in st["items"]] == ["0:00", "0:06", "0:12"] and st["items"][1]["active"]
    h.command("script-binding", "mu_books/books-menu")
    h.wait_property("user-data/mu/books", lambda v: v.get("view") == "root", timeout=10)
    event({"view": "bookmarks"})
    st = h.wait_property("user-data/mu/books", lambda v: v.get("view") == "bookmarks" and
                         any(i["title"] == "Me gusta esta parte" for i in v.get("items") or []), timeout=10)
    mark = next(i for i in st["items"] if i["title"] == "Me gusta esta parte")
    event(mark["value"])
    h.wait_property("time-pos", lambda v: is_num(v) and abs(v - 8) < 0.6, timeout=10)
    h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=10)
    # delete it (Tab action)
    h.command("script-binding", "mu_books/books-menu")
    h.wait_property("user-data/mu/books", lambda v: v.get("view") == "root", timeout=10)
    event({"view": "bookmarks"})
    h.wait_property("user-data/mu/books", lambda v: v.get("view") == "bookmarks", timeout=10)
    event(mark["value"], action="delete")
    h.wait_property("user-data/mu/books", lambda v: v.get("bookmarks") == 0, timeout=10)
    assert h.script_errors() == [], h.script_errors()


def test_mu_books_sleep_timer(books_mpv, tmp_path):
    h, _d = books_mpv
    book = make_m4b(tmp_path / "libro.m4b", chapters=3, seconds=6)
    h.command("loadfile", str(book))
    h.wait_property("user-data/mu/books", lambda v: bool(v) and bool(v.get("book")), timeout=30)
    h.command("set_property", "volume", 80)

    # «15 minutes» with 0.5-second minutes = 7.5 s of playback; the last second fades out, then pause
    h.command("script-message-to", "mu_books", "mu-books-sleep", "15")
    st = h.wait_property("user-data/mu/books", lambda v: bool(v.get("sleep")), timeout=10)
    assert st["sleep"]["mode"] == "minutes" and st["sleep"]["remaining"] > 7
    h.command("set_property", "pause", False)
    h.wait_property("user-data/mu/books", lambda v: (v.get("sleep") or {}).get("fading"), timeout=15)
    assert h.get("volume") < 80
    h.wait_property("pause", lambda v: v is True, timeout=10)
    st = h.wait_property("user-data/mu/books", lambda v: v.get("sleep") is None, timeout=5)
    assert h.get("volume") == 80                     # volume back for the next time
    assert 6.5 < h.get("time-pos") < 9.5             # about 7.5 s of playback

    # «at the end of the chapter»: from inside chapter 2 (6–12 s), pause just before chapter 3 starts
    h.command("seek", "9", "absolute+exact")
    h.command("script-message-to", "mu_books", "mu-books-sleep", "chapter")
    h.wait_property("user-data/mu/books", lambda v: (v.get("sleep") or {}).get("mode") == "chapter", timeout=10)
    h.command("set_property", "pause", False)
    h.wait_property("user-data/mu/books", lambda v: v.get("sleep") is None, timeout=15)
    assert h.get("pause") is True
    assert 11.3 < h.get("time-pos") < 12.3 and h.get("volume") == 80

    # cancelled timers leave nothing behind
    h.command("script-message-to", "mu_books", "mu-books-sleep", "30")
    h.wait_property("user-data/mu/books", lambda v: bool(v.get("sleep")), timeout=10)
    h.command("script-message-to", "mu_books", "mu-books-sleep", "off")
    h.wait_property("user-data/mu/books", lambda v: v.get("sleep") is None, timeout=10)
    assert h.script_errors() == [], h.script_errors()


def test_mu_books_folder_book_resumes_in_another_track(books_mpv, tmp_path):
    h, d = books_mpv
    folder = tmp_path / "saga"
    folder.mkdir()
    tracks = [tone(folder / f"{i:02d} Parte.mp3", 6, "-metadata", "album=Saga", "-metadata", f"title=Parte {i}",
                   "-metadata", f"track={i}") for i in (1, 2, 3)]
    d.call("books.mark", {"path": str(tracks[0]), "value": True, "folder": True})   # tiny test tracks: forced
    h.command("loadfile", str(tracks[0]))
    st = h.wait_property("user-data/mu/books", lambda v: bool(v) and bool(v.get("book")), timeout=30)
    assert st["book"]["folder"] and st["book"]["tracks"] == 3 and st["book"]["track_index"] == 0
    # the other tracks of the book were put in the playlist, in order
    h.wait_property("playlist-count", lambda v: v == 3, timeout=10)
    assert [Path(e["filename"]).name for e in h.get("playlist")] == [t.name for t in tracks]

    h.command("playlist-play-index", "1")
    h.wait_property("user-data/mu/books", lambda v: (v.get("book") or {}).get("track_index") == 1, timeout=20)
    h.command("seek", "3", "absolute+exact")
    h.wait_property("user-data/mu/books", lambda v: (v.get("last_saved") or {}).get("track") == tracks[1].name
                    and abs(v["last_saved"]["time"] - 3) < 0.5, timeout=10)

    # another day: opening the first track of the book goes on in track 2 at 0:03
    h.command("quit")
    h.proc.wait(timeout=10)
    h2 = start_mpv(d.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h2.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h2.command("loadfile", str(tracks[0]))
        h2.wait_property("user-data/mu/books", lambda v: (v.get("book") or {}).get("track_index") == 1, timeout=30)
        h2.wait_property("time-pos", lambda v: is_num(v) and abs(v - 3) < 0.6, timeout=10)
        assert Path(h2.get("path")).name == tracks[1].name
        assert h2.script_errors() == [], h2.script_errors()
    finally:
        h2.stop()
