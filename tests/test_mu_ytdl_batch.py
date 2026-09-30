"""H19 · download manager in mu-ytdl (headless mpv + mpvd + the fake yt-dlp): several URLs pasted at once, a list with
check boxes (own numbered folder, only the marked entries) and the download settings (simultaneous downloads, speed
limit, archive)."""

from __future__ import annotations

import json
import time

from tests.test_mu_ytdl import argv_lines, send_event, wait_view, ytdl_mpv  # noqa: F401 - fixture


def batch_event(h, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-batch-event", json.dumps({**base, **ev}))


def titles(v):
    return [i["title"] for i in v.get("items", [])]


def ytdl_state(h, pred, timeout: float = 30.0):
    return h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and pred(v), timeout=timeout)


def argv_for(arglog, url, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [a for a in argv_lines(arglog) if a and a[-1] == url and "--no-simulate" in a]
        if rows:
            return rows[-1]
        time.sleep(0.1)
    raise AssertionError(f"no yt-dlp download for {url}")


def test_batch_list_with_checkboxes_and_settings(ytdl_mpv):
    h, d, arglog, _tmp = ytdl_mpv
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    ytdl_state(h, lambda v: v.get("view") == "root" and
               {"Descargar varias URL…", "Descargar de una lista o canal…", "Ajustes de descarga"} <= set(titles(v)))

    # several URLs pasted at once (repeats dropped) → one download each
    send_event(h, {"type": "activate", "index": 1, "value": {"view": "batch"}})
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-ytdl-batch", timeout=10)
    batch_event(h, {"type": "search", "query": "https://fake.test/uno https://fake.test/dos, https://fake.test/uno"})
    st = ytdl_state(h, lambda v: "Descargar 2 enlaces · vídeo" in titles(v))
    batch_event(h, {"type": "activate", "index": 1, "value": {"batch": "video"}})
    wait_view(h, "downloads")
    for url in ("https://fake.test/uno", "https://fake.test/dos"):
        a = argv_for(arglog, url)
        assert "--download-archive" in a
    d.wait(lambda: sum(1 for r in d.call("ytdl.downloads.list") if r["status"] == "done") >= 2, timeout=60)

    # a list: every entry checked at first; unmark the 2nd → only 1,3,4,5 in a numbered folder
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 2, "value": {"view": "batch", "list": True}})
    h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-ytdl-batch", timeout=10)
    pl_url = "https://www.youtube.com/playlist?list=PLtest"
    batch_event(h, {"type": "search", "query": pl_url})
    ytdl_state(h, lambda v: "Ver la lista y elegir" in titles(v))
    batch_event(h, {"type": "activate", "index": 1, "value": {"pl_url": pl_url}})
    st = ytdl_state(h, lambda v: v.get("view") == "playlist" and "Descargar 5 · vídeo" in titles(v), timeout=40)
    assert titles(st)[0] == "Desmarcar todo" and len(st["items"]) == 3 + 5
    send_event(h, {"type": "activate", "index": 5, "value": {"pl_toggle": 2}})
    st = ytdl_state(h, lambda v: "Descargar 4 · vídeo" in titles(v))
    send_event(h, {"type": "activate", "index": 2, "value": {"pl_download": "video"}})
    a = argv_for(arglog, pl_url)
    assert a[a.index("--playlist-items") + 1] == "1,3,4,5" and "--yes-playlist" in a
    assert a[a.index("-o") + 1].startswith("%(playlist_title,playlist_id|Lista)s/%(playlist_index)03d - ")

    # settings: simultaneous downloads and speed limit cycle, the archive toggles
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    wait_view(h, "root")
    send_event(h, {"type": "activate", "index": 3, "value": {"view": "dl_settings"}})
    st = ytdl_state(h, lambda v: v.get("view") == "dl_settings" and "Límite de velocidad" in titles(v))
    send_event(h, {"type": "activate", "index": 2, "value": {"dlset": "rate_limit"}})
    d.wait(lambda: d.call("ytdl.settings.get")["rate_limit"] == "500K", timeout=10)
    send_event(h, {"type": "activate", "index": 1, "value": {"dlset": "concurrent"}})
    d.wait(lambda: d.call("ytdl.settings.get")["concurrent"] == 3, timeout=10)
    send_event(h, {"type": "activate", "index": 3, "value": {"dlset": "archive"}})
    d.wait(lambda: d.call("ytdl.settings.get")["archive"] is False, timeout=10)
    st = ytdl_state(h, lambda v: v.get("view") == "dl_settings" and
                    any(i["title"] == "Límite de velocidad" and i["hint"] == "500KB/s" for i in v.get("items", [])))
    # «usar mi sesión del navegador»: off by default; one step → Firefox, for playback (ytdl_hook) and downloads
    assert d.call("ytdl.settings.get")["cookies_browser"] == ""
    send_event(h, {"type": "activate", "index": 5, "value": {"dlset": "cookies_browser"}})
    d.wait(lambda: d.call("ytdl.settings.get")["cookies_browser"] == "firefox", timeout=10)
    h.wait_property("ytdl-raw-options", lambda v: (v or {}).get("cookies-from-browser") == "firefox", timeout=10)
    r = d.call("ytdl.download", {"url": "https://fake.test/cinco", "preset": "video_360"})
    a = argv_for(arglog, "https://fake.test/cinco")
    assert a[a.index("--cookies-from-browser") + 1] == "firefox"
    try:
        d.call("ytdl.settings.set", {"cookies_browser": "netscape"})
        raise AssertionError("unknown browser accepted")
    except Exception as exc:  # noqa: BLE001
        assert "navegador" in str(exc)
    assert r["id"]
    assert h.script_errors() == [], h.script_errors()


