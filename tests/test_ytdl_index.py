"""Índice local (SQLite): anotar, deduplicar por id + perfil, consultar lo que falta y el historial."""

import time

import pytest

from mpvd.ytdl.index import DB_NAME, FOLDER, Index, default_path, hash_fichero, id_de_ruta


def test_default_path_is_beside_the_downloads(tmp_path):
    assert default_path(tmp_path) == tmp_path / FOLDER / DB_NAME
    idx = Index(salida=tmp_path)
    assert idx.path == tmp_path / FOLDER / DB_NAME and idx.path.is_file()


def test_add_dedupe_by_video_id_and_profile(tmp_path):
    idx = Index(ruta=tmp_path / "i.db")
    assert idx.add("abc123", perfil="video_720", canal="Canal", titulo="Uno", ruta=str(tmp_path / "a.mkv"),
                   contenedor="mkv", bytes_=1000) is True
    # mismo id y perfil: no se duplica ni se pisa la ruta buena
    assert idx.add("abc123", perfil="video_720", ruta=str(tmp_path / "otro.mkv")) is False
    # mismo vídeo en otro perfil: es otro fichero, entra
    assert idx.add("abc123", perfil="opus_origen", ruta=str(tmp_path / "a.opus"), contenedor="opus") is True
    assert len(idx) == 2
    assert idx.ya_descargado("abc123").ruta.endswith(("a.mkv", "a.opus"))
    assert idx.ya_descargado("abc123", perfil="video_720").ruta.endswith("a.mkv")
    assert idx.ya_descargado("abc123", perfil="compatible") is None
    assert idx.ya_descargado("noexiste") is None and idx.ya_descargado("") is None


def test_faltan_preserves_order_and_ignores_blanks(tmp_path):
    idx = Index(ruta=tmp_path / "i.db")
    idx.add("a1", perfil="video_720")
    idx.add("b2", perfil="video_720")
    assert idx.faltan(["b2", "c3", "a1", "d4", "c3", "", None]) == ["c3", "d4"]
    assert idx.faltan([]) == []
    # con perfil, un vídeo bajado en otro perfil sigue faltando
    assert idx.faltan(["a1"], perfil="compatible") == ["a1"]
    assert idx.faltan(["a1"], perfil="video_720") == []


def test_faltan_with_more_than_the_sqlite_parameter_limit(tmp_path):
    idx = Index(ruta=tmp_path / "i.db")
    idx.add("v0", perfil="video_720")
    ids = [f"v{i}" for i in range(1000)]
    faltan = idx.faltan(ids)
    assert len(faltan) == 999 and "v0" not in faltan


def test_historial_is_newest_first_and_filtered_by_channel(tmp_path):
    idx = Index(ruta=tmp_path / "i.db")
    for i in range(5):
        idx.add(f"v{i}", perfil="video_720", canal="A" if i % 2 == 0 else "B", titulo=f"T{i}",
                descargado=time.time() + i)
    hist = idx.historial(3)
    assert [h.video_id for h in hist] == ["v4", "v3", "v2"]
    assert [h.video_id for h in idx.historial(10, canal="A")] == ["v4", "v2", "v0"]
    assert len(idx.historial(0)) == 5


def test_canales_and_total(tmp_path):
    idx = Index(ruta=tmp_path / "i.db")
    idx.add("a", perfil="video_720", canal="Uno", bytes_=100)
    idx.add("b", perfil="video_720", canal="Uno", bytes_=200)
    idx.add("c", perfil="video_720", canal="Dos", bytes_=50)
    canales = {c["canal"]: c for c in idx.canales()}
    assert canales["Uno"]["items"] == 2 and canales["Uno"]["bytes"] == 300
    assert idx.total() == {"items": 3, "bytes": 350}


def test_anotar_salida_reads_the_size_and_the_container(tmp_path):
    fichero = tmp_path / "Título [dQw4w9WgXcQ].mkv"
    fichero.write_bytes(b"x" * 2048)
    idx = Index(ruta=tmp_path / "i.db")
    assert idx.anotar_salida("dQw4w9WgXcQ", str(fichero), perfil="video_720", canal="C", titulo="T") is True
    item = idx.ya_descargado("dQw4w9WgXcQ", perfil="video_720")
    assert item.bytes == 2048 and item.contenedor == "mkv" and item.canal == "C"
    # con hash pedido, se guarda el sha256 del fichero
    idx2 = Index(ruta=tmp_path / "j.db")
    idx2.anotar_salida("abc", str(fichero), hash_=True)
    assert idx2.ya_descargado("abc").hash.startswith("sha256:")


def test_id_de_ruta_uses_the_template_brackets():
    assert id_de_ruta("/x/Título de prueba [dQw4w9WgXcQ].mkv") == "dQw4w9WgXcQ"
    assert id_de_ruta("Canal - 01 [abc123def45].mp4") == "abc123def45"
    assert id_de_ruta("abc123def45.mp4") == "abc123def45"
    assert id_de_ruta("sin id.mp4") == ""


def test_hash_fichero_missing_file_is_empty(tmp_path):
    assert hash_fichero(tmp_path / "no-existe") == ""


def test_update_and_borrar(tmp_path):
    idx = Index(ruta=tmp_path / "i.db")
    idx.add("a", perfil="video_720", ruta="/viejo.mkv")
    assert idx.actualizar("a", "video_720", ruta="/nuevo.mkv", bytes=42) is True
    assert idx.ya_descargado("a").ruta == "/nuevo.mkv"
    assert idx.actualizar("a", "video_720") is False       # sin campos, no hay nada que hacer
    assert idx.borrar("a", perfil="video_720") == 1
    assert idx.ya_descargado("a") is None
    assert idx.borrar("a") == 0


def test_survives_reopening(tmp_path):
    ruta = tmp_path / "i.db"
    Index(ruta=ruta).add("persistente", perfil="video_720", canal="C")
    otra = Index(ruta=ruta)
    assert otra.ya_descargado("persistente").canal == "C"


def test_add_without_id_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        Index(ruta=tmp_path / "i.db").add("")
