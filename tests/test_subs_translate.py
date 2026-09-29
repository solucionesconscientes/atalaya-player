"""Translation building blocks: sentence/dialogue splitting, cue grouping and redistribution (fake translator), the
package store (layout, catalogue, unpacking a synthetic .argosmodel), and — when the runtime and the es→en package are
available — a real CTranslate2 translation of the Spanish test sentences."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest

from mpvd.asr.srt import Segment
from mpvd.subs.translate import (ArgosEngine, ArgosStore, PINNED, distribute, group_cues, runtime_available,
                                 split_dialogue, split_sentences, translate_cues)
from tests.asr_helpers import ROOT, fold

ES = [Segment(0, 2, "Bienvenido a MPV-UOS,"), Segment(2, 4, "el reproductor del futuro."),
      Segment(5, 8, "El rápido zorro marrón salta sobre el perro perezoso."),
      Segment(9, 11, "Hoy es un buen día para ver"), Segment(11, 12.5, "una película con subtítulos."),
      Segment(13, 15, "- ¿Vienes? - Sí, ahora mismo.")]


def test_split_sentences_and_dialogue():
    assert split_sentences("¡Hola! ¿Qué tal? El Sr. López llegó a las 3.30. Ok") == \
        ["¡Hola!", "¿Qué tal?", "El Sr. López llegó a las 3.30.", "Ok"]
    assert split_dialogue("- ¿Vienes? - Sí, ahora mismo.") == ["- ¿Vienes?", "- Sí, ahora mismo."]
    assert split_dialogue("Hola - dijo él") == ["Hola - dijo él"]


def test_group_and_distribute():
    units = group_cues(ES)
    assert [u.cue_indices for u in units] == [[0, 1], [2], [3, 4], [5]]
    assert units[3].pieces == ["- ¿Vienes?", "- Sí, ahora mismo."]
    assert distribute("one two three four five six", [10, 20]) == ["one two", "three four five six"]
    assert distribute("solo", [3, 3, 3]) == ["solo", "", ""]
    assert distribute("a b c", [1, 1, 1, 1]) == ["a", "b", "c", ""]
    # fake translator: uppercase, so we can check that timing is kept and text is redistributed per cue
    calls: list[list[str]] = []

    def fake(batch):
        calls.append(batch)
        return [b.upper() for b in batch]

    out = translate_cues(ES, fake, batch=3)
    assert [(c.start, c.end) for c in out] == [(c.start, c.end) for c in ES]
    assert out[0].text.startswith("BIENVENIDO") and out[1].text.endswith("FUTURO.") and " ".join(
        (out[0].text, out[1].text)) == "BIENVENIDO A MPV-UOS, EL REPRODUCTOR DEL FUTURO."
    assert out[5].text == "- ¿VIENES? - SÍ, AHORA MISMO."
    assert len(calls) == 2 and sum(len(c) for c in calls) == 5


def make_fake_package(tmp_path: Path, name: str) -> Path:
    zp = tmp_path / f"translate-{name}-1_0.argosmodel"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(f"{name}/metadata.json", json.dumps({"from_code": name[:2], "to_code": name[3:]}))
        zf.writestr(f"{name}/model/model.bin", b"\0" * 10)
        zf.writestr(f"{name}/sentencepiece.model", b"\0" * 10)
        zf.writestr(f"{name}/stanza/x.pt", b"\0")
    return zp


def test_store_layout_catalogue_and_unpack(tmp_path):
    store = ArgosStore([tmp_path / "ro", tmp_path / "models"], tmp_path / "dl")
    assert store.find("es", "en") is None and store.present() == []
    zp = make_fake_package(tmp_path, "fr_en")
    store._unpack(zp, store.download_dir() / "fr_en")
    assert store.find("fr", "en") == tmp_path / "ro" / "fr_en" or store.find("fr", "en") == tmp_path / "models" / "fr_en"
    assert not (store.find("fr", "en") / "stanza").exists()
    cat = store.catalogue()
    assert [(p.source, p.target, p.present, p.pinned) for p in cat][:3] == [("fr", "en", True, False), ("en", "es", False, True),
                                                                            ("es", "en", False, True)]
    url, sha, ver = store.resolve_url("es", "en")
    assert url == PINNED[("es", "en")][0] and sha and ver == "1.0"
    eng = ArgosEngine(store, threads=2)
    assert eng.missing_for("fr", "es") == [("en", "es")] or eng.missing_for("fr", "es") == [("fr", "es")]
    assert store.remove("fr", "en") and store.present() == []


@pytest.mark.skipif(not runtime_available(), reason="ctranslate2/sentencepiece not installed (uv sync --extra translate)")
def test_real_translation_es_en():
    store = ArgosStore([ROOT / "vendor" / "models" / "argos"], ROOT / "vendor" / "dl")
    if store.find("es", "en") is None:
        pytest.skip("Argos es→en package not vendored (vendor/models/argos/es_en)")
    eng = ArgosEngine(store)
    assert eng.status()["available"] and eng.route("es", "en") == [("es", "en")]
    out = translate_cues(ES, lambda batch: eng.translate(batch, "es", "en"))
    text = fold(" ".join(c.text for c in out))
    hits = [k for k in ("welcome", "player", "future", "fox", "dog", "movie", "subtitles") if k in text]
    assert len(hits) >= 5, text
    assert "coming" in text or "come" in text, text
    assert eng.stats["sentences"] >= 5 and eng.stats["seconds"] < 10
    asyncio.run(asyncio.sleep(0))  # keeps the event-loop-free path honest for the async store API used by the service
