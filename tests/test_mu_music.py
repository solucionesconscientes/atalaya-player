"""H32 · «Música» end to end (headless mpv + mpvd): the XDG Music folder found and scanned on first open, Artistas ›
álbum › pistas, play from a track, «Reproducir a continuación» (right after the current entry) and «Añadir a la cola»,
the Cola view (move and remove), a list created from «Añadir a una lista…», Ajustes (sin cortes, volumen igualado →
replaygain + the gain computed by mpvd applied as file-local replaygain-fallback, fundido through volume-gain) and the
local history."""

from __future__ import annotations

import json
import math
import shutil

import pytest

from tests.conftest import APP, start_mpv
from tests.music_helpers import build_music
from tests.test_nav import wait_nav

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"


@pytest.fixture(scope="module")
def music_src(tmp_path_factory):
    root = tmp_path_factory.mktemp("mu-music-src")
    return root / "Música", build_music(root / "Música")


@pytest.fixture
def music_mpv(daemon_env, music_src, tmp_path):
    src, files = music_src
    dest = tmp_path / "Música"
    shutil.copytree(src, dest)
    p = {k: dest / v.relative_to(src) for k, v in files.items()}
    daemon_env.extra_env["MPV_UOS_MUSIC_DIR"] = str(dest)
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--loop-file=inf"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env, dest, p
    finally:
        h.stop()


def ev(h, event: dict, message: str = "mu-music-event") -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_music", message, json.dumps({**base, **event}))


def st(h, pred, timeout: float = 20.0):
    return h.wait_property("user-data/mu/music", lambda v: bool(v) and pred(v), timeout=timeout)


def titles(v):
    return [i["title"] for i in v.get("items", [])]


def row(v, title):
    i = titles(v).index(title)
    return i + 2, v["items"][i]        # menu index (the «Atrás» row is 1) and the item


def hint(v, title):
    return row(v, title)[1]["hint"] if title in titles(v) else None


def activate(h, v, title, action=None):
    # H63 · la vista se publica sin filas y las de verdad llegan detrás: si la fila aún no está, se espera a que
    # llegue en vez de reventar con la lista de la vista anterior
    if title not in titles(v):
        v = st(h, lambda s: title in titles(s))
    idx, it = row(v, title)
    e = {"type": "activate", "index": idx, "value": it["value"]}
    if action:
        e["action"] = action
    ev(h, e)


def playlist(h):
    return [e["filename"] for e in h.get("playlist")]


