"""H23 · «Suscripciones» (mu-feeds) end to end: headless mpv + uosc + mpvd with the fake yt-dlp and podcast feeds served
over local HTTP (tests/fixtures/feeds). Opening the menu, adding by URL (detect → name → how many → subscribe), the rules
(quality, keep N, the chain after downloading, move to a folder), pause / resume from the row actions, the global
settings (window, limit, metered pause), deleting with confirmation, the entry in «Descargas y conversión», the
downloaded episodes with the state of their chain, and ⌫ / Esc."""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from tests.conftest import start_mpv
from tests.test_nav import press, wait_closed, wait_nav

FIX = Path(__file__).parent / "fixtures"
FAKE = FIX / "ytdlp" / "fake_ytdlp.py"
MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5"
ROOT = "MPV-UOS › Suscripciones"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture
def feed_server():
    handler = lambda *a, **kw: _Quiet(*a, directory=str(FIX / "feeds"), **kw)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _start(daemon_env, media_dir, metered: str):
    daemon_env.extra_env.update({
        "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_MEDIA": str(media_dir), "FAKE_YTDLP_DELAY": "0.01",
        "MPV_UOS_YTDLP_AUTO_UPDATE": "0", "MPV_UOS_DOWNLOAD_DIR": str(daemon_env.base / "dl"),
        "MPV_UOS_METERED": metered, "MPV_UOS_FEEDS_TICK": "3600",
    })
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=daemon_env.env)
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                    timeout=40)
    return h


@pytest.fixture
def metered_mpv(daemon_env, media_dir):
    """A metered connection: subscriptions check but never download (deterministic pending counts)."""
    h = _start(daemon_env, media_dir, "1")
    try:
        yield h, daemon_env
    finally:
        h.stop()


@pytest.fixture
def open_mpv(daemon_env, media_dir):
    h = _start(daemon_env, media_dir, "0")
    try:
        yield h, daemon_env
    finally:
        h.stop()


def ev(h, event: dict, message: str = "mu-feeds-event") -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_feeds", message, json.dumps({**base, **event}))


def act(h, value: dict, action: str | None = None) -> None:
    e = {"type": "activate", "index": 1, "value": value}
    if action:
        e["action"] = action
    ev(h, e)


def typed(h, text: str) -> None:
    """Type ``text`` in the open text box and press Enter on «Usar/Seguir: …»."""
    ev(h, {"type": "search", "query": text}, "mu-feeds-input-event")
    ev(h, {"type": "activate", "index": 1, "value": {"save": text}}, "mu-feeds-input-event")


def st(h, pred, timeout: float = 30.0) -> dict:
    return h.wait_property("user-data/mu/feeds", lambda v: bool(v) and pred(v), timeout=timeout)


def titles(v: dict) -> list[str]:
    return [i["title"] for i in v.get("items") or []]


def item(v: dict, title: str) -> dict:
    return next((i for i in v.get("items") or [] if i["title"] == title), {})


def hint(v: dict, title: str) -> str | None:
    return next((i["hint"] for i in v.get("items") or [] if i["title"] == title), None)


def add_by_url(h, url: str, crumbs: str) -> dict:
    act(h, {"add": True})
    st(h, lambda v: v.get("input") == "url")
    typed(h, url)
    wait_nav(h, "mu-feeds", crumbs + " › Añadir suscripción")
    return st(h, lambda v: v.get("view") == "add" and not v["add"]["busy"] and "Suscribirse" in titles(v))


