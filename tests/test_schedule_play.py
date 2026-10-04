"""H57 · programar que SUENE algo en una franja, no solo grabarlo.

Lo que pidió Ser: «a X hora se enciende el canal de TV o radio, finaliza la emisión a la hora deseada y si es
preciso suspensión o apagado», y lo mismo para canciones o listas. La maquinaria de las grabaciones programadas ya
tenía franja, despertador y apagado al terminar; lo que faltaba era el modo.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

import pytest

from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


@pytest.fixture
def prog(daemon_env, media_dir):
    # H66 · una franja abre su propia ventana; aquí no se pueden abrir ventanas, así que se le dice que reutilice
    # la que el test ya tiene. Que abra una nueva lo comprueba `test_la_franja_abre_su_propia_ventana` abajo.
    daemon_env.extra_env["MPVD_SCHEDULE_WINDOW"] = "reuse"
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


def test_la_franja_abre_su_propia_ventana_maximizada(daemon_env, media_dir, tmp_path):
    """H66 · lo que pidió Ser: la franja NO se queda con la ventana que estés usando, abre la suya y la maximiza.
    Y la cierra al acabar, que si no se van acumulando ventanas vacías.

    Aquí no se abren ventanas: el mpv que lanza el programa se sustituye por un envoltorio que apunta sus
    argumentos y arranca el mpv de verdad sin vídeo ni audio. Así se ve lo que se le pasa y se ve que la ventana
    que ya estaba no se toca."""
    argv = tmp_path / "argv.log"
    envoltorio = tmp_path / "mpv-sin-ventana"
    envoltorio.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {argv}\nexec mpv --vo=null --ao=null "$@"\n')
    envoltorio.chmod(0o755)
    daemon_env.extra_env["MPV_UOS_MPV"] = str(envoltorio)
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--idle=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        d = daemon_env
        assert len(d.call("sessions.list")) == 1

        ahora = time.time()
        res = d.call("iptv.schedule.add", {"media": str(media_dir / "video30.mkv"), "title": "Su ventana",
                                           "start": ahora + 2, "stop": ahora + 10, "after": "nothing"})
        # aparece una SEGUNDA instancia, que es la que pone la franja
        fin = time.monotonic() + 90
        while time.monotonic() < fin and len(d.call("sessions.list")) < 2:
            time.sleep(0.5)
        assert len(d.call("sessions.list")) == 2, "la franja tenía que abrir su propia ventana"
        assert "--window-maximized=yes" in argv.read_text(), argv.read_text()

        # y la ventana que ya estaba abierta sigue sin nada puesto: no se le quita a nadie
        with contextlib.suppress(Exception):
            assert not h.get("path"), h.get("path")
        fila = next(r for r in d.call("iptv.schedule.list")["items"] if r["id"] == res["id"])
        assert fila["status"] in ("playing", "done"), fila

        # al acabar la franja, la ventana que abrió se cierra sola
        fin = time.monotonic() + 60
        while time.monotonic() < fin and len(d.call("sessions.list")) > 1:
            time.sleep(0.5)
        assert len(d.call("sessions.list")) == 1, "la ventana de la franja tenía que cerrarse al acabar"
        assert h.script_errors() == [], h.script_errors()
    finally:
        h.stop()


def test_se_programan_archivos_y_carpetas_del_equipo(prog, media_dir, tmp_path):
    """H66 · lo que pidió Ser: además de las listas creadas, elegir uno o varios archivos de las carpetas del
    equipo. Se exploran con los mismos `files.*` que la biblioteca, se marcan con Tab y los marcados se programan
    como una sola cosa (un .m3u8, que el programador ya sabe repetir mientras dure la franja)."""
    h, d = prog
    carpeta = tmp_path / "Pelis"
    carpeta.mkdir()
    for nombre in ("uno.mkv", "dos.mkv"):
        (carpeta / nombre).write_bytes((media_dir / "video30.mkv").read_bytes())
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}

    def event(value, **extra):
        h.command("script-message-to", "mu_iptv", "mu-iptv-event",
                  json.dumps({**base, "type": "activate", "index": 1, "value": value, **extra}))

    def filas(pred, timeout: float = 20.0):
        return h.wait_property("user-data/mu/iptv-menu", lambda v: bool(v) and pred(v.get("items") or []),
                               timeout=timeout)

    h.command("script-binding", "mu_iptv/tv-menu")
    h.wait_property("user-data/mu/iptv", lambda v: bool(v) and v.get("view") == "root", timeout=15)
    event({"view": "sched_new"})
    filas(lambda it: any(i.get("title") == "Un archivo o una carpeta del equipo…" for i in it))

    # se entra directamente en la carpeta de la prueba (las puertas son las del equipo de cada uno)
    event({"view": "sched_browse", "path": str(carpeta)})
    v = filas(lambda it: any(i.get("title") == "uno.mkv" for i in it))
    titulos = [i["title"] for i in v["items"]]
    assert "Programar toda esta carpeta" in titulos and "Subir" in titulos, titulos

    # Tab marca dos archivos y aparece la fila para programarlos juntos
    event({"sched_file": str(carpeta / "uno.mkv"), "name": "uno.mkv"}, action="mark")
    filas(lambda it: any(i.get("title") == "✓ uno.mkv" for i in it))
    event({"sched_file": str(carpeta / "dos.mkv"), "name": "dos.mkv"}, action="mark")
    v = filas(lambda it: any(i.get("title") == "Programar los 2 seleccionados" for i in it))

    # y la franja sale con el .m3u8 de los dos marcados
    event({"sched_marked": True})
    menu = h.wait_property("user-data/mu/iptv-menu",
                           lambda x: bool(x) and "inicio y fin" in str(x.get("title") or ""), timeout=20)
    assert menu["title"].startswith("Poner «Pelis»"), menu["title"]
    m3u = d.data_dir / "programadas" / "Pelis.m3u8"
    fin = time.monotonic() + 15
    while time.monotonic() < fin and not m3u.is_file():
        time.sleep(0.3)
    assert m3u.is_file(), sorted((d.data_dir / "programadas").glob("*")) if (d.data_dir / "programadas").is_dir() else "sin carpeta"
    rutas = [ln for ln in m3u.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert rutas == [str(carpeta / "uno.mkv"), str(carpeta / "dos.mkv")], rutas

    # un archivo suelto se programa con Enter, sin marcar nada
    event({"sched_file": str(carpeta / "uno.mkv"), "name": "uno.mkv"})
    menu = h.wait_property("user-data/mu/iptv-menu",
                           lambda x: bool(x) and "inicio y fin" in str(x.get("title") or ""), timeout=20)
    assert menu["title"].startswith("Poner «uno.mkv»"), menu["title"]
    assert h.script_errors() == [], h.script_errors()
