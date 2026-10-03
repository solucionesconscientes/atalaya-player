"""H64 · explorar las carpetas del equipo, que es la única forma de elegir una película sin teclado.

Pedido por Ser pensando en una Raspberry conectada al televisor: allí «escribe la ruta» —lo único que había para
añadir una carpeta— no sirve. Todo se navega con arriba, abajo y aceptar, que es lo que manda el mando de la tele.
"""

from __future__ import annotations

import json
import shutil

import pytest

from mpvd.files import browse, kind_of, natural_key, places
from pathlib import Path
from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


def test_una_carpeta_se_lista_con_lo_que_este_reproductor_abre(tmp_path, media_dir):
    (tmp_path / "Serie").mkdir()
    shutil.copy(media_dir / "video30.mkv", tmp_path / "Capítulo 10.mkv")
    shutil.copy(media_dir / "video30.mkv", tmp_path / "Capítulo 2.mkv")
    shutil.copy(media_dir / "voz_es.flac", tmp_path / "Serie" / "voz.flac")
    (tmp_path / "notas.txt").write_text("no es un medio", encoding="utf-8")
    (tmp_path / ".oculto.mkv").write_bytes(b"")

    r = browse(str(tmp_path))
    nombres = [e["name"] for e in r["entries"]]
    # las carpetas primero, y «Capítulo 2» antes que «Capítulo 10», que es lo que espera cualquiera
    assert nombres == ["Serie", "Capítulo 2.mkv", "Capítulo 10.mkv"], nombres
    assert r["dirs"] == 1 and r["files"] == 2 and r["parent"] == str(tmp_path.parent)
    assert r["entries"][0]["items"] == 1          # lo que hay dentro, para saber si merece la pena entrar
    assert r["entries"][1]["kind"] == "video" and r["entries"][1]["size"] > 0
    # lo que no se puede reproducir no estorba, y lo oculto tampoco
    assert "notas.txt" not in nombres and ".oculto.mkv" not in nombres
    assert "notas.txt" in [e["name"] for e in browse(str(tmp_path), only_playable=False)["entries"]]
    assert ".oculto.mkv" in [e["name"] for e in browse(str(tmp_path), hidden=True)["entries"]]


def test_lo_que_no_es_una_carpeta_se_dice_y_no_revienta(tmp_path):
    from mpvd.rpc import RpcError
    with pytest.raises(RpcError):
        browse(str(tmp_path / "no-existe"))
    with pytest.raises(RpcError):
        browse("")


def test_las_puertas_no_se_repiten_y_solo_salen_las_que_existen(tmp_path):
    rows = places([str(tmp_path), str(tmp_path), "/no/existe/esta"])
    rutas = [r["path"] for r in rows]
    assert rutas.count(str(tmp_path)) == 1 and "/no/existe/esta" not in rutas
    assert any(r["kind"] == "home" for r in rows)
    assert kind_of(Path("a.mkv")) == "video" and kind_of(Path("a.m3u8")) == "playlist"
    assert sorted(["b10", "b2"], key=natural_key) == ["b2", "b10"]


@pytest.fixture
def explorar(daemon_env, media_dir, tmp_path):
    arbol = tmp_path / "Pelis"
    (arbol / "Vacaciones").mkdir(parents=True)
    shutil.copy(media_dir / "video30.mkv", arbol / "Vacaciones" / "uno.mkv")
    shutil.copy(media_dir / "chapters.mkv", arbol / "Vacaciones" / "dos.mkv")
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--idle=yes"], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected" and v.get("uosc"),
                        timeout=40)
        yield h, daemon_env, arbol
    finally:
        h.stop()


def lib(h, pred, timeout: float = 25.0):
    return h.wait_property("user-data/mu/library", lambda v: bool(v) and pred(v), timeout=timeout)


def ev(h, value):
    base = {"menu_id": "{root}", "is_pointer": False, "alt": False, "ctrl": False, "shift": False}
    h.command("script-message-to", "mu_library", "mu-library-event",
              json.dumps({**base, "type": "activate", "index": 1, "value": value}))


def titulos(v):
    return [i["title"] for i in v.get("items", [])]


def test_se_navega_hasta_un_video_y_se_abre_la_carpeta_entera(explorar):
    h, _d, arbol = explorar
    # la puerta única lleva al explorador sin teclear nada
    h.command("script-message-to", "mu_library", "mu-library-explore")
    lib(h, lambda v: v.get("view") == "explore" and any(i.get("hint") for i in v.get("items") or []))

    # se entra en la carpeta de prueba por su ruta, como haría una fila de «puertas»
    ev(h, {"view": "browse", "path": str(arbol)})
    v = lib(h, lambda v: v.get("view") == "browse" and "Vacaciones" in titulos(v))
    assert "Añadir esta carpeta a la biblioteca" in titulos(v)
    assert "Reproducir toda esta carpeta" not in titulos(v)      # aquí dentro solo hay una subcarpeta
    assert any(t.startswith("Subir a ") for t in titulos(v))

    ev(h, {"view": "browse", "path": str(arbol / "Vacaciones")})
    v = lib(h, lambda v: v.get("view") == "browse" and "uno.mkv" in titulos(v))
    assert "Reproducir toda esta carpeta" in titulos(v)

    # una carpeta entera: mpv la abre él mismo (--directory-mode) y monta la lista
    ev(h, {"play": str(arbol / "Vacaciones")})
    h.wait_property("playlist-count", lambda n: n == 2, timeout=25)
    h.wait_property("path", lambda p: isinstance(p, str) and p.endswith(".mkv"), timeout=25)

    # y un archivo suelto, el que se elija
    h.command("script-message-to", "mu_library", "mu-library-explore")
    lib(h, lambda v: v.get("view") == "explore")
    ev(h, {"view": "browse", "path": str(arbol / "Vacaciones")})
    lib(h, lambda v: v.get("view") == "browse" and "dos.mkv" in titulos(v))
    ev(h, {"play": str(arbol / "Vacaciones" / "dos.mkv")})
    h.wait_property("path", lambda p: isinstance(p, str) and p.endswith("dos.mkv"), timeout=25)
    assert h.script_errors() == [], h.script_errors()