def test_music_menu_browse_play_next_queue_lists_gain_fade_history(music_mpv):
    h, d, root, p = music_mpv

    # first open: the XDG Music folder is added and scanned in the background
    h.command("script-binding", "mu_music/music-menu")
    wait_nav(h, "mu-music", f"{APP} › Música")
    d.wait(lambda: d.call("music.status", {"default_folder": False})["tracks"] == 5
           and not d.call("music.status", {"default_folder": False})["scanning"], timeout=60)
    assert [f["path"] for f in d.call("music.folders.list")] == [str(root)]
    ev(h, {"type": "back"})
    h.wait_property("user-data/uosc/menu/type", lambda t: t != "mu-music", timeout=10)
    h.command("script-binding", "mu_music/music-menu")
    s = st(h, lambda v: v["view"] == "root" and "Artistas" in titles(v) and v["counts"].get("tracks") == 5)
    assert row(s, "Artistas")[1]["hint"] == "4" and row(s, "Álbumes")[1]["hint"] == "3"

    # Artistas › Mónica Naranjo › Palabra de mujer › Enter on track 2 plays the album from there
    activate(h, s, "Artistas")
    s = st(h, lambda v: v["view"] == "artists" and "Mónica Naranjo" in titles(v))
    assert set(row(s, "Mónica Naranjo")[1]["actions"]) == {"play", "next", "queue", "list"}
    activate(h, s, "Mónica Naranjo")
    s = st(h, lambda v: v["view"] == "artist" and "Palabra de mujer" in titles(v))
    activate(h, s, "Palabra de mujer")
    s = st(h, lambda v: v["view"] == "album" and "2. Desátame" in titles(v))
    assert h.get("user-data/mu/nav")["title"] == f"{APP} › … › Mónica Naranjo › Palabra de mujer"   # long trails shortened
    activate(h, s, "2. Desátame")
    h.wait_property("path", lambda v: v == str(p["t2"]), timeout=15)
    assert playlist(h) == [str(p["t1"]), str(p["t2"])] and h.get("playlist-pos") == 1
    h.wait_property("user-data/uosc/menu/type", lambda t: not t, timeout=10)

    # «Reproducir a continuación» (album Senderos) goes right after the current track; «Añadir a la cola» at the end
    h.command("set_property", "pause", True)
    h.command("script-binding", "mu_music/music-menu")
    s = st(h, lambda v: v["view"] == "root" and "Álbumes" in titles(v))
    activate(h, s, "Álbumes")
    s = st(h, lambda v: v["view"] == "albums" and "Senderos" in titles(v))
    activate(h, s, "Senderos", "next")
    h.wait_property("playlist", lambda v: len(v) == 3, timeout=10)
    assert playlist(h) == [str(p["t1"]), str(p["t2"]), str(p["emb"])]
    activate(h, s, "Con ganancia", "queue")
    h.wait_property("playlist", lambda v: len(v) == 4, timeout=10)
    activate(h, s, "Palabra de mujer", "next")      # two tracks, in album order, before Senderos
    h.wait_property("playlist", lambda v: len(v) == 6, timeout=10)
    assert playlist(h) == [str(p["t1"]), str(p["t2"]), str(p["t1"]), str(p["t2"]), str(p["emb"]), str(p["tagged"])]
    assert h.get("path") == str(p["t2"])

    # Cola: what follows the current track; ctrl+↓ (move event) and Tab › Quitar
    ev(h, {"type": "back"})
    s = st(h, lambda v: v["view"] == "root" and hint(v, "Cola") == "4 pistas")
    activate(h, s, "Cola")
    s = st(h, lambda v: v["view"] == "queue" and "Entre dos tierras" in titles(v))
    assert titles(s)[:5] == ["Desátame", "Sobreviviré", "Desátame", "Entre dos tierras", "Etiquetada"]
    first = row(s, "Sobreviviré")[0]
    ev(h, {"type": "move", "from_index": first, "to_index": first + 2})     # Sobreviviré after Entre dos tierras
    s = st(h, lambda v: v["view"] == "queue" and titles(v)[1:5] == ["Desátame", "Entre dos tierras", "Sobreviviré",
                                                                   "Etiquetada"])
    assert playlist(h)[2:] == [str(p["t2"]), str(p["emb"]), str(p["t1"]), str(p["tagged"])]
    idx, it = row(s, "Etiquetada")
    ev(h, {"type": "activate", "index": idx, "value": it["value"], "action": "remove"})
    h.wait_property("playlist", lambda v: len(v) == 5, timeout=10)
    s = st(h, lambda v: v["view"] == "queue" and "Etiquetada" not in titles(v) and "Sobreviviré" in titles(v))
    idx, it = row(s, "Entre dos tierras")
    ev(h, {"type": "activate", "index": idx, "value": it["value"], "action": "up"})
    s = st(h, lambda v: v["view"] == "queue" and titles(v)[1:4] == ["Entre dos tierras", "Desátame", "Sobreviviré"])

    # «Añadir a una lista…» → «Nueva lista…» → typed name: the list is saved as M3U8 with that track
    ev(h, {"type": "back"})
    s = st(h, lambda v: v["view"] == "root" and "Álbumes" in titles(v))
    activate(h, s, "Álbumes")
    s = st(h, lambda v: v["view"] == "albums" and "Senderos" in titles(v))
    activate(h, s, "Senderos", "list")
    s = st(h, lambda v: v["view"] == "pick" and v["pick"] == 1 and "Nueva lista…" in titles(v))
    activate(h, s, "Nueva lista…")
    h.wait_property("user-data/uosc/menu/type", lambda t: t == "mu-music-input", timeout=10)
    ev(h, {"type": "search", "query": "Rock español"}, "mu-music-input-event")
    ev(h, {"type": "activate", "index": 1, "value": {"save": "Rock español"}}, "mu-music-input-event")
    d.wait(lambda: [x["name"] for x in d.call("music.playlists.list")] == ["Rock español"], timeout=15)
    pl = d.call("music.playlists.get", {"name": "Rock español"})
    assert [e["path"] for e in pl["tracks"]] == [str(p["emb"])]
    s = st(h, lambda v: v["view"] == "albums" and v["pick"] == 0)

    # Ajustes: sin cortes → gapless-audio=yes; volumen igualado: no → por pista → por álbum
    ev(h, {"type": "back"})
    s = st(h, lambda v: v["view"] == "root" and "Ajustes" in titles(v))
    activate(h, s, "Ajustes")
    s = st(h, lambda v: v["view"] == "settings" and "Volumen igualado" in titles(v))
    activate(h, s, "Sin cortes entre pistas")
    h.wait_property("gapless-audio", lambda v: v in (True, "yes"), timeout=10)   # choice with yes/no: a native flag
    assert h.get("prefetch-playlist") is True
    activate(h, s, "Volumen igualado")
    h.wait_property("replaygain", lambda v: v == "track", timeout=10)
    s = st(h, lambda v: v["view"] == "settings" and hint(v, "Volumen igualado") == "por pista")

    # the current track (no tags): mpvd's computed gain → file-local replaygain-fallback
    g = d.call("music.gain", {"path": str(p["t2"])})
    assert g["gain"] is not None
    s = st(h, lambda v: v["gain"]["source"] == "computed" and v["gain"]["path"] == str(p["t2"]))
    assert abs(s["gain"]["db"] - g["gain"]) < 0.011
    assert abs(h.get("file-local-options/replaygain-fallback") - g["gain"]) < 0.011
    activate(h, s, "Volumen igualado")
    h.wait_property("replaygain", lambda v: v == "album", timeout=10)
    s = st(h, lambda v: v["replaygain"] == "album" and v["gain"]["source"] == "computed"
           and abs(v["gain"]["db"] - g["album_gain"]) < 0.011)
    # a file with ReplayGain tags is left to mpv
    h.command("loadfile", str(p["tagged"]), "replace")
    st(h, lambda v: v["gain"]["source"] == "tags" and v["gain"]["path"] == str(p["tagged"]))
    assert "Applying replay-gain" in h.log_text()

    # fundido 2 s: the next track starts faded (volume-gain < 0) and comes back to the user's level
    activate(h, s, "Fundido entre pistas")
    st(h, lambda v: v["fade"] == 2)
    h.command("loadfile", str(p["t1"]), "replace")
    h.command("set_property", "pause", False)
    st(h, lambda v: v["fading"] == "in", timeout=10)
    h.wait_property("volume-gain", lambda v: isinstance(v, (int, float)) and v < -1, timeout=5)
    h.wait_property("volume-gain", lambda v: isinstance(v, (int, float)) and math.isclose(v, 0, abs_tol=1e-6),
                    timeout=10)

    # history: half of the 4 s track played → one local listen
    st(h, lambda v: v["listened"] is True, timeout=15)
    d.wait(lambda: any(x["path"] == str(p["t1"]) for x in d.call("music.history")), timeout=10)
    assert h.script_errors() == [], h.script_errors()


