"""Suscripciones por feed: el Atom de YouTube, las reglas por canal, el sondeo incremental y el fondo histórico.

Nada de red: el feed es el XML grabado en ``tests/fixtures/ytdlp/youtube_feed.xml`` y el ``-J`` de cada vídeo lo pone
el yt-dlp falso.
"""

import json
import datetime
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.ytdl import subscriptions as subs
from mpvd.ytdl.index import Index
from mpvd.ytdl.presets import DownloadSpec, build_args

FIX = Path(__file__).parent / "fixtures" / "ytdlp"
FEED_XML = (FIX / "youtube_feed.xml").read_bytes()
FAKE = FIX / "fake_ytdlp.py"
CANAL = "UCprueba0000000000000000"


# -- direcciones y feed ------------------------------------------------------------------------


def test_feed_url_and_channel_id():
    assert subs.feed_url(CANAL) == f"https://www.youtube.com/feeds/videos.xml?channel_id={CANAL}"
    assert subs.feed_url(f"https://www.youtube.com/channel/{CANAL}/videos") == subs.feed_url(CANAL)
    assert subs.feed_url(subs.feed_url(CANAL)) == subs.feed_url(CANAL)
    assert subs.canal_id(f"https://www.youtube.com/channel/{CANAL}") == CANAL
    assert subs.canal_id("no hay id aquí") == ""


def test_feed_url_rejects_handles_without_resolving():
    with pytest.raises(subs.SubError) as exc:
        subs.feed_url("@unCanal")
    assert "resolver" in str(exc.value)
    with pytest.raises(subs.SubError):
        subs.feed_url("")


def test_entradas_newest_first_with_video_ids():
    filas = subs.entradas(FEED_XML)
    assert [f["video_id"] for f in filas] == ["vidprimero1", "vidtercero1", "vidsegundo2"]
    assert filas[0]["title"] == "Directo de la semana #shorts"
    assert filas[0]["url"] == "https://www.youtube.com/watch?v=vidprimero1"
    assert filas[0]["published"] and filas[0]["duration"] is None


def test_entradas_bad_document_is_a_clear_error():
    with pytest.raises(subs.SubError):
        subs.entradas(b"<html><body>no soy un feed</body></html>")


def test_video_id_from_entry_id_or_url():
    assert subs.video_id_de("yt:video:dQw4w9WgXcQ", "") == "dQw4w9WgXcQ"
    assert subs.video_id_de("", "https://www.youtube.com/watch?v=abc123def45&t=10") == "abc123def45"
    assert subs.video_id_de("", "https://youtu.be/abc123def45") == "abc123def45"
    assert subs.video_id_de("", "https://ejemplo.test/video") == ""


# -- reglas ------------------------------------------------------------------------------------


def test_regla_title_filter_duration_and_window():
    r = subs.Regla(titulo_excluir="#shorts", duracion_min=120)
    assert r.excluye_titulo("Un directo #Shorts") is True
    assert r.excluye_titulo("Una entrevista normal") is False
    assert r.duracion_vale(60) is False and r.duracion_vale(600) is True
    assert r.duracion_vale(None) is True          # sin dato no se descarta: se mira al bajar
    # ventana de 22:00 a 08:00 (cruza la medianoche)
    noche = subs.Regla(hora_desde=22, hora_hasta=8)
    assert noche.en_ventana(time.mktime((2026, 10, 31, 23, 0, 0, 0, 0, -1))) is True
    assert noche.en_ventana(time.mktime((2026, 10, 31, 3, 0, 0, 0, 0, -1))) is True
    assert noche.en_ventana(time.mktime((2026, 10, 31, 12, 0, 0, 0, 0, -1))) is False
    assert subs.Regla().en_ventana(0.0) is True   # 0 → 24: siempre


def test_regla_validation_is_legible():
    for malo in ({"perfil": "pistas"}, {"intervalo": 45}, {"hora_desde": 30}, {"tope": 0},
                 {"titulo_excluir": "(["}):
        with pytest.raises(subs.SubError):
            subs.Regla(**malo).validar()
    assert subs.Regla().pasa({"title": "x", "duration": None}) is True


def test_nuevos_dedupes_seen_index_and_applies_filters(tmp_path):
    s = subs.Suscripcion(canal=CANAL, regla=subs.Regla(titulo_excluir="#shorts", tope=10))
    filas = subs.entradas(FEED_XML)
    assert [f["video_id"] for f in subs.nuevos(s, filas)] == ["vidtercero1", "vidsegundo2"]
    s.marcar_vistos(["vidtercero1"])
    assert [f["video_id"] for f in subs.nuevos(s, filas)] == ["vidsegundo2"]
    # el índice manda: lo ya descargado no vuelve a la cola
    idx = Index(ruta=tmp_path / "i.db")
    idx.add("vidsegundo2", perfil="video_720")
    assert subs.nuevos(s, filas, idx) == []


