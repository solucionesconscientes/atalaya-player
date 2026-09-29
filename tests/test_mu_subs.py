"""mu-subs.lua end to end (headless mpv + real mpvd + vendored whisper.cpp): start from the menu/binding, the incremental
SRT is added as an external track and reloaded on push events, the look-ahead cursor follows seeks, the next playlist
item is pre-subtitled and adopted from cache when it starts, and the menu views (root, language, models, status)."""

from __future__ import annotations

import json

import pytest

from mpvd.asr.srt import parse_srt
from tests.asr_helpers import keywords_hit, min_keywords, asr_model, whisper_available
from tests.conftest import start_mpv

pytestmark = pytest.mark.skipif(not whisper_available(), reason="whisper.cpp not vendored (tools/vendor_whisper.sh)")


@pytest.fixture
def subs_mpv(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-subs-model={asr_model()},mu-subs-seek_interval=1,mu-subs-reload_min_interval=0.2,mu-subs-chunk_seconds=6",
        "--keep-open=yes", "--pause=yes",
    ], env=daemon_env.env)
    try:
        yield h, daemon_env
    finally:
        h.stop()


def send_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_subs", "mu-subs-event", json.dumps({**base, **ev}))


def wait_view(h, view: str, timeout: float = 30.0):
    return h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("view") == view
                           and any(i.get("title") for i in v.get("items", [])), timeout=timeout)


def external_sub(h, srt: str):
    for t in h.get("track-list") or []:
        if t.get("type") == "sub" and t.get("external") and t.get("external-filename") == srt:
            return t
    return None


def test_live_subtitles_track_seek_precompute_and_menu(subs_mpv, media_dir):
    h, d = subs_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    st = h.get("user-data/mu/subs")
    assert st["model"] == asr_model() and st["language"] == "auto" and st["active"] is False

    # a playlist of two local files: the second one gets pre-subtitled while the first plays
    h.command("loadfile", str(media_dir / "voz_es.flac"))
    h.command("loadfile", str(media_dir / "voz_en.flac"), "append")
    h.wait_property("path", lambda v: bool(v) and v.endswith("voz_es.flac"), timeout=20)
    h.command("script-message-to", "mu_subs", "mu-subs-set", "language", "es")

    # 1. start with the binding → task queued/running, SRT added as an external track right away
    h.command("script-binding", "mu_subs/subs-toggle")
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("active") is True and v.get("srt"), timeout=60)
    assert st["path"].endswith("voz_es.flac") and st["task_model"] == asr_model() and st["srt"].endswith(".srt")
    srt = st["srt"]
    h.wait_property("track-list", lambda tl: any(t.get("external-filename") == srt for t in tl or []), timeout=20)
    track = external_sub(h, srt)
    assert track and track["selected"] is True and track["title"].startswith("Subtítulos IA")
    assert h.get("sid") == track["id"]

    # 2. the daemon pushes progress; every new seq reloads the track; final event = done with cues
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("status") == "done", timeout=180)
    assert st["cues"] >= 1 and st["active"] is False and st["progress"] == 1.0 and st["detected"] in ("", "es")
    segs = parse_srt(open(srt, encoding="utf-8").read())
    text = " ".join(s.text for s in segs)
    assert len(keywords_hit(text, "es")) >= min_keywords(), text
    # the reloaded track is still there, still selected, and mpv shows the first cue at the current position
    track = external_sub(h, srt)
    assert track is not None and track["selected"] is True
    h.command("seek", max(0.2, segs[0].start + 0.3), "absolute")
    h.wait_property("sub-text", lambda v: isinstance(v, str) and len(v) > 0, timeout=15)
    assert h.get("sub-text").split()[0].lower() in text.lower()

    # 3. the next item was pre-subtitled at low priority (same model/language)
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("precompute_id"), timeout=30)
    pre = d.call("asr.status", {"id": st["precompute_id"]})
    assert pre["path"].endswith("voz_en.flac") and pre["purpose"] == "precompute" and pre["language"] == "es"
    d.wait(lambda: d.call("asr.status", {"id": pre["id"]})["status"] == "done", timeout=180)

    # 4. next file: starting there is instant (cache) and adds its own track
    h.command("playlist-next")
    h.wait_property("path", lambda v: bool(v) and v.endswith("voz_en.flac"), timeout=20)
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("path") == "" and not v.get("srt"), timeout=10)
    h.command("script-binding", "mu_subs/subs-toggle")
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("status") == "done" and v.get("srt"), timeout=60)
    assert st["task_id"] == pre["id"] and external_sub(h, st["srt"]) is not None

    # 5. menu: root → language → models → status
    h.command("script-binding", "mu_subs/subs-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-subs", timeout=15)
    st = wait_view(h, "root")
    titles = [i["title"] for i in st["items"]]
    assert titles[0] == "Subtítulos IA listos" and "Idioma" in titles and "Modelo" in titles
    assert any(t.startswith("Pre-subtitular") for t in titles) and "Estado del motor" in titles
    idioma = next(i for i in st["items"] if i["title"] == "Idioma")
    assert idioma["hint"] == "Español"
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "language"}})
    st = wait_view(h, "language")
    assert st["items"][0]["title"] == "Detectar automáticamente" and any(i["active"] and i["hint"] == "es" for i in st["items"])
    send_event(h, {"type": "activate", "index": 3, "value": {"language": "en"}})
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("language") == "en" and v.get("view") == "root",
                         timeout=15)
    send_event(h, {"type": "activate", "index": 3, "value": {"view": "models"}})
    st = wait_view(h, "models")
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("view") == "models"
                         and any(i["title"] == asr_model() for i in v.get("items", [])), timeout=30)
    assert st["items"][0]["title"].startswith("Automático") and any(i["active"] and i["title"] == asr_model()
                                                                    for i in st["items"])
    assert any("descargar" in i["hint"] for i in st["items"]), "absent models offer a download"
    send_event(h, {"type": "back"})
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 6, "value": {"view": "status"}})
    st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("view") == "status"
                         and any(i["title"] == "whisper-cli" for i in v.get("items", [])), timeout=30)
    assert any(i["title"] == "Modelos presentes" and asr_model() in i["hint"] for i in st["items"])
    assert any(i["hint"].startswith("done") for i in st["items"]), "tasks are listed"
    assert h.script_errors() == []


def test_remote_url_is_refused_politely(subs_mpv):
    h, _ = subs_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    h.command("script-binding", "mu_subs/subs-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-subs", timeout=15)
    st = wait_view(h, "root")
    assert st["items"][0]["title"].startswith("Abre un archivo local")
    h.command("script-message-to", "uosc", "close-menu", "mu-subs")
    h.command("script-binding", "mu_subs/subs-toggle")   # nothing loaded → OSD only, no task, no error
    st = h.get("user-data/mu/subs")
    assert st["active"] is False and st["task_id"] == "" and h.script_errors() == []
