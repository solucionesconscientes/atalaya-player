"""H27 · mu-modes (headless mpv + mpvd): the mini player, modo salón and modo sencillo change what they promise and put
everything back when turned off; salón and sencillo are remembered; the short main menu of modo sencillo; a gamepad
(a FIFO standing in for /dev/input/js0) drives mpv through mpvd while salón is on. Plus the joystick decoding."""

from __future__ import annotations

import os
import struct

import pytest

from mpvd.gamepad import AXIS_THRESHOLD, EVENT, JS_AXIS, JS_BUTTON, JS_INIT, Mapper, decode
from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


def js(etype: int, number: int, value: int, t: int = 0) -> bytes:
    return EVENT.pack(t, value, etype, number)


def test_decode_and_mapper():
    data = js(JS_BUTTON | JS_INIT, 0, 0) + js(JS_BUTTON, 0, 1) + js(JS_BUTTON, 0, 0) + js(JS_AXIS, 6, 32767)
    events = decode(data + b"\x01\x02")      # a trailing partial event is ignored
    assert events == [(JS_BUTTON | JS_INIT, 0, 0), (JS_BUTTON, 0, 1), (JS_BUTTON, 0, 0), (JS_AXIS, 6, 32767)]
    m = Mapper()
    assert [m.feed(*e) for e in events] == [None, "play_pause", None, "forward"]
    # an axis fires once until it comes back to the centre
    assert m.feed(JS_AXIS, 6, 30000) is None
    assert m.feed(JS_AXIS, 6, 0) is None
    assert m.feed(JS_AXIS, 6, -AXIS_THRESHOLD - 1) == "back"
    assert m.feed(JS_AXIS, 7, -32767) == "volume_up"
    assert m.feed(JS_BUTTON, 7, 1) == "menu" and m.feed(JS_BUTTON, 3, 1) is None


@pytest.fixture
def modes_mpv(daemon_env, media_dir):
    fifo = daemon_env.runtime_dir / "js0"
    os.mkfifo(fifo)
    daemon_env.extra_env["MPV_UOS_GAMEPAD_DEVICE"] = str(fifo)
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)
        yield h, daemon_env, fifo
    finally:
        h.stop()


def modes(h, pred, timeout: float = 10.0) -> dict:
    return h.wait_property("user-data/mu/modes", lambda v: bool(v) and pred(v), timeout=timeout)


def uosc_opt(h, key: str):
    return (h.get("script-opts") or {}).get("uosc-" + key)


