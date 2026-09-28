"""mu-ytdl.lua end to end (headless mpv + real mpvd + the fake yt-dlp wired into mpv's own ytdl_hook):
vendored path handed to ytdl_hook, video/audio-only switch keeping the position, quality menu with hot format
change, download presets, live downloads panel fed by mpvd push events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import start_mpv
from tests.test_mu_iptv import serve

FAKE = Path(__file__).parent / "fixtures" / "ytdlp" / "fake_ytdlp.py"
URL = "https://fake.test/clip"


@pytest.fixture
def ytdl_mpv(daemon_env, media_dir, tmp_path):
    arglog = tmp_path / "argv.log"
    # ytdl_hook only accepts real URLs as streams, so the media the fake "extracts" is served over HTTP
    httpd = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    media_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    env = {
        **daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_ARGLOG": str(arglog),
        "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_MEDIA_URL": media_url,
        "FAKE_YTDLP_DELAY": "0.05", "FAKE_YTDLP_STEPS": "8",
        "MPV_UOS_DOWNLOAD_DIR": str(tmp_path / "dl"), "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
    }
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-ytdl-ytdl_path={FAKE}",
        "--keep-open=no", "--pause=yes",
    ], env=env)
    try:
        yield h, daemon_env, arglog, tmp_path
    finally:
        h.stop()
        httpd.shutdown()


def send_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-event", json.dumps({**base, **ev}))


def wait_view(h, view: str, timeout: float = 30.0):
    return h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == view, timeout=timeout)


def argv_lines(arglog: Path) -> list[list[str]]:
    if not arglog.exists():
        return []
    return [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()]


def test_hook_path_switch_quality_download_and_panel(ytdl_mpv, media_dir):
    h, d, arglog, tmp_path = ytdl_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)

    # ytdl_hook gets our binary through script-opts (hot, no restart) — docs/MPV_YTDL.md §2e
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("hook_path"), timeout=10)
    assert st["hook_path"] == str(FAKE) and st["active"] is False
    assert h.get("script-opts")["ytdl_hook-ytdl_path"] == str(FAKE)

    # 1. a network URL resolved by ytdl_hook with the fake -J → video + audio (137+140 from mpv.conf's ytdl-format)
    h.command("loadfile", URL)
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True, timeout=40)
    assert st["url"] == URL and st["mode"] == "video" and st["title"].startswith("Fake Test Video")
    assert st["current_ids"] == ["137", "140"] and st["format"].startswith("bestvideo")
    hook_argv = [a for a in argv_lines(arglog) if a[-1] == URL and "-J" in a][-1]
    assert "--format" in hook_argv or "-f" in hook_argv
    tracks = h.get("track-list")
    assert any(t["type"] == "video" for t in tracks) and any(t["type"] == "audio" for t in tracks)
    assert h.get("user-data/mpv/ytdl/json-subprocess-result")["status"] == 0
    h.command("set_property", "pause", False)
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 1.0, timeout=30)
    h.command("set_property", "pause", True)
    pos = h.get("time-pos")

    # 2. audio-only keeps the position and the pause state; ytdl_hook re-runs the fake with bestaudio/best
    n = len(argv_lines(arglog))
    h.command("script-binding", "mu_ytdl/ytdl-toggle-audio")
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("mode") == "audio" and v.get("active"),
                         timeout=40)
    assert st["format"] == "bestaudio/best" and st["current_ids"] == ["140"] and h.get("path") == URL
    assert h.get("vid") is False and h.get("pause") is True
    assert abs(h.get("time-pos") - pos) < 1.5, (h.get("time-pos"), pos)
    reload_argv = [a for a in argv_lines(arglog)[n:] if "-J" in a][-1]
    assert reload_argv[reload_argv.index("--format") + 1] == "bestaudio/best"
    assert h.get("stream-open-filename").endswith("voz_es.flac")

    # …and back to video (vid=auto restores the video track)
    h.command("script-binding", "mu_ytdl/ytdl-toggle-audio")
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("mode") == "video" and v.get("active")
                         and v.get("current_ids") == ["137", "140"], timeout=40)
    assert h.get("vid") not in (False, "no")

    # 3. quality menu: formats grouped from ytdl.info (seeded with ytdl_hook's JSON: no extra yt-dlp run)
    n = len(argv_lines(arglog))
    h.command("script-binding", "mu_ytdl/ytdl-quality")
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-ytdl", timeout=15)
    wait_view(h, "quality")
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "quality"
                         and any(i["submenu"] for i in v.get("items", [])), timeout=30)
    assert [i["title"] for i in st["items"]][:1] == ["Automático (mejor ≤1080p)"]
    assert [(i["title"], i["submenu"]) for i in st["items"][1:]] == [("Vídeo + audio", 1),
                                                                     ("Solo vídeo (+ mejor audio)", 1), ("Solo audio", 2)]
    assert len([a for a in argv_lines(arglog)[n:] if "-J" in a]) == 0, "quality menu must reuse ytdl_hook's JSON"
    info = d.call("ytdl.info", {"url": URL})
    assert info["counts"] == {"combined": 1, "video": 1, "audio": 2}
    assert h.script_errors() == []
    # pick the combined format 18 (video30.mkv) → hot reload at the same position
    send_event(h, {"type": "activate", "index": 1, "value": {"quality": {"format": "18", "audio_only": False, "id": "18"}}})
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("current_ids") == ["18"] and v.get("active"),
                         timeout=40)
    assert st["format"] == "18" and h.get("stream-open-filename").endswith("video30.mkv")
    # …and the "download this format" item action queues an exact download
    h.command("script-binding", "mu_ytdl/ytdl-quality")
    wait_view(h, "quality")
    send_event(h, {"type": "activate", "index": 1, "action": "download",
                   "value": {"quality": {"format": "137+140", "audio_only": False, "id": "137"}}})
    d.wait(lambda: any(r["kind"] == "exact" and r["spec"]["format"] == "137+140" for r in d.call("ytdl.downloads.list")),
           timeout=20)

    # 4. download menu → preset; the daemon runs the fake, pushes progress events into mpv (user-data last_event)
    h.command("script-message-to", "uosc", "close-menu", "mu-ytdl")
    h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=10)
    h.command("script-binding", "mu_ytdl/ytdl-download")
    wait_view(h, "download")
    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "download"
                         and any(i["title"] == "Opciones" for i in v.get("items", [])), timeout=30)
    assert [(i["title"], i["hint"]) for i in st["items"]][:2] == [("Vídeo", "mp4"), ("Audio", "9")]
    send_event(h, {"type": "activate", "index": 1, "value": {"opt": "container"}})  # mp4 → mkv, menu stays open
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and any(i["title"] == "Vídeo" and i["hint"] == "mkv"
                                                                     for i in v.get("items", [])), timeout=15)
    send_event(h, {"type": "activate", "index": 1, "value": {"preset": "audio_mp3_128"}})
    ev = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and isinstance(v.get("last_event"), dict)
                         and v["last_event"].get("status") == "done", timeout=60)
    assert ev["last_event"]["progress"] == 1.0
    d.wait(lambda: all(r["status"] in ("done", "failed") for r in d.call("ytdl.downloads.list")), timeout=60)
    rows = d.call("ytdl.downloads.list")
    mp3 = next(r for r in rows if r["preset"] == "audio_mp3_128")
    assert mp3["status"] == "done" and Path(mp3["outputs"][0]).is_file() and mp3["title"].startswith("Fake Test Video")
    assert mp3["spec"]["container"] == "mkv"  # option toggled in the menu travels with the request
    exact = next(r for r in rows if r["kind"] == "exact")
    assert exact["status"] == "done" and exact["spec"]["container"] == "mp4"  # queued before the toggle
    dl_argv = [a for a in argv_lines(arglog) if a[-1] == URL and "-x" in a][-1]
    assert dl_argv[dl_argv.index("--audio-quality") + 1] == "128K"

    # 5. downloads panel lists them with actions; remove works through the item action
    h.command("script-binding", "mu_ytdl/ytdl-downloads")
    wait_view(h, "downloads")
    def dl_items(v):
        return [i for i in (v or {}).get("items", []) if isinstance(i.get("value"), dict) and i["value"].get("download")]

    st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "downloads"
                         and len(dl_items(v)) >= 2, timeout=30)
    item = next(i for i in dl_items(st) if i["value"]["download"] == mp3["id"])
    assert item["icon"] == "check_circle" and set(item["actions"]) == {"retry", "remove"}
    send_event(h, {"type": "activate", "index": 1, "action": "remove", "value": {"download": mp3["id"]}})
    d.wait(lambda: all(r["id"] != mp3["id"] for r in d.call("ytdl.downloads.list")), timeout=20)
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and all(i["value"]["download"] != mp3["id"]
                                                                     for i in dl_items(v)), timeout=20)
    h.command("script-message-to", "uosc", "close-menu", "mu-ytdl")
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "", timeout=10)
    assert h.script_errors() == [], h.script_errors()