def test_subtitles_options_and_subtitles_only(ytdl_mpv):
    """Descargar → Opciones: subtítulos no → dentro del vídeo → archivo SRT aparte, idiomas que rotan; «Solo subtítulos
    (SRT)» baja solo los .srt (sin vídeo) con «originales» resueltos por mpvd."""
    h, d, arglog, _tmp = ytdl_mpv
    from tests.test_mu_ytdl import URL

    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    h.command("loadfile", URL)
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True, timeout=40)
    h.command("script-binding", "mu_ytdl/ytdl-download")
    st = ytdl_state(h, lambda v: v.get("view") == "download" and "Solo subtítulos (SRT)" in titles(v))
    opts = next(i for i in st["items"] if i["title"] == "Opciones")
    assert opts["submenu"] > 0
    for expected in ("dentro del vídeo", "archivo SRT aparte"):
        send_event(h, {"type": "activate", "index": 1, "value": {"opt": "subtitles"}})
        h.wait_property("user-data/mu/ytdl", lambda v, e=expected: bool(v) and v.get("view") == "download", timeout=10)
    send_event(h, {"type": "activate", "index": 1, "value": {"opt": "sub_langs"}})   # → solo el original
    send_event(h, {"type": "activate", "index": 1, "value": {"opt": "sub_langs"}})   # → español
    st = ytdl_state(h, lambda v: v.get("view") == "download" and any(
        i["title"] == "Solo subtítulos (SRT)" and i["hint"] == "español" for i in v.get("items", [])))
    item = next(i for i in st["items"] if i["title"] == "Solo subtítulos (SRT)")
    send_event(h, {"type": "activate", "index": 3, "value": item["value"]})
    a = argv_for(arglog, URL)
    assert "--skip-download" in a and a[a.index("--sub-langs") + 1] == "es.*" and "-f" not in a
    d.wait(lambda: any(r["status"] == "done" and r["outputs"] and all(o.endswith(".es.srt") for o in r["outputs"])
                       for r in d.call("ytdl.downloads.list")), timeout=30)
    # a video with subtitles as a separate file: the next download carries --convert-subs srt, not --embed-subs
    send_event(h, {"type": "activate", "index": 1, "value": {"preset": "video_360"}})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        rows = [x for x in argv_lines(arglog) if x and x[-1] == URL and "--no-simulate" in x and "-f" in x]
        if rows:
            break
        time.sleep(0.1)
    v = rows[-1]
    assert "--convert-subs" in v and "--embed-subs" not in v and v[v.index("--sub-langs") + 1] == "es.*"
    assert h.script_errors() == [], h.script_errors()


