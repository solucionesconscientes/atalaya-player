"""H45 · el resumen y el índice en los vídeos de internet.

Un vídeo de internet no trae ninguna pista de subtítulos cargada, así que mu-recap se quedaba sin material y «¿Qué me
he perdido?» no aparecía nunca. Ahora, al pulsar, se piden los que ofrece la web (solo los idiomas NATIVOS del vídeo:
las traducciones automáticas de YouTube responden 429) y de ahí sale el resumen. Y «Resumen e índice» se encuentra:
está en la raíz del menú y dentro del panel de subtítulos."""

from __future__ import annotations

import json

from tests.conftest import start_mpv
from tests.test_mu_iptv import serve
from tests.test_mu_ytdl import FAKE
from tests.test_subs_web import serve_subs

URL = "https://fake.test/websubs"
MU_OPTS = ("--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=5,"
           f"mu-ytdl-ytdl_path={FAKE}")


def ev(h, script: str, event: dict) -> None:
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", script, f"{script.replace('_', '-')}-event", json.dumps({**base, **event}))


def titles(v) -> list[str]:
    return [i["title"] for i in v.get("items") or []]


def recap_state(h, pred, timeout: float = 40.0):
    return h.wait_property("user-data/mu/recap", lambda v: bool(v) and pred(v), timeout=timeout)


def web_mpv(daemon_env, media_dir):
    httpd_media = serve({"/" + p.name: p.read_bytes() for p in media_dir.iterdir() if p.suffix in (".mkv", ".flac")})
    httpd, base = serve_subs()
    env = {**daemon_env.env, "MPV_UOS_YTDLP": str(FAKE), "FAKE_YTDLP_SUBS_URL": base,
           "FAKE_YTDLP_MEDIA": str(media_dir),
           "FAKE_YTDLP_MEDIA_URL": f"http://127.0.0.1:{httpd_media.server_address[1]}",
           "MPV_UOS_YTDLP_AUTO_UPDATE": "0"}
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes"], env=env)
    return h, httpd, httpd_media


def test_el_resumen_usa_los_subtitulos_de_la_web(daemon_env, media_dir):
    """D1/D2 · sin ninguna pista cargada, al pulsar se piden los de la web y el resumen sale de ahí. Se elige el
    castellano porque lo hay (manual); la pista en inglés de la misma web no se usa, y no se le pide a YouTube
    ninguna traducción automática (devuelve 429)."""
    h, httpd, httpd_media = web_mpv(daemon_env, media_dir)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        h.command("loadfile", URL)
        h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and v.get("active") is True and v.get("url") == URL,
                        timeout=40)
        h.command("seek", 25, "absolute+exact")
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v > 20, timeout=20)
        assert not [t for t in h.get("track-list") or [] if t["type"] == "sub"], "el vídeo no trae subtítulos"

        h.command("script-binding", "mu_recap/recap")
        st = recap_state(h, lambda v: v.get("status") == "done" and v.get("result"), timeout=60)
        assert st["web_lang"] == "es" and st["web_kind"] == "manual", st
        assert st["result"]["count"] > 0 and st["result"]["source"], st["result"]
        # la pista está en español: no se ofrece traducirla
        assert not any(t.startswith("Estos subtítulos están en") for t in titles(st)), titles(st)
        # y lo que se ofrece lleva a algún minuto del vídeo
        assert "Índice del vídeo entero" in titles(st)
        from tests.test_subs_web import _Subs
        assert not any("tlang=" in p for p in _Subs.hits), _Subs.hits

        # la segunda vez no se vuelve a pedir nada a la web: el SRT ya está traído
        hits = len(_Subs.hits)
        h.command("script-binding", "mu_recap/recap")
        recap_state(h, lambda v: v.get("status") == "done")
        assert len(_Subs.hits) == hits

        # D3 · el índice del vídeo entero, desde el mismo sitio, con el mismo material
        h.command("script-binding", "mu_recap/outline")
        st = recap_state(h, lambda v: v.get("result") and v["result"].get("outline"), timeout=60)
        assert st["status"] == "done" and st["web_lang"] == "es"
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
        httpd.shutdown()
        httpd_media.shutdown()


def test_resumen_e_indice_se_encuentra_en_la_raiz_y_en_los_subtitulos(daemon_env, media_dir):
    """D3 · antes «¿Qué me he perdido?» estaba en el tercer nivel (dentro de «Herramientas», 15 filas) y el índice
    del vídeo no estaba en el menú en absoluto. Ahora hay una entrada «Resumen e índice» en la raíz —como fila de lo
    que se está viendo, no como novena categoría— y otra dentro del panel de subtítulos."""
    h, httpd, httpd_media = web_mpv(daemon_env, media_dir)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        # sin nada abierto no sale: no tiene nada que resumir
        h.command("script-binding", "mu_menu/root")
        st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "root" and v.get("items"),
                             timeout=20)
        assert "Resumen e índice" not in titles(st)
        h.command("script-message-to", "uosc", "close-menu", "mu-menu")

        h.command("loadfile", str(media_dir / "voz_es_en.mkv"))
        h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 0, timeout=20)
        h.command("script-binding", "mu_menu/root")
        st = h.wait_property("user-data/mu/menu", lambda v: bool(v) and v.get("view") == "root"
                             and "Resumen e índice" in titles(v), timeout=20)
        # la raíz sigue siendo de ocho categorías: esta fila es del archivo, y se va con él
        fila = next(i for i in st["items"] if i["title"] == "Resumen e índice")
        assert fila["value"] == {"child": {"script": "mu_recap", "entry": "recap-menu"}}
        ev(h, "mu_menu", {"type": "activate", "index": 1, "value": fila["value"]})
        st = recap_state(h, lambda v: v.get("view") == "root" and "Índice del vídeo entero" in titles(v))
        assert any(t.startswith("Resumen de los últimos") or t == "Lo que te has perdido" for t in titles(st))

        # y dentro del panel de subtítulos, que es de donde sale el material
        h.command("script-binding", "mu_subs/subs-menu")
        st = h.wait_property("user-data/mu/subs", lambda v: bool(v) and v.get("view") == "root"
                             and "Resumen e índice del vídeo" in titles(v), timeout=20)
        fila = next(i for i in st["items"] if i["title"] == "Resumen e índice del vídeo")
        ev(h, "mu_subs", {"type": "activate", "index": 1, "value": fila["value"]})
        recap_state(h, lambda v: v.get("view") == "root" and "Índice del vídeo entero" in titles(v))
        nav = h.wait_property("user-data/mu/nav", lambda v: bool(v) and v.get("script") == "mu_recap", timeout=20)
        assert "Resumen e índice" in nav["title"], nav
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()
        httpd.shutdown()
        httpd_media.shutdown()