def test_tope_limita_los_nuevos_por_sondeo():
    s = subs.Suscripcion(canal=CANAL, regla=subs.Regla(tope=1))
    assert len(subs.nuevos(s, subs.entradas(FEED_XML))) == 1


def test_spec_de_carries_profile_priority_and_no_list():
    s = subs.Suscripcion(canal=CANAL, regla=subs.Regla(perfil="compatible", subtitulos=True))
    spec = subs.spec_de({"video_id": "abc123def45", "title": "T", "url": ""}, s)
    assert spec.perfil == "compatible" and spec.playlist is False and spec.archive is True
    assert spec.subtitles is True and spec.extra["priority"] == "low"
    assert spec.url.endswith("watch?v=abc123def45")


# -- el almacén --------------------------------------------------------------------------------


def test_store_add_update_delete_and_reload(tmp_path):
    store = subs.Suscripciones(tmp_path / "subs.json")
    s = store.add(CANAL, titulo="Canal de prueba", intervalo=30, tope=3)
    assert s.regla.intervalo == 30 and s.regla.tope == 3
    with pytest.raises(subs.SubError):
        store.add(CANAL)                       # ya se sigue
    store.update(s.id, perfil="compatible", activa=False)
    otra = subs.Suscripciones(tmp_path / "subs.json")
    recargada = otra.get(s.id)
    assert recargada is not None and recargada.regla.perfil == "compatible" and recargada.activa is False
    with pytest.raises(subs.SubError):
        otra.update(s.id, perfil="pistas")
    assert store.borrar(s.id) is True and store.lista() == []
    assert store.borrar(s.id) is False


def test_store_debida_respects_interval_and_window(tmp_path):
    store = subs.Suscripciones(tmp_path / "subs.json")
    s = store.add(CANAL, intervalo=360)
    # Hora fija (06:00 local) para que la prueba no dependa de cuándo se ejecute: con time.time(), a las
    # 17:00 el +7 h caía dentro de la ventana 22-23 y el test fallaba sin que hubiera nada roto.
    ahora = datetime.datetime(2026, 6, 15, 6, 0).timestamp()
    assert s.debida(ahora) is True                    # nunca se ha mirado
    s.ultima = ahora
    assert s.debida(ahora) is False
    assert s.debida(ahora + 6 * 3600 + 10) is True
    s.regla.hora_desde, s.regla.hora_hasta = 22, 23   # solo de 22:00 a 23:00: a las 6 de la mañana no toca
    assert s.debida(ahora + 7 * 3600) is False


def test_estado_texto(tmp_path):
    store = subs.Suscripciones(tmp_path / "subs.json")
    s = store.add(CANAL, intervalo=1440)
    store.anotar(s.id, nuevos=3, error="")
    assert "3 nuevos" in s.estado()
    store.anotar(s.id, nuevos=0, error="no se pudo leer el feed")
    assert "error:" in s.estado()


# -- fondo histórico ---------------------------------------------------------------------------


def test_historico_args_and_spec():
    args = subs.historico_args(time.mktime((2025, 1, 15, 12, 0, 0, 0, 0, -1)), "/tmp/archivo.txt")
    assert "--dateafter" in args and "20250115" in args
    assert args[args.index("--download-archive") + 1] == "/tmp/archivo.txt"
    assert "--break-on-existing" in args
    # sin archivo no hay corte: --break-on-existing a solas no tiene sentido
    solo_fecha = subs.historico_args(time.mktime((2025, 1, 15, 12, 0, 0, 0, 0, -1)), "")
    assert "--break-on-existing" not in solo_fecha

    s = subs.Suscripcion(canal=CANAL, regla=subs.Regla(perfil="opus_origen"))
    spec = subs.spec_historico(CANAL, s, desde=time.mktime((2025, 1, 15, 12, 0, 0, 0, 0, -1)),
                               archivo="/tmp/archivo.txt")
    assert spec.playlist is True and spec.archive is True and spec.perfil == "opus_origen"
    assert spec.url == f"https://www.youtube.com/channel/{CANAL}"
    argv = build_args(spec, "/tmp/salida")
    assert "--download-archive" in argv and "/tmp/archivo.txt" in argv
    assert "--dateafter" in argv and "--break-on-existing" in argv and "--yes-playlist" in argv


# -- RPC de punta a punta con el yt-dlp falso ---------------------------------------------------


def make_settings(tmp_path: Path) -> Settings:
    return Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                    idle_timeout=0, workers=3)


def with_server(tmp_path: Path, fn):
    import asyncio

    async def go():
        server = MpvdServer(make_settings(tmp_path))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


