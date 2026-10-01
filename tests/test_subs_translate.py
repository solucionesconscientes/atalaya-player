"""Translation building blocks: sentence/dialogue splitting, cue grouping (punctuated and pause-based) and
punctuation-aware redistribution (fake translator, cases from the 2026-09-30 diagnosis of tmp/diag-subs), the Argos
package store, the OPUS-MT store and the engine router, and — when the runtime and the models are available — real
CTranslate2 translations (Argos es→en; OPUS-MT tc-big es→en if converted). The OPUS-MT download + conversion test
is @network and opt-in (860 MB)."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import zipfile
from pathlib import Path

import pytest

from mpvd.asr.srt import Segment, render_srt
from mpvd.subs import opus
from mpvd.subs.formats import load_cues
from mpvd.subs.translate import (PINNED, ArgosEngine, ArgosStore, Leg, TranslationRouter, dialogue_turns, distribute,
                                 group_cues, is_punctuated, normalize_lang, runtime_available, split_dialogue,
                                 split_sentences, translate_cues)
from tests.asr_helpers import ROOT, fold

ES = [Segment(0, 2, "Bienvenido a MPV-UOS,"), Segment(2, 4, "el reproductor del futuro."),
      Segment(5, 8, "El rápido zorro marrón salta sobre el perro perezoso."),
      Segment(9, 11, "Hoy es un buen día para ver"), Segment(11, 12.5, "una película con subtítulos."),
      Segment(13, 15, "- ¿Vienes? - Sí, ahora mismo.")]

# Start of tmp/diag-subs/entrada_es.srt (colloquial dialogue): 3 sentences over 3 cues each + a dialogue cue.
DIAG = """1
00:00:01,000 --> 00:00:04,525
Oye, Marta, ¿al final vienes esta noche

2
00:00:04,775 --> 00:00:07,700
a la cena en casa de mis padres

3
00:00:07,950 --> 00:00:10,500
o te vas a rajar otra vez?

4
00:00:10,750 --> 00:00:13,300
No me tomes el pelo, Luis.

5
00:00:13,550 --> 00:00:16,325
Te dije que iría si terminaba

6
00:00:16,575 --> 00:00:19,575
el informe que me pidió el jefe,

7
00:00:19,825 --> 00:00:22,975
y todavía me quedan dos apartados.

