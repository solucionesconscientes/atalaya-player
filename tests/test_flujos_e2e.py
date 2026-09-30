"""H34 · flows across modules in ONE session (headless mpv + mpvd), which the per-module tests do not cover.

Two things only show up when someone really uses the player: jumping from one module's menu straight into another one
with its key (alt+t, then alt+M, without going back), and everything a session left behind — a note, a preference, the
position of what was playing — being there after mpv is restarted against the same daemon and data folder.
"""

from __future__ import annotations

import shutil

import pytest

from tests.conftest import start_mpv
from tests.test_nav import MODULES, press, wait_closed, wait_nav

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"
# a hop between modules from three different areas: TV, music and notes
HOPS = ["mu_iptv", "mu_music", "mu_notes"]


def launch(daemon_env, *extra: str):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", *extra], env=daemon_env.env)
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                    timeout=40)
    return h


@pytest.fixture
def flow(daemon_env, media_dir, tmp_path):
    daemon_env.extra_env["MPV_UOS_MUSIC_DIR"] = ""   # «Música» must not scan the developer's real Music folder
    video = tmp_path / "Mi tarde de cine.mkv"
    shutil.copyfile(media_dir / "video30.mkv", video)
    started = []
    try:
        yield daemon_env, video, lambda *extra: started.append(launch(daemon_env, *extra)) or started[-1]
    finally:
        for h in started:
            h.stop()


def test_saltar_de_un_modulo_a_otro_con_su_tecla_no_deja_menus_ni_pila_colgando(flow, media_dir):
    d, video, spawn = flow
    h = spawn()
    h.command("loadfile", str(video))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    # open each module with its own key while the previous module's menu is still open (no «Atrás» in between)
    for script in HOPS:
        binding, menu_type, title = MODULES[script]
        h.command("script-binding", f"{script}/{binding}")
        st = wait_nav(h, menu_type, f"MPV-UOS › {title}")
        assert st["parent"] == "mu_menu", f"{script}: el padre debería seguir siendo el menú principal, no {st['parent']}"

    # ⌫ from the last module lands on the main menu once (the stack did not pile up the previous modules)
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "BS")
    wait_closed(h)
    assert h.script_errors() == [], h.script_errors()


def test_nota_preferencia_y_posicion_siguen_ahi_tras_reiniciar_mpv(flow, media_dir):
    d, video, spawn = flow
    h = spawn()

    # watching: position past the resumable threshold, a note at 12 s and a preference the user changed by hand
    h.command("loadfile", str(video))
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("tracked") is True and v.get("path") == str(video),
                    timeout=30)
    h.command("seek", "22", "absolute", "exact")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= 22, timeout=20)
    h.command("script-message-to", "mu_study", "mu-study-note", "La escena del puerto")
    d.wait(lambda: any(n["text"] == "La escena del puerto" for n in d.call("notes.get", {"path": str(video)})["items"]),
           timeout=20)
    h.command("set_property", "volume", 77)
    h.wait_property("user-data/mu/prefs", lambda v: bool(v) and v.get("values", {}).get("volume") == 77, timeout=15)
    h.command("set_property", "pause", True)   # pause → the position is saved right away
    d.wait(lambda: any(r["path"] == str(video) and r["position"] >= 22 and r["resume"]
                       for r in d.call("watch.recents")), timeout=20)
    errors_first = h.script_errors()
    h.stop()

    # a new mpv against the same daemon and the same data folder: everything is still there
    h2 = spawn()
    assert h2.get("volume") == 77
    h2.command("script-binding", "mu_menu/root")
    st = h2.wait_property("user-data/mu/menu",
                          lambda v: bool(v) and v.get("view") == "root"
                          and any(i["title"] == "Continuar viendo" for i in v.get("items", [])), timeout=20)
    cont = next(i for i in st["items"] if i["title"] == "Continuar viendo")
    assert cont["hint"] == "1"
    press(h2, "ESC")
    wait_closed(h2)

    h2.command("loadfile", str(video))
    h2.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("path") == str(video) and v.get("resumed"),
                     timeout=30)
    h2.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 21 <= v <= 27, timeout=20)

    h2.command("script-binding", "mu_notes/notes-menu")
    wait_nav(h2, "mu-notes", "MPV-UOS › Mis notas")
    notes = h2.wait_property("user-data/mu/notes", lambda v: bool(v) and v.get("view") == "root"
                             and any("Mi tarde de cine" in i["title"] for i in v.get("items", [])), timeout=20)
    row = next(i for i in notes["items"] if "Mi tarde de cine" in i["title"])
    assert row["hint"] == "1 nota"
    press(h2, "ESC")
    wait_closed(h2)

    assert errors_first == [], errors_first
    assert h2.script_errors() == [], h2.script_errors()