def test_a_missing_file_in_the_album_does_not_shift_what_enter_plays(music_mpv):
    """H34 · `start` era el número de fila, y las pistas que faltan no están en la lista de rutas: Enter sonaba otra."""
    h, d, root, p = music_mpv

    h.command("script-binding", "mu_music/music-menu")
    wait_nav(h, "mu-music", f"{APP} › Música")
    d.wait(lambda: d.call("music.status", {"default_folder": False})["tracks"] == 5
           and not d.call("music.status", {"default_folder": False})["scanning"], timeout=60)

    # the first track of the album is gone (a disconnected disk, a file moved outside the player): it is still in the
    # index until the next scan, listed with exists=false
    p["t1"].unlink()
    key = next(a["key"] for a in d.call("music.albums") if a["title"] == "Palabra de mujer")
    album = d.call("music.album", {"key": key})
    assert [t.get("exists") for t in album["tracks"]] == [False, True]

    ev(h, {"type": "back"})
    h.wait_property("user-data/uosc/menu/type", lambda t: t != "mu-music", timeout=10)
    h.command("script-binding", "mu_music/music-menu")
    s = st(h, lambda v: v["view"] == "root" and "Álbumes" in titles(v))
    activate(h, s, "Álbumes")
    s = st(h, lambda v: v["view"] == "albums" and "Palabra de mujer" in titles(v))
    activate(h, s, "Palabra de mujer")
    s = st(h, lambda v: v["view"] == "album" and any("Desátame" in t for t in titles(v)))
    activate(h, s, next(t for t in titles(s) if "Desátame" in t))

    # Desátame is row 2 but the only playable track: it must play, not «the second of one path» clamped to the first
    h.wait_property("path", lambda v: v == str(p["t2"]), timeout=15)
    assert playlist(h) == [str(p["t2"])] and h.get("playlist-pos") == 0
    assert h.script_errors() == [], h.script_errors()


