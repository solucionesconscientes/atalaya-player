"""K3 · que todo suene igual de fuerte, y el temporizador para dormirse en cualquier cosa.

Las dos piezas existían y estaban escondidas: ReplayGain solo se ofrecía en Música (viendo una película nadie lo
busca ahí) y el temporizador, que ya valía para cualquier reproducción, solo dentro del menú de audiolibros.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


@pytest.fixture
def sonido(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes",
                                           str(media_dir / "video30.mkv")], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def event(h, script, value, index=1):
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", script, script.replace("mu_", "mu-") + "-event",
              json.dumps({**base, "type": "activate", "index": index, "value": value}))


def test_las_dos_formas_de_igualar_el_volumen_estan_juntas_en_imagen_y_sonido(sonido):
    h, _d = sonido
    h.command("script-binding", "mu_av/av-menu")
    st = h.wait_property("user-data/mu/av", lambda v: bool(v) and v.get("view") == "root"
                         and any(i["title"].startswith("Volumen parejo · con las etiquetas")
                                 for i in v.get("items") or []), timeout=20)
    titulos = [i["title"] for i in st["items"]]
    # juntas y en este orden: primero el filtro que vale para todo, luego la vía gratis
    i = titulos.index("Volumen parejo · siempre")
    assert titulos[i + 1] == "Volumen parejo · con las etiquetas"
    fila = st["items"][i + 1]
    assert fila["hint"].startswith("desactivado") and fila["active"] is False

    # activarla pone el modo en mu-music, que es quien lo aplica, y en la propiedad de mpv
    event(h, "mu_av", fila["value"])
    h.wait_property("user-data/mu/music", lambda v: bool(v) and v.get("replaygain") == "track", timeout=15)
    h.wait_property("replaygain", lambda v: v == "track", timeout=10)
    st = h.wait_property("user-data/mu/av", lambda v: bool(v) and any(
        i["title"] == "Volumen parejo · con las etiquetas" and i["active"] for i in v.get("items") or []), timeout=15)
    assert next(i for i in st["items"] if i["title"] == "Volumen parejo · con las etiquetas")["hint"] \
        .startswith("por pista")
    assert h.script_errors() == [], h.script_errors()


def test_el_temporizador_se_abre_sin_pasar_por_audiolibros(sonido):
    h, _d = sonido
    h.command("script-binding", "mu_books/sleep-menu")
    st = h.wait_property("user-data/mu/books", lambda v: bool(v) and v.get("view") == "sleep"
                         and any("Dentro de" in i["title"] for i in v.get("items") or []), timeout=20)
    assert [i["title"] for i in st["items"]][-1] == "Al terminar el capítulo"

    # y funciona con un vídeo cualquiera, no solo con un audiolibro
    fila = next(i for i in st["items"] if i["title"] == "Dentro de 15 minutos")
    event(h, "mu_books", fila["value"])
    estado = h.wait_property("user-data/mu/books", lambda v: bool(v) and bool(v.get("sleep")), timeout=15)
    assert estado["sleep"]["mode"] == "minutes"
    assert h.script_errors() == [], h.script_errors()
