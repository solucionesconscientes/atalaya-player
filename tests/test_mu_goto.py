"""H58 · ir a un minuto escribiéndolo.

Pedido por Ser: «poder introducir manualmente tiempo: darle a un icono y poner p ej 00:02:15 y va allí».
H74: los dos últimos tests teclean de verdad, porque los de arriba mandan el evento `activate` ya cocinado y por
eso el fallo de «escribo el minuto, doy a Enter y no hace nada» pudo pasar desapercibido.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


@pytest.fixture
def goto_mpv(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes",
                                           str(media_dir / "video30.mkv")], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=40)
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 10, timeout=20)
        yield h
    finally:
        h.stop()


def ev_menu(h, event: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_menu", "mu-menu-event", json.dumps({**base, **event}))


def test_se_escribe_el_minuto_y_va_alli(goto_mpv):
    h = goto_mpv
    h.command("script-binding", "mu_menu/goto")
    h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-goto", timeout=10)

    # lo que escribe Ser: 00:00:12
    ev_menu(h, {"type": "search", "query": "00:00:12"})
    ev_menu(h, {"type": "activate", "index": 1, "value": {"goto_secs": 12, "goto_rel": 0}})
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 12) < 1.0, timeout=15)
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)

    # y en relativo, que es lo que se usa para corregir un poco
    h.command("script-binding", "mu_menu/goto")
    h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-goto", timeout=10)
    ev_menu(h, {"type": "activate", "index": 1, "value": {"goto_secs": 5, "goto_rel": -1}})
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 7) < 1.0, timeout=15)
    assert h.script_errors() == [], h.script_errors()


def test_sin_duracion_no_se_ofrece(daemon_env):
    """Una opción que no puede funcionar no se abre como si pudiera (la regla de docs/IDEAS.md 3.2)."""
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--idle=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=40)
        h.command("script-binding", "mu_menu/goto")
        with pytest.raises(TimeoutError):
            h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-goto", timeout=4)
    finally:
        h.stop()


def teclear(h, texto: str) -> None:
    """Escribe como una persona: una tecla por vez (por IPC, `keypress`)."""
    for ch in texto:
        h.command("keypress", ch)
        time.sleep(0.1)


def abrir_caja(h) -> None:
    h.command("script-binding", "mu_menu/goto")
    h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-goto", timeout=10)
    # y a que uosc termine de enlazar las teclas del menú: publica `menu/type` unos ms ANTES de tenerlas puestas
    # (docs/UOSC_API.md §2). Sin esta espera la primera tecla se pierde y el test se vuelve una lotería.
    h.wait_property("input-bindings", lambda bs: any("menu-any" in str(b.get("cmd", "")) for b in bs or []),
                    timeout=10)


def test_teclear_el_minuto_y_dar_enter_salta(goto_mpv):
    """El camino bueno, con teclas de verdad de principio a fin: teclear 0:12, Enter, y el vídeo en el segundo 12."""
    h = goto_mpv
    abrir_caja(h)
    teclear(h, "0:12")
    h.command("keypress", "enter")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 12) < 1.0, timeout=15)
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)   # y la caja se cierra
    assert h.script_errors() == [], h.script_errors()


@pytest.mark.parametrize("texto, esperado", [("2x", "No entiendo"), ("9:99", "se sale del archivo")])
def test_si_no_se_puede_ir_lo_dice_y_no_se_queda_callado(goto_mpv, texto, esperado):
    """El fallo que vio Ser (2026-10-15): escribir algo que no se entiende, dar a Enter y que NO pase nada, sin decir
    por qué. uosc no activa nada (la única fila es la ayuda, que no se puede seleccionar) y manda el Enter como
    evento `key`: ahora se atiende, se dice el motivo en pantalla y la caja sigue abierta para corregir.
    Al corregir, ojo: ⌫ hasta vaciar el cuadro lo cierra uosc por su cuenta, así que la caja se vuelve a abrir
    (docs/UOSC_API.md §7)."""
    h = goto_mpv
    h.command("set_property", "user-data/mu/menu-osd", "")
    antes = h.get("time-pos")
    abrir_caja(h)
    teclear(h, texto)
    h.command("keypress", "enter")
    h.wait_property("user-data/mu/menu-osd", lambda v: isinstance(v, str) and esperado in v, timeout=10)
    assert h.get("user-data/uosc/menu/type") == "mu-goto", "la caja se queda abierta para poder corregir"
    assert h.get("time-pos") == pytest.approx(antes, abs=0.5), "no se ha movido el vídeo"
    # y corrigiendo encima sí salta. Con ⌫ hasta el final el cuadro se queda vacío y uosc cierra el palette él solo
    # (`Menu.lua:1052`), así que hay que volver a abrirlo: es lo que hace una persona que se equivoca.
    for _ in texto:
        h.command("keypress", "BS")   # ⌫ borra lo escrito, uno por letra
        time.sleep(0.1)
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)   # se ha cerrado sola
    assert h.script_errors() == [], h.script_errors()
    abrir_caja(h)
    teclear(h, "0:12")
    h.command("keypress", "enter")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - 12) < 1.0, timeout=15)
    assert h.script_errors() == [], h.script_errors()
