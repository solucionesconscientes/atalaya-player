"""H22 · «Biblioteca» end to end (headless mpv + mpvd): add a folder by typing its path, background scan, Series ›
temporada › episodio with ✓ / %, play an episode, automatic next episode with a cancellable countdown (keep-open end
of file), the switch in Ajustes, the start screen rows in user-data/mu/library/home and «Buscar subtítulos en
internet» against the fake OpenSubtitles API."""

from __future__ import annotations

import json
import shutil
import time

import pytest

from mpvd.hashing import file_hash
from tests.conftest import APP, start_mpv
from tests.test_nav import wait_nav
from tests.test_opensubtitles import fake_api  # noqa: F401 - fixture

MU_OPTS = ("--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
           "mu-library-countdown_seconds=2,mu-library-open_command=true")


@pytest.fixture
def lib_mpv(daemon_env, media_dir, fake_api):  # noqa: F811
    base, st = fake_api
    daemon_env.extra_env["MPV_UOS_OSUB_URL"] = base
    # C4: mpvd lee «el portapapeles» de una propiedad propia, así el test no toca el portapapeles de verdad
    daemon_env.extra_env["MPVD_LIBRARY_CLIPBOARD_PROP"] = "user-data/prueba/clip"
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env, st
    finally:
        h.stop()


