"""H17 · «Mis notas» end to end (headless mpv + mpvd): notes taken with alt+b show up in the menu (this video and all
of them), Enter jumps to the minute, Tab edits (text box) and deletes, export next to the video and to a folder, an
mpv-uos:// link loaded in mpv opens the file at its minute, and ⌫ walks back to the main menu."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mpvd.notes import link
from tests.conftest import start_mpv
from tests.test_nav import press, wait_nav

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


@pytest.fixture
def notes_mpv(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def ev(h, message: str, event: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_notes", message, json.dumps({**base, **event}))


def notes_state(h, pred, timeout: float = 20.0):
    return h.wait_property("user-data/mu/notes", lambda v: bool(v) and pred(v), timeout=timeout)


def titles(v):
    return [i["title"] for i in v.get("items", [])]


def test_my_notes_menu_edit_delete_export_and_links(notes_mpv, media_dir, tmp_path):
    h, d = notes_mpv
    video = tmp_path / "Mi película.mkv"
    shutil.copyfile(media_dir / "video30.mkv", video)
    h.command("loadfile", str(video))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    # two notes with alt+b's message (mu-study → notes.add)
    for t, text in ((10.0, "Segunda nota"), (3.0, "Primera nota")):
        h.command("seek", t, "absolute+exact")
        h.wait_property("time-pos", lambda v, t=t: isinstance(v, (int, float)) and abs(v - t) < 0.5, timeout=10)
        h.command("script-message-to", "mu_study", "mu-study-note", text)
        d.wait(lambda text=text: any(text in (d.call("notes.get", {"path": str(video)})["items"][i]["text"])
                                     for i in range(len(d.call("notes.get", {"path": str(video)})["items"]))),
               timeout=15)
    got = d.call("notes.get", {"path": str(video)})
    assert [n["text"] for n in got["items"]] == ["Primera nota", "Segunda nota"]   # ordered by time
    assert Path(got["file"]).name == "Mi película.md"

    # menu: this video + the list of all
    h.command("script-binding", "mu_notes/notes-menu")
    wait_nav(h, "mu-notes", "MPV-UOS › Mis notas")
    st = notes_state(h, lambda v: v.get("view") == "root" and "Mi película" in titles(v))
    assert titles(st)[0] == "Notas de este vídeo"
    row = next(i for i in st["items"] if i["title"] == "Mi película")
    assert row["hint"] == "2 notas"
    ev(h, "mu-notes-event", {"type": "activate", "index": 3, "value": row["value"]})
    wait_nav(h, "mu-notes", "MPV-UOS › Mis notas › Mi película")
    st = notes_state(h, lambda v: v.get("view") == "file" and "Primera nota" in titles(v))
    assert [i["hint"] for i in st["items"][:2]] == ["0:03", "0:10"] and "Exportar junto al vídeo" in titles(st)

    # Enter jumps to the minute
    ev(h, "mu-notes-event", {"type": "activate", "index": 2, "value": {"note": 1, "time": 10.0}})
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 10.0) < 0.6, timeout=10)

    # Tab → edit: a text box with the current text; Enter saves and comes back to the list
    ev(h, "mu-notes-event", {"type": "activate", "index": 1, "action": "edit", "value": {"note": 0, "time": 3.0}})
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-notes-input", timeout=10)
    notes_state(h, lambda v: v.get("input") == "edit")
    ev(h, "mu-notes-input-event", {"type": "search", "query": "Primera nota, corregida"})
    ev(h, "mu-notes-input-event", {"type": "activate", "index": 1, "value": {"save": "Primera nota, corregida"}})
    st = notes_state(h, lambda v: v.get("view") == "file" and "Primera nota, corregida" in titles(v))
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-notes", timeout=10)

    # export next to the video, then into a folder chosen in the text box (remembered)
    ev(h, "mu-notes-event", {"type": "activate", "index": 4, "value": {"export": "video"}})
    st = notes_state(h, lambda v: v.get("last_export", "").endswith("Mi película.notas.md"))
    assert "Primera nota, corregida" in Path(st["last_export"]).read_text(encoding="utf-8")
    vault = tmp_path / "Obsidian" / "Cine"
    ev(h, "mu-notes-event", {"type": "activate", "index": 5, "value": {"export": "folder"}})
    notes_state(h, lambda v: v.get("input") == "folder")
    ev(h, "mu-notes-input-event", {"type": "activate", "index": 1, "value": {"save": str(vault)}})
    st = notes_state(h, lambda v: v.get("last_export") == str(vault / "Mi película.md"))
    assert (vault / "Mi película.md").exists()

    # Tab → delete
    ev(h, "mu-notes-event", {"type": "activate", "index": 2, "action": "delete", "value": {"note": 1, "time": 10.0}})
    st = notes_state(h, lambda v: v.get("view") == "file" and "Segunda nota" not in titles(v)
                     and "Primera nota, corregida" in titles(v))
    assert len(d.call("notes.get", {"path": str(video)})["items"]) == 1

    # ⌫ walks back: the list of all notes, then the main menu
    press(h, "BS")
    wait_nav(h, "mu-notes", "MPV-UOS › Mis notas")
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "ESC")
    h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=10)

    # an mpv-uos:// link loaded inside mpv (pasted, a playlist…) opens that file at that minute
    h.command("loadfile", str(media_dir / "chapters.mkv"))
    h.wait_property("path", lambda v: str(v).endswith("chapters.mkv"), timeout=15)
    h.command("loadfile", link(str(video), 12.0))
    h.wait_property("path", lambda v: v == str(video), timeout=15)
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 12.0) < 1.0, timeout=15)
    assert h.script_errors() == [], h.script_errors()