@pytest.fixture
def ytdl_env(tmp_path, media_dir, monkeypatch):
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_ARGLOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_YTDLP_MEDIA", str(media_dir))
    monkeypatch.setenv("FAKE_YTDLP_DELAY", "0.01")
    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    monkeypatch.setenv("MPV_UOS_YTDLP_SUBS", "0")     # el bucle no se lanza: el sondeo se pide a mano
    monkeypatch.setattr(subs, "leer_feed", lambda valor, fetcher=None, timeout=None: FEED_XML)
    return tmp_path


def test_rpc_subs_add_list_check_and_dedupe(ytdl_env):
    tmp_path = ytdl_env

    async def fn(server, c):
        alta = await c.call("ytdl.subs.add", {"url": CANAL, "titulo": "Canal de prueba", "intervalo": 30,
                                              "titulo_excluir": "#shorts"})
        assert alta["canal"] == CANAL and alta["regla"]["intervalo"] == 30
        lista = await c.call("ytdl.subs.list")
        assert len(lista["suscripciones"]) == 1 and lista["intervalos"] == [30, 360, 1440]
        assert lista["perfiles"] == ["opus_origen", "video_720", "compatible"]

        r = await c.call("ytdl.subs.check", {"id": alta["id"]})
        assert r["sondeadas"] == 1 and r["nuevos"] == 2          # el #shorts queda fuera
        cola = await c.call("ytdl.downloads.list", {"include_finished": False})
        assert sorted(i["url"].rsplit("v=", 1)[-1] for i in cola) == ["vidsegundo2", "vidtercero1"]

        # segunda pasada: nada nuevo, y no se repite lo ya encolado
        r2 = await c.call("ytdl.subs.check", {"id": alta["id"]})
        assert r2["nuevos"] == 0
        assert len(await c.call("ytdl.downloads.list", {})) == 2

        # un canal que no existe en el almacén: error claro, no un silencio
        with pytest.raises(RpcError):
            await c.call("ytdl.subs.check", {"id": "noexiste"})

        # borrar el canal deja lo descargado donde está
        assert (await c.call("ytdl.subs.remove", {"id": alta["id"]}))["removed"] is True
        assert (await c.call("ytdl.subs.list"))["suscripciones"] == []

    with_server(tmp_path, fn)


def test_rpc_subs_check_skips_what_the_index_already_has(ytdl_env):
    tmp_path = ytdl_env

    async def fn(server, c):
        idx = Index(salida=tmp_path / "dl")
        idx.add("vidprimero1", perfil="video_720")
        idx.add("vidtercero1", perfil="video_720")
        alta = await c.call("ytdl.subs.add", {"url": CANAL})
        r = await c.call("ytdl.subs.check", {"id": alta["id"]})
        assert r["nuevos"] == 1
        urls = [i["url"] for i in await c.call("ytdl.downloads.list", {"include_finished": False})]
        assert urls and urls[0].endswith("vidsegundo2")

    with_server(tmp_path, fn)


def test_rpc_subs_historico_enqueues_a_channel_with_archive(ytdl_env):
    tmp_path = ytdl_env

    async def fn(server, c):
        alta = await c.call("ytdl.subs.add", {"url": CANAL})
        item = await c.call("ytdl.subs.historico", {"id": alta["id"]})
        assert item["spec"]["playlist"] is True and item["spec"]["archive"] is True
        d = await c.call("ytdl.downloads.get", {"id": item["id"]})
        assert "--download-archive" in d["argv"] and "--break-on-existing" in d["argv"]
        assert "--yes-playlist" in d["argv"]

    with_server(tmp_path, fn)


# -- subtítulos: dos opciones independientes, apagadas por defecto ---------------------------------


def test_subs_embed_y_subs_file_apagados_por_defecto():
    spec = DownloadSpec(url="https://x/y")
    assert spec.subs_embed is False and spec.subs_file is False


def test_subs_file_y_subs_embed_generan_sus_argumentos_y_conviven():
    file = build_args(DownloadSpec(url="https://x/y", subs_file=True, sub_langs="es.*"), "/o")
    assert "--write-subs" in file and "--sub-format" in file and "--convert-subs" in file
    assert "--embed-subs" not in file and file[file.index("--sub-langs") + 1] == "es.*"
    embed = build_args(DownloadSpec(url="https://x/y", subs_embed=True, sub_langs="es.*"), "/o")
    assert "--embed-subs" in embed and "--convert-subs" not in embed
    both = build_args(DownloadSpec(url="https://x/y", subs_embed=True, subs_file=True, sub_langs="es.*"), "/o")
    assert "--embed-subs" in both and "--convert-subs" in both
    # compatibilidad: subtitles=True + subs_mode=file equivale a subs_file
    legacy = build_args(DownloadSpec(url="https://x/y", subtitles=True, subs_mode="file", sub_langs="es.*"), "/o")
    assert "--convert-subs" in legacy and "--embed-subs" not in legacy