def ev(h, message: str, event: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_library", message, json.dumps({**base, **event}))


def lib_state(h, pred, timeout: float = 20.0):
    return h.wait_property("user-data/mu/library", lambda v: bool(v) and pred(v), timeout=timeout)


def titles(v):
    return [i["title"] for i in v.get("items", [])]


# H63 · un `item()` que revienta dentro de un `wait_property` tumba la espera entera en vez de volver a mirar.
# Desde que una vista nueva se publica SIN las filas de la anterior, eso pasa de verdad: la primera lectura llega
# con la lista vacía. Devuelve None y que decida quien pregunta.
def item(v, title):
    return next((i for i in v.get("items") or [] if i["title"] == title), None)


def test_library_menu_play_next_episode_countdown_home_and_subtitles(lib_mpv, media_dir, tmp_path):
    h, d, st = lib_mpv
    lib = tmp_path / "Biblioteca"
    season = lib / "Mi Serie" / "Temporada 1"
    season.mkdir(parents=True)
    eps = []
    for n in (1, 2, 3):
        p = season / f"Mi.Serie.S01E0{n}.720p.mkv"
        shutil.copyfile(media_dir / "serie" / f"ep0{n}.mkv", p)
        eps.append(p)
    (lib / "Películas").mkdir()
    shutil.copyfile(media_dir / "video30.mkv", lib / "Películas" / "Tiburón (1975).mkv")

    # Biblioteca › Carpetas › type the path
    h.command("script-binding", "mu_library/library-menu")
    wait_nav(h, "mu-library", f"{APP} › Biblioteca")
    s = lib_state(h, lambda v: v.get("view") == "root" and "Carpetas" in titles(v))
    assert "Películas" in titles(s) and "Series" in titles(s)
    ev(h, "mu-library-event", {"type": "activate", "index": 5, "value": item(s, "Carpetas")["value"]})
    s = lib_state(h, lambda v: v.get("view") == "folders" and "Escribir o pegar una ruta…" in titles(v))
    ev(h, "mu-library-event", {"type": "activate", "index": 1, "value": {"input": "folder"}})
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-library-input", timeout=10)
    lib_state(h, lambda v: v.get("input") == "folder")
    ev(h, "mu-library-input-event", {"type": "search", "query": str(lib)})
    ev(h, "mu-library-input-event", {"type": "activate", "index": 1, "value": {"save": str(lib)}})
    d.wait(lambda: d.call("library.status")["episodes"] == 3 and not d.call("library.status")["scanning"], timeout=60)
    s = lib_state(h, lambda v: v.get("view") == "folders" and str(lib) in titles(v)
                  and item(v, str(lib))["hint"] == "4 archivos", timeout=30)
    lib_state(h, lambda v: not v.get("scanning"))

    # ⌫ → Biblioteca › Series › Mi Serie › Temporada 1
    ev(h, "mu-library-event", {"type": "back"})
    s = lib_state(h, lambda v: v.get("view") == "root" and "Series" in titles(v) and v["counts"].get("episodes") == 3)
    assert item(s, "Películas")["hint"] == "1"
    ev(h, "mu-library-event", {"type": "activate", "index": 3, "value": item(s, "Series")["value"]})
    s = lib_state(h, lambda v: v.get("view") == "shows" and "Mi Serie" in titles(v))
    assert item(s, "Mi Serie")["hint"] == "3 episodios"
    ev(h, "mu-library-event", {"type": "activate", "index": 1, "value": item(s, "Mi Serie")["value"]})
    s = lib_state(h, lambda v: v.get("view") == "show" and "Temporada 1" in titles(v))
    ev(h, "mu-library-event", {"type": "activate", "index": 1, "value": item(s, "Temporada 1")["value"]})
    s = lib_state(h, lambda v: v.get("view") == "season" and titles(v) == ["1x01", "1x02", "1x03"])
    nav = h.get("user-data/mu/nav")
    assert nav["title"].endswith("Mi Serie › Temporada 1") and nav["title"].startswith(APP)

    # Enter plays the episode and closes the menu
    ev(h, "mu-library-event", {"type": "activate", "index": 1, "value": item(s, "1x01")["value"]})
    h.wait_property("path", lambda v: v == str(eps[0]), timeout=15)
    h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=10)
    lib_state(h, lambda v: v.get("next_path") == str(eps[1]) and v.get("next_source") == "library")

    # end of the episode (keep-open: paused at the end) → countdown → the next one plays by itself
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=15)
    h.command("seek", 36.5, "absolute")
    s = lib_state(h, lambda v: v.get("countdown", 0) > 0, timeout=15)
    assert s["next_title"] == "Mi Serie · 1x02"
    h.wait_property("path", lambda v: v == str(eps[1]), timeout=15)
    lib_state(h, lambda v: v.get("countdown") == 0 and v.get("next_path") == str(eps[2]))

    # the episode left behind counts as watched (mu-menu saved its position at the end)
    d.wait(lambda: d.call("library.list", {"show": "mi serie", "season": 1})["episodes"][0]["finished"], timeout=20)

    # Esc cancels the countdown: the player stays on the finished episode
    h.command("seek", 36.5, "absolute")
    lib_state(h, lambda v: v.get("countdown", 0) > 0, timeout=15)
    h.command("keypress", "ESC")
    lib_state(h, lambda v: v.get("countdown") == 0)
    time.sleep(3)
    assert h.get("path") == str(eps[1])

    # start screen rows: 1x02 ended (paused at its end) → the series comes back as «siguiente episodio» 1x03
    def home_has_next() -> bool:
        h.command("script-message-to", "mu_library", "mu-library-home")
        time.sleep(0.5)
        home = h.get("user-data/mu/library").get("home") or []
        return any(r["value"]["open"] == str(eps[2]) for r in home)

    d.wait(home_has_next, timeout=30, interval=0.5)
    s = h.get("user-data/mu/library")
    row = next(r for r in s["home"] if r["title"].startswith("Mi Serie"))
    assert row == {"title": "Mi Serie · 1x03", "hint": "Siguiente episodio", "icon": "skip_next",
                   "value": {"open": str(eps[2]), "library": True, "row": "next"}}
    assert h.get("user-data/mu/library/home") == s["home"]

    # the season shows ✓ for the finished episode
    h.command("script-binding", "mu_library/library-menu")
    s = lib_state(h, lambda v: v.get("view") == "root" and "Ajustes" in titles(v))
    assert any(t == "Seguir viendo" for t in titles(s))

    # Ajustes: switch the automatic next episode off → the end of 1x01 no longer jumps
    ev(h, "mu-library-event", {"type": "activate", "index": 7, "value": item(s, "Ajustes")["value"]})
    s = lib_state(h, lambda v: v.get("view") == "settings" and "Siguiente episodio automático" in titles(v))
    assert item(s, "Siguiente episodio automático")["hint"] == "sí"
    ev(h, "mu-library-event", {"type": "activate", "index": 1, "value": {"toggle": "auto_next"}})
    s = lib_state(h, lambda v: v.get("auto_next") is False and v.get("view") == "settings"
                  and (item(v, "Siguiente episodio automático") or {}).get("hint") == "no")
    h.command("script-message-to", "uosc", "close-menu", "mu-library")
    h.command("loadfile", str(eps[0]))
    h.command("set", "pause", "no")          # keep-open left the previous episode paused
    h.wait_property("path", lambda v: v == str(eps[0]), timeout=15)
    lib_state(h, lambda v: v.get("next_path") == str(eps[1]))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=15)
    h.command("seek", 36.5, "absolute")
    h.wait_property("eof-reached", lambda v: v is True, timeout=15)
    time.sleep(3)
    assert h.get("path") == str(eps[0]) and h.get("user-data/mu/library")["countdown"] == 0

    # «Buscar subtítulos en internet» (fake OpenSubtitles): results by hash, Enter downloads and selects the track
    st["hash"] = file_hash(eps[0]).opensubtitles
    d.call("library.settings.set", {"osub_enabled": True, "osub_api_key": "clave-os", "osub_resync": "never"})
    h.command("script-binding", "mu_library/library-subs")
    s = lib_state(h, lambda v: v.get("view") == "subs" and "Descargar el mejor" in titles(v), timeout=30)
    best = item(s, "Descargar el mejor")
    assert best["value"]["sub"] == 2000002
    assert any(i["hint"].startswith("✓ exacto") for i in s["items"])
    ev(h, "mu-library-event", {"type": "activate", "index": 2, "value": best["value"]})
    tracks = h.wait_property("track-list", lambda v: any(t.get("type") == "sub" and t.get("external")
                                                         and t.get("title") == "OpenSubtitles · es" for t in v or []),
                             timeout=30)
    sub = next(t for t in tracks if t.get("title") == "OpenSubtitles · es")
    assert sub["selected"] is True and sub["lang"] == "es"
    lib_state(h, lambda v: v.get("subs_status") == "added" and v.get("last_subs", "").endswith("2000002.es.srt"))
    assert h.script_errors() == [], h.script_errors()