def test_moving_in_the_queue_uses_the_track_index_not_the_menu_row(music_mpv):
    """H34 · uosc manda el número de fila del menú que dibuja, y el buscador lo filtra: con la búsqueda activa, Tab ›
    Subir movía otra entrada (o ninguna). La fila lleva su índice real, que es el que vale."""
    h, d, root, p = music_mpv
    h.command("script-binding", "mu_music/music-menu")
    wait_nav(h, "mu-music", f"{APP} › Música")
    d.wait(lambda: d.call("music.status", {"default_folder": False})["tracks"] == 5
           and not d.call("music.status", {"default_folder": False})["scanning"], timeout=60)

    for path in (p["t1"], p["t2"], p["emb"]):
        h.command("loadfile", str(path), "append-play")
    h.wait_property("playlist", lambda v: len(v) >= 3, timeout=20)
    h.command("set_property", "pause", True)

    ev(h, {"type": "back"})
    h.wait_property("user-data/uosc/menu/type", lambda t: t != "mu-music", timeout=10)
    h.command("script-binding", "mu_music/music-menu")
    s = st(h, lambda v: v["view"] == "root" and "Cola" in titles(v))
    activate(h, s, "Cola")
    s = st(h, lambda v: v["view"] == "queue"
           and len([i for i in v["items"] if isinstance(i.get("value"), dict) and "qplay" in i["value"]]) >= 2)

    rows = [i for i in s["items"] if isinstance(i.get("value"), dict) and "qplay" in i["value"]]
    target = rows[-1]["value"]["qplay"]
    before = playlist(h)
    # the row number is deliberately wrong (as it is when the search box filtered the list); the value is right
    ev(h, {"type": "activate", "index": 999, "value": rows[-1]["value"], "action": "up"})
    h.wait_property("playlist", lambda v: [e["filename"] for e in v][target - 1] == before[target], timeout=20)
    after = playlist(h)
    assert after[target - 1] == before[target] and after[target] == before[target - 1]
    assert sorted(after) == sorted(before)
    assert h.script_errors() == [], h.script_errors()
