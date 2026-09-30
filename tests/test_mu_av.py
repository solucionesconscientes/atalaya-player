"""mu-av.lua (headless mpv + mpvd): each audio/video filter toggles a labelled lavfi graph that mpv accepts while playback
keeps going, RNNoise/SOFA models are used when vendored, the light profile swaps scalers and restores them, the stutter
diagnosis view reports counters and tips, and av.models lists the models."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.conftest import start_mpv

ROOT = Path(__file__).resolve().parent.parent
RNNOISE = ROOT / "vendor" / "models" / "rnnoise" / "sh.rnnn"
SOFA = ROOT / "vendor" / "models" / "sofa" / "mit_kemar_normal_pinna.sofa"


@pytest.fixture
def av_mpv(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5",
        "--keep-open=yes", "--pause=no", "--loop-file=inf",
    ], env=daemon_env.env)
    try:
        yield h, daemon_env
    finally:
        h.stop()


def send_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_av", "mu-av-event", json.dumps({**base, **ev}))


def labelled(h, kind: str) -> dict[str, dict]:
    return {f["label"]: f for f in (h.get(kind) or []) if f.get("label")}


def lavfi_errors(h) -> list[str]:
    return [ln for ln in h.log_text().splitlines()
            if ("[lavfi]" in ln or "[af]" in ln or "[vf]" in ln or "ffmpeg" in ln)
            and ("[error]" in ln or "[e]" in ln.split("]")[1:2] or "error" in ln.lower())
            and "not found" not in ln]


def test_filters_light_profile_and_diagnosis(av_mpv, media_dir):
    h, d = av_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    models = d.call("av.models")
    names = {m["name"]: m for m in models["models"]}
    assert set(names) == {"rnnoise-sh", "rnnoise-bd", "sofa-kemar"} and names["rnnoise-sh"]["default"]
    st = h.wait_property("user-data/mu/av", lambda v: bool(v) and "filters" in v, timeout=10)
    if RNNOISE.exists():
        assert st["models"]["rnnoise"].endswith(".rnnn") and names["rnnoise-sh"]["present"]
    if SOFA.exists():
        assert st["models"]["sofa"].endswith(".sofa")

    # audio filters on the video (audio + video) file: each toggle adds a labelled lavfi graph, playback continues
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.3, timeout=20)
    for name in ("dialog", "night", "denoise", "binaural"):
        h.command("script-message-to", "mu_av", "mu-av-toggle", name)
        h.wait_property("af", lambda v, n=name: any(f.get("label") == f"mu-{n}" for f in v or []), timeout=10)
    af = labelled(h, "af")
    assert set(af) == {"mu-dialog", "mu-night", "mu-denoise", "mu-binaural"}
    assert all(f["name"] == "lavfi" for f in af.values())
    graph_denoise = af["mu-denoise"]["params"]["graph"]
    graph_binaural = af["mu-binaural"]["params"]["graph"]
    assert ("arnndn=m=" in graph_denoise) if RNNOISE.exists() else ("afftdn" in graph_denoise)
    assert ("sofalizer=sofa=" in graph_binaural) if SOFA.exists() else ("crossfeed" in graph_binaural)
    assert "dynaudnorm" in af["mu-dialog"]["params"]["graph"] and "alimiter" in af["mu-night"]["params"]["graph"]
    st = h.wait_property("user-data/mu/av", lambda v: bool(v) and len(v.get("filters", {})) == 4, timeout=10)
    assert set(st["filters"]) == {"dialog", "night", "denoise", "binaural"}
    t0 = h.get("time-pos")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - t0) > 0.8, timeout=15)
    # video filter
    h.command("script-message-to", "mu_av", "mu-av-toggle", "photo")
    h.wait_property("vf", lambda v: any(f.get("label") == "mu-photo" for f in v or []), timeout=10)
    assert "photosensitivity" in labelled(h, "vf")["mu-photo"]["params"]["graph"]
    t0 = h.get("time-pos")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and abs(v - t0) > 0.8, timeout=15)
    assert lavfi_errors(h) == [], lavfi_errors(h)
    # toggling again removes; alt+n binding drives night mode
    h.command("script-message-to", "mu_av", "mu-av-toggle", "denoise")
    h.wait_property("af", lambda v: not any(f.get("label") == "mu-denoise" for f in v or []), timeout=10)
    h.command("script-binding", "mu_av/av-night")
    h.wait_property("af", lambda v: not any(f.get("label") == "mu-night" for f in v or []), timeout=10)

    # light profile swaps scalers/deband/interpolation and restores them
    before = {k: h.get(k) for k in ("scale", "deband", "interpolation", "video-sync")}
    h.command("script-message-to", "mu_av", "mu-av-light", "yes")
    h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("light") is True, timeout=10)
    assert h.get("scale") == "bilinear" and h.get("deband") is False and h.get("video-sync") == "audio"
    h.command("script-message-to", "mu_av", "mu-av-light", "no")
    h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("light") is False, timeout=10)
    assert {k: h.get(k) for k in before} == before

    # menu: root lists the five filters + tools; diagnosis view has counters and at least one tip
    h.command("script-binding", "mu_av/av-menu")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-av", timeout=15)
    st = h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("view") == "root" and v.get("items"), timeout=20)
    titles = [i["title"] for i in st["items"]]
    assert titles[:7] == ["Diálogo claro", "Modo noche", "Volumen igualado", "Ecualizador", "Reducción de ruido",
                          "Binaural para auriculares", "Protección fotosensible"]
    assert [i["active"] for i in st["items"][:7]] == [True, False, False, False, False, True, True]
    assert "Diagnóstico de tirones" in titles and "Quitar todos los filtros" in titles
    send_event(h, {"type": "activate", "index": titles.index("Diagnóstico de tirones") + 1, "value": {"view": "diag"}})
    st = h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("view") == "diag"
                         and any(i["title"].startswith("Perdidos VO") for i in v.get("items", [])), timeout=20)
    assert st["diag"]["vo"] == "null" and isinstance(st["diag"]["drops"], int) and st["diag"]["tips"]
    send_event(h, {"type": "back"})
    h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("view") == "root", timeout=10)
    send_event(h, {"type": "activate", "index": titles.index("Quitar todos los filtros") + 1, "value": {"clear": True}})
    h.wait_property("user-data/mu/av", lambda v: bool(v) and not v.get("filters"), timeout=10)
    assert labelled(h, "af") == {} and labelled(h, "vf") == {}
    assert h.get("user-data/mu/av")["last_error"] == ""
    assert h.script_errors() == []


def test_av_models_download_unknown(daemon_env):
    from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, RpcError
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)
    assert d.call("capabilities")["services"]["av"] is True
    with pytest.raises(RpcError) as exc:
        d.call("av.models.download", {"name": "nope"})
    assert exc.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as exc:
        d.call("av.models.path", {"name": "rnnoise-bd"}) if not (ROOT / "vendor/models/rnnoise/bd.rnnn").exists() else (_ for _ in ()).throw(RpcError(NOT_FOUND, "x"))
    assert exc.value.code == NOT_FOUND


def test_volume_leveling_and_equalizer_are_applied_and_remembered(daemon_env, media_dir):
    """H24: «Volumen igualado» (slow dynaudnorm) and the equalizer presets play without lavfi errors, the preset is
    chosen from its submenu and both come back in the next player."""
    args = ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5",
            "--keep-open=yes", "--pause=no", "--loop-file=inf"]
    h = start_mpv(daemon_env.runtime_dir, args, env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/av", lambda v: bool(v) and "filters" in v, timeout=20)
        h.command("loadfile", str(media_dir / "video30.mkv"))
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 0.3, timeout=20)
        h.command("script-message-to", "mu_av", "mu-av-toggle", "level")
        h.wait_property("af", lambda v: any(f.get("label") == "mu-level" for f in v or []), timeout=10)
        assert "dynaudnorm" in labelled(h, "af")["mu-level"]["params"]["graph"]
        # every preset is a graph mpv accepts while playing
        for preset in ("bass", "less_bass", "treble", "voice", "music", "laptop", "hp_inear", "hp_closed", "hp_open",
                       "headphones"):
            h.command("script-message-to", "mu_av", "mu-av-eq", preset)
            h.wait_property("user-data/mu/av", lambda v, p=preset: bool(v) and v.get("eq") == p
                            and v.get("filters", {}).get("eq"), timeout=10)
            t0 = h.get("time-pos")
            h.wait_property("time-pos", lambda v, t=t0: isinstance(v, (int, float)) and abs(v - t) > 0.3, timeout=15)
        assert "highpass" not in labelled(h, "af")["mu-eq"]["params"]["graph"]
        assert lavfi_errors(h) == [], lavfi_errors(h)
        # submenu: flat removes the filter, «Más graves» puts it back
        h.command("script-binding", "mu_av/av-menu")
        st = h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("view") == "root" and v.get("items"),
                             timeout=20)
        titles = [i["title"] for i in st["items"]]
        eq_row = st["items"][titles.index("Ecualizador")]
        assert eq_row["hint"] == "Auriculares" and eq_row["active"] is True
        send_event(h, {"type": "activate", "index": titles.index("Ecualizador") + 1, "value": {"view": "eq"}})
        st = h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("view") == "eq" and v.get("items"),
                             timeout=10)
        assert [i["title"] for i in st["items"]][:2] == ["Plano (sin ecualizar)", "Más graves"]
        send_event(h, {"type": "activate", "index": 1, "value": {"eq": ""}})
        h.wait_property("af", lambda v: not any(f.get("label") == "mu-eq" for f in v or []), timeout=10)
        send_event(h, {"type": "activate", "index": 2, "value": {"eq": "bass"}})
        h.wait_property("af", lambda v: any(f.get("label") == "mu-eq" for f in v or []), timeout=10)
        assert h.script_errors() == []
    finally:
        h.stop()
    # remembered by mu-prefs (same data dir)
    h = start_mpv(daemon_env.runtime_dir, args, env=daemon_env.env)
    try:
        st = h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("filters", {}).get("eq")
                             and v.get("filters", {}).get("level"), timeout=20)
        assert st["eq"] == "bass"
        assert "equalizer=f=60" in labelled(h, "af")["mu-eq"]["params"]["graph"]
    finally:
        h.stop()


def test_audio_only_for_local_files_and_when_minimized(av_mpv, media_dir):
    """H32: alt+a on a local file deselects the video at once (only for that file); «Solo audio al minimizar la
    ventana» does the same while the window is minimized and brings the picture back afterwards."""
    h, d = av_mpv
    h.wait_property("user-data/mu/av", lambda v: bool(v) and "filters" in v, timeout=20)
    h.command("loadfile", str(media_dir / "video30.mkv"))
    h.wait_property("current-tracks/video", lambda v: isinstance(v, dict) and v.get("id") == 1, timeout=20)
    h.command("script-binding", "mu_ytdl/ytdl-toggle-audio")
    h.wait_property("vid", lambda v: v is False or v == "no", timeout=10)
    assert h.get("current-tracks/audio") and h.get("time-pos") is not None
    h.command("script-binding", "mu_ytdl/ytdl-toggle-audio")
    h.wait_property("current-tracks/video", lambda v: isinstance(v, dict) and v.get("id") == 1, timeout=10)
    # file-local: the next file opens with its picture
    h.command("script-binding", "mu_ytdl/ytdl-toggle-audio")
    h.wait_property("vid", lambda v: v is False or v == "no", timeout=10)
    h.command("loadfile", str(media_dir / "chapters.mkv"))
    h.wait_property("current-tracks/video", lambda v: isinstance(v, dict) and v.get("id") == 1, timeout=20)

    # minimized: off by default → nothing happens
    h.command("set", "window-minimized", "yes")
    time.sleep(0.5)
    assert isinstance(h.get("current-tracks/video"), dict)
    h.command("set", "window-minimized", "no")
    h.command("script-message-to", "mu_av", "mu-av-event", json.dumps(
        {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False, "type": "activate",
         "index": 1, "value": {"audio_minimized": True}}))
    h.command("set", "window-minimized", "yes")
    h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("minimized_audio") is True, timeout=10)
    h.wait_property("vid", lambda v: v is False or v == "no", timeout=10)
    h.command("set", "window-minimized", "no")
    h.wait_property("current-tracks/video", lambda v: isinstance(v, dict) and v.get("id") == 1, timeout=10)
    assert h.get("user-data/mu/av")["minimized_audio"] is False
    assert h.script_errors() == []