def test_alta_guiada_de_opensubtitles_desde_el_menu(lib_mpv, media_dir, tmp_path):
    """H36/C4: sin clave, «Buscar subtítulos en internet» no es un callejón sin salida: guía el alta en dos pasos."""
    h, d, st = lib_mpv
    video = tmp_path / "Mi.Serie.S01E01.720p.mkv"
    shutil.copyfile(media_dir / "serie" / "ep01.mkv", video)
    st["hash"] = file_hash(video).opensubtitles
    h.command("loadfile", str(video))
    h.wait_property("path", lambda v: v == str(video), timeout=15)

    # 1. sin clave: los dos pasos, no un error
    h.command("script-binding", "mu_library/library-subs")
    s = lib_state(h, lambda v: v.get("view") == "subs"
                  and any(t.startswith("Paso 1") for t in titles(v)), timeout=30)
    paso1 = next(i for i in s["items"] if i["title"].startswith("Paso 1"))
    paso2 = next(i for i in s["items"] if i["title"].startswith("Paso 2"))
    assert "consumers" in paso1["hint"] or "Consumers" in paso1["hint"]
    assert any(t == "…o escribirla a mano" for t in titles(s))

    # 2. Paso 1 abre la página (open_command=true en los tests: no se abre ningún navegador de verdad)
    ev(h, "mu-library-event", {"type": "activate", "index": 3, "value": paso1["value"]})

    # 3. Paso 2 pega la clave del portapapeles: la lee mpvd, no el script
    h.command("set_property", "user-data/prueba/clip", "clave-os")
    ev(h, "mu-library-event", {"type": "activate", "index": 4, "value": paso2["value"]})
    s = lib_state(h, lambda v: v.get("view") == "subs" and any(t == "Descargar el mejor" for t in titles(v)),
                  timeout=30)
    ajustes = d.call("library.settings.get")
    assert ajustes["has_osub_key"] is True and ajustes["osub_enabled"] is True
    assert d.call("library.subs.help")["active"] is True

    # 4. y en Ajustes ya no se ofrecen los pasos, sino la clave guardada y el cupo
    h.command("script-message-to", "uosc", "close-menu", "mu-library")
    h.command("script-binding", "mu_library/library-menu")
    s = lib_state(h, lambda v: v.get("view") == "root" and "Ajustes" in titles(v))
    ev(h, "mu-library-event", {"type": "activate", "index": 7, "value": item(s, "Ajustes")["value"]})
    s = lib_state(h, lambda v: v.get("view") == "settings"
                  and any(t == "Api-Key de OpenSubtitles" for t in titles(v)), timeout=30)
    assert item(s, "Api-Key de OpenSubtitles")["hint"] == "guardada"
    assert not any(t.startswith("Paso ") for t in titles(s))
    s = lib_state(h, lambda v: v.get("quota", "") not in ("", "consultando…"), timeout=20)
    assert "se sabrá al descargar" in s["quota"] or "descargas" in s["quota"]
    assert h.script_errors() == [], h.script_errors()
