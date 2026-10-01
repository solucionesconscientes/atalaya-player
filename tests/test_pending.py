"""H36 · C8: qué sigue trabajando por detrás cuando se cierra la ventana, y qué se puede parar.

mpvd sobrevive a mpv a propósito (una transcripción al 60 % se termina en vez de tirarse), pero hasta ahora no se
avisaba. Aquí se comprueba el resumen que alimenta la línea del menú y el aviso de escritorio: qué entra, cómo se
cuenta y, sobre todo, que una grabación programada se lista pero NO se puede parar desde aquí.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mpvd.asr.service import AsrTask
from mpvd.config import Settings
from mpvd.pending import describe, human
from mpvd.server import MpvdServer


@pytest.fixture
def server(tmp_path: Path):
    return MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", idle_timeout=0, workers=1))


def tarea(server, **kw) -> AsrTask:
    base = {"id": "t1", "key": "mu:a", "path": "/v/peli.mkv", "duration": 600.0, "model": "small-q8_0",
            "language": "es", "chunk_seconds": 30.0}
    t = AsrTask(**{**base, **kw})
    t.plan()
    t.status = kw.get("status", "running")
    server.asr.tasks[t.id] = t
    return t


def test_sin_nada_en_marcha_no_hay_nada_que_decir(server):
    p = server.pending.summary()
    assert p == {"subs": [], "downloads": 0, "converts": 0, "recordings": 0, "busy": False, "text": ""}
    assert server.pending.stoppable() == []


def test_una_transcripcion_a_medias_se_cuenta_con_su_tiempo(server):
    t = tarea(server)
    t.rtf_last = [(15.0, 30.0)]           # ritmo 0,5
    t.done = set(range(10))               # 10 de 20 trozos de 30 s → quedan 300 s de audio → 150 s de proceso
    p = server.pending.summary()
    assert p["busy"] and len(p["subs"]) == 1
    assert p["subs"][0]["remaining"] == pytest.approx(150.0)
    assert "peli.mkv" in p["text"] and "50 %" in p["text"] and "2 min" in p["text"]
    assert server.pending.stoppable() == ["t1"]


def test_una_transcripcion_terminada_ya_no_cuenta(server):
    t = tarea(server)
    t.done = set(range(len(t.chunks)))
    t.status = "done"
    assert t.complete and server.pending.summary()["busy"] is False


def test_parar_para_las_transcripciones_y_lo_hecho_se_queda(server):
    t = tarea(server)
    t.done = {0, 1, 2}
    assert server.pending.stop_subs() == 1
    assert server.asr.tasks["t1"].status in ("cancelled", "failed", "done")
    assert server.asr.tasks["t1"].done == {0, 1, 2}      # lo transcrito no se tira: seguirá de ahí
    assert server.pending.summary()["busy"] is False


def test_una_grabacion_programada_se_dice_pero_no_se_para(server, monkeypatch):
    class Falsa:
        status = "recording"
    server.schedule.items["r1"] = Falsa()
    p = server.pending.summary()
    assert p["busy"] and p["recordings"] == 1 and "grabación" in p["text"]
    # no está entre lo que se puede parar: una grabación es una cita con una hora, y pararla pierde el programa
    assert server.pending.stoppable() == []
    assert server.pending.stop_subs() == 0
    assert server.schedule.items["r1"].status == "recording"


def test_el_texto_junta_las_tres_colas_en_una_linea():
    t = describe({"subs": [{"path": "C:\\cine\\La vida.mkv", "progress": 0.07, "remaining": 4200}],
                  "downloads": 1, "converts": 2, "recordings": 0, "busy": True})
    assert t == "Subtítulos de La vida.mkv (7 %, 1 h 10 min) · 1 descarga · 2 conversiones"
    assert describe({"subs": [], "downloads": 0, "converts": 0, "recordings": 2, "busy": True}) \
        == "2 grabaciones programadas"


def test_los_tiempos_se_dicen_en_unidades_que_se_entienden():
    assert human(0) == "0 s" and human(59) == "59 s"
    assert human(60) == "1 min" and human(3599) == "60 min"
    assert human(3600) == "1 h" and human(5400) == "1 h 30 min" and human(5000) == "1 h 23 min"
