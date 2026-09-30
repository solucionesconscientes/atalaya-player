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
    assert h.script_errors() == [], h.script_errors()
