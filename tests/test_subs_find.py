"""H36 · C5: una sola lista con todo lo que hay, ordenada por fiabilidad, y los proveedores que no se pueden ofrecer.

Lo importante del test no es el orden exacto de dos resultados equivalentes: es que lo que viene del propio vídeo gane
a lo que viene por hash, el hash gane al nombre, lo automático quede al final, y que un proveedor que falta diga POR QUÉ
(falta una clave, el sitio ya no existe) en vez de hacer creer que no hay subtítulos.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.hashing import file_hash
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.subs.service import RELIABILITY, UNAVAILABLE_PROVIDERS
from tests.test_opensubtitles import fake_api  # noqa: F401 - fixture


def run(settings: Settings, fn, setup=None):
    async def go():
        server = MpvdServer(settings)
        if setup is not None:
            setup(server)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                return await fn(c, server)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_los_proveedores_que_no_estan_dicen_por_que():
    assert RELIABILITY == ("canal", "hash", "nombre", "auto")
    ids = {p[0] for p in UNAVAILABLE_PROVIDERS}
    assert ids == {"subdl", "podnapisi"}
    for pid, name, reason in UNAVAILABLE_PROVIDERS:
        assert name and reason and len(reason) > 10, pid
    # comprobado el 2026-10-01: subdl pide clave (403 not_authorized) y podnapisi.net ya no resuelve
    assert "clave" in dict((p[0], p[2]) for p in UNAVAILABLE_PROVIDERS)["subdl"]
    assert "no existe" in dict((p[0], p[2]) for p in UNAVAILABLE_PROVIDERS)["podnapisi"]


def test_cascada_para_un_archivo_local(tmp_path, media_dir, fake_api):  # noqa: F811
    base, st = fake_api
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)
    video = tmp_path / "Mi.Serie.S01E02.720p.mkv"
    shutil.copyfile(media_dir / "serie" / "ep02.mkv", video)
    st["hash"] = file_hash(video).opensubtitles

    def setup(server):
        server.library.osub_url = base

    async def go(c, server):
        # 1. sin clave, OpenSubtitles no está «ok» pero dice por qué, y los dos proveedores imposibles también
        res = await c.call("subs.find", {"path": str(video)})
        prov = {p["id"]: p for p in res["providers"]}
        assert prov["opensubtitles"]["ok"] is False and "desactivad" in prov["opensubtitles"]["reason"]
        assert prov["web"]["ok"] is False and "internet" in prov["web"]["reason"]
        assert prov["subdl"]["ok"] is False and prov["podnapisi"]["ok"] is False
        assert res["sources"] == [] and res["web"] is False

        # 2. con clave: resultados mezclados y ordenados, el del hash primero
        await c.call("library.settings.set", {"osub_enabled": True, "osub_api_key": "clave-os",
                                             "osub_languages": "es,en"})
        res = await c.call("subs.find", {"path": str(video)})
        assert {p["id"] for p in res["providers"] if p["ok"]} == {"opensubtitles"}
        assert len(res["sources"]) >= 2
        assert res["sources"][0]["reliability"] == "hash" and res["sources"][0]["language"] == "es"
        orden = [RELIABILITY.index(s["reliability"]) for s in res["sources"]]
        assert orden == sorted(orden), orden
        assert all(s["provider"] == "opensubtitles" for s in res["sources"])

        # 3. el idioma preferido va antes: con en,es el inglés sube
        res_en = await c.call("subs.find", {"path": str(video), "languages": "en,es"})
        primeros = [s["language"] for s in res_en["sources"] if s["reliability"] == "nombre"]
        assert primeros and primeros[0] == "en"

        # 4. «pick» trae el elegido sin que el menú sepa de qué proveedor es
        got = await c.call("subs.pick", {"source": res["sources"][0]["pick"]})
        assert got["status"] == "done" and Path(got["srt"]).is_file()
        assert Path(got["srt"]).read_text(encoding="utf-8").startswith("1\n")

        # 5. un pick inventado no se ejecuta
        with pytest.raises(RpcError):
            await c.call("subs.pick", {"source": {"otracosa": {"path": str(video)}}})

    run(settings, go, setup)
