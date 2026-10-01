"""H36 · C8 en pantalla: salir con `q` cuando no hay nada y cuando sí lo hay.

`q` ya no es `quit` a secas: pasa por mu-core, que le pregunta a mpvd qué queda trabajando. Lo que se comprueba aquí es
lo primero que se rompería: que sin nada pendiente mpv se cierra igual de rápido, y que la línea «qué se está haciendo
por detrás» llega al menú.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


@pytest.fixture
def mpv(daemon_env):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def test_sin_nada_pendiente_la_q_cierra_igual(mpv):
    h, d = mpv
    assert d.call("pending.status")["busy"] is False
    h.command("keypress", "q")
    # sin nada que preguntar no hay diálogo: mpv se va (el margen de seguridad de mu-core es de 6 s)
    h.proc.wait(timeout=20)
    assert h.proc.returncode is not None


def test_la_linea_de_trabajo_de_fondo_llega_al_menu(mpv):
    h, _d = mpv
    core = h.get("user-data/mu/core")
    assert core["work"] == "" and core["work_subs"] == 0

    # el mismo evento que manda mpvd cuando hay algo en marcha (aquí sin gastar una transcripción de verdad)
    resumen = {"subs": [{"id": "t1", "path": "/v/peli.mkv", "progress": 0.4, "model": "small-q8_0",
                         "remaining": 180.0}],
               "downloads": 1, "converts": 0, "recordings": 0, "busy": True,
               "text": "Subtítulos de peli.mkv (40 %, 3 min) · 1 descarga"}
    h.command("script-message-to", "mu_core", "mu-event",
              json.dumps({"event": "pending", "pending": resumen}))
    core = h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("work") != "", timeout=10)
    assert core["work"] == resumen["text"] and core["work_subs"] == 1

    # y el menú principal lo enseña como una fila más, sin tener que entrar en ningún sitio
    h.command("script-binding", "mu_menu/root")
    menu = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "root"
                           and any(i.get("title") == "Trabajando por detrás" for i in v.get("items", [])),
                           timeout=15)
    fila = next(i for i in menu["items"] if i["title"] == "Trabajando por detrás")
    assert fila["hint"] == resumen["text"]

    # cuando mpvd dice que ya no queda nada, la fila se va
    h.command("script-message-to", "mu_core", "mu-event",
              json.dumps({"event": "pending",
                          "pending": {"subs": [], "downloads": 0, "converts": 0, "recordings": 0,
                                      "busy": False, "text": ""}}))
    menu = h.wait_property("user-data/mu/menu",
                           lambda v: bool(v) and v.get("view") == "root"
                           and all(i.get("title") != "Trabajando por detrás" for i in v.get("items", [])),
                           timeout=15)
    assert h.get("user-data/mu/core")["work"] == ""


def test_el_resumen_tiene_la_forma_que_espera_el_menu(mpv):
    _h, d = mpv
    p = d.call("pending.status")
    assert set(p) == {"subs", "downloads", "converts", "recordings", "busy", "text"}
    assert p["text"] == "" and p["subs"] == []
    assert d.call("pending.stop_subs") == {"stopped": 0}
