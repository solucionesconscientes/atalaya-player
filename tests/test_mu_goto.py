"""H58 · ir a un minuto escribiéndolo.

Pedido por Ser: «poder introducir manualmente tiempo: darle a un icono y poner p ej 00:02:15 y va allí».
"""

from __future__ import annotations

import json

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
