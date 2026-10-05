"""mu-prefs + mu/prefs.lua (headless mpv, no daemon): user choices survive a restart (volume, speed, subtitle size, mu-av
filters, mu-menu "continue watching", mu-study smart speed), the command line wins over prefs.json, automatic changes
(file loading, per-file options, smart-speed silences) are not stored, track choices teach alang/slang, turning
subtitles off is remembered, a corrupt file is quarantined, two instances merge their changes and the reset keeps a
.bak copy. Every mpv here gets MPV_UOS_DATA_DIR in tmp: the user's real prefs.json is never touched."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import ROOT, TMP, start_mpv

FIXTURES = ROOT / "tests" / "fixtures" / "lua"
BASE = ["--script-opts=mu-core-autostart=no"]


@pytest.fixture
def pdir():
    """A fresh data dir (prefs.json lives there) and run dir for the mpv instances of one test."""
    base = TMP / "test-prefs" / uuid.uuid4().hex[:8]
    (base / "data").mkdir(parents=True)
    yield base
    if not os.environ.get("MU_KEEP_LOGS"):
        import shutil
        shutil.rmtree(base, ignore_errors=True)


def launch(pdir: Path, *extra: str, env: dict[str, str] | None = None):
    h = start_mpv(pdir / "run", [*BASE, *extra], env={"MPV_UOS_DATA_DIR": str(pdir / "data"), **(env or {})})
    # H63 · no basta con que mu-prefs haya publicado: hay que esperar a que esté MIRANDO. mpv junta el primer aviso
    # de cada observador con un cambio que llegue en el mismo instante, y ese primer aviso se descarta a propósito
    # (lo que venga de tu mpv.conf no es una elección tuya de ahora), así que un cambio hecho en los primeros
    # milisegundos se pierde. `watching` es ese estado y lo publica el propio script.
    h.wait_property("user-data/mu/prefs", lambda v: bool(v) and v.get("watching") is True, timeout=15)
    for script in ("study", "menu", "av"):   # every script restored its own preferences
        h.wait_property(f"user-data/mu/{script}", bool, timeout=10)
    return h


def prefs_file(pdir: Path) -> dict:
    p = pdir / "data" / "prefs.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_prefs(pdir: Path, data: dict | str) -> None:
    text = data if isinstance(data, str) else json.dumps(data)
    (pdir / "data" / "prefs.json").write_text(text, encoding="utf-8")


def play(h, path: Path, *loadfile_args) -> None:
    """Load a file and wait until mu-prefs considers its loading over (track auto-selection window closed)."""
    loads = h.get("user-data/mu/prefs")["loads"]
    h.command("loadfile", str(path), *loadfile_args)
    h.wait_property("user-data/mu/prefs", lambda v: bool(v) and v.get("loads", 0) > loads
                    and v.get("loading") is False, timeout=15)
    assert h.get("path") == str(path)


def stored(h) -> dict:
    return (h.get("user-data/mu/prefs") or {}).get("values") or {}


def wait_stored(h, pred, timeout: float = 5.0) -> dict:
    return h.wait_property("user-data/mu/prefs", lambda v: bool(v) and pred(v.get("values") or {}), timeout=timeout)


def labels(h, kind: str = "af") -> set[str]:
    return {f.get("label") for f in (h.get(kind) or []) if f.get("label")}


def test_whitelist_names_are_mpv_options(mpv_option_names):
    src = (ROOT / "mpv-config" / "scripts" / "mu-prefs.lua").read_text(encoding="utf-8")
    names = set(re.findall(r"\{ name = '([a-z-]+)'", src))
    assert len(names) == 20 and names <= mpv_option_names, names - mpv_option_names
    # per-file choices stay out on purpose (docs/USO.md §11)
    assert not names & {"pause", "aid", "sid", "audio-delay", "sub-delay", "video-zoom", "loop-file", "deinterlace"}


def test_module_unit(pdir):
    write_prefs(pdir, {"unit": {"n": 5, "s": 7, "t": ["x"], "extra": 1}, "other": {"k": "v"}})
    h = start_mpv(pdir / "run", ["--load-scripts=no", f"--script={FIXTURES / 'prefs_unit.lua'}"],
                  env={"MPV_UOS_DATA_DIR": str(pdir / "data")})
    try:
        res = h.wait_property("user-data/prefs-unit", lambda v: bool(v) and v.get("done"), timeout=10)
        assert res["failures"] == [], res["failures"]
        assert res["checks"] >= 25
        assert h.script_errors() == []
    finally:
        h.stop()


def test_choices_survive_restart(pdir, media_dir):
    video = media_dir / "video30.mkv"
    h = launch(pdir)
    try:
        h.command("set_property", "volume", 70)
        h.command("set_property", "speed", 1.5)
        h.command("set_property", "sub-scale", 1.3)
        h.command("script-message-to", "mu_av", "mu-av-toggle", "night")
        h.command("script-binding", "mu_menu/resume-toggle")
        h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("resume") is False, timeout=5)
        play(h, video)
        h.command("script-message-to", "mu_study", "mu-study-smart", "yes")
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("smart") is True, timeout=5)
        wait_stored(h, lambda v: v.get("volume") == 70 and v.get("speed") == 1.5)
        assert h.script_errors() == []
    finally:
        h.stop()
    saved = prefs_file(pdir)
    assert saved["mpv"]["volume"] == 70 and saved["mpv"]["speed"] == 1.5 and saved["mpv"]["sub-scale"] == 1.3
    assert saved["mu-av"]["filters"] == ["night"]
    assert saved["mu-menu"]["resume"] is False
    assert saved["mu-study"]["smart"] is True
    assert "pause" not in saved["mpv"] and "time-pos" not in saved["mpv"]

    h = launch(pdir)
    try:
        assert h.get("volume") == 70 and h.get("speed") == 1.5 and abs(h.get("sub-scale") - 1.3) < 1e-4
        assert "mu-night" in labels(h)
        assert h.get("user-data/mu/menu")["resume"] is False
        assert h.get("user-data/mu/study")["smart_wanted"] is True
        play(h, video)
        h.wait_property("user-data/mu/study", lambda v: bool(v) and v.get("smart") is True, timeout=5)
        assert "mu-night" in labels(h)
        st = h.get("user-data/mu/prefs")
        assert set(st["applied"]) >= {"volume", "speed", "sub-scale"}
        # nothing new to write on a restart that only re-applied the stored values
        assert st["writes"] == 0 and st["pending"] is False
        assert h.script_errors() == []
    finally:
        h.stop()


def test_command_line_wins(pdir):
    write_prefs(pdir, {"mpv": {"volume": 70, "speed": 1.5}, "mu-menu": {"resume": False},
                       "mu-study": {"silence_speed": 3}})
    h = launch(pdir, "--volume=40", "--script-opts-append=mu-menu-resume=yes")
    try:
        assert h.get("volume") == 40 and h.get("speed") == 1.5
        assert h.get("user-data/mu/menu")["resume"] is True
        assert h.get("user-data/mu/study")["silence_speed"] == 3
        st = h.get("user-data/mu/prefs")
        assert "volume" in st["skipped_cli"] and "speed" in st["applied"]
    finally:
        h.stop()
    assert prefs_file(pdir)["mpv"]["volume"] == 70   # untouched: the user did not change it


def test_automatic_changes_are_not_stored(pdir, media_dir):
    h = launch(pdir, f"--script={FIXTURES / 'auto_change.lua'}")
    try:
        # smart speed inside a silence (mu-study publishes in_silence before touching speed)
        h.command("set_property", "user-data/mu/study", {"in_silence": True})
        h.command("set_property", "speed", 2.5)
        time.sleep(0.4)
        h.command("set_property", "user-data/mu/study", {"in_silence": False})
        # per-file option (loadfile options → set-locally) and changes while the file loads (auto_change.lua)
        play(h, media_dir / "video30.mkv", "replace", -1, "volume=33")
        assert h.get("volume") == 33 and h.get("sub-scale") == 2.5 and h.get("brightness") == 10
        # a real user change afterwards is stored
        h.command("set_property", "contrast", 5)
        wait_stored(h, lambda v: v.get("contrast") == 5)
    finally:
        h.stop()
    saved = prefs_file(pdir)["mpv"]
    assert saved == {"contrast": 5}, saved


def srt(path: Path, text: str) -> None:
    path.write_text(f"1\n00:00:00,500 --> 00:00:29,000\n{text}\n", encoding="utf-8")


def test_languages_and_subtitles_off(pdir, media_dir):
    clip = pdir / "clip"
    clip.mkdir()
    (clip / "video30.mkv").symlink_to(media_dir / "video30.mkv")
    srt(clip / "video30.es.srt", "Hola")
    srt(clip / "video30.en.srt", "Hello")
    generated = pdir / "ia.srt"
    srt(generated, "Bonjour")

    def sub_ids(h) -> dict[str, int]:
        return {t.get("lang"): t["id"] for t in h.get("track-list") if t["type"] == "sub"}

    h = launch(pdir)
    try:
        play(h, clip / "video30.mkv")
        ids = sub_ids(h)
        assert h.get("sid") == ids["es"]   # mpv.conf: slang=es,spa,en,eng
        h.command("set_property", "sid", ids["en"])
        wait_stored(h, lambda v: v.get("slang") == ["en", "eng", "es", "spa"])
        assert h.get("slang") == ["en", "eng", "es", "spa"]
        # audio: the English track of the bilingual file
        play(h, media_dir / "voz_es_en.mkv")
        eng = next(t["id"] for t in h.get("track-list") if t["type"] == "audio" and t.get("lang") == "eng")
        h.command("set_property", "aid", eng)
        wait_stored(h, lambda v: v.get("alang") == ["en", "eng", "es", "spa"])
        # subtitles off by the user
        play(h, clip / "video30.mkv")
        assert h.get("sid") == sub_ids(h)["en"]   # learned slang picks English now
        h.command("set_property", "sid", "no")
        wait_stored(h, lambda v: v.get("subs_off") is True)
    finally:
        h.stop()
    assert prefs_file(pdir)["mpv"]["subs_off"] is True

    h = launch(pdir)
    try:
        assert h.get("slang") == ["en", "eng", "es", "spa"] and h.get("alang") == ["en", "eng", "es", "spa"]
        play(h, clip / "video30.mkv")
        assert h.get("sid") is False and len(sub_ids(h)) == 2
        # an AI/generated track (mu-subs title) teaches nothing and does not clear subs_off
        h.command("sub-add", str(generated), "select", "Subtítulos IA (whisper)", "fr")
        h.wait_property("sid", lambda v: v not in (False, None), timeout=5)
        time.sleep(0.3)
        assert stored(h).get("slang") == ["en", "eng", "es", "spa"] and stored(h).get("subs_off") is True
        # turning a real track back on forgets subs_off
        h.command("set_property", "sid", sub_ids(h)["es"])
        wait_stored(h, lambda v: "subs_off" not in v and v.get("slang", [None])[0] == "es")
        assert h.script_errors() == []
    finally:
        h.stop()
    assert "subs_off" not in prefs_file(pdir)["mpv"]


def test_corrupt_file_and_invalid_keys(pdir):
    write_prefs(pdir, '{"mpv": {"volume": 7')
    h = launch(pdir)
    try:
        st = h.get("user-data/mu/prefs")
        assert st["corrupt"] and Path(st["corrupt"]).name.startswith("prefs.json.corrupt-")
        assert h.get("volume") == 100
        h.command("set_property", "volume", 55)
        wait_stored(h, lambda v: v.get("volume") == 55)
    finally:
        h.stop()
    assert prefs_file(pdir)["mpv"] == {"volume": 55}
    corrupt = list((pdir / "data").glob("prefs.json.corrupt-*"))
    assert len(corrupt) == 1 and corrupt[0].read_text() == '{"mpv": {"volume": 7'
    assert "prefs.json dañado" in h.log_text()

    # invalid values are dropped one by one, valid ones still apply
    write_prefs(pdir, {"mpv": {"volume": "alto", "speed": 1.25, "ontop": "sí", "hwdec": "cuda"},
                       "mu-av": {"filters": [1, 2], "light": True}})
    h = launch(pdir)
    try:
        assert h.get("volume") == 100 and h.get("speed") == 1.25 and h.get("ontop") is False
        assert h.get("user-data/mu/av")["light"] is True
    finally:
        h.stop()
    saved = prefs_file(pdir)
    assert saved["mpv"] == {"speed": 1.25} and saved["mu-av"] == {"light": True}


def test_two_instances_merge(pdir):
    a = launch(pdir)
    b = launch(pdir)
    try:
        a.command("set_property", "volume", 70)
        b.command("set_property", "sub-scale", 1.4)
        wait_stored(a, lambda v: v.get("volume") == 70)
        wait_stored(b, lambda v: v.get("sub-scale") == 1.4)   # stored as the file will hold it
        a.command("script-message-to", "mu_prefs", "flush")
        a.wait_property("user-data/mu/prefs", lambda v: v["writes"] >= 1, timeout=5)
        b.command("script-message-to", "mu_prefs", "flush")
        b.wait_property("user-data/mu/prefs", lambda v: v["writes"] >= 1, timeout=5)
        assert prefs_file(pdir)["mpv"] == {"volume": 70, "sub-scale": 1.4}
        # same key: the last change wins, the other instance's quit does not rewrite its older value
        a.command("set_property", "speed", 1.5)
        wait_stored(a, lambda v: v.get("speed") == 1.5)
        a.command("script-message-to", "mu_prefs", "flush")
        a.wait_property("user-data/mu/prefs", lambda v: v["writes"] >= 2, timeout=5)
        b.command("set_property", "speed", 2)
        wait_stored(b, lambda v: v.get("speed") == 2)
    finally:
        a.stop()
        b.stop()
    assert prefs_file(pdir)["mpv"] == {"volume": 70, "sub-scale": 1.4, "speed": 2}
    c = launch(pdir)
    try:
        assert c.get("volume") == 70 and abs(c.get("sub-scale") - 1.4) < 1e-4 and c.get("speed") == 2
    finally:
        c.stop()


def test_reset_with_confirmation_keeps_backup(pdir):
    old = {"mpv": {"volume": 70, "slang": ["en", "eng", "es", "spa"]}, "mu-av": {"filters": ["dialog"]},
           "mu-menu": {"resume": False}, "mu-study": {"silence_speed": 3}}
    write_prefs(pdir, old)
    h = launch(pdir)
    try:
        assert h.get("volume") == 70 and "mu-dialog" in labels(h) and h.get("slang")[0] == "en"
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("uosc"), timeout=15)
        h.command("script-message-to", "mu_prefs", "reset-ask")
        req = h.wait_property("user-data/mu/confirm_request", lambda v: bool(v) and v.get("token"), timeout=5)
        assert "Restablecer" in req["text"]
        h.command("script-message-to", "mu_menu", "mu-menu-confirm-event",
                  json.dumps({"type": "activate", "value": {"token": req["token"], "answer": "yes"}}))
        h.wait_property("volume", lambda v: v == 100, timeout=5)
        h.wait_property("af", lambda v: not any(f.get("label") == "mu-dialog" for f in v or []), timeout=5)
        assert h.get("slang") == ["es", "spa", "en", "eng"]
        h.wait_property("user-data/mu/menu", lambda v: v.get("resume") is True, timeout=5)
        h.wait_property("user-data/mu/study", lambda v: v.get("silence_speed") == 2.5, timeout=5)
        st = h.get("user-data/mu/prefs")
        assert st["backup"] and not st["values"]
        time.sleep(0.3)
    finally:
        h.stop()
    baks = list((pdir / "data").glob("prefs.json.bak-*"))
    assert len(baks) == 1 and json.loads(baks[0].read_text()) == old
    assert not (pdir / "data" / "prefs.json").exists()   # factory values are not written back


def test_disabled_switch(pdir):
    write_prefs(pdir, {"mpv": {"volume": 70}})
    h = launch(pdir, env={"MPV_UOS_PREFS": "0"})
    try:
        assert h.get("volume") == 100 and h.get("user-data/mu/prefs")["enabled"] is False
        h.command("set_property", "volume", 30)
        time.sleep(0.3)
    finally:
        h.stop()
    assert prefs_file(pdir) == {"mpv": {"volume": 70}}


def test_subtitle_ai_choices_survive_restart(pdir):
    """mu-subs (menu alt+i): language, model, automatic start and translation engine are remembered."""
    def ev(h, value):
        base = {"type": "activate", "index": 1, "menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False,
                "shift": False}
        h.command("script-message-to", "mu_subs", "mu-subs-event", json.dumps({**base, "value": value}))

    h = launch(pdir)
    try:
        h.wait_property("user-data/mu/subs", bool, timeout=10)
        ev(h, {"language": "en"})
        ev(h, {"model": "small-q8_0"})
        ev(h, {"opt": "auto_start"})
        ev(h, {"engine": "opus-big"})
        import time
        deadline = time.time() + 10
        while time.time() < deadline and prefs_file(pdir).get("mu-subs", {}).get("translate_engine") != "opus-big":
            time.sleep(0.2)
    finally:
        h.stop()
    saved = prefs_file(pdir)["mu-subs"]
    assert saved["language"] == "en" and saved["model"] == "small-q8_0" and saved["auto_start"] is True
    h = launch(pdir)
    try:
        st = h.wait_property("user-data/mu/subs", bool, timeout=10)
        assert st["language"] == "en" and st["model"] == "small-q8_0" and st["auto_start"] is True
        assert st["translate_engine"] == "opus-big"
    finally:
        h.stop()


def test_intro_choices_survive_restart(pdir):
    """mu-intro (menu alt+j): automatic skipping is remembered."""
    import time

    h = launch(pdir)
    try:
        h.wait_property("user-data/mu/intro", bool, timeout=10)
        h.command("script-message-to", "mu_intro", "mu-intro-set", "auto_skip_intro", "yes")
        deadline = time.time() + 10
        while time.time() < deadline and prefs_file(pdir).get("mu-intro", {}).get("auto_skip_intro") is not True:
            time.sleep(0.2)
    finally:
        h.stop()
    assert prefs_file(pdir)["mu-intro"]["auto_skip_intro"] is True
    h = launch(pdir)
    try:
        st = h.wait_property("user-data/mu/intro", lambda v: bool(v) and "auto_skip_intro" in v, timeout=10)
        assert st["auto_skip_intro"] is True and st["auto_skip_credits"] is False
    finally:
        h.stop()