def test_modes_apply_and_restore(modes_mpv):
    h, _d, _fifo = modes_mpv
    before = {k: h.get(k) for k in ("ontop", "border", "fullscreen", "sub-scale", "osd-font-size")}

    h.command("script-binding", "mu_modes/mini-toggle")
    modes(h, lambda v: v["mini"])
    assert h.get("ontop") is True and h.get("border") is False and h.get("fullscreen") is False
    h.command("script-binding", "mu_modes/mini-toggle")
    modes(h, lambda v: not v["mini"])
    assert h.get("ontop") == before["ontop"] and h.get("border") == before["border"]

    h.command("script-message-to", "mu_modes", "mu-modes-set", "salon", "yes")
    modes(h, lambda v: v["salon"])
    assert h.get("fullscreen") is True and h.get("sub-scale") == pytest.approx(1.5) and h.get("osd-font-size") == 50
    assert uosc_opt(h, "scale") == "1.8"
    h.command("script-message-to", "mu_modes", "mu-modes-set", "salon", "no")
    modes(h, lambda v: not v["salon"])
    assert {k: h.get(k) for k in before} == before
    assert uosc_opt(h, "scale") is None

    h.command("script-binding", "mu_modes/simple-toggle")
    modes(h, lambda v: v["simple"])
    assert "button:mu-menu" in uosc_opt(h, "controls")
    # H53 · la opción `controls` solo la lee uosc al arrancar, así que lo que encoge la barra AHORA es `hide` en cada
    # botón propio. uosc no publica sus botones, así que se comprueba lo que se le manda (mu.uosc lo publica).
    barra = h.wait_property("user-data/mu/bar", lambda v: bool(v) and (v.get("mu_record") or {}).get("simple") is True,
                            timeout=10)
    escondidos = sorted({n for s in barra.values() for n in (s.get("hidden") or [])})
    assert "mu-record" in escondidos and "mu-menu" not in escondidos, escondidos
    con_barra = sorted(barra)                 # los scripts que han publicado: los mismos tienen que devolverla luego
    h.command("script-binding", "mu_menu/root")
    # H63 · la vista se publica antes que sus filas (desde N1 se vacían al cambiar de vista, así que leer la lista a
    # medias es leer []), y esperar «view == root» era esperar un instante: se espera a que esté la fila que SOLO
    # existe en modo sencillo, que es el estado «el menú corto está en pantalla».
    st = h.wait_property("user-data/mu/menu",
                         lambda v: bool(v) and v.get("view") == "root"
                         and any(i.get("title") == "Menú completo" for i in v.get("items") or []), timeout=10)
    titles = [i["title"] for i in st["items"]]
    assert [t for t in titles if t != "Continuar viendo"] == ["Abrir o descargar", "TV y radio", "Subtítulos",
                                                          "Preferencias",
                                                              "Menú completo", "Salir"]
    # «Menú completo» turns the mode off and the open main menu grows back to its eight categories
    h.command("script-message-to", "mu_modes", "mu-modes-set", "simple", "no")
    modes(h, lambda v: not v["simple"])
    st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and any(i["title"] == "Herramientas"
                                                                         for i in v.get("items") or []), timeout=10)
    assert uosc_opt(h, "controls") is None
    # H63 · cada script observa el modo por su cuenta y publica lo suyo, así que NO cambian todos a la vez: esperar a
    # uno (mu_record) y exigir a los veinte era leer un instante. Se espera por el estado completo, que además es el
    # invariante que de verdad importa: salir del modo sencillo devuelve TODOS los botones, no la mayoría.
    barra = h.wait_property("user-data/mu/bar",
                            lambda v: bool(v) and all(not ((v.get(n) or {}).get("hidden") or []) for n in con_barra),
                            timeout=10)
    assert (barra.get("mu_record") or {}).get("simple") is False
    assert all(not ((barra.get(n) or {}).get("hidden") or []) for n in con_barra), barra


def test_modes_remembered(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("script-message-to", "mu_modes", "mu-modes-set", "simple", "yes")
        h.command("script-message-to", "mu_modes", "mu-modes-set", "salon", "yes")
        modes(h, lambda v: v["simple"] and v["salon"])
    finally:
        h.stop()
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        modes(h, lambda v: v["simple"] and not v["salon"])       # salón waits for the first file
        h.command("loadfile", str(media_dir / "video30.mkv"))
        modes(h, lambda v: v["salon"])
        assert h.get("fullscreen") is True
    finally:
        h.stop()


def test_gamepad_drives_salon(modes_mpv):
    h, _d, fifo = modes_mpv
    h.command("script-message-to", "mu_modes", "mu-modes-set", "salon", "yes")
    modes(h, lambda v: v["salon"] and v["gamepad"] == str(fifo), timeout=20)
    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)      # mpvd has it open for reading already
    try:
        assert h.get("pause") is True
        os.write(fd, js(JS_BUTTON | JS_INIT, 0, 0) + js(JS_BUTTON, 0, 1) + js(JS_BUTTON, 0, 0))
        h.wait_property("pause", lambda v: v is False, timeout=10)
        os.write(fd, js(JS_BUTTON, 0, 1))
        h.wait_property("pause", lambda v: v is True, timeout=10)
        pos = h.get("time-pos") or 0
        os.write(fd, js(JS_AXIS, 6, 32767) + js(JS_AXIS, 6, 0))
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= pos + 9, timeout=10)
        vol = h.get("volume")
        os.write(fd, js(JS_AXIS, 7, -32767))
        h.wait_property("volume", lambda v: v == pytest.approx(min(vol + 5, 130)), timeout=10)
        # with salón off the buttons are ignored
        h.command("script-message-to", "mu_modes", "mu-modes-set", "salon", "no")
        modes(h, lambda v: not v["salon"])
        os.write(fd, js(JS_BUTTON, 0, 1))
        with pytest.raises(Exception):
            h.wait_property("pause", lambda v: v is False, timeout=2)
    finally:
        os.close(fd)


