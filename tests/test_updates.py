"""H68 · avisar de que hay versión nueva, y solo avisar.

Lo que se comprueba es lo que puede molestar o engañar: que no avise dos veces de lo mismo, que no avise de una
versión que no es más nueva, que no diga nada cuando no hay red o cuando el fichero todavía no existe (la web se
publica después que esto), que consulte una vez al día y no en cada apertura, que en una copia del repositorio
esté apagado —ahí se actualiza con `git pull`— y, la más importante, que **no haya forma de que esto instale
nada**: avisar es una cosa y reemplazarse a sí mismo mientras está en marcha es otra, y la segunda, mal hecha,
deja a alguien sin reproductor.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from mpvd import __version__
from mpvd.updates import UpdateService, enabled, latest_url, version_tuple

ROOT = Path(__file__).resolve().parent.parent


class Servidor:
    """Sirve un `latest.json` y cuenta las peticiones, que es como se comprueba la caché de un día."""

    def __init__(self, cuerpo: bytes | None, codigo: int = 200):
        self.peticiones = 0
        yo = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                yo.peticiones += 1
                if cuerpo is None or codigo != 200:
                    self.send_response(codigo if cuerpo is None else 500)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(cuerpo)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/latest.json"

    def close(self):
        self.httpd.shutdown()


class FakeServer:
    def __init__(self, tmp_path: Path):
        class S:
            cache_dir = tmp_path / "cache"
            data_dir = tmp_path / "data"
        self.settings = S()


def servicio(tmp_path: Path) -> UpdateService:
    return UpdateService(FakeServer(tmp_path))


def ficha(version: str, **extra) -> bytes:
    datos = {"version": version, "date": "2026-11-01", "notes": "Dos cosas nuevas y un arreglo.",
             "url": "https://ejemplo.invalido/atalaya",
             "files": [{"name": f"atalaya-player_{version}_amd64.deb", "sha256": "ab" * 32, "size": 28311552}]}
    datos.update(extra)
    return json.dumps(datos).encode()


@pytest.fixture
def encendido(monkeypatch):
    monkeypatch.setenv("MPV_UOS_UPDATES", "1")


def test_la_cuenta_de_versiones_no_confunde_una_candidata_con_una_nueva():
    assert version_tuple("0.2.0") > version_tuple("0.1.0")
    assert version_tuple("1.0") > version_tuple("0.99.99")
    assert version_tuple("0.1.0") == version_tuple("0.1.0")
    assert not version_tuple("0.1.0-rc1") > version_tuple("0.1.0"), "una candidata no es una versión nueva"
    assert version_tuple("") == ()


def test_en_una_copia_del_repositorio_esta_apagado_y_no_toca_la_red(tmp_path, monkeypatch):
    """Aquí se actualiza con `git pull`: avisar sería ruido. Y «apagado» quiere decir que no pide nada."""
    for var in ("MPV_UOS_UPDATES", "MPV_UOS_PACKAGED", "MPV_UOS_APPIMAGE"):
        monkeypatch.delenv(var, raising=False)
    s = Servidor(ficha("9.9.9"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)
    try:
        assert enabled() is False
        estado = servicio(tmp_path).check()
        assert estado["enabled"] is False and estado["newer"] is False
        assert estado["current"] == __version__
        assert s.peticiones == 0, "apagado es apagado: ni una petición"
    finally:
        s.close()


@pytest.mark.parametrize("var", ["MPV_UOS_PACKAGED", "MPV_UOS_APPIMAGE"])
def test_en_un_paquete_esta_encendido(monkeypatch, var):
    for v in ("MPV_UOS_UPDATES", "MPV_UOS_PACKAGED", "MPV_UOS_APPIMAGE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(var, "1")
    assert enabled() is True


def test_avisa_de_una_version_nueva_con_su_enlace_y_su_suma(tmp_path, monkeypatch, encendido):
    s = Servidor(ficha("9.9.9"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)
    try:
        estado = servicio(tmp_path).check()
        assert estado["newer"] is True and estado["latest"] == "9.9.9"
        assert estado["notes"].startswith("Dos cosas")
        assert estado["url"] == "https://ejemplo.invalido/atalaya"
        assert estado["files"][0]["sha256"] == "ab" * 32
    finally:
        s.close()


@pytest.mark.parametrize("version", [__version__, "0.0.1"])
def test_no_avisa_de_lo_que_no_es_mas_nuevo(tmp_path, monkeypatch, encendido, version):
    s = Servidor(ficha(version))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)
    try:
        assert servicio(tmp_path).check()["newer"] is False
    finally:
        s.close()


def test_lo_dice_una_vez_y_no_cada_vez_que_se_abre(tmp_path, monkeypatch, encendido):
    s = Servidor(ficha("9.9.9"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)
    try:
        serv = servicio(tmp_path)
        primera = serv.announce()
        assert primera["announce"] is True and primera["latest"] == "9.9.9"
        assert serv.announce()["announce"] is False, "ya se dijo"
        # otro arranque (otro objeto, los mismos datos en disco): sigue sin repetirlo
        assert servicio(tmp_path).announce()["announce"] is False
    finally:
        s.close()


def test_una_version_aun_mas_nueva_vuelve_a_avisar(tmp_path, monkeypatch, encendido):
    s1 = Servidor(ficha("9.9.9"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s1.url)
    try:
        assert servicio(tmp_path).announce()["announce"] is True
    finally:
        s1.close()
    s2 = Servidor(ficha("10.0.0"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s2.url)
    try:
        assert servicio(tmp_path).announce()["announce"] is True
    finally:
        s2.close()


def test_se_consulta_una_vez_al_dia(tmp_path, monkeypatch, encendido):
    s = Servidor(ficha("9.9.9"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)
    try:
        serv = servicio(tmp_path)
        serv.check()
        serv.check()
        serv.check()
        assert s.peticiones == 1, "la segunda y la tercera salen de la caché"
        serv.check(force=True)
        assert s.peticiones == 2, "y con force se vuelve a preguntar"
    finally:
        s.close()


@pytest.mark.parametrize(("cuerpo", "codigo"), [
    (None, 404),                       # el fichero todavía no existe: la web se publica después
    (b"no soy json", 200),             # algo raro en medio
    (b'{"sin": "version"}', 200),      # json válido que no dice la versión
])
def test_cuando_no_se_puede_consultar_no_pasa_nada(tmp_path, monkeypatch, encendido, cuerpo, codigo):
    s = Servidor(cuerpo, codigo)
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)
    try:
        estado = servicio(tmp_path).check()
        assert estado["newer"] is False and estado["latest"] == ""
        assert estado["current"] == __version__, "lo que sí se sabe siempre es la versión que hay"
    finally:
        s.close()


def test_sin_servidor_ninguno_tampoco_se_queja(tmp_path, monkeypatch, encendido):
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", "http://127.0.0.1:9/latest.json")   # puerto descartado
    estado = servicio(tmp_path).check()
    assert estado["newer"] is False and "error" in estado


def test_por_defecto_se_consulta_en_el_sitio_del_proyecto(monkeypatch):
    monkeypatch.delenv("MPV_UOS_UPDATE_URL", raising=False)
    url = latest_url()
    assert url.startswith("https://") and url.endswith("/latest.json")


def test_esto_no_puede_instalar_nada():
    """La regla de H68, vigilada: avisar sí, instalarse solo no. Si algún día alguien añade aquí una descarga de
    paquetes o un chmod +x, este test lo dice: reemplazarse mientras está en marcha es lo que deja a alguien sin
    reproductor, y en Linux eso ya lo saben hacer apt o el gestor de AppImage."""
    fuente = (ROOT / "mpvd/updates.py").read_text(encoding="utf-8")
    for prohibido in ("chmod", "os.replace(", "shutil.move", "subprocess", "urlretrieve", "os.rename",
                      "extractall", "tarfile", "zipfile"):
        if prohibido == "os.replace(":
            # el único os.replace que vale es el del ficherito que recuerda qué versión ya se dijo
            assert fuente.count(prohibido) == 1, "solo se escribe updates.json"
            continue
        assert prohibido not in fuente, f"updates.py no debería usar {prohibido}"
    assert "def install" not in fuente and "def apply" not in fuente


def test_por_la_puerta_del_demonio_tambien(tmp_path, monkeypatch, encendido):
    """Integración: el servicio registrado en el demonio de verdad, preguntado por su socket como lo hace el
    reproductor. Es lo que comprueba que `updates.announce` está enchufado y no solo escrito."""
    import asyncio

    from mpvd.client import MpvdClient
    from mpvd.config import Settings
    from mpvd.server import MpvdServer

    s = Servidor(ficha("9.9.9"))
    monkeypatch.setenv("MPV_UOS_UPDATE_URL", s.url)

    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=1))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                primera = await c.call("updates.announce")
                segunda = await c.call("updates.announce")
                consulta = await c.call("updates.check")
                return primera, segunda, consulta
        finally:
            await server.stop()

    try:
        primera, segunda, consulta = asyncio.run(go())
    finally:
        s.close()
    assert primera["announce"] is True and primera["latest"] == "9.9.9"
    assert segunda["announce"] is False, "una vez y no más"
    assert consulta["newer"] is True and consulta["current"] == __version__
    assert consulta["files"][0]["sha256"] == "ab" * 32
    assert (tmp_path / "data/updates.json").is_file(), "lo dicho se apunta, y sobrevive a cerrar el programa"
