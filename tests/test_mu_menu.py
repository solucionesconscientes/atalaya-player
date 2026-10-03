"""mu-menu.lua end to end: continue watching by content hash (file renamed between plays), root menu with recents,
command palette (commands + channels + recents + mpvd actions) and the idle start screen."""

from __future__ import annotations

import contextlib
import json
import shutil
import time

import pytest

from tests.conftest import start_mpv
from tests.test_mu_iptv import tv  # noqa: F401 - fixture: local TV list + live stream + daemon

MU_OPTS = ("--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
           "mu-menu-save_interval=1,mu-menu-resume_min=3")


def send_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_menu", "mu-menu-event", json.dumps({**base, **ev}))


def wait_menu(h, view: str, pred=lambda v: True, timeout: float = 30.0):
    return h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == view and pred(v), timeout=timeout)


def titles(v):
    return [i["title"] for i in v.get("items", [])]


@pytest.fixture
def menu_mpv(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=no"], env=daemon_env.env)
    try:
        yield h, daemon_env
    finally:
        h.stop()


def test_continue_watching_survives_rename_and_root_menu(menu_mpv, media_dir, tmp_path):
    h, d = menu_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    video = media_dir / "video30.mkv"

    # play, seek to 22 s (≥ 20 s counts as resumable), positions are reported to mpvd (every second here)
    h.command("loadfile", str(video))
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("tracked") is True and v.get("path") == str(video),
                    timeout=30)
    h.command("seek", "22", "absolute", "exact")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= 22, timeout=20)
    h.command("set_property", "pause", True)  # pause → immediate save
    d.wait(lambda: any(r["title"].startswith("video30") and r["position"] >= 22 and r["resume"]
                       for r in d.call("watch.recents")), timeout=20)

    # the same content under another name resumes at ~22 s (no mpv watch_later involved: disabled in tests)
    renamed = tmp_path / "otro título.mkv"
    shutil.copyfile(video, renamed)
    h.command("loadfile", str(renamed))
    st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("path") == str(renamed) and v.get("resumed"),
                         timeout=30)
    assert st["tracked"] is True
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 21 <= v <= 27, timeout=20)
    recents = d.call("watch.recents")
    assert len(recents) == 1 and recents[0]["path"] == str(renamed) and recents[0]["plays"] == 2

    # a finished file is not resumed: play chapters.mkv to (nearly) the end
    h.command("set_property", "pause", True)
    h.command("loadfile", str(media_dir / "chapters.mkv"))
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("path", "").endswith("chapters.mkv"), timeout=30)
    dur = h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)
    h.command("seek", str(dur - 0.5), "absolute", "exact")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= dur - 1, timeout=20)
    h.command("set_property", "pause", False)
    h.wait_property("idle-active", lambda v: v is True, timeout=30)  # keep-open=no → eof → idle
    d.wait(lambda: any(r["path"].endswith("chapters.mkv") and r["finished"] for r in d.call("watch.recents")), timeout=20)

    # root menu: "Continuar viendo" submenu with the unfinished video on top of the eight categories
    h.command("script-binding", "mu_menu/root")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-menu", timeout=15)
    st = wait_menu(h, "root", lambda v: "Continuar viendo" in titles(v))
    t = titles(st)
    assert t[0] == "Continuar viendo" and "Abrir o descargar" in t and "TV y radio" in t
    assert "Preferencias" in t and "Salir" in t
    cont = next(i for i in st["items"] if i["title"] == "Continuar viendo")
    assert cont["submenu"] == 2 and cont["hint"] == "1"  # 1 resumable + "Todos los recientes…"
    # activating a recent through the callback loads it (uosc would send the leaf item's value)
    send_event(h, {"type": "activate", "index": 1, "value": {"open": str(renamed), "position": 22, "resume": True}})
    h.wait_property("path", lambda v: v == str(renamed), timeout=20)
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("resumed"), timeout=30)
    assert h.script_errors() == [], h.script_errors()


