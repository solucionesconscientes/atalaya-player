"""H62 · «debe haber alguna forma de monitorear los procesos en segundo plano, cualquiera».

El panel «Tareas» solo enseñaba conversiones y descargas, así que «lo que está trabajando» era media verdad: los
subtítulos con IA, el índice por temas, la traducción, la intro o la música pasaban sin que nada lo dijera.
"""

from __future__ import annotations

import time

from mpvd.jobs import job_label, job_rows


def test_el_nombre_interno_de_un_trabajo_se_dice_en_castellano():
    assert job_label("asr.model.small") == "Descargar un modelo de subtítulos"
    assert job_label("asr.subtitles") == "Subtítulos con IA"       # gana el prefijo más largo
    assert job_label("semantic.index") == "Índice por temas"
    assert job_label("loquesea.raro") == "loquesea.raro"           # lo que no esté en la tabla, con su nombre


def test_las_chapucillas_de_fondo_no_hacen_parpadear_la_lista():
    """Un indicador que parpadea con cada tarea de 50 ms se aprende a ignorar: sale lo pesado y lo que tarda."""
    filas = job_rows([
        {"id": "a", "name": "semantic.index", "heavy": True, "status": "running",
         "meta": {"path": "/x/y/peli.mkv"}},
        {"id": "b", "name": "radio.click", "heavy": False, "status": "running", "started_at": 100.0},
        {"id": "c", "name": "convert:7", "heavy": True, "status": "running"},
        {"id": "d", "name": "iptv.health", "heavy": False, "status": "running", "started_at": 90.0},
        {"id": "e", "name": "av.model.rnnoise", "heavy": False, "status": "queued"},
    ], now=101.0)
    assert [f["id"] for f in filas] == ["a", "d"], [f["id"] for f in filas]
    assert filas[0]["title"] == "Índice por temas" and filas[0]["description"] == "peli.mkv"
    assert filas[0]["type"] == "job" and filas[0]["actions"] == ["cancel"]
    # una conversión NO se cuenta dos veces: ya tiene su propia fila, con su progreso y sus acciones
    assert all(f["id"] != "c" for f in filas)


def test_tareas_ensena_cualquier_trabajo_del_servidor(daemon_env):
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    job = d.call("jobs.sleep", {"seconds": 8, "name": "semantic.index", "heavy": True})

    fin = time.monotonic() + 20
    fila = None
    while time.monotonic() < fin:
        res = d.call("tasks.list")
        fila = next((r for r in res["tasks"] if r["id"] == job["id"]), None)
        if fila and fila["status"] == "running":
            assert res["active"] >= 1
            break
        time.sleep(0.3)
    assert fila is not None, "el trabajo no aparece en Tareas"
    assert fila["type"] == "job" and fila["kind"] == "Índice por temas"
    assert fila["actions"] == ["cancel"]

    # y se puede parar desde ahí, que es la única acción que tiene sentido en un trabajo
    assert d.call("jobs.cancel", {"id": job["id"]})["cancelled"] is True
    fin = time.monotonic() + 15
    while time.monotonic() < fin:
        fila = next((r for r in d.call("tasks.list")["tasks"] if r["id"] == job["id"]), None)
        if fila and fila["status"] == "cancelled":
            break
        time.sleep(0.3)
    assert fila and fila["status"] == "cancelled" and fila["actions"] == []