def test_playback_retries_once_with_the_nightly_build(daemon_env, media_dir, tmp_path):
    """A URL the stable yt-dlp cannot open is loaded again with the nightly build first in ytdl_hook's path; afterwards
    the stable one is first again. A URL that fails with both is not retried in a loop."""
    from tests.conftest import start_mpv
    from tests.test_mu_iptv import serve
    from tests.test_mu_ytdl import FAKE

    wrapper = tmp_path / "yt-dlp-nightly"
    wrapper.write_text("#!/usr/bin/env python3\nimport os, runpy, sys\nos.environ['FAKE_YTDLP_NIGHTLY'] = '1'\n"
                       f"sys.argv[0] = {str(FAKE)!r}\nrunpy.run_path({str(FAKE)!r}, run_name='__main__')\n",
                       encoding="utf-8")
    wrapper.chmod(0o755)
    httpd = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    env = {**daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "MPV_UOS_YTDLP_NIGHTLY": str(wrapper),
           "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{httpd.server_address[1]}",
           "MPV_UOS_YTDLP_AUTO_UPDATE": "0", "FAKE_YTDLP_ARGLOG": str(tmp_path / "argv.log")}
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-ytdl-ytdl_path={FAKE}", "--keep-open=yes", "--pause=yes"], env=env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        url = "https://fake.test/fail-extract"
        h.command("loadfile", url)
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True and v.get("url") == url,
                        timeout=40)
        assert h.get("path") == url
        # back to the stable build first once the retried file is playing
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and not str(v.get("hook_path", "")).startswith(
            str(wrapper)), timeout=10)
        # both fail: one retry, then it stays failed (idle), no loop
        h.command("loadfile", "https://fake.test/fail-always")
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("last_event") == "nightly-retry", timeout=20)
        h.wait_property("idle-active", lambda v: v is True, timeout=30)
        runs = [json.loads(ln) for ln in (tmp_path / "argv.log").read_text().splitlines()]
        assert sum(1 for a in runs if a and a[-1] == "https://fake.test/fail-always") <= 2
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
        httpd.shutdown()


def test_playback_format_follows_hardware_decoding(daemon_env, media_dir, tmp_path):
    """H31: while ytdl-format is mpv.conf's, internet videos open with the codecs this machine decodes in hardware
    first (here «AV1 + H.264»); a format chosen by the user is respected."""
    from tests.conftest import start_mpv
    from tests.test_mu_iptv import serve
    from tests.test_mu_ytdl import FAKE

    arglog = tmp_path / "argv.log"
    httpd = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    daemon_env.extra_env["MPV_UOS_HWDECODE"] = "av1,h264"
    env = {**daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_ARGLOG": str(arglog),
           "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{httpd.server_address[1]}",
           "MPV_UOS_YTDLP_AUTO_UPDATE": "0"}
    h = start_mpv(daemon_env.runtime_dir, [
        "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
        f"mu-ytdl-ytdl_path={FAKE}", "--keep-open=yes", "--pause=yes"], env=env)
    try:
        ytdl_state(h, lambda v: v.get("hw_format", "").startswith("bestvideo[height<=?1080][vcodec^=av01]"), timeout=40)

        def hook_format(url):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                rows = [a for a in argv_lines(arglog) if a and a[-1] == url and "-J" in a]
                if rows:
                    return rows[-1][rows[-1].index("--format") + 1]
                time.sleep(0.1)
            raise AssertionError(f"ytdl_hook did not run for {url}")

        h.command("loadfile", "https://fake.test/uno")
        assert hook_format("https://fake.test/uno").startswith("bestvideo[height<=?1080][vcodec^=av01]+bestaudio/")
        h.wait_property("path", lambda v: v == "https://fake.test/uno", timeout=20)
        h.command("stop")
        h.wait_property("idle-active", lambda v: v is True, timeout=20)
        # a file-local option: the global value (what mu-prefs would remember) is still mpv.conf's
        h.wait_property("ytdl-format", lambda v: str(v).startswith("bestvideo[height<=?1080][vcodec^=avc1]"), timeout=10)
        h.command("set", "ytdl-format", "worst")
        h.command("loadfile", "https://fake.test/dos")
        assert hook_format("https://fake.test/dos") == "worst"
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
        httpd.shutdown()