def test_palette_and_start_screen(tv, media_dir):  # noqa: F811
    _tv_mpv, d, _record_dir = tv  # the fixture's own mpv stays idle; we need one started with the start screen on
    h = start_mpv(d.runtime_dir, [MU_OPTS, "--keep-open=no"], env=d.env, start_screen=True)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        # start screen opened by itself (idle, no file)
        st = wait_menu(h, "start")
        assert st["start_shown"] is True and h.get("user-data/uosc/menu/type") == "mu-menu"
        assert "Abrir o descargar…" in titles(st) and "TV y radio" in titles(st)
        h.command("script-message-to", "uosc", "close-menu", "mu-menu")
        h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "", timeout=10)

        # seed the history and the TV catalogue
        d.call("watch.update", {"path": str(media_dir / "video30.mkv"), "position": 25, "duration": 30,
                                "title": "Película de prueba"})
        d.call("iptv.refresh")

        h.command("script-binding", "mu_menu/palette")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-palette", timeout=15)
        # H46: los comandos salen al instante de la tabla propia (antes se leían de `input-bindings`, que tarda un
        # momento en estar), así que hay que esperar a lo que viene de mpvd y no a «haya algún resultado»
        st = wait_menu(h, "palette", lambda v: "Recientes" in titles(v) and "Película de prueba" in titles(v))
        t = titles(st)
        assert "Comandos" in t  # empty query: recents + top commands

        send_event(h, {"type": "search", "query": "captura"})
        st = wait_menu(h, "palette", lambda v: v.get("palette_query") == "captura" and "Captura de pantalla" in titles(v))
        cmd_item = next(i for i in st["items"] if i["title"] == "Captura de pantalla")
        assert cmd_item["hint"] == "Ctrl+s" and cmd_item["value"]["cmd"].startswith("async screenshot")

        send_event(h, {"type": "search", "query": "canal"})
        st = wait_menu(h, "palette", lambda v: v.get("palette_query") == "canal" and "Canales" in titles(v))
        assert {"Canal Uno", "Canal Dos", "Canal Tres"} <= set(titles(st))
        assert "yt-dlp › Menú (calidad, descargas)" not in titles(st)  # commands filtered by the query

        send_event(h, {"type": "search", "query": "pelicula"})  # accent-insensitive recents
        st = wait_menu(h, "palette", lambda v: v.get("palette_query") == "pelicula" and "Película de prueba" in titles(v))

        send_event(h, {"type": "search", "query": "yt-dlp"})
        st = wait_menu(h, "palette", lambda v: v.get("palette_query") == "yt-dlp" and "mpvd" in titles(v))
        assert "Buscar actualización de yt-dlp" in titles(st)

        # activate a channel → played through mu-iptv
        send_event(h, {"type": "search", "query": "canal dos"})
        st = wait_menu(h, "palette", lambda v: v.get("palette_query") == "canal dos" and "Canal Dos" in titles(v))
        ch = next(i for i in st["items"] if i["title"] == "Canal Dos")
        send_event(h, {"type": "activate", "index": 1, "value": ch["value"]})
        h.wait_property("user-data/mu/iptv", lambda v: bool(v) and isinstance(v.get("current"), dict)
                        and v["current"]["name"] == "Canal Dos", timeout=30)
        h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("tracked") is False, timeout=20)  # live: not tracked

        # a command from the palette runs
        h.command("script-binding", "mu_menu/palette")
        wait_menu(h, "palette", lambda v: v.get("palette_results", 0) > 0)
        send_event(h, {"type": "activate", "index": 1, "value": {"cmd": "set pause yes"}})
        h.wait_property("pause", lambda v: v is True, timeout=10)
        h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=10)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_recents_closed_while_mpvd_answers_does_not_reopen_itself(daemon_env, media_dir):
    """H34 · Esc mientras se espera a watch.recents dejaba un menú huérfano encima del vídeo.

    La carrera se hace segura parando el daemon (SIGSTOP) mientras se cierra el menú: la respuesta llega después.
    """
    import os
    import signal

    from tests.test_nav import press, wait_closed

    # watchdog largo: mu-core no debe intentar resucitar al daemon mientras está parado a propósito
    h = start_mpv(daemon_env.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=600,mu-core-rpc_timeout=20",
                                           "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    pid = None
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)

        pid = int((daemon_env.runtime_dir / "mpvd.pid").read_text())
        os.kill(pid, signal.SIGSTOP)
        h.command("script-binding", "mu_menu/recents")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-menu", timeout=15)
        press(h, "ESC")
        wait_closed(h)
    finally:
        if pid is not None:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGCONT)
    try:
        time.sleep(2.0)                                # the answer to watch.recents lands in this window
        assert not h.get("user-data/uosc/menu/type")
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_backspace_closes_the_palette(tv, media_dir):  # noqa: F811
    """H34 · el pie dice «⌫ cierra», pero se cerraba el tipo de menú equivocado (mu-menu, no mu-palette)."""
    from tests.test_nav import press, wait_closed

    _tv_mpv, d, _record_dir = tv
    h = start_mpv(d.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.command("script-binding", "mu_menu/palette")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-palette", timeout=20)
        press(h, "BS")
        wait_closed(h)
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_abrir_varios_archivos_ensena_la_lista_y_se_quita_sola(tv, media_dir):  # noqa: F811
    """H47 · abrir varios de golpe ya construía la lista (eso lo hace mpv), pero no se veía: lo único que lo
    insinuaba eran los botones ⏮⏭ de la barra. Ahora se enseña al abrir el primero y se quita sola, para no dejar
    un menú encima de la película. Con un solo archivo no aparece."""
    _tv_mpv, d, _record_dir = tv
    h = start_mpv(d.runtime_dir, [MU_OPTS, "--script-opts-append=mu-menu-playlist_on_open=3",
                                  "--keep-open=yes", "--pause=yes"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=40)

        # un solo archivo: no hay lista, así que no se enseña nada
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)
        time.sleep(1.5)
        # la clave no existe mientras no haya menú, y leerla entonces es un error, no un None
        with contextlib.suppress(Exception):
            assert not h.get("user-data/uosc/menu/type"), "con un archivo no debe salir la lista"

        # tres de golpe (dos vídeos y un audio): sale la lista de uosc, con los tres
        h.command("loadfile", str(media_dir / "video30.mkv"))
        for extra in ("chapters.mkv", "voz_es.flac"):
            h.command("loadfile", str(media_dir / extra), "append")
        h.wait_property("playlist-count", lambda v: v == 3, timeout=20)
        h.command("playlist-play-index", 0)
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "playlist", timeout=20)

        # y se quita sola sin que nadie toque nada
        h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=20)
        assert h.get("playlist-count") == 3   # la lista sigue ahí, solo se ha cerrado el menú
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_una_reproduccion_se_cuenta_una_vez(menu_mpv, media_dir, tmp_path):
    """H58 · «Continuar viendo» contaba de más. Dos cosas lo rompían y las dos dependían de quién ganara una
    carrera, así que el mismo vídeo salía unas veces con 2 reproducciones y otras con 3: el reproductor reintenta
    las llamadas que no contesta a tiempo (y sumar no es idempotente), y la fila la puede crear un guardado de
    posición, cuyo INSERT ya pone plays=1, de modo que el «he empezado esto» que llegaba detrás sumaba otra.
    Ahora el identificador de la reproducción viaja en todas las llamadas y se cuenta una vez por identificador."""
    h, d = menu_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    video = media_dir / "video30.mkv"

    h.command("loadfile", str(video))
    h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("tracked") is True, timeout=30)
    h.command("seek", "10", "absolute", "exact")
    h.command("set_property", "pause", True)
    d.wait(lambda: any(r["position"] >= 9 for r in d.call("watch.recents")), timeout=20)
    assert [r["plays"] for r in d.call("watch.recents")] == [1]

    # otra reproducción cuenta una vez, y sus reintentos NO: es el identificador el que manda, no cuántas
    # llamadas lleguen ni cuándo
    for _ in range(3):
        d.call("watch.update", {"path": str(video), "position": 11, "new_play": True, "play_id": "segunda"})
    assert [r["plays"] for r in d.call("watch.recents")] == [2]
    d.call("watch.update", {"path": str(video), "position": 12, "new_play": True, "play_id": "tercera"})
    assert [r["plays"] for r in d.call("watch.recents")] == [3]
    # y un guardado de posición sin declarar reproducción no cuenta nunca
    d.call("watch.update", {"path": str(video), "position": 13})
    assert [r["plays"] for r in d.call("watch.recents")] == [3]