def test_esc_sale_de_pantalla_completa_y_devuelve_la_ventana(modes_mpv):
    """Esc sin nada abierto sale de pantalla completa (antes no hacía nada y en KDE parecía colgado) y la ventana
    vuelve maximizada, que es como abre (ADR-132); nunca cierra la aplicación. Con un menú abierto el primer Esc
    cierra el menú y NO sale de pantalla completa: primero se cierra lo abierto, después se devuelve la ventana."""
    h, _d, _fifo = modes_mpv
    assert h.get("window-maximized") is True

    h.command("set_property", "fullscreen", True)
    h.wait_property("fullscreen", lambda v: v is True, timeout=10)
    h.command("keypress", "ESC")
    h.wait_property("fullscreen", lambda v: v is False, timeout=10)
    assert h.proc.poll() is None, "Esc ha cerrado el reproductor"
    h.wait_property("window-maximized", lambda v: v is True, timeout=10)

    h.command("set_property", "fullscreen", True)
    h.wait_property("fullscreen", lambda v: v is True, timeout=10)
    h.command("script-binding", "mu_menu/root")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-menu", timeout=15)
    h.command("keypress", "ESC")
    h.wait_property("user-data/uosc/menu/type", lambda v: v in (None, ""), timeout=10)
    assert h.get("fullscreen") is True, "el primer Esc ha salido de pantalla completa con el menú abierto"
    h.command("keypress", "ESC")
    h.wait_property("fullscreen", lambda v: v is False, timeout=10)
    assert h.proc.poll() is None


def test_salir_de_pantalla_completa_apaga_el_salon(modes_mpv):
    """El modo salón ES la pantalla completa: salir de ella (Esc, `f` o el botón) lo apaga también, y con él vuelven
    el tamaño de letra y la escala de la interfaz. Antes el estado seguía diciendo «salón activo» sin pantalla
    completa."""
    h, _d, _fifo = modes_mpv
    antes = {k: h.get(k) for k in ("sub-scale", "osd-font-size")}

    h.command("script-message-to", "mu_modes", "mu-modes-set", "salon", "yes")
    modes(h, lambda v: v["salon"])
    assert h.get("fullscreen") is True and h.get("sub-scale") == pytest.approx(1.5)

    h.command("keypress", "ESC")
    h.wait_property("fullscreen", lambda v: v is False, timeout=10)
    modes(h, lambda v: not v["salon"], timeout=10)
    assert {k: h.get(k) for k in antes} == antes
    assert uosc_opt(h, "scale") is None
    assert h.proc.poll() is None


def test_mini_desde_maximizado_vuelve_maximizado(modes_mpv):
    """El mini necesita tamaño propio: desde una ventana maximizada hay que des-maximizarla antes de pedir el 30 %,
    y al salir del mini la ventana vuelve maximizada (antes se quedaba con el tamaño del mini)."""
    h, _d, _fifo = modes_mpv
    h.command("set_property", "window-maximized", True)
    h.wait_property("window-maximized", lambda v: v is True, timeout=10)

    h.command("script-binding", "mu_modes/mini-toggle")
    modes(h, lambda v: v["mini"])
    assert h.get("window-maximized") is False and h.get("border") is False

    h.command("script-binding", "mu_modes/mini-toggle")
    modes(h, lambda v: not v["mini"])
    assert h.get("window-maximized") is True and h.get("border") is True


def test_js_event_layout():
    # struct js_event from linux/joystick.h: __u32 time; __s16 value; __u8 type; __u8 number
    assert EVENT.size == 8 and EVENT.format in ("<IhBB", b"<IhBB")
    assert struct.unpack("<IhBB", js(JS_BUTTON, 3, -2, t=7)) == (7, -2, JS_BUTTON, 3)
