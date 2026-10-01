"""H34 · what identifies an AI-subtitle task: file key, model, language, translation AND the audio track.

A film with its original voices and a Spanish dub is one file with two audio tracks: transcribing one must never serve
(or cache) the subtitles of the other. No whisper needed — this only exercises the bookkeeping.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mpvd.asr.service import AsrTask, _quality_rank
from mpvd.config import Settings
from mpvd.server import MpvdServer


@pytest.fixture
def asr(tmp_path: Path):
    return MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", idle_timeout=0,
                               workers=1)).asr


def task(**kw) -> AsrTask:
    base = {"id": "t", "key": "mu:abc", "path": "/v/peli.mkv", "duration": 60.0, "model": "base",
            "language": "auto", "translate": False, "chunk_seconds": 28.5}
    return AsrTask(**{**base, **kw})


def test_a_task_of_another_audio_track_is_not_reused(asr):
    dub = task(id="dub", audio_track=1)
    asr.tasks[dub.id] = dub
    assert asr.find_task("mu:abc", "base", "auto", False, 1) is dub
    assert asr.find_task("mu:abc", "base", "auto", False, 0) is None
    assert asr.find_task("mu:abc", "base", "auto", False, None) is None


def test_the_srt_and_the_cache_entry_differ_per_audio_track(asr):
    a = asr._srt_path("mu:abc", "base", "auto", False, 0)
    b = asr._srt_path("mu:abc", "base", "auto", False, 1)
    default = asr._srt_path("mu:abc", "base", "auto", False, None)
    assert a != b and a != default and b != default
    assert asr._cache_params(task(audio_track=0)) != asr._cache_params(task(audio_track=1))


def test_choosing_the_model_automatically_survives_english_only_models():
    # CATALOG offers base.en / small.en; QUALITY_ORDER does not rank them, and index() used to raise ValueError
    assert _quality_rank("base.en") == -1 and _quality_rank("small.en") == -1
    assert _quality_rank("small") > _quality_rank("base") > _quality_rank("base.en")


def test_adopt_model_does_not_raise_with_an_english_only_finished_task(asr):
    import asyncio

    done_en = task(id="en", model="base.en")
    done_en.status = "done"
    asr.tasks[done_en.id] = done_en
    # the model is not downloaded here, so nothing is adopted, but it must not blow up on the ranking either
    assert asyncio.run(asr._adopt_model("mu:abc", "auto", False, 28.5, None)) in (None, "base", "base.en")


def test_la_primera_pista_y_la_que_elige_ffmpeg_son_lo_mismo_si_solo_hay_una(asr, media_dir):
    """H36: el pre-cálculo manda ``audio_track=None`` (el archivo aún no está abierto) y la reproducción manda 0.

    Con una sola pista de audio son la misma cosa, y si se tratan como identidades distintas el trabajo ya hecho se tira
    y se vuelve a transcribir desde cero. Con dos pistas, 0 sigue siendo 0: ahí sí importa.
    """
    una = str(media_dir / "voz_es.flac")
    dos = str(media_dir / "voz_es_en.mkv")
    assert asyncio.run(asr._canonical_track(una, 0)) is None
    assert asyncio.run(asr._canonical_track(una, None)) is None
    assert asyncio.run(asr._canonical_track(dos, 0)) == 0
    assert asyncio.run(asr._canonical_track(dos, 1)) == 1
    assert asyncio.run(asr._canonical_track(dos, None)) is None
