"""H15 · one menu for everything (mu/nav.lua): breadcrumbs in the title, "Atrás" as the first row, ⌫/← go back one level,
the root of every module returns to the menu that opened it (the main menu when opened with its key) and Esc closes.
Driven through the real uosc (keypress BS / LEFT / ESC) with headless mpv + mpvd; the menu JSON uosc receives is checked
through the scripts' own mirrors (user-data/mu/nav, user-data/mu/menu)."""

from __future__ import annotations

import json

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"

# module → (key binding that opens it, uosc menu type, title of its first level)
MODULES = {
    "mu_iptv": ("tv-menu", "mu-iptv", "TV y radio"),
    "mu_ytdl": ("ytdl-menu", "mu-ytdl", "Descargas y conversión"),
    "mu_subs": ("subs-menu", "mu-subs", "Subtítulos IA"),
    "mu_av": ("av-menu", "mu-av", "Filtros de imagen y sonido"),
    "mu_intro": ("intro-menu", "mu-intro", "Saltar intro y créditos"),
    "mu_study": ("study-menu", "mu-study", "Estudio"),
    "mu_remote": ("remote-menu", "mu-remote", "Mando a distancia"),
}
CATEGORIES = ["Abrir", "TV y radio", "Descargas y conversión", "Subtítulos", "Imagen y sonido", "Grabar", "Herramientas",
              "Preferencias"]


@pytest.fixture
def nav_mpv(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def menu_event(h, script: str, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", script, f"{script.replace('_', '-')}-event", json.dumps({**base, **ev}))


def wait_nav(h, menu_type: str, title: str, timeout: float = 20.0) -> dict:
    h.wait_property("user-data/uosc/menu/type", lambda v: v == menu_type, timeout=timeout)
    return h.wait_property("user-data/mu/nav", lambda v: bool(v) and v.get("title") == title, timeout=timeout)


def press(h, key: str) -> None:
    """keypress once uosc has bound its menu keys: it publishes the menu type a few ms before binding them."""
    cmd = f"script-binding uosc/menu-{key.lower()}"
    h.wait_property("input-bindings", lambda v: any(str(b.get("cmd", "")).endswith(cmd) for b in v or []), timeout=10)
    h.command("keypress", key)


def wait_closed(h, timeout: float = 10.0) -> None:
    h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=timeout)


def test_main_menu_categories_breadcrumbs_and_back_into_modules(nav_mpv, media_dir):
    h, _d = nav_mpv
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    h.command("script-binding", "mu_menu/root")
    wait_nav(h, "mu-menu", "MPV-UOS")
    st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "root", timeout=10)
    titles = [i["title"] for i in st["items"]]
    assert [t for t in titles if t in CATEGORIES] == CATEGORIES
    assert "Atrás" not in titles  # the top of the main menu has nowhere to go back to

    # category → module one level down: the module shows the whole trail and "Atrás" returns to the category
    menu_event(h, "mu_menu", {"type": "activate", "index": 4, "value": {"view": "subs"}})
    wait_nav(h, "mu-menu", "MPV-UOS › Subtítulos")
    menu_event(h, "mu_menu", {"type": "activate", "index": 4,
                              "value": {"child": {"script": "mu_subs", "entry": "subs-menu"}}})
    st = wait_nav(h, "mu-subs", "MPV-UOS › Subtítulos › Subtítulos IA")
    assert st["script"] == "mu_subs" and st["parent"] == "mu_menu"
    # a click on the "Atrás" row goes through uosc/menu-back → back at the module root → the category again
    menu_event(h, "mu_subs", {"type": "activate", "index": 1, "value": {"nav": "back"}})
    wait_nav(h, "mu-menu", "MPV-UOS › Subtítulos")

    # real keys through uosc: ⌫ one level up, ← too (at a root it arrives as a key event), Esc closes
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    menu_event(h, "mu_menu", {"type": "activate", "index": 1, "value": {"child": {"script": "mu_iptv", "entry": "tv-menu"}}})
    wait_nav(h, "mu-iptv", "MPV-UOS › TV y radio")
    menu_event(h, "mu_iptv", {"type": "activate", "index": 2, "value": {"view": "favorites"}})
    wait_nav(h, "mu-iptv", "MPV-UOS › TV y radio › Favoritos")
    press(h, "LEFT")
    wait_nav(h, "mu-iptv", "MPV-UOS › TV y radio")
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "ESC")
    wait_closed(h)

    # help (?) is a view of the main menu with "Atrás" to the main menu
    h.command("script-binding", "mu_menu/help")
    wait_nav(h, "mu-menu", "MPV-UOS › Ayuda")
    st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "help", timeout=10)
    assert any(i["hint"] == "Espacio" for i in st["items"])
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "ESC")
    wait_closed(h)
    assert h.script_errors() == [], h.script_errors()


@pytest.mark.parametrize("script", list(MODULES))
def test_every_module_opens_goes_back_to_main_menu_and_closes(nav_mpv, media_dir, script):
    h, _d = nav_mpv
    binding, menu_type, title = MODULES[script]
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

    # opened with its key: the parent is the main menu
    h.command("script-binding", f"{script}/{binding}")
    st = wait_nav(h, menu_type, f"MPV-UOS › {title}")
    assert st["script"] == script and st["parent"] == "mu_menu"
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "ESC")
    wait_closed(h)

    # again, closing from inside the module with Esc leaves no menu behind
    h.command("script-binding", f"{script}/{binding}")
    wait_nav(h, menu_type, f"MPV-UOS › {title}")
    press(h, "ESC")
    wait_closed(h)
    assert h.script_errors() == [], h.script_errors()


def test_click_to_pause_is_an_opt_in_preference(nav_mpv, media_dir):
    h, _d = nav_mpv
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)
    h.command("set_property", "pause", True)
    st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and "click_pause" in v, timeout=10)
    assert st["click_pause"] is False
    h.command("keypress", "MBTN_LEFT")
    h.command("script-binding", "mu_menu/click-pause-toggle")  # a later command: the click above was processed
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("click_pause") is True, timeout=10)
    assert h.get("pause") is True  # off by default: the click did nothing

    h.command("keypress", "MBTN_LEFT")
    h.wait_property("pause", lambda v: v is False, timeout=10)
    h.command("keypress", "MBTN_LEFT")
    h.wait_property("pause", lambda v: v is True, timeout=10)
    h.command("script-binding", "mu_menu/click-pause-toggle")
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("click_pause") is False, timeout=10)
    h.command("keypress", "MBTN_LEFT")
    h.command("script-binding", "mu_menu/root")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-menu", timeout=10)
    assert h.get("pause") is True
    assert h.script_errors() == [], h.script_errors()
