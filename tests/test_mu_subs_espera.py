"""H36 · C3 en pantalla: el aviso de espera que pinta mu-subs a partir de lo que publica mpvd.

No hace falta whisper: se le empuja a mu-subs el mismo evento ``asr`` que manda el daemon y se lee lo que publica.
Lo que se comprueba es el criterio, no el formato: sin medida no se dice ningún número, por debajo de 1 se promete que
no te alcanzará, y por encima se da el total y la alternativa rápida.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import start_mpv


@pytest.fixture
def subs(daemon_env):
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5",
        "--keep-open=yes", "--pause=yes",
    ], env=daemon_env.env)
    try:
        yield h
    finally:
        h.stop()


def push(h, **task):
    base = {"id": "tarea-1", "path": "", "status": "running", "progress": 0.0, "cues": 0, "seq": 1,
            "model": "small-q8_0", "language": "es", "complete": False, "srt": ""}
    h.command("script-message-to", "mu_subs", "mu-event",
              json.dumps({"event": "asr", "task": {**base, **task}}))


def espera(h, pred, timeout: float = 15.0):
    return h.wait_property("user-data/mu/subs", lambda v: bool(v) and pred(v), timeout=timeout)


def test_el_aviso_de_espera_sale_de_la_medida(subs):
    h = subs
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)

    # 1. recién empezada no hay ritmo: no se inventa ningún número
    push(h, seq=1, rtf=None, rtf_recent=None, remaining=None)
    v = espera(h, lambda v: v["task_id"] == "tarea-1")
    assert v["wait"] == ""

    # 2. más rápido que el vídeo: se dice cuánto falta y que no te alcanzará
    push(h, seq=2, rtf=0.45, rtf_recent=0.45, remaining=420.0, progress=0.1)
    v = espera(h, lambda v: v["wait"] != "")
    assert "7 min" in v["wait"] and "no te alcanzará" in v["wait"]

    # 3. un modelo lento: el total y la alternativa con su propio número
    push(h, seq=3, model="large-v3-turbo-q5_0", rtf=5.22, rtf_recent=5.22, remaining=4200.0,
         alt={"model": "small-q8_0", "remaining": 362.0})
    v = espera(h, lambda v: "small-q8_0" in v["wait"])
    assert "1 h 10 min" in v["wait"] and "6 min" in v["wait"] and "5.2 veces más lento" in v["wait"]

    # 4. si la máquina se carga, el ritmo reciente manda y el aviso sube con él
    push(h, seq=4, model="large-v3-turbo-q5_0", rtf=5.22, rtf_recent=11.0, remaining=8400.0,
         alt={"model": "small-q8_0", "remaining": 724.0})
    v = espera(h, lambda v: "2 h 20 min" in v["wait"])
    assert "11.0 veces más lento" in v["wait"]

    # 5. terminada: ya no se anuncia ninguna espera
    push(h, seq=5, status="done", complete=True, progress=1.0, rtf=5.22, rtf_recent=5.22, remaining=None)
    v = espera(h, lambda v: v["status"] == "done")
    assert v["wait"] == ""
