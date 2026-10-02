"""H57 · programar que SUENE algo en una franja, no solo grabarlo.

Lo que pidió Ser: «a X hora se enciende el canal de TV o radio, finaliza la emisión a la hora deseada y si es
preciso suspensión o apagado», y lo mismo para canciones o listas. La maquinaria de las grabaciones programadas ya
tenía franja, despertador y apagado al terminar; lo que faltaba era el modo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


@pytest.fixture
def prog(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--idle=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def test_una_cancion_programada_suena_y_se_para_sola(prog, media_dir):
    h, d = prog
    ahora = time.time()
    res = d.call("iptv.schedule.add", {"media": str(media_dir / "video30.mkv"), "title": "Prueba",
                                       "start": ahora + 2, "stop": ahora + 7, "after": "nothing"})
    assert res["mode"] == "play" and res["channel"]["kind"] == "media"
    assert res["status"] == "scheduled"

    # arranca sola en el reproductor que ya está abierto
    h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=25)
    assert h.get("pause") is False
    estado = d.call("iptv.schedule.list")["items"]
    assert [r["status"] for r in estado if r["id"] == res["id"]] == ["playing"]

    # y se para sola al acabar la franja
    h.wait_property("path", lambda v: not v, timeout=30)
    fin = time.monotonic() + 20
    while time.monotonic() < fin:
        fila = next(r for r in d.call("iptv.schedule.list")["items"] if r["id"] == res["id"])
        if fila["status"] == "done":
            break
        time.sleep(0.3)
    assert fila["status"] == "done", fila


def test_lo_que_no_existe_no_se_programa(prog):
    _h, d = prog
    from mpvd.rpc import RpcError
    with pytest.raises(RpcError):
        d.call("iptv.schedule.add", {"media": "/no/existe/cancion.mp3", "start": time.time() + 60,
                                     "stop": time.time() + 120})
    with pytest.raises(RpcError):      # ni un modo inventado
        d.call("iptv.schedule.add", {"media": "/tmp", "start": time.time() + 60, "stop": time.time() + 120,
                                     "mode": "loquesea"})


def test_una_franja_de_radio_se_pone_sola(prog, tmp_path):
    """Lo que pidió Ser con la radio: a tal hora se enciende y suena, y a tal otra para. Aquí la «emisora» es un
    archivo servido por el propio equipo, para no depender de internet."""
    h, d = prog
    from tests.conftest import ROOT
    emisora = ROOT / "tests" / "fixtures" / "media" / "video30.mkv"
    ahora = time.time()
    res = d.call("iptv.schedule.add", {"media": str(emisora), "title": "Radio de las 7",
                                       "start": ahora + 2, "stop": ahora + 6, "mode": "play"})
    assert res["mode"] == "play" and res["title"] == "Radio de las 7"
    h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=25)
    h.wait_property("path", lambda v: not v, timeout=30)      # para sola al acabar la franja


def test_el_modo_viaja_hasta_mpvd_desde_el_menu(prog, media_dir):
    """El interruptor «Qué hacer en esa franja» del menú acaba en el `mode` de la programación."""
    h, d = prog
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_iptv", "mu-iptv-event",
              json.dumps({**base, "type": "activate", "index": 1, "value": {"sched_mode": "play"}}))
    estado = h.wait_property("user-data/mu/prefs", lambda v: bool(v) and v.get("writes", 0) > 0, timeout=15)
    guardado = json.loads(Path(estado["path"]).read_text(encoding="utf-8"))
    assert guardado.get("mu-iptv", {}).get("sched_mode") == "play", guardado
