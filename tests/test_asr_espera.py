"""H36 · C3: el aviso de espera de los subtítulos IA sale de medidas, no de promesas.

mpvd publica en cada tarea el ritmo de los últimos trozos (``rtf_recent``), lo que queda a ese ritmo (``remaining``) y
lo que tardaría el modelo rápido (``alt``). Aquí se comprueba la aritmética y, sobre todo, que no se inventa un número
cuando todavía no hay ninguna medida.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mpvd.asr.models import (FAST_MODEL, REFERENCE_RTF, TIER_PRECOMPUTE, TIER_PREPARE, ModelStore,
                             model_filename)
from mpvd.asr.service import AsrTask
from mpvd.config import Settings
from mpvd.server import MpvdServer


@pytest.fixture
def asr(tmp_path: Path):
    return MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", idle_timeout=0,
                               workers=1)).asr


def task(**kw) -> AsrTask:
    base = {"id": "t", "key": "mu:abc", "path": "/v/peli.mkv", "duration": 600.0, "model": "small-q8_0",
            "language": "es", "chunk_seconds": 30.0}
    t = AsrTask(**{**base, **kw})
    t.plan()
    return t


def test_sin_medida_no_hay_numero():
    t = task()
    assert t.rtf is None and t.rtf_recent is None and t.remaining_seconds() is None
    assert t.to_dict()["remaining"] is None


def test_el_ritmo_reciente_manda_sobre_la_media():
    t = task()
    # primeros trozos rápidos, el último con la máquina cargada: la media dice 0,5 y el reciente, 2,0
    t.rtf_elapsed, t.rtf_audio = 30.0, 60.0
    t.rtf_last = [(60.0, 30.0)]
    assert t.rtf == 0.5 and t.rtf_recent == 2.0
    t.done = set(range(10))                      # 10 trozos de 30 s hechos de 20 → quedan 300 s de audio
    assert t.remaining_seconds() == pytest.approx(600.0)   # 300 s × 2,0
    t.rtf_last = []                              # sin trozos recientes se cae a la media de la tarea
    assert t.remaining_seconds() == pytest.approx(150.0)   # 300 s × 0,5


def test_solo_cuentan_los_trozos_que_faltan():
    t = task(duration=300.0)                     # 10 trozos de 30 s
    t.rtf_last = [(30.0, 30.0)]                  # ritmo 1,0: queda tanto como audio sin hacer
    assert t.remaining_seconds() == pytest.approx(300.0)
    t.done = set(range(9))
    assert t.remaining_seconds() == pytest.approx(30.0)
    t.done = set(range(10))
    assert t.complete and t.remaining_seconds() is None    # terminada: no se anuncia ninguna espera


def test_la_alternativa_rapida_se_ofrece_solo_si_esta_y_es_mas_rapida(tmp_path):
    # un almacén de modelos propio: el del repo trae small-q8_0 y entonces no se podría probar el caso «no está»
    # (MPV_UOS_WHISPER_MODELS no sirve: añade un directorio, no sustituye a vendor/whisper/models)
    modelos = tmp_path / "modelos"
    modelos.mkdir()
    asr = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", idle_timeout=0,
                              workers=1)).asr
    asr.models = ModelStore([modelos])
    lento = task(model="large-v3-turbo-q5_0")
    lento.rtf_last = [(52.2, 10.0)]              # ritmo 5,22, el medido para ese modelo
    # sin el modelo rápido en disco no se ofrece: sería mandar a la gente a una descarga que no se ha pedido
    assert asr._alternative(lento) is None
    (modelos / model_filename(FAST_MODEL)).write_bytes(b"lmgg" + b"\0" * 2048)
    alt = asr._alternative(lento)
    assert alt is not None and alt["model"] == FAST_MODEL
    razon = REFERENCE_RTF[FAST_MODEL] / REFERENCE_RTF["large-v3-turbo-q5_0"]
    assert alt["remaining"] == pytest.approx(lento.remaining_seconds() * razon, rel=0.01)
    assert alt["remaining"] < lento.remaining_seconds()
    # con el modelo rápido ya puesto no hay nada que ofrecer
    rapido = task(model=FAST_MODEL)
    rapido.rtf_last = [(4.5, 10.0)]
    assert asr._alternative(rapido) is None
    assert asr.task_dict(rapido)["alt"] is None


def test_la_tabla_de_referencia_cubre_todo_lo_que_se_puede_elegir():
    # si mañana se añade un modelo al catálogo y no a la tabla, el aviso se queda sin alternativa sin avisar
    for tabla in (TIER_PREPARE, TIER_PRECOMPUTE):
        for modelo in tabla.values():
            assert modelo in REFERENCE_RTF, modelo
    assert REFERENCE_RTF[FAST_MODEL] < 1.0       # el rápido tiene que ser más rápido que el vídeo
