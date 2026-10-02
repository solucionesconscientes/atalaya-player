"""Downloads panel (H23): the page and API served by the remote's HTTP server of a real mpvd (fake yt-dlp from
tests/fixtures/ytdlp), same pairing cookie as the remote, live tasks over SSE, bulk actions, adding links, disk
space; «Enviar a MPV-UOS» links (``mpv-uos://download?url=…``) through ``mpvd link``; and the mu-remote entry that
opens the panel in this computer's browser."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from mpvd import handoff
from mpvd.remote.downloads import disk_usage
from mpvd.rpc import RpcError
from tests.conftest import APP, APP_FOLDER, start_mpv
from tests.test_remote import RemoteClient

FAKE = Path(__file__).parent / "fixtures" / "ytdlp" / "fake_ytdlp.py"


# -- pure helpers ------------------------------------------------------------------------------------------------


def test_download_links_roundtrip_and_rejects():
    url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=10s#frag"
    link = handoff.download_link(url, "audio_mp3_192")
    assert link.startswith("mpv-uos://download?url=https%3A%2F%2Fwww.youtube.com") and link.endswith("&preset=audio_mp3_192")
    assert handoff.parse(link) == {"action": "download", "url": url, "preset": "audio_mp3_192"}
    assert handoff.parse(handoff.download_link("http://example.com/v")) == {"action": "download",
                                                                          "url": "http://example.com/v", "preset": None}
    for bad in ("mpv-uos://open?path=%2Fx.mkv", "mpv-uos://download", "mpv-uos://download?url=",
                handoff.download_link("file:///etc/passwd"), handoff.download_link("javascript:alert(1)"),
                handoff.download_link("/home/ser/x.mkv"), handoff.download_link("https://a.b/x y"),
                handoff.download_link("https://a.b/x", "../../x"), "https://example.com/?url=https://a.b",
                handoff.download_link("https://" + "a" * 5000)):
        assert handoff.parse(bad) is None, bad


def test_disk_usage_walks_up_to_an_existing_folder_and_joins_filesystems(tmp_path):
    video = tmp_path / "Vídeos" / APP_FOLDER          # not created yet: its parent's filesystem is reported
    audio = tmp_path / "Música" / APP_FOLDER
    rows = disk_usage({"video": video, "audio": audio})
    assert len(rows) == 1 and rows[0]["kinds"] == ["video", "audio"] and rows[0]["path"] == str(video)
    real = shutil.disk_usage(tmp_path)
    assert rows[0]["total"] == real.total and 0 < rows[0]["free"] <= real.total and 0 <= rows[0]["percent_free"] <= 100
    assert disk_usage({}) == []


# -- a real daemon ------------------------------------------------------------------------------------------------


@pytest.fixture
def panel_env(daemon_env, media_dir):
    dl = daemon_env.base / "dl"
    daemon_env.extra_env.update({
        "MPVD_REMOTE_HOST": "127.0.0.1", "MPVD_REMOTE_PUBLIC_HOST": "127.0.0.1", "MPVD_REMOTE_PORT": "0",
        "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_DELAY": "0.15",
        "FAKE_YTDLP_STEPS": "8", "MPV_UOS_DOWNLOAD_DIR": str(dl), "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
        "MPV_UOS_NO_NOTIFY": "1"})
    r = daemon_env.cli("ensure")
    assert r.returncode == 0, r.stdout + r.stderr
    return daemon_env, dl


def _paired(d) -> RemoteClient:  # type: ignore[no-untyped-def]
    pair = d.call("remote.pair", {"path": "/downloads", "local": True})
    assert pair["url"].startswith("http://127.0.0.1:") and "/downloads#t=" + pair["token"] in pair["url"]
    c = RemoteClient(pair["url"].split("/downloads#")[0])
    status, data = c.req("/api/pair", {"token": pair["token"]})
    assert status == 200 and data["ok"], data
    return c


def _wait_tasks(c: RemoteClient, predicate, timeout: float = 40.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    data = None
    while time.monotonic() < deadline:
        status, data = c.req("/api/tasks")
        assert status == 200, data
        if predicate(data["tasks"]):
            return data
        time.sleep(0.2)
    raise AssertionError(f"tareas: condición no alcanzada: {[(t['title'], t['status']) for t in data['tasks']]}")


def _pick(tasks, ids):  # type: ignore[no-untyped-def]
    return [t for t in tasks if t["id"] in ids]


def test_panel_page_auth_add_and_live_tasks(panel_env):
    d, dl = panel_env
    with pytest.raises(RpcError):
        d.call("remote.pair", {"path": "/etc/passwd"})
    c = _paired(d)
    anon = RemoteClient(c.base)
    # the page is public (like the PWA), the API needs the remote's cookie
    status, html = anon.req("/downloads")
    assert status == 200 and f"Descargas {APP}".encode() in html and b"/downloads.js" in html
    status, js = anon.req("/downloads.js")
    assert status == 200 and b"/events/tasks" in js and b"mpv-uos://download?url=" in js
    status, pwa = anon.req("/")
    assert status == 200 and b'href="/downloads"' in pwa
    for path in ("/api/tasks", "/api/downloads/presets", "/api/disk", "/events/tasks"):
        assert anon.req(path)[0] == 401, path
    assert anon.req("/api/downloads/add", {"text": "https://fake.test/x"})[0] == 401
    assert anon.req("/api/tasks/action", {"action": "cancel", "items": [{"type": "download", "id": "x"}]})[0] == 401

    status, presets = c.req("/api/downloads/presets")
    assert status == 200 and presets["default"] == "video_best"
    ids = [p["id"] for p in presets["presets"]]
    assert "video_best" in ids and "audio_mp3_192" in ids and all(p["title"] for p in presets["presets"])

    status, disk = c.req("/api/disk")
    assert status == 200 and disk["disk"] and disk["disk"][0]["free"] > 0 and disk["disk"][0]["path"] == str(dl)
    assert disk["disk"][0]["kinds"] == ["video", "audio"]

    # pasted text with repeats and noise → two downloads with the chosen preset
    assert c.req("/api/downloads/add", {"text": "   "})[0] == 400
    assert c.req("/api/downloads/add", {"text": "https://fake.test/a", "preset": "nope"})[0] == 400
    status, added = c.req("/api/downloads/add", {"text": "mira https://fake.test/uno y\nhttps://fake.test/dos\n"
                                                         "https://fake.test/uno", "preset": "video_360"})
    assert status == 200 and added["count"] == 2 and added["preset"] == "video_360" and len(added["ids"]) == 2
    # the live stream sends the tasks right away, then again when they change
    u = urlsplit(c.base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    conn.request("GET", "/events/tasks", headers={"Cookie": c.cookie})
    resp = conn.getresponse()
    assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/event-stream")
    events: list[tuple[str, dict]] = []
    event = None
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        line = resp.fp.readline().decode().rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line[6:])))
            data = events[-1][1]
            if event == "tasks" and sum(1 for t in data["tasks"] if t["status"] == "done") >= 2:
                break
    conn.close()
    assert events[0][0] == "hello" and events[1][0] == "tasks"
    assert any(t["status"] in ("queued", "running") for t in events[1][1]["tasks"])
    assert any(t["status"] == "running" and 0 < t["progress"] < 1 for _, e in events[1:] for t in e["tasks"])
    last = events[-1][1]
    assert last["active"] == 0 and last["history"] == 2 and last["disk"]
    done = [t for t in last["tasks"] if t["status"] == "done"]
    assert {t["type"] for t in done} == {"download"} and all(t["outputs"] for t in done)
    assert all(Path(t["outputs"][0]).parent == dl for t in done)
    assert all("360" in t["description"] for t in done)
    assert "retry" in done[0]["actions"] and "remove" in done[0]["actions"]
    # CSRF guard of the remote applies to the panel too
    r = urllib.request.Request(c.base + "/api/downloads/add", data=b'{"text":"https://fake.test/z"}', method="POST",
                               headers={"Content-Type": "application/json", "Cookie": c.cookie,
                                        "Origin": "http://evil.example"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(r, timeout=10)
    assert exc.value.code == 403


def test_panel_bulk_actions_play_and_clear(panel_env):
    d, _ = panel_env
    c = _paired(d)
    status, added = c.req("/api/downloads/add", {"urls": ["https://fake.test/lento1", "https://fake.test/lento2",
                                                          "https://fake.test/private"]})
    assert status == 200 and added["count"] == 3
    slow_ids, private_id = added["ids"][:2], added["ids"][2:]
    slow = _pick(c.req("/api/tasks")[1]["tasks"], slow_ids)
    assert len(slow) == 2 and all(t["status"] in ("queued", "running") for t in slow)
    # several at once: cancel both slow downloads; a bogus item is reported, not fatal
    items = [{"type": t["type"], "id": t["id"]} for t in slow] + [{"type": "nope", "id": "x"}]
    status, res = c.req("/api/tasks/action", {"action": "cancel", "items": items})
    assert status == 200 and res["done"] == 2 and [r["ok"] for r in res["results"]] == [True, True, False]
    _wait_tasks(c, lambda ts: all(t["status"] == "cancelled" for t in _pick(ts, slow_ids)))
    _wait_tasks(c, lambda ts: any(t["status"] == "failed" for t in _pick(ts, private_id)))
    # retry both (they run again), then let them finish
    status, res = c.req("/api/tasks/action", {"action": "retry", "items": items[:2]})
    assert status == 200 and res["done"] == 2
    data = _wait_tasks(c, lambda ts: all(t["status"] == "done" for t in _pick(ts, slow_ids)))
    failed = _pick(data["tasks"], private_id)[0]
    assert failed["error"] and "Private video" in failed["error"]
    # play: a finished task opens its file in the player; none is open here → 503; a failed one has no file → 400
    done = _pick(data["tasks"], slow_ids)[0]
    status, res = c.req("/api/tasks/action", {"action": "play", "items": [{"type": "download", "id": done["id"]}]})
    assert status == 503 and "reproductor" in res["error"]
    status, res = c.req("/api/tasks/action", {"action": "play", "items": [{"type": "download", "id": failed["id"]}]})
    assert status == 400
    # validation
    assert c.req("/api/tasks/action", {"action": "rm -rf", "items": items})[0] == 400
    assert c.req("/api/tasks/action", {"action": "cancel", "items": []})[0] == 400
    assert c.req("/api/tasks/action", ["x"])[0] == 400
    # remove one from the history, then clear the rest (files stay on disk)
    status, res = c.req("/api/tasks/action", {"action": "remove", "items": [{"type": "download", "id": failed["id"]}]})
    assert status == 200 and res["done"] == 1
    assert not _pick(c.req("/api/tasks")[1]["tasks"], private_id)
    status, res = c.req("/api/tasks/clear", {})
    assert status == 200 and res["removed"] == 2
    status, data = c.req("/api/tasks")
    assert data["tasks"] == [] and data["history"] == 0
    assert Path(done["outputs"][0]).exists()


def test_panel_plays_a_finished_download_in_the_player(panel_env):
    d, _ = panel_env
    c = _paired(d)
    assert c.req("/api/downloads/add", {"text": "https://fake.test/verlo", "preset": "video_360"})[1]["count"] == 1
    data = _wait_tasks(c, lambda ts: any(t["status"] == "done" for t in ts))
    task = data["tasks"][0]
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1", "--pause=yes"],
                  env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        status, res = c.req("/api/tasks/action", {"action": "play", "items": [{"type": "download", "id": task["id"]}]})
        assert status == 200 and res["target"] == task["outputs"][0]
        h.wait_property("path", lambda v: v == task["outputs"][0], timeout=20)
        assert not h.script_errors(), h.script_errors()
    finally:
        h.stop()


def test_mpvd_link_queues_downloads_sent_from_the_browser(panel_env):
    d, dl = panel_env
    link = handoff.download_link("https://fake.test/desde-el-navegador", "audio_mp3_128")
    r = d.cli("link", link, handoff.download_link("https://fake.test/otro"), "mpv-uos://download?url=file%3A%2F%2F%2Fx")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["ok"] and out["count"] == 2 and out["rejected"] == 1, r.stdout + r.stderr
    rows = d.call("ytdl.downloads.list")
    by_url = {row["url"]: row for row in rows}
    assert by_url["https://fake.test/desde-el-navegador"]["preset"] == "audio_mp3_128"
    assert by_url["https://fake.test/otro"]["preset"] == "video_best"
    # nothing valid: no daemon call, exit 1 with the reason
    r = d.cli("link", "--quiet", "mpv-uos://download?url=javascript%3Aalert(1)")
    assert r.returncode == 1 and "no válido" in json.loads(r.stdout)["error"]


def test_mpvd_link_starts_the_daemon_when_needed(daemon_env, media_dir):
    d = daemon_env
    d.extra_env.update({"MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_DELAY": "0.01",
                        "MPV_UOS_DOWNLOAD_DIR": str(d.base / "dl"), "MPV_UOS_YTDLP_AUTO_UPDATE": "0",
                        "MPV_UOS_NO_NOTIFY": "1", "MPVD_REMOTE_HOST": "127.0.0.1", "MPVD_REMOTE_PORT": "0"})
    assert not d.alive()
    r = d.cli("link", handoff.download_link("https://fake.test/arranque"))
    assert r.returncode == 0 and json.loads(r.stdout)["count"] == 1, r.stdout + r.stderr
    assert d.alive()
    d.wait(lambda: any(row["status"] == "done" for row in d.call("ytdl.downloads.list")), timeout=30)


def test_mu_remote_opens_the_panel_in_this_computers_browser(panel_env):
    d, _ = panel_env
    h = start_mpv(d.runtime_dir, ["--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,"
                                  "mu-remote-open_command=true"], env=d.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("script-message", "mu-remote-downloads")
        st = h.wait_property("user-data/mu/remote", lambda v: bool(v) and bool(v.get("downloads_url")), timeout=20)
        url = st["downloads_url"]
        assert url.startswith("http://127.0.0.1:") and "/downloads#t=" in url
        # the token in that URL pairs the browser (one time)
        c = RemoteClient(url.split("/downloads#")[0])
        assert c.req("/api/pair", {"token": url.split("#t=")[1]})[0] == 200
        assert c.req("/api/tasks")[0] == 200
        # and the menu lists the entry
        h.command("script-binding", "mu_remote/remote-menu")
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-remote", timeout=10)
        h.command("script-message-to", "uosc", "close-menu", "mu-remote")
        assert not h.script_errors(), h.script_errors()
    finally:
        h.stop()


@pytest.mark.skipif(os.name == "nt", reason="bin/mpv-uos es bash")
def test_launcher_snippet_hands_download_links_to_mpvd(project_root, tmp_path):
    """The bin/mpv-uos change proposed for H23 (bin/mpv-uos is shared): applied to a copy, a download link never
    reaches mpv and goes to ``mpvd link``; other links and files still open in the player."""
    src = (project_root / "bin" / "mpv-uos").read_text()
    if "mpv-uos://download" not in src:
        marker = "# Links from «Mis notas»"
        assert marker in src
        src = src.replace(marker, LAUNCHER_SNIPPET + marker, 1)
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "mpv-config").mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    launcher = root / "bin" / "mpv-uos"
    launcher.write_text(src)
    launcher.chmod(0o755)
    log = tmp_path / "calls.log"
    for name in ("python", "mpv"):
        fake = root / ".venv" / "bin" / name if name == "python" else tmp_path / "fake-mpv"
        fake.write_text(f'#!/bin/sh\nprintf "{name}" >> "{log}"; for a in "$@"; do printf " [%s]" "$a" >> "{log}"; done; '
                        f'echo >> "{log}"\n')
        fake.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "MPV_UOS_MPV": str(tmp_path / "fake-mpv"),
           "MPV_UOS_RUNTIME_DIR": str(tmp_path / "rt"), "MPV_UOS_DATA_DIR": str(tmp_path / "data")}
    import subprocess

    link = handoff.download_link("https://example.com/v?a=1&b=2")
    subprocess.run([str(launcher), "--player-operation-mode=pseudo-gui", "--", link], check=True, env=env)
    subprocess.run([str(launcher), "--", "/x.mkv"], check=True, env=env)
    calls = log.read_text().splitlines()
    assert calls[0] == f"python [-m] [mpvd] [link] [--root] [{root.resolve()}] [{link}]"
    assert calls[1].startswith("mpv ") and "[/x.mkv]" in calls[1] and "link" not in calls[1]


# Text proposed for bin/mpv-uos (shared file), right before the «Mis notas» block. Kept here so the test above checks
# exactly what the final report hands over.
LAUNCHER_SNIPPET = r'''# «Enviar a MPV-UOS» from the browser (H23): mpv-uos://download?url=<url>[&preset=<id>] queues the download in
# mpvd (started if needed) and shows a desktop notification; no player window opens.
dl_links=()
for a in "$@"; do case "$a" in mpv-uos://download\?*) dl_links+=("$a") ;; esac; done
if [ "${#dl_links[@]}" -gt 0 ]; then
  PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY=python3
  export MPV_UOS_ROOT="$ROOT" PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  exec "$PY" -m mpvd link --root "$ROOT" "${dl_links[@]}"
fi

'''


# -- a real browser (optional: Chrome/Chromium + node >= 22, like the share-room test) ------------------------------


def test_browser_panel_pairs_adds_and_warns_when_done(panel_env):
    from tests.test_share_browser import CHROME, Browser, _node_ok

    if not CHROME or not _node_ok():
        pytest.skip("sin Chrome/Chromium o node >= 22")
    d, _ = panel_env
    url = d.call("remote.pair", {"path": "/downloads", "local": True})["url"]
    base = url.split("/downloads#")[0]
    b = Browser()
    try:
        b.nav(url)
        b.wait("document.getElementById('preset').options.length > 5")
        assert b.js("document.getElementById('preset').value") == "video_best"
        assert b.js("location.hash") == ""   # the token leaves the address bar
        assert b.js("document.getElementById('mk-scheme').href").startswith("javascript:")
        assert "mpv-uos://download?url=" in b.js("decodeURIComponent(document.getElementById('mk-scheme').href)")
        assert f"{base}/downloads#add=" in b.js("decodeURIComponent(document.getElementById('mk-panel').href)")
        b.js("document.getElementById('preset').value = 'video_360'; "
             "document.getElementById('preset').dispatchEvent(new Event('change')); "
             "document.getElementById('links').value = 'https://fake.test/navegador'; "
             "document.getElementById('add').click(); true")
        b.wait("document.querySelectorAll('#active .task, #history .task').length === 1")
        b.wait("document.querySelector('#history .task.done button') !== null", timeout=40)
        notice = b.wait("document.getElementById('notice').textContent.includes('Terminada') && "
                        "document.getElementById('notice').textContent")
        assert "Fake Test Video" in notice
        assert b.js("localStorage.getItem('mu-dl-preset')") == "video_360"
        # select it and remove it from the history
        b.js("document.querySelector('#history .task').click(); true")
        assert b.js("document.getElementById('act-remove').disabled") is False
        b.js("document.getElementById('act-remove').click(); true")
        b.wait("document.querySelectorAll('.task').length === 0")
        # a link sent by the «Enviar al panel» bookmarklet is only prefilled: the user confirms
        b.nav(base + "/downloads#add=https%3A%2F%2Ffake.test%2Fotro")
        b.wait("document.getElementById('links').value === 'https://fake.test/otro'")
        time.sleep(1.5)
        assert d.call("tasks.list")["tasks"] == []
    finally:
        b.close()
