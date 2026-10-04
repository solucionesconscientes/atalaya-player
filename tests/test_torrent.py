"""H59 · ver un torrent mientras se descarga, probado SIN RED.

Un sembrador y un cliente en este mismo equipo, como se probó el relay de las salas: se crea un .torrent de un
vídeo de prueba, lo siembra una sesión de libtorrent en 127.0.0.1 y mpvd lo baja de ahí. Lo que se comprueba es la
cadena entera: metadatos, prioridad de ficheros, el servidor HTTP local con Range y que **los bytes que entrega son
los del vídeo**, incluso pidiendo un trozo del final cuando aún no está descargado.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.test_mu_ytdl import ytdl_mpv  # noqa: F401 - fixture
from tests.test_mu_ytdl_puerta import gate_event, open_gate, ytdl_state

lt = pytest.importorskip("libtorrent", reason="el extra `torrent` no está instalado")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def semilla(tmp_path, media_dir):
    """Un .torrent de un vídeo de prueba y una sesión que lo siembra, todo en 127.0.0.1 y sin DHT ni trackers."""
    origen = tmp_path / "siembra"
    origen.mkdir()
    shutil.copy(media_dir / "video30.mkv", origen / "pelicula.mkv")

    # la receta moderna de libtorrent 2.1: `list_files` + `create_torrent` sobre esa lista (lo de
    # `file_storage` + `add_files` está deprecado y avisa)
    ct = lt.create_torrent(lt.list_files(str(origen / "pelicula.mkv")), piece_size=64 * 1024)
    lt.set_piece_hashes(ct, str(origen))
    torrent = tmp_path / "pelicula.torrent"
    torrent.write_bytes(lt.bencode(ct.generate()))

    port = free_port()
    ses = lt.session({"listen_interfaces": f"127.0.0.1:{port}", "enable_dht": False, "enable_lsd": False,
                      "enable_upnp": False, "enable_natpmp": False, "alert_mask": 0})
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(str(torrent))
    params.save_path = str(origen)
    handle = ses.add_torrent(params)
    fin = time.monotonic() + 30
    while time.monotonic() < fin and not handle.status().is_seeding:
        time.sleep(0.2)
    assert handle.status().is_seeding, "el sembrador no llegó a sembrar"
    try:
        yield torrent, port, (origen / "pelicula.mkv")
    finally:
        ses.remove_torrent(handle)
        del ses


def test_se_ve_mientras_se_descarga_y_los_bytes_son_los_del_video(daemon_env, semilla, tmp_path):
    torrent, port, original = semilla
    d = daemon_env
    d.cli("ensure")
    d.wait(d.alive, timeout=30)

    # apagado de fábrica: no se abre nada hasta que se enciende, y se dice por qué
    assert d.call("torrent.settings.get")["enabled"] is False
    from mpvd.rpc import RpcError
    with pytest.raises(RpcError):
        d.call("torrent.open", {"link": str(torrent)})
    assert d.call("torrent.settings.set", {"enabled": True})["enabled"] is True

    res = d.call("torrent.open", {"link": str(torrent)}, timeout=90)
    assert res["name"] and res["url"].startswith("http://127.0.0.1:") and "k=" in res["url"]
    assert res["aviso"] and res["size"] == original.stat().st_size
    assert [f["name"] for f in res["files"]] == ["pelicula.mkv"]

    # nadie nos ha dado peers (no hay DHT ni trackers): se le dice dónde está el sembrador
    d.call("torrent.connect", {"id": res["id"], "host": "127.0.0.1", "port": port})

    esperado = original.read_bytes()
    # un trozo del principio
    assert pedir(res["url"], 0, 65535) == esperado[:65536]
    # y uno del final, que es lo que prueba que no va secuencial a lo bruto
    fin = len(esperado) - 1
    assert pedir(res["url"], fin - 20000, fin) == esperado[fin - 20000:]

    filas = d.call("torrent.list")["torrents"]
    # no se comprueban los peers: a estas alturas la descarga ya ha terminado y el sembrador se ha ido, que es lo
    # normal. Lo que importa es que se ha bajado de verdad
    assert len(filas) == 1 and filas[0]["id"] == res["id"]
    assert filas[0]["wanted_done"] > 0 and filas[0]["progress"] > 0
    assert d.call("torrent.remove", {"id": res["id"], "data": True})["removed"] is True
    assert d.call("torrent.list")["torrents"] == []


def pedir(url: str, start: int, end: int, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 127.0.0.1, puesto por el test
        assert r.status == 206, r.status
        return r.read()


# -- la puerta única --------------------------------------------------------------------------

MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Prueba"


def test_la_puerta_reconoce_un_magnet_y_dice_si_esta_apagado(ytdl_mpv, daemon_env):  # noqa: F811
    """H59 · un magnet pegado en la puerta única se reconoce, y si los torrents están apagados se dice ANTES de
    pulsar y dónde se encienden (la regla de H58/K2), en vez de dejar una fila que falla."""
    h, _d = ytdl_mpv[0], ytdl_mpv[1]
    h.wait_property("user-data/mu/ytdl", lambda v: bool(v) and isinstance(v.get("torrent"), dict), timeout=40)
    v = open_gate(h)
    gate_event(h, {"type": "search", "query": MAGNET})
    v = ytdl_state(h, lambda s: any("torrent" in (i.get("hint") or "") or "apagados" in i["title"]
                                    for i in s.get("items") or []))
    titulos = [i["title"] for i in v["items"]]
    assert "Los torrents están apagados" in titulos, titulos
    assert not any(t == "Ver mientras se descarga" for t in titulos)

    # encendidos, la fila aparece y es pulsable
    daemon_env.call("torrent.settings.set", {"enabled": True})
    h.command("script-binding", "mu_ytdl/ytdl-gate")   # se vuelve a preguntar al abrir
    h.wait_property("user-data/mu/ytdl", lambda s: bool(s) and (s.get("torrent") or {}).get("enabled") is True,
                    timeout=20)
    gate_event(h, {"type": "search", "query": MAGNET})
    v = ytdl_state(h, lambda s: "Ver mientras se descarga" in [i["title"] for i in s.get("items") or []])
    fila = next(i for i in v["items"] if i["title"] == "Ver mientras se descarga")
    assert fila["value"] == {"torrent": MAGNET}
    assert h.script_errors() == [], h.script_errors()


def test_un_torrent_arrastrado_a_la_ventana_se_abre(ytdl_mpv, semilla):  # noqa: F811
    """Lo que de verdad hace la gente: soltar el .torrent en la ventana. mpv no sabe abrirlo y falla en el acto, así
    que el script recoge ese fallo y pide a mpvd la dirección local. Antes esto no hacía nada (solo funcionaba
    pegándolo en ctrl+o), que es justo lo que Ser encontró."""
    h, d = ytdl_mpv[0], ytdl_mpv[1]
    torrent, _port, _origen = semilla
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)

    def ruta() -> str:
        """`path` no está disponible cuando mpv se ha quedado sin nada puesto, que es justo el caso de abajo."""
        with contextlib.suppress(Exception):
            return str(h.get("path") or "")
        return ""

    # apagados: no se abre nada, y no se queda intentándolo
    h.command("loadfile", str(torrent), "replace")
    time.sleep(3)
    assert not ruta().startswith("http://127.0.0.1"), "no debería abrirse apagado"

    d.call("torrent.settings.set", {"enabled": True})
    h.command("loadfile", str(torrent), "replace")
    url = h.wait_property("path", lambda v: isinstance(v, str) and v.startswith("http://127.0.0.1"), timeout=90)
    assert "/t/" in url, url
    assert h.script_errors() == [], h.script_errors()


def test_preferencias_torrents_enciende_y_apaga(ytdl_mpv):  # noqa: F811
    """La fila que la puerta promete («se encienden en Preferencias › Torrents») tiene que existir y funcionar: la
    primera versión de H59 dejó los ajustes solo en el fichero, así que la pista apuntaba a un sitio que no había."""
    from tests.test_mu_ytdl import send_event

    h, d = ytdl_mpv[0], ytdl_mpv[1]
    h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
    assert d.call("torrent.settings.get")["enabled"] is False

    h.command("script-binding", "mu_ytdl/torrents-menu")
    v = h.wait_property("user-data/mu/ytdl", lambda s: bool(s) and s.get("view") == "torrents" and any(
        i["title"] == "Abrir torrents" for i in s.get("items") or []), timeout=30)
    fila = next(i for i in v["items"] if i["title"] == "Abrir torrents")
    assert fila["hint"] == "no" and fila["value"] == {"torset": "enabled"}
    assert any(i["title"] == "Seguir compartiendo al acabar" for i in v["items"])

    send_event(h, {"type": "activate", "index": 1, "value": {"torset": "enabled"}})
    fin = time.monotonic() + 20
    while time.monotonic() < fin and not d.call("torrent.settings.get")["enabled"]:
        time.sleep(0.3)
    assert d.call("torrent.settings.get")["enabled"] is True, "la fila no encendió los torrents"
    v = h.wait_property("user-data/mu/ytdl", lambda s: bool(s) and any(
        i["title"] == "Abrir torrents" and i.get("hint") == "sí" for i in s.get("items") or []), timeout=20)
    assert h.script_errors() == [], h.script_errors()
