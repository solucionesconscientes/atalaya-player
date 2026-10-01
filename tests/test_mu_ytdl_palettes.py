"""mu-ytdl «Abrir URL» and «Buscar en YouTube» palettes end to end (headless mpv + real mpvd + the fake yt-dlp, no
network), the JS runtime handed to ytdl_hook before the very first yt-dlp run, and a real search + playback (@network)."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from tests.conftest import TMP, start_mpv
from tests.test_mu_iptv import serve

FAKE = Path(__file__).parent / "fixtures" / "ytdlp" / "fake_ytdlp.py"
CLIP = "https://fake.test/pasted"
BASE_OPTS = "mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


def media_server(media_dir: Path):
    # ytdl_hook only accepts real URLs as streams, so the media the fake "extracts" is served over HTTP
    return serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})


def fake_env(media_dir: Path, arglog: Path, httpd) -> dict[str, str]:
    return {"MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_ARGLOG": str(arglog), "FAKE_YTDLP_MEDIA": str(media_dir),
            "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{httpd.server_address[1]}", "MPV_UOS_YTDLP_AUTO_UPDATE": "0"}


def argv_lines(arglog: Path) -> list[list[str]]:
    if not arglog.exists():
        return []
    return [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()]


def send(h, message: str, ev: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_ytdl", message, json.dumps({**base, **ev}))


def url_event(h, ev):
    send(h, "mu-ytdl-url-event", ev)


def search_event(h, ev):
    send(h, "mu-ytdl-search-event", ev)


def st(h, pred, timeout=20.0):
    return h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and pred(v), timeout=timeout)


def menu_type(h, t, timeout=15.0):
    return h.wait_property("user-data/uosc/menu/type", lambda v: v == t, timeout=timeout)


def titles(v):
    return [i["title"] for i in v.get("items", [])]


def wait_keys(h, present: bool, timeout: float = 10.0) -> None:
    """uosc binds its type-to-search keys a moment after a menu appears (the first render loads the fonts) and drops
    them when it closes: wait for that before simulating typing, or the keys reach input.conf (`a` = audio menu)."""
    h.wait_property("input-bindings", lambda bs: isinstance(bs, list) and present == any(
        "menu-any_unicode" in (b.get("cmd") or "") for b in bs), timeout=timeout)


# -- Bug A: js-runtimes before the first yt-dlp run -------------------------------------------------


def test_first_ytdl_run_already_has_js_runtime(media_dir):
    """`mpv-uos <url>`: the first -J must carry --js-runtimes (yt-dlp only enables deno by default) even though
    mpvd answers later — here it never answers (autostart off). A value set by the user is left alone."""
    for user_value in (None, "deno"):
        run_dir = TMP / "test-ytdl-js" / uuid.uuid4().hex[:8]
        arglog = run_dir / "argv.log"
        httpd = media_server(media_dir)
        args = [f"--script-opts=mu-core-autostart=no,mu-ytdl-ytdl_path={FAKE}", "--pause=yes"]
        if user_value:
            args.append(f"--ytdl-raw-options=js-runtimes={user_value}")
        h = start_mpv(run_dir, [*args, "https://fake.test/first"], env=fake_env(media_dir, arglog, httpd))
        try:
            st(h, lambda v: v.get("active") is True, timeout=40)
            first = [a for a in argv_lines(arglog) if "-J" in a][0]
            expected = user_value or "node"
            assert first[first.index("--js-runtimes") + 1] == expected, first
            assert h.get("ytdl-raw-options")["js-runtimes"] == expected
            assert h.script_errors() == []
        finally:
            h.stop()
            httpd.shutdown()
            if not os.environ.get("MU_KEEP_LOGS"):
                shutil.rmtree(run_dir, ignore_errors=True)


# -- palettes ---------------------------------------------------------------------------------------


@pytest.fixture
def pal_mpv(daemon_env, media_dir, tmp_path):
    arglog = tmp_path / "argv.log"
    httpd = media_server(media_dir)
    env = {**daemon_env.env, **fake_env(media_dir, arglog, httpd), "FAKE_YTDLP_DELAY": "0.02",
           "FAKE_YTDLP_STEPS": "4", "FAKE_YTDLP_SEARCH_DELAY": "0.8", "MPV_UOS_DOWNLOAD_DIR": str(tmp_path / "dl")}
    h = start_mpv(daemon_env.runtime_dir, [
        f"--script-opts={BASE_OPTS},mu-ytdl-ytdl_path={FAKE},mu-ytdl-clipboard_text={CLIP}",
        "--clipboard-backends-clr", "--keep-open=no", "--pause=yes",
    ], env=env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env, arglog
    finally:
        h.stop()
        httpd.shutdown()


def test_open_url_palette(pal_mpv):
    h, d, arglog = pal_mpv
    # mpvd refines the early js-runtimes=node with the exact runtime it found (if any)
    hook = d.call("ytdl.hook")
    want = hook["raw_options"].get("js-runtimes", "node")
    h.wait_property("ytdl-raw-options", lambda v: bool(v) and v.get("js-runtimes") == want, timeout=15)

    # clipboard URL first ("Pegar: …"), with the item actions
    h.command("script-binding", "mu_ytdl/open-url")
    menu_type(h, "mu-ytdl-url")
    v = st(h, lambda v: v.get("view") == "open_url")
    assert titles(v) == ["Pegar: " + CLIP]
    assert v["items"][0]["value"] == {"open": CLIP} and v["items"][0]["actions"] == ["append", "download"]

    # what is typed turns into «Buscar …» or «Abrir …» (the clipboard stays as second option)
    url_event(h, {"type": "search", "query": "hola mundo"})
    v = st(h, lambda v: titles(v)[:1] == ["Buscar «hola mundo» en YouTube"])
    assert v["items"][0]["value"] == {"yt_search": "hola mundo"} and titles(v)[1] == "Pegar: " + CLIP
    url_event(h, {"type": "search", "query": "youtu.be/abc123"})
    st(h, lambda v: titles(v)[:1] == ["Abrir https://youtu.be/abc123"])
    url_event(h, {"type": "search", "query": "  rtsp://cam.local:554/live  "})
    st(h, lambda v: titles(v)[:1] == ["Abrir rtsp://cam.local:554/live"])
    url_event(h, {"type": "search", "query": CLIP})
    v = st(h, lambda v: titles(v) == ["Abrir " + CLIP])  # no duplicate «Pegar» for the same URL
    url_event(h, {"type": "search", "query": ""})
    st(h, lambda v: titles(v) == ["Pegar: " + CLIP])

    # Enter on a URL: loadfile replace, palette closed
    url_event(h, {"type": "activate", "index": 1, "value": {"open": "https://fake.test/clip"}})
    v = st(h, lambda v: v.get("active") is True and v.get("url") == "https://fake.test/clip", timeout=40)
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)
    st(h, lambda v: v.get("view") == "", timeout=10)

    # Tab → «Añadir a la lista» on the clipboard item: append-play, the palette stays open
    h.command("script-binding", "mu_ytdl/open-url")
    menu_type(h, "mu-ytdl-url")
    url_event(h, {"type": "activate", "index": 1, "action": "append", "value": {"open": CLIP}})
    h.wait_property("playlist", lambda pl: isinstance(pl, list) and len(pl) == 2, timeout=10)
    assert h.get("playlist")[1]["filename"] == CLIP and h.get("path") == "https://fake.test/clip"
    assert h.get("user-data/uosc/menu/type") == "mu-ytdl-url"

    # text → YouTube search palette, submitted right away; «back» returns to the URL palette with the text
    url_event(h, {"type": "activate", "index": 1, "value": {"yt_search": "fake cats"}})
    menu_type(h, "mu-ytdl-search")
    st(h, lambda v: v.get("view") == "yt_search" and v.get("search_status") == "loading", timeout=10)
    v = st(h, lambda v: v.get("search_status") == "done", timeout=20)
    assert v["search_query"] == "fake cats" and v["search_results"] == 3
    search_event(h, {"type": "back"})
    menu_type(h, "mu-ytdl-url")
    st(h, lambda v: v.get("view") == "open_url" and titles(v)[:1] == ["Buscar «fake cats» en YouTube"])
    url_event(h, {"type": "back"})  # ⌫ on an empty palette with nothing below: closes
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)

    # real keys through uosc (instant updates): type a URL, Enter opens it
    wait_keys(h, False)
    h.command("script-binding", "mu_ytdl/open-url")
    menu_type(h, "mu-ytdl-url")
    wait_keys(h, True)
    for ch in "https://fake.test/typed":  # ':', '/' and '.' reach uosc's any_unicode binding too
        h.command("keypress", ch)
    st(h, lambda v: titles(v)[:1] == ["Abrir https://fake.test/typed"])
    h.command("keypress", "ENTER")
    st(h, lambda v: v.get("active") is True and v.get("url") == "https://fake.test/typed", timeout=40)
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)
    assert h.script_errors() == [], h.script_errors()


def test_youtube_search_palette(pal_mpv):
    h, d, arglog = pal_mpv

    # H42: la raíz tiene UNA puerta; las dos paletas siguen existiendo (teclas y dentro de la caja)
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    v = st(h, lambda v: v.get("view") == "root" and bool(v.get("items")))
    assert titles(v)[0] == "Abrir o descargar…"
    assert "Abrir URL…" not in titles(v) and "Buscar en YouTube…" not in titles(v)
    send(h, "mu-ytdl-event", {"type": "activate", "index": 2, "value": {"view": "yt_search"}})
    menu_type(h, "mu-ytdl-search")
    v = st(h, lambda v: v.get("view") == "yt_search")
    assert v["search_status"] == "idle" and titles(v) == ["Escribe y pulsa Enter para buscar en YouTube"]
    search_event(h, {"type": "back"})
    menu_type(h, "mu-ytdl")
    st(h, lambda v: v.get("view") == "root")
    h.command("script-message-to", "uosc", "close-menu", "mu-ytdl")
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)
    wait_keys(h, False)

    # real keys through uosc: typing deselects, Enter submits (search_debounce='submit'), Enter again plays
    h.command("script-binding", "mu_ytdl/yt-search")
    menu_type(h, "mu-ytdl-search")
    wait_keys(h, True)
    for key in ("f", "a", "k", "e", "SPACE", "d", "o", "g", "s"):
        h.command("keypress", key)
    n = len([a for a in argv_lines(arglog) if a and a[-1].startswith("ytsearch")])
    h.command("keypress", "ENTER")
    v = st(h, lambda v: v.get("search_status") == "done", timeout=20)
    assert v["search_query"] == "fake dogs" and v["search_results"] == 3
    runs = [a for a in argv_lines(arglog) if a and a[-1].startswith("ytsearch")]
    assert len(runs) == n + 1 and runs[-1][-1] == "ytsearch15:fake dogs"
    assert titles(v) == ["Fake Result A", "Fake Live B", "Fake Result C"]
    assert [i["hint"] for i in v["items"]] == ["0:30 · MPV-UOS tests", "EN DIRECTO · MPV-UOS live",
                                               "1:02:05 · Uploader C"]
    assert [i["icon"] for i in v["items"]] == ["smart_display", "live_tv", "smart_display"]
    assert v["items"][0]["value"] == {"result": {"url": "https://fake.test/a", "title": "Fake Result A"}}
    h.command("keypress", "ENTER")  # the first result is selected once the results arrive
    st(h, lambda v: v.get("active") is True and v.get("url") == "https://fake.test/a", timeout=40)
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)

    # a query from another script (mu-menu / remote) is submitted at once
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-search", "fake birds")
    menu_type(h, "mu-ytdl-search")
    v = st(h, lambda v: v.get("search_status") == "done" and v.get("search_query") == "fake birds", timeout=20)

    # item actions: «Añadir a la lista» keeps the palette; «Descargar» opens the download menu for that result
    c_item = {"result": {"url": "https://fake.test/c", "title": "Fake Result C"}}
    search_event(h, {"type": "activate", "index": 3, "action": "append", "value": c_item})
    h.wait_property("playlist", lambda pl: isinstance(pl, list) and any(e["filename"] == "https://fake.test/c"
                                                                          for e in pl), timeout=10)
    assert h.get("user-data/uosc/menu/type") == "mu-ytdl-search"
    search_event(h, {"type": "activate", "index": 3, "action": "download", "value": c_item})
    menu_type(h, "mu-ytdl")
    v = st(h, lambda v: v.get("view") == "download" and any(i["title"] == "Opciones" for i in v.get("items", [])))
    assert [i["title"] for i in v["items"]] == ["Vídeo", "Audio", "Solo subtítulos (SRT)", "Opciones"]
    # back to the same results without searching again
    n = len(argv_lines(arglog))
    send(h, "mu-ytdl-event", {"type": "back"})
    menu_type(h, "mu-ytdl-search")
    v = st(h, lambda v: v.get("view") == "yt_search" and v.get("search_status") == "done")
    assert v["search_query"] == "fake birds" and titles(v)[0] == "Fake Result A" and len(argv_lines(arglog)) == n
    search_event(h, {"type": "activate", "index": 3, "action": "download", "value": c_item})
    st(h, lambda v: v.get("view") == "download" and any(i["title"] == "Opciones" for i in v.get("items", [])))
    send(h, "mu-ytdl-event", {"type": "activate", "index": 2, "value": {
        "preset": "audio_mp3_128", "url": "https://fake.test/c", "title": "Fake Result C"}})
    d.wait(lambda: any(r["url"] == "https://fake.test/c" and r["status"] == "done"
                       for r in d.call("ytdl.downloads.list")), timeout=60)
    row = next(r for r in d.call("ytdl.downloads.list") if r["url"] == "https://fake.test/c")
    assert row["preset"] == "audio_mp3_128" and row["title"] == "Fake Result C" and Path(row["outputs"][0]).is_file()
    assert h.get("path") == "https://fake.test/a"  # downloading a result does not touch what is playing

    # a URL typed in the search palette is offered as «Abrir»; errors and empty queries are shown in place
    h.command("script-binding", "mu_ytdl/yt-search")
    menu_type(h, "mu-ytdl-search")
    search_event(h, {"type": "search", "query": "https://fake.test/x"})
    v = st(h, lambda v: v.get("search_status") == "url")
    assert titles(v) == ["Abrir https://fake.test/x"] and v["items"][0]["value"] == {"open": "https://fake.test/x"}
    search_event(h, {"type": "search", "query": "fail please"})
    v = st(h, lambda v: v.get("search_status") == "error", timeout=20)
    assert titles(v)[0].startswith("No se pudo buscar:") and "Unsupported URL" in titles(v)[0]
    search_event(h, {"type": "search", "query": "  "})
    st(h, lambda v: v.get("search_status") == "idle")
    h.command("script-message-to", "uosc", "close-menu")
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)
    st(h, lambda v: v.get("view") == "", timeout=10)
    assert h.script_errors() == [], h.script_errors()


# -- real YouTube -------------------------------------------------------------------------------------


@pytest.mark.network
def test_real_search_and_play_first_result(daemon_env):
    h = start_mpv(daemon_env.runtime_dir, [f"--script-opts={BASE_OPTS}", "--pause=yes", "--keep-open=no"],
                  env={**daemon_env.env, "MPV_UOS_YTDLP_AUTO_UPDATE": "0"})
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("script-message-to", "mu_ytdl", "mu-ytdl-search", "big buck bunny blender")
        menu_type(h, "mu-ytdl-search")
        v = st(h, lambda v: v.get("search_status") in ("done", "error"), timeout=60)
        assert v["search_status"] == "done" and v["search_results"] >= 1, v
        first = v["items"][0]
        url = first["value"]["result"]["url"]
        assert url.startswith("https://www.youtube.com/watch?v=") and first["hint"]
        search_event(h, {"type": "activate", "index": 1, "value": first["value"]})
        v = st(h, lambda v: v.get("active") is True and v.get("url") == url, timeout=120)
        assert v["title"] and h.get("user-data/mpv/ytdl/json-subprocess-result")["status"] == 0
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