def test_add_rules_pause_settings_and_delete(metered_mpv, feed_server, tmp_path):
    h, d = metered_mpv
    url = feed_server + "/npr_news_now.xml"

    h.command("script-binding", "mu_feeds/feeds-menu")
    nv = wait_nav(h, "mu-feeds", ROOT)
    assert nv["script"] == "mu_feeds" and nv["parent"] == "mu_menu"
    v = st(h, lambda v: v.get("view") == "root" and "Comprobar todas ahora" in titles(v))
    assert titles(v)[0] == "Añadir suscripción…" and v["subscriptions"] == []
    assert any(t.startswith("Aún no sigues nada") for t in titles(v))
    assert "Ajustes de suscripciones" in titles(v)

    # add by URL: detect → the podcast with its newest episodes → name and how many → subscribe
    v = add_by_url(h, url, ROOT)
    assert v["add"]["kind"] == "rss" and v["add"]["title"] == "NPR News Now" and v["add"]["initial"] == 3
    assert "Es un podcast" in titles(v) and "NPR News: 09-30-2026 7AM EDT" in titles(v)
    assert hint(v, "Al suscribirte, descargar") == "los 3 últimos"
    act(h, {"name_input": True})
    st(h, lambda v: v.get("input") == "name")
    typed(h, "Noticias NPR")
    v = st(h, lambda v: v.get("view") == "add" and hint(v, "Nombre") == "Noticias NPR")
    act(h, {"initial": True})
    st(h, lambda v: hint(v, "Al suscribirte, descargar") == "nada, solo lo que salga")
    act(h, {"initial": True})
    v = st(h, lambda v: hint(v, "Al suscribirte, descargar") == "el último")
    assert v["add"]["initial"] == 1
    act(h, {"subscribe": True})
    wait_nav(h, "mu-feeds", ROOT + " › Noticias NPR")
    v = st(h, lambda v: v.get("view") == "sub" and v["subscriptions"] and v["subscriptions"][0]["pending"] == 1)
    sub = v["subscriptions"][0]
    sid = sub["id"]
    assert sub["title"] == "Noticias NPR" and sub["kind"] == "rss" and v["last_action"] == "add:" + sid
    # the first check found the newest episode: announced once, and on a metered connection it only waits
    v = st(h, lambda v: v["last_notice"] == "Noticias NPR: 1 nuevo")
    g = d.call("feeds.get", {"id": sid})
    assert g["pending"] == 1 and g["initial"] == 1 and g["files"] == 0 and g["preset"] == "audio_original"
    assert {"Comprobar ahora", "Reglas", "Pendientes y descargados", "Pausar", "Borrar la suscripción"} <= set(titles(v))

    # rules: quality, keep N, the chain after downloading (a whole chain of its own), move to a folder
    act(h, {"view": "rules", "id": sid})
    wait_nav(h, "mu-feeds", ROOT + " › Noticias NPR › Reglas")
    v = st(h, lambda v: v.get("view") == "rules" and "Calidad" in titles(v))
    assert hint(v, "Calidad") == "Audio · original (sin recodificar)" and hint(v, "Solo audio") == "sí"
    assert "Saltar patrocinios (SponsorBlock)" not in titles(v)          # a podcast: nothing to skip
    assert hint(v, "Conservar") == "todo" and "Al quitar, solo lo ya visto" not in titles(v)
    assert hint(v, "Volumen igualado") == "no" and "Traducir los subtítulos" not in titles(v)
    act(h, {"rule": "keep", "id": sid})
    v = st(h, lambda v: hint(v, "Conservar") == "los 3 más nuevos")
    assert hint(v, "Al quitar, solo lo ya visto") == "sí"
    act(h, {"rule": "delete_watched", "id": sid})
    st(h, lambda v: hint(v, "Borrar lo que ya hayas visto") == "sí · tras 1 día")
    act(h, {"chain": "loudnorm", "id": sid})
    st(h, lambda v: hint(v, "Volumen igualado") == "sí")
    act(h, {"chain": "subtitles", "id": sid})
    st(h, lambda v: hint(v, "Traducir los subtítulos") == "no")
    act(h, {"chain": "translate", "id": sid})
    st(h, lambda v: hint(v, "Traducir los subtítulos") == "español")
    act(h, {"chain": "rename", "id": sid})
    st(h, lambda v: hint(v, "Renombrar") == "fecha - título")

    act(h, {"view": "quality", "id": sid})
    wait_nav(h, "mu-feeds", "MPV-UOS › … › Reglas › Calidad")   # long trails are shortened
    v = st(h, lambda v: v.get("view") == "quality" and "Audio · MP3 192 kbps" in titles(v))
    assert item(v, "Audio · original (sin recodificar, recomendado)")["active"] is True
    act(h, {"set_preset": "audio_mp3_192", "id": sid})
    wait_nav(h, "mu-feeds", ROOT + " › Noticias NPR › Reglas")
    st(h, lambda v: v.get("view") == "rules" and hint(v, "Calidad") == "Audio · MP3 192 kbps")

    lib = tmp_path / "Podcasts"
    act(h, {"view": "move", "id": sid})
    wait_nav(h, "mu-feeds", "MPV-UOS › … › Reglas › Mover a la biblioteca")
    v = st(h, lambda v: v.get("view") == "move" and "Otra carpeta…" in titles(v))
    assert item(v, "No mover")["active"] is True
    act(h, {"move_input": True, "id": sid})
    st(h, lambda v: v.get("input") == "move")
    typed(h, str(lib))
    wait_nav(h, "mu-feeds", ROOT + " › Noticias NPR › Reglas")
    st(h, lambda v: v.get("view") == "rules" and hint(v, "Mover a la biblioteca") == str(lib))
    g = d.call("feeds.get", {"id": sid})
    assert g["preset"] == "audio_mp3_192" and g["keep"] == 3 and g["keep_watched_only"] and g["delete_watched"]
    assert g["chain_custom"] and g["chain"] == {"loudnorm": True, "subtitles": True, "translate": "es",
                                                "rename": "{date} - {title}", "move_to": str(lib)}
    act(h, {"apply": sid})
    st(h, lambda v: v["last_action"] == "apply:" + sid)

    # ⌫ to the subscription; pause there, resume with the row action (Tab) of the list
    press(h, "BS")
    wait_nav(h, "mu-feeds", ROOT + " › Noticias NPR")
    act(h, {"pause": sid, "paused": True})
    v = st(h, lambda v: v["subscriptions"][0]["paused"] and "Reanudar" in titles(v))
    assert d.call("feeds.get", {"id": sid})["status"] == "paused"
    press(h, "BS")
    wait_nav(h, "mu-feeds", ROOT)
    v = st(h, lambda v: v.get("view") == "root" and (hint(v, "Noticias NPR") or "").startswith("podcast · en pausa"))
    assert item(v, "Noticias NPR")["actions"] == ["check", "resume", "remove"]
    act(h, {"view": "sub", "id": sid}, action="resume")
    v = st(h, lambda v: not v["subscriptions"][0]["paused"] and hint(v, "Noticias NPR") == "podcast · 1 pendiente")
    assert item(v, "Noticias NPR")["actions"] == ["check", "pause", "remove"]
    assert "En espera: conexión medida: en pausa" in titles(v)
    act(h, {"view": "sub", "id": sid}, action="check")
    st(h, lambda v: v["last_action"] == "check:" + sid)

    # settings: limit, window (a preset and a typed one; a wrong one is refused), metered pause, keep running
    act(h, {"view": "settings"})
    wait_nav(h, "mu-feeds", ROOT + " › Ajustes de suscripciones")
    v = st(h, lambda v: v.get("view") == "settings" and "Límite por franja" in titles(v))
    assert hint(v, "Pausar con conexión medida") == "sí · ahora: medida"
    assert hint(v, "Horario de descarga") == "siempre" and hint(v, "Límite por franja") == "sin límite"
    assert hint(v, "Comprobar cada") == "2 h"
    act(h, {"set": "max_items"})
    st(h, lambda v: hint(v, "Límite por franja") == "1 descarga")
    act(h, {"set": "max_mb"})
    st(h, lambda v: hint(v, "Límite de datos por franja") == "500 MB")
    act(h, {"set": "keep_running"})
    st(h, lambda v: hint(v, "Seguir comprobando con el reproductor cerrado") == "sí")
    act(h, {"set": "interval_h"})
    st(h, lambda v: hint(v, "Comprobar cada") == "4 h")
    act(h, {"view": "window"})
    wait_nav(h, "mu-feeds", ROOT + " › Ajustes de suscripciones › Horario de descarga")
    v = st(h, lambda v: v.get("view") == "window" and "De 1:00 a 7:00" in titles(v))
    assert item(v, "Siempre")["active"] is True
    act(h, {"window": "01:00-07:00"})
    wait_nav(h, "mu-feeds", ROOT + " › Ajustes de suscripciones")
    st(h, lambda v: v.get("view") == "settings" and hint(v, "Horario de descarga") == "de 1:00 a 7:00")
    act(h, {"view": "window"})
    st(h, lambda v: v.get("view") == "window" and item(v, "De 1:00 a 7:00").get("active") is True)
    act(h, {"window_input": True})
    st(h, lambda v: v.get("input") == "window")
    typed(h, "por la noche")
    st(h, lambda v: "franja no válida" in v["last_error"] and v.get("view") == "window")
    act(h, {"window_input": True})
    st(h, lambda v: v.get("input") == "window")
    typed(h, "de 23 a 6")
    wait_nav(h, "mu-feeds", ROOT + " › Ajustes de suscripciones")
    st(h, lambda v: v.get("view") == "settings" and hint(v, "Horario de descarga") == "de 23:00 a 6:00")
    s = d.call("feeds.settings.get")
    assert (s["window"], s["max_items"], s["max_mb"], s["keep_running"], s["interval_h"]) == \
        ("23:00-06:00", 1, 500, True, 4)
    act(h, {"set": "pause_metered"})
    st(h, lambda v: hint(v, "Pausar con conexión medida") == "no · ahora: medida")
    assert d.call("feeds.settings.get")["pause_metered"] is False

    # delete: asks first («No» goes back), then the subscription is gone (from the list and from mpvd)
    press(h, "BS")
    wait_nav(h, "mu-feeds", ROOT)
    act(h, {"view": "sub", "id": sid}, action="remove")
    wait_nav(h, "mu-feeds", ROOT + " › Borrar «Noticias NPR»")
    v = st(h, lambda v: v.get("view") == "remove")
    assert titles(v) == ["Sí, borrar la suscripción", "No, mantenerla"]
    act(h, {"nav_back": True})
    wait_nav(h, "mu-feeds", ROOT)
    act(h, {"view": "sub", "id": sid}, action="remove")
    st(h, lambda v: v.get("view") == "remove")
    act(h, {"remove": sid})
    wait_nav(h, "mu-feeds", ROOT)
    v = st(h, lambda v: v.get("view") == "root" and v["subscriptions"] == [] and "Noticias NPR" not in titles(v))
    assert v["last_action"] == "remove:" + sid and d.call("feeds.list")["subscriptions"] == []

    # ⌫ at the root: back to the main menu; Esc closes
    press(h, "BS")
    wait_nav(h, "mu-menu", "MPV-UOS")
    press(h, "ESC")
    wait_closed(h)
    h.command("script-binding", "mu_feeds/feeds-menu")
    wait_nav(h, "mu-feeds", ROOT)
    press(h, "ESC")
    wait_closed(h)
    st(h, lambda v: v.get("view") == "" and v.get("depth") == 0)
    assert h.script_errors() == [], h.script_errors()