8
00:00:33,425 --> 00:00:36,425
- ¿Y tu hermano?
- Viene seguro.
"""


def test_split_sentences_and_dialogue():
    assert split_sentences("¡Hola! ¿Qué tal? El Sr. López llegó a las 3.30. Ok") == \
        ["¡Hola!", "¿Qué tal?", "El Sr. López llegó a las 3.30.", "Ok"]
    assert split_dialogue("- ¿Vienes? - Sí, ahora mismo.") == ["- ¿Vienes?", "- Sí, ahora mismo."]
    assert split_dialogue("Hola - dijo él") == ["Hola - dijo él"]
    # a mid-sentence dash or a hyphenated word is not a new speaker; a line break before a dash is
    assert len(dialogue_turns("- Espera - dijo, y se fue")) == 1 and len(dialogue_turns("MPV-UOS y - nada - más")) == 1
    assert dialogue_turns("- ¿Y tu hermano?\n- Viene seguro.") == [("", True, "¿Y tu hermano?"),
                                                                  ("\n", True, "Viene seguro.")]
    assert dialogue_turns("¿Y tu hermano?\n- Viene seguro.") == [("", False, "¿Y tu hermano?"),
                                                                ("\n", True, "Viene seguro.")]


def test_group_and_distribute():
    units = group_cues(ES)
    assert [u.cue_indices for u in units] == [[0, 1], [2], [3, 4], [5]]
    assert units[3].pieces == ["¿Vienes?", "Sí, ahora mismo."] and units[3].seps == ["- ", " - "]
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
    assert out[0].text == "BIENVENIDO A MPV-UOS,"     # cut at the comma, not proportionally
    assert out[5].text == "- ¿VIENES? - SÍ, AHORA MISMO."
    assert len(calls) == 2 and sum(len(c) for c in calls) == 5


@pytest.mark.parametrize(("text", "weights", "expected"), [
    # OPUS-MT tc-big output for cues 5-7 of the diagnosis; the old proportional split gave
    # "I told you I'd go if I" / "finished the report the boss asked me to" / "do, and I still have two paragraphs left."
    ("I told you I'd go if I finished the report the boss asked me to do, and I still have two paragraphs left.",
     [29, 32, 34], ["I told you I'd go", "if I finished the report the boss asked me to do,",
                    "and I still have two paragraphs left."]),
    ("Besides, if you don't show up, my mom's gonna piss me off.", [23, 38],
     ["Besides, if you don't show up,", "my mom's gonna piss me off."]),
    ("I had it in my backpack, I think, but maybe my nephew took it.", [30, 39],
     ["I had it in my backpack, I think,", "but maybe my nephew took it."]),
    ("Take your laptop, mine ran out of battery on the train and I can't find the charger anywhere.", [29, 30, 42],
     ["Take your laptop,", "mine ran out of battery on the train", "and I can't find the charger anywhere."]),
    ("Okay, okay, but my mom's been making paella all week and she's gonna be upset.", [30, 29, 39],
     ["Okay, okay,", "but my mom's been making paella all week", "and she's gonna be upset."]),
])
def test_distribute_cuts_at_punctuation_and_conjunctions(text, weights, expected):
    assert distribute(text, weights) == expected


def test_distribute_never_leaves_articles_or_prepositions_dangling():
    # old split: "Hey, Marta, are you finally coming to my" / "parents' dinner tonight or are you" / "going to break up again?"
    parts = distribute("Hey, Marta, are you finally coming to my parents' dinner tonight or are you going to break up "
                       "again?", [39, 31, 26])
    assert parts[0].endswith("tonight") and parts[1].startswith("or ")
    parts = distribute("Well, then let him eat my part, that I am up to my nose of so much work.", [28, 40, 17])
    assert parts == ["Well, then let him eat my part,", "that I am up to my nose", "of so much work."]
    for p in parts:
        assert p.split()[-1].lower() not in ("my", "to", "of", "the", "a", "and", "or")


def test_group_cues_without_punctuation_uses_pauses():
    # Whisper base style: no sentence ends. Old behaviour: groups of 3 regardless of pauses.
    cues = [Segment(0.0, 2.0, "oye marta al final vienes"), Segment(2.1, 4.0, "esta noche a la cena"),
            Segment(4.2, 6.0, "en casa de mis padres"), Segment(6.3, 8.0, "o te vas a rajar"),
            Segment(8.4, 10.0, "otra vez"), Segment(11.0, 12.0, "no me tomes el pelo"),
            Segment(12.2, 13.0, "luis")]
    assert not is_punctuated(cues) and is_punctuated(ES)
    assert [u.cue_indices for u in group_cues(cues)] == [[0, 1, 2, 3], [4], [5, 6]]
    # a pause ≥ 0.6 s always ends the group
    cues[1] = Segment(2.7, 4.0, cues[1].text)
    assert [u.cue_indices for u in group_cues(cues)][:2] == [[0], [1, 2, 3, 4]]


def test_dialogue_line_breaks_survive_loading_and_translation(tmp_path):
    srt = tmp_path / "diag.srt"
    srt.write_text(DIAG + "\n9\n00:00:40,000 --> 00:00:42,000\nuna línea\npartida en dos\n", encoding="utf-8")
    cues = load_cues(srt)
    assert cues[7].text == "- ¿Y tu hermano?\n- Viene seguro."
    assert cues[8].text == "una línea partida en dos"                   # not a dialogue: joined
    assert load_cues(srt, lines="keep")[8].text == "una línea\npartida en dos"
    units = group_cues(cues)
    assert [u.cue_indices for u in units] == [[0, 1, 2], [3], [4, 5, 6], [7], [8]]
    out = translate_cues(cues, lambda batch: [b.upper() for b in batch])
    assert out[7].text == "- ¿Y TU HERMANO?\n- VIENE SEGURO."
    assert "\n- VIENE" in render_srt(out)
    # a dialogue cue is never glued to the unfinished sentence before it
    two = [Segment(0, 2, "Y entonces"), Segment(2.1, 4, "- ¿Vienes?\n- Sí.")]
    assert [u.cue_indices for u in group_cues(two, punctuated=False)] == [[0], [1]]


def test_short_translation_merges_empty_cues_instead_of_leaving_original_text():
    cues = [Segment(0, 1, "Pues"), Segment(1, 2, "sí,"), Segment(2, 3.5, "claro.")]
    out = translate_cues(cues, lambda batch: ["Sure."] * len(batch))
    assert [(c.start, c.end, c.text) for c in out] == [(0, 3.5, "Sure.")]
    out = translate_cues(cues, lambda batch: ["Yes, sure."] * len(batch))
    assert [c.text for c in out] == ["Yes,", "sure."] and out[-1].end == 3.5 and out[0].start == 0


def test_normalize_lang():
    assert [normalize_lang(x) for x in ("spa", "en-US", "pt_BR", "ES", "auto", "", "zz-9", "und")] == \
        ["es", "en", "pt", "es", "auto", "", "zz", "und"]


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


def fake_argos(root: Path, *pairs: str) -> None:
    for pair in pairs:
        (root / pair / "model").mkdir(parents=True)
        (root / pair / "model" / "model.bin").write_bytes(b"\0")
        (root / pair / "sentencepiece.model").write_bytes(b"\0")


def fake_opus(root: Path, model_id: str) -> None:
    d = root / model_id
    d.mkdir(parents=True)
    for f in ("model.bin", "source.spm", "target.spm", "config.json", "shared_vocabulary.json"):
        (d / f).write_bytes(b"{}")


def test_opus_catalogue_tokens_and_store(tmp_path):
    assert opus.model_for("es", "en")[1] is None and opus.model_for("en", "es")[1] == ">>spa<<"
    assert opus.model_for("en", "ca")[1] == ">>cat<<"
    # H36/C6: francés ↔ inglés, modelos de un solo idioma a cada lado → sin token >>lang<<
    assert opus.model_for("fr", "en")[1] is None and opus.model_for("en", "fr")[1] is None
    assert opus.supported_pairs() == [("ca", "en"), ("en", "ca"), ("en", "es"), ("en", "fr"), ("es", "en"),
                                      ("fr", "en")]
    # el triángulo es/en/fr se cubre entero con OPUS-MT (es↔fr por inglés: no existe ningún tc-big spa-fra)
    for par in (("es", "en"), ("en", "es"), ("fr", "en"), ("en", "fr")):
        assert opus.model_for(*par) is not None, par
    assert opus.model_for("es", "fr") is None and opus.model_for("fr", "es") is None
    for m in opus.MODELS.values():
        assert m.url.startswith("https://object.pouta.csc.fi/Tatoeba-MT-models/") and len(m.sha256) == 64
        assert m.zip_bytes > 800_000_000 and m.size_mb == 234
    assert opus.normalize("“Hola…”\x07 ¿qué’s  tal?") == '"Hola..." ¿qué\'s tal?'
    store = opus.OpusStore([tmp_path / "ro"], tmp_path / "models")
    assert store.find("es", "en") is None and store.present_pairs() == []
    fake_opus(tmp_path / "ro", "tc-big-cat_oci_spa-eng-2022-03-13")
    assert store.find("es", "en")[0] == tmp_path / "ro" / "tc-big-cat_oci_spa-eng-2022-03-13"
    assert store.present_pairs() == [("ca", "en"), ("es", "en")]
    cat = {(r["source"], r["target"]): r for r in store.catalogue()}
    assert cat[("es", "en")]["present"] and not cat[("en", "es")]["present"] and cat[("en", "es")]["download_mb"] == 863
    with pytest.raises(opus.OpusError):
        asyncio.run(store.download("es", "fr"))      # OPUS-MT no tiene ese par directo: hay que pivotar


def test_router_picks_engine_per_leg(tmp_path):
    fake_argos(tmp_path / "argos", "es_en", "fr_en")
    argos_store = ArgosStore([tmp_path / "argos"], tmp_path / "dl")
    (tmp_path / "dl").mkdir()
    (tmp_path / "dl" / "argospm-index.json").write_text(json.dumps([
        {"from_code": "en", "to_code": "es", "package_version": "1.0", "links": ["https://example.invalid/en_es"]},
        {"from_code": "en", "to_code": "de", "package_version": "1.0", "links": ["https://example.invalid/en_de"]}]))
    opus_store = opus.OpusStore([], tmp_path / "opus")
    router = TranslationRouter(ArgosEngine(argos_store, threads=1), opus.OpusEngine(opus_store, threads=1))
    # OPUS-MT not downloaded: auto → Argos; explicit opus-big → asks for the OPUS-MT model
    assert router.plan("es", "en") == [Leg("argos", "es", "en")]
    assert router.missing_for("es", "en", "opus-big") == [Leg("opus-big", "es", "en")]
    assert router.missing_for("en", "es", "auto") == [Leg("argos", "en", "es")]
    fake_opus(tmp_path / "opus", "tc-big-cat_oci_spa-eng-2022-03-13")
    fake_opus(tmp_path / "opus", "tc-big-eng-cat_oci_spa-2022-03-13")
    assert router.plan("es", "en") == [Leg("opus-big", "es", "en")]
    assert router.plan("es", "en", "argos") == [Leg("argos", "es", "en")]
    # pivot: Argos fr→en + OPUS-MT en→es (no Argos en→es on disk)
    assert router.plan("fr", "es") == [Leg("argos", "fr", "en"), Leg("opus-big", "en", "es")]
    assert router.missing_for("fr", "es", "argos") == [Leg("argos", "en", "es")]
    assert router.missing_for("fr", "de") == [Leg("argos", "en", "de")]
    # C6 · con «máxima calidad», es→fr pide los dos modelos OPUS-MT del pivote, no los de Argos
    assert router.missing_for("es", "fr", "opus-big") == [Leg("opus-big", "en", "fr")]
    fake_opus(tmp_path / "opus", "tc-big-eng-fra-2022-03-09")
    assert router.plan("es", "fr", "opus-big") == [Leg("opus-big", "es", "en"), Leg("opus-big", "en", "fr")]
    # y un idioma que OPUS-MT no cubre sigue yendo por Argos aunque se pida «máxima calidad»
    assert router.missing_for("es", "de", "opus-big") == [Leg("argos", "en", "de")]
    assert router.beams([Leg("opus-big", "en", "es"), Leg("argos", "fr", "en")]) == {"opus-big": 4, "argos": 2}
    with pytest.raises(Exception):
        router.plan("es", "en", "deepl")


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


def _opus_store() -> opus.OpusStore:
    dirs, download_to, dl = opus.default_dirs(ROOT, ROOT / ".cache" / "data")
    return opus.OpusStore(dirs, download_to, dl)


@pytest.mark.skipif(not runtime_available(), reason="ctranslate2/sentencepiece not installed (uv sync --extra translate)")
def test_real_translation_opus_big_colloquial(tmp_path):
    store = _opus_store()
    if store.find("es", "en") is None:
        pytest.skip("OPUS-MT tc-big es→en not converted (menu Traducir → Calidad, or MPV_UOS_OPUS_MODELS)")
    eng = opus.OpusEngine(store, beam_size=4)
    srt = tmp_path / "diag.srt"
    srt.write_text(DIAG, encoding="utf-8")
    cues = load_cues(srt)
    out = translate_cues(cues, lambda batch: eng.translate(batch, "es", "en"))
    assert [(c.start, c.end) for c in out] == [(c.start, c.end) for c in cues]
    text = fold(" ".join(c.text for c in out))
    assert "hair" not in text, text                     # Argos 1.0: "Don't take my hair"
    assert "tease" in text or "kidding" in text or "joke" in text, text
    assert "\n- " in out[7].text and "brother" in fold(out[7].text), out[7].text
    if store.find("en", "es") is not None:
        back = eng.translate(["Don't tease me, Luis.", "Where is my brother?"], "en", "es")
        assert "hermano" in fold(back[1]), back
    eng.unload()


@pytest.mark.network
def test_opus_release_urls_are_alive():
    for m in opus.MODELS.values():
        req = urllib.request.Request(m.url, method="HEAD", headers={"User-Agent": opus.USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            assert resp.status == 200 and int(resp.headers["Content-Length"]) == m.zip_bytes


@pytest.mark.network
@pytest.mark.timeout(3600)
@pytest.mark.skipif(not (os.environ.get("MPV_UOS_OPUS_DL") or os.environ.get("MPV_UOS_TEST_BIG_DOWNLOADS")),
                    reason="860 MB download: set MPV_UOS_TEST_BIG_DOWNLOADS=1 (or MPV_UOS_OPUS_DL=<dir with the zip>)")
@pytest.mark.skipif(not opus.converter_available(), reason="ctranslate2 converter (numpy, pyyaml) not installed")
def test_opus_download_verify_and_convert(tmp_path):
    dl = [Path(os.environ["MPV_UOS_OPUS_DL"])] if os.environ.get("MPV_UOS_OPUS_DL") else []
    store = opus.OpusStore([], tmp_path / "models", dl)
    steps: list[tuple[float, str]] = []
    path = asyncio.run(store.download("es", "en", progress=lambda f, m: steps.append((f, m))))
    assert store.valid(path) and store.find("es", "en")[0] == path
    meta = json.loads((path / "mpv-uos.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == opus.MODELS["tc-big-cat_oci_spa-eng-2022-03-13"].sha256 and meta["quantization"] == "int8"
    size_mb = sum(f.stat().st_size for f in path.iterdir()) / 1e6
    assert 200 < size_mb < 280, size_mb
    assert steps[-1] == (1.0, "listo") and any("convirtiendo" in m for _f, m in steps)
    assert sorted(p.name for p in (tmp_path / "models").iterdir()) == [path.name]   # no zip / npz left behind
    out = opus.OpusEngine(store).translate(["No me tomes el pelo, Luis."], "es", "en")
    assert "hair" not in out[0].lower(), out


def test_the_translator_leaves_cores_for_whisper_and_mpv():
    """H34 · transcribir y traducir es justo lo que el menú invita a hacer a la vez (y lo que hace la cadena tras
    descargar): con cpu-1 hilos cada uno pedían seis en un portátil de cuatro núcleos."""
    import os

    from mpvd.subs.opus import OpusEngine
    from mpvd.subs.translate import ArgosEngine

    cpu = os.cpu_count() or 2
    for engine in (ArgosEngine(None, threads=None), OpusEngine(None, threads=None)):
        assert 1 <= engine.threads <= max(1, cpu // 2)
    # y se respeta lo que se pida a mano, también en máquinas de uno o dos núcleos (antes fallaban los paréntesis)
    assert ArgosEngine(None, threads=3).threads == 3
    assert OpusEngine(None, threads=3).threads == 3
