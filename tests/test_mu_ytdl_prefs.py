"""mu-ytdl remembers "solo audio" and the download options across restarts (mu/prefs)."""

from __future__ import annotations

import json
import time

from tests.conftest import start_mpv
from tests.test_mu_iptv import serve
from tests.test_mu_ytdl import FAKE, URL, send_event


def _start(daemon_env, media_dir, tmp_path):
    httpd = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    env = {
        **daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_ARGLOG": str(tmp_path / "argv.log"),
        "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{httpd.server_address[1]}",
        "FAKE_YTDLP_DELAY": "0.05", "FAKE_YTDLP_STEPS": "4",
        "MPV_UOS_DOWNLOAD_DIR": str(tmp_path / "dl"), "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
    }
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-ytdl-ytdl_path={FAKE}", "--keep-open=no", "--pause=yes"], env=env)
    return h, httpd


def _prefs(daemon_env) -> dict:
    path = daemon_env.env["MPV_UOS_DATA_DIR"] + "/prefs.json"
    for _ in range(80):  # the writer debounces 1.5 s
        try:
            return json.loads(open(path, encoding="utf-8").read())
        except (OSError, ValueError):
            time.sleep(0.1)
    return {}


def test_audio_only_and_download_options_are_remembered(daemon_env, media_dir, tmp_path):
    h, httpd = _start(daemon_env, media_dir, tmp_path)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", URL)
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True, timeout=40)
        h.command("script-binding", "mu_ytdl/ytdl-toggle-audio")
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") and v.get("mode") == "audio",
                        timeout=40)
        # a download option changed from the menu (container mp4 → mkv)
        h.command("script-binding", "mu_ytdl/ytdl-download")
        # the options exist once mpvd answered ytdl.presets (the "Opciones" submenu is then in the menu)
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and str(v.get("view", "")).startswith("download")
                        and any(str(it.get("title", "")).startswith("Opciones") for it in v.get("items") or []),
                        timeout=30)
        send_event(h, {"type": "activate", "index": 1, "value": {"opt": "container"}})
        deadline = time.time() + 15
        while time.time() < deadline:
            p = _prefs(daemon_env).get("mu-ytdl", {})
            if p.get("prefer_audio") is True and p.get("dl_options", {}).get("container") == "mkv":
                break
            time.sleep(0.3)
        assert p.get("prefer_audio") is True and p["dl_options"]["container"] == "mkv"
        assert "playlist" not in p["dl_options"]
    finally:
        h.stop()
        httpd.shutdown()

    # next start: the URL opens straight in audio-only mode
    h, httpd = _start(daemon_env, media_dir, tmp_path)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", URL)
        st = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True, timeout=40)
        assert st["mode"] == "audio"
        assert h.get("vid") is False
        assert h.script_errors() == []
    finally:
        h.stop()
        httpd.shutdown()
