"""H58 · que se vea lo que pasa por detrás.

La queja de Ser —«al dar guardar los 3 elegidos en uno, no hace nada, y por separado tampoco»— era falsa: los
archivos se creaban. Lo que no había era ninguna señal. Mientras hay trabajo, un icono en la barra con cuántas
tareas van; sin trabajo, el icono no está (`hide`).
"""

from __future__ import annotations

import time

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


@pytest.fixture
def conv(daemon_env, media_dir):
    daemon_env.extra_env.update({"MPV_UOS_VAAPI": "0"})
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes",
                                           str(media_dir / "video30.mkv")], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def test_el_indicador_aparece_mientras_hay_trabajo_y_luego_se_va(conv, media_dir, tmp_path):
    h, d = conv
    estado = h.wait_property("user-data/mu/convert", lambda v: bool(v), timeout=20)
    assert estado["tasks_active"] == 0        # sin trabajo no hay nada que enseñar

    d.call("convert.start", {"path": str(media_dir / "video30.mkv"), "preset": "mp4",
                             "out_dir": str(tmp_path / "salida"),
                             "options": {"speed": "fast", "height": 480, "start": 0, "end": 4}}, timeout=60)
    # el reproductor se entera por el canal de eventos, sin preguntar
    h.wait_property("user-data/mu/convert", lambda v: bool(v) and v.get("tasks_active", 0) >= 1, timeout=30)

    fin = time.monotonic() + 180
    while time.monotonic() < fin:
        if h.get("user-data/mu/convert").get("tasks_active", 0) == 0:
            break
        time.sleep(0.5)
    assert h.get("user-data/mu/convert")["tasks_active"] == 0
    assert h.script_errors() == [], h.script_errors()


def test_el_boton_esta_declarado_en_la_barra():
    """Y está en la barra: sin esto el indicador existiría y no se vería, que es el fallo original."""
    from tests.conftest import ROOT
    conf = (ROOT / "mpv-config" / "script-opts" / "uosc.conf").read_text(encoding="utf-8")
    controls = next(ln for ln in conf.splitlines() if ln.startswith("controls="))
    assert "button:mu-tasks" in controls and "button:mu-goto" in controls
    lua = (ROOT / "mpv-config" / "scripts" / "mu-convert" / "main.lua").read_text(encoding="utf-8")
    assert "set_button('mu-tasks'" in lua and "hide = n == 0" in lua