def test_from_downloads_menu_downloads_with_the_default_chain(open_mpv, feed_server):
    h, d = open_mpv
    url = feed_server + "/rtve_180_grados.xml"
    crumbs = "MPV-UOS › Descargas y conversión › Suscripciones"

    # «Descargas y conversión» → «Suscripciones» (a child: ⌫ comes back to it)
    h.command("script-binding", "mu_ytdl/ytdl-menu")
    wait_nav(h, "mu-ytdl", "MPV-UOS › Descargas y conversión")
    yv = h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("view") == "root", timeout=10)
    row = next(i for i in yv["items"] if i["title"] == "Suscripciones")
    assert row["value"] == {"child": "feeds-menu", "script": "mu_feeds"}
    h.command("script-message-to", "mu_ytdl", "mu-ytdl-event", json.dumps(
        {"type": "activate", "index": 1, "menu_id": "{root}", "value": row["value"]}))
    wait_nav(h, "mu-feeds", crumbs)
    st(h, lambda v: v.get("view") == "root" and "Ajustes de suscripciones" in titles(v))

    # the default chain (for subscriptions without their own): rename the files
    act(h, {"view": "settings"})
    v = st(h, lambda v: v.get("view") == "settings" and "Tras descargar (para las nuevas)" in titles(v))
    assert hint(v, "Pausar con conexión medida") == "sí · ahora: normal"
    assert hint(v, "Tras descargar (para las nuevas)") == "nada"
    act(h, {"view": "chain_global"})
    wait_nav(h, "mu-feeds", "MPV-UOS › … › Ajustes de suscripciones › Tras descargar")
    st(h, lambda v: v.get("view") == "chain_global" and hint(v, "Renombrar") == "nombre original")
    act(h, {"chain": "rename", "global": True})
    st(h, lambda v: hint(v, "Renombrar") == "fecha - título")
    assert d.call("feeds.settings.get")["chain"]["rename"] == "{date} - {title}"
    press(h, "BS")
    st(h, lambda v: v.get("view") == "settings" and hint(v, "Tras descargar (para las nuevas)") == "nombre")
    press(h, "BS")
    wait_nav(h, "mu-feeds", crumbs)

    # a wrong address says why and offers another try
    act(h, {"add": True})
    st(h, lambda v: v.get("input") == "url")
    typed(h, "ftp://x")
    v = st(h, lambda v: v.get("view") == "add" and "Probar otra dirección…" in titles(v))
    assert "http" in v["add"]["error"]
    press(h, "BS")
    wait_nav(h, "mu-feeds", crumbs)

    # subscribe with the defaults: the 3 newest episodes download and go through the chain
    v = add_by_url(h, url, crumbs)
    assert v["add"]["title"] == "180 grados" and hint(v, "Al suscribirte, descargar") == "los 3 últimos"
    act(h, {"subscribe": True})
    wait_nav(h, "mu-feeds", crumbs + " › 180 grados")
    sid = st(h, lambda v: v.get("view") == "sub" and bool(v["subscriptions"]))["subscriptions"][0]["id"]
    d.wait(lambda: d.call("feeds.get", {"id": sid})["files"] == 3, timeout=60)
    g = d.call("feeds.get", {"id": sid})
    assert g["chain_custom"] is False and g["chain"]["rename"] == "{date} - {title}"
    assert all(Path(r["path"]).name.startswith("2026-09-") for r in g["file_records"])

    act(h, {"view": "files", "id": sid})
    wait_nav(h, "mu-feeds", "MPV-UOS › … › 180 grados › Pendientes y descargados")
    v = st(h, lambda v: v.get("view") == "files"
           and sum(1 for i in v["items"] if i["hint"] == "listo · 1 paso") == 3)
    rows = [i for i in v["items"] if i["hint"] == "listo · 1 paso"]
    assert rows[0]["value"]["play"] == g["file_records"][-1]["path"]      # newest first
    assert "Pendientes" not in titles(v)

    press(h, "BS")
    wait_nav(h, "mu-feeds", crumbs + " › 180 grados")
    v = st(h, lambda v: v.get("view") == "sub" and hint(v, "Pendientes y descargados") == "0 pendientes · 3 guardados")
    press(h, "BS")
    wait_nav(h, "mu-feeds", crumbs)
    act(h, {"check_all": True})
    st(h, lambda v: v["last_action"] == "check-all:1")
    press(h, "BS")
    wait_nav(h, "mu-ytdl", "MPV-UOS › Descargas y conversión")
    press(h, "ESC")
    wait_closed(h)
    assert h.script_errors() == [], h.script_errors()
