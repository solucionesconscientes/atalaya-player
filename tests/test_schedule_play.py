"""H57 · programar que SUENE algo en una franja, no solo grabarlo.

Lo que pidió Ser: «a X hora se enciende el canal de TV o radio, finaliza la emisión a la hora deseada y si es
preciso suspensión o apagado», y lo mismo para canciones o listas. La maquinaria de las grabaciones programadas ya
tenía franja, despertador y apagado al terminar; lo que faltaba era el modo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


@pytest.fixture
def prog(daemon_env, media_dir):
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--idle=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env
    finally:
        h.stop()


def test_una_cancion_programada_suena_y_se_para_sola(prog, media_dir):
    h, d = prog
    ahora = time.time()
    res = d.call("iptv.schedule.add", {"media": str(media_dir / "video30.mkv"), "title": "Prueba",
                                       "start": ahora + 2, "stop": ahora + 7, "after": "nothing"})
    assert res["mode"] == "play" and res["channel"]["kind"] == "media"
    assert res["status"] == "scheduled"

    # arranca sola en el reproductor que ya está abierto
    h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=25)
    assert h.get("pause") is False
    estado = d.call("iptv.schedule.list")["items"]
    assert [r["status"] for r in estado if r["id"] == res["id"]] == ["playing"]

    # y se para sola al acabar la franja
    h.wait_property("path", lambda v: not v, timeout=30)
    fin = time.monotonic() + 20
    while time.monotonic() < fin:
        fila = next(r for r in d.call("iptv.schedule.list")["items"] if r["id"] == res["id"])
        if fila["status"] == "done":
            break
        time.sleep(0.3)
    assert fila["status"] == "done", fila


def test_una_lista_guardada_se_pone_entera_y_se_repite(prog, media_dir, tmp_path):
    """J5 · una lista se carga con `loadlist` (no `loadfile`) y se repite hasta que acabe la franja: una franja de
    dos horas con una lista de veinte minutos, si no, se acabaría a y veinte."""
    h, d = prog
    lista = tmp_path / "cena.m3u8"
    lista.write_text("#EXTM3U\n%s\n%s\n" % (media_dir / "video30.mkv", media_dir / "chapters.mkv"),
                     encoding="utf-8")
    antes = h.get("loop-playlist")
    ahora = time.time()
    res = d.call("iptv.schedule.add", {"media": str(lista), "title": "Cena", "start": ahora + 2, "stop": ahora + 8,
                                       "after": "nothing"})
    assert res["mode"] == "play"

    # las dos canciones están en la cola, no solo la primera: eso es `loadlist`
    h.wait_property("playlist-count", lambda v: v == 2, timeout=25)
    assert h.get("pause") is False
    assert h.get("loop-playlist") in ("inf", "yes", True)

    # al acabar la franja se para y se devuelve el valor que hubiera
    h.wait_property("path", lambda v: not v, timeout=30)
    fin = time.monotonic() + 20
    while time.monotonic() < fin:
        if h.get("loop-playlist") == antes:
            break
        time.sleep(0.3)
    assert h.get("loop-playlist") == antes, h.get("loop-playlist")


def test_lo_que_no_existe_no_se_programa(prog):
    _h, d = prog
    from mpvd.rpc import RpcError
    with pytest.raises(RpcError):
        d.call("iptv.schedule.add", {"media": "/no/existe/cancion.mp3", "start": time.time() + 60,
                                     "stop": time.time() + 120})
    with pytest.raises(RpcError):      # ni un modo inventado
        d.call("iptv.schedule.add", {"media": "/tmp", "start": time.time() + 60, "stop": time.time() + 120,
                                     "mode": "loquesea"})


def test_una_franja_de_radio_se_pone_sola(prog, tmp_path):
    """Lo que pidió Ser con la radio: a tal hora se enciende y suena, y a tal otra para. Aquí la «emisora» es un
    archivo servido por el propio equipo, para no depender de internet."""
    h, d = prog
    from tests.conftest import ROOT
    emisora = ROOT / "tests" / "fixtures" / "media" / "video30.mkv"
    ahora = time.time()
    res = d.call("iptv.schedule.add", {"media": str(emisora), "title": "Radio de las 7",
                                       "start": ahora + 2, "stop": ahora + 6, "mode": "play"})
    assert res["mode"] == "play" and res["title"] == "Radio de las 7"
    h.wait_property("path", lambda v: isinstance(v, str) and v.endswith("video30.mkv"), timeout=25)
    h.wait_property("path", lambda v: not v, timeout=30)      # para sola al acabar la franja


def test_el_modo_viaja_hasta_mpvd_desde_el_menu(prog, media_dir):
    """El interruptor «Qué hacer en esa franja» del menú acaba en el `mode` de la programación."""
    h, d = prog
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_iptv", "mu-iptv-event",
              json.dumps({**base, "type": "activate", "index": 1, "value": {"sched_mode": "play"}}))
    estado = h.wait_property("user-data/mu/prefs", lambda v: bool(v) and v.get("writes", 0) > 0, timeout=15)
    guardado = json.loads(Path(estado["path"]).read_text(encoding="utf-8"))
    assert guardado.get("mu-iptv", {}).get("sched_mode") == "play", guardado


def test_la_lista_guardada_se_elige_desde_el_menu(prog, media_dir):
    """J5 · «Programar › Una lista guardada…» enseña las listas de Música y lleva la ruta del .m3u8 a la franja."""
    h, d = prog
    d.call("music.playlists.create", {"name": "Cena", "paths": [str(media_dir / "video30.mkv"),
                                                                str(media_dir / "chapters.mkv")]})
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}

    def event(value):
        h.command("script-message-to", "mu_iptv", "mu-iptv-event",
                  json.dumps({**base, "type": "activate", "index": 1, "value": value}))

    h.command("script-binding", "mu_iptv/tv-menu")
    h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("view") == "root", timeout=15)
    event({"view": "sched_new"})
    # mu-iptv publica las filas del menú aparte, porque uosc no expone las suyas
    h.wait_property("user-data/mu/iptv-menu", lambda v: bool(v)
                    and any(i.get("title") == "Una lista guardada…" for i in v.get("items") or []), timeout=20)
    event({"view": "sched_lists"})
    estado = h.wait_property("user-data/mu/iptv-menu", lambda v: bool(v)
                             and any(i.get("title") == "Cena" for i in v.get("items") or []), timeout=20)
    fila = next(i for i in estado["items"] if i["title"] == "Cena")
    assert fila["hint"] == "2 canciones"
    assert fila["value"]["media"].endswith(".m3u8") and fila["value"]["name"] == "Cena"

    # y la franja se titula «Poner», no «Grabar»: lo que está en el disco no se graba
    event(fila["value"])
    menu = h.wait_property("user-data/mu/iptv-menu",
                           lambda v: bool(v) and "inicio y fin" in str(v.get("title") or ""), timeout=15)
    assert menu["title"].startswith("Poner «Cena»"), menu["title"]

    # y la programación que sale de ahí lleva la lista y el modo «ponerlo», no «grabarlo»
    ahora = time.time()
    event({"sched_add": {"media": fila["value"]["media"], "start": ahora + 3600, "stop": ahora + 7200,
                         "title": "Cena"}})
    fin = time.monotonic() + 20
    hecho = None
    while time.monotonic() < fin:
        hecho = next((r for r in d.call("iptv.schedule.list")["items"] if r["title"] == "Cena"), None)
        if hecho:
            break
        time.sleep(0.3)
    assert hecho and hecho["mode"] == "play" and hecho["channel"]["kind"] == "media", hecho
    assert h.script_errors() == [], h.script_errors()
