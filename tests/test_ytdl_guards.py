"""Guardas de la cola: tope por lista, espacio en disco, archivo por defecto en listas, verificación con reintento,
informe de fallidos y pausa/reanudación con prioridades."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.ytdl import downloads as dl
from mpvd.ytdl.presets import DownloadSpec, build_args

FIX = Path(__file__).parent / "fixtures" / "ytdlp"
FAKE = FIX / "fake_ytdlp.py"


# -- espacio -----------------------------------------------------------------------------------


def test_necesario_is_estimate_plus_margin_and_breathing_room():
    assert dl.necesario(0) == 2 * 1024 ** 3
    assert dl.necesario(10 * 1024 ** 3) == int(10 * 1024 ** 3 * 1.2 + 2 * 1024 ** 3)
    assert dl.necesario(-5) == 2 * 1024 ** 3


def test_comprobar_espacio_ok_and_not_ok(tmp_path, monkeypatch):
    bien = dl.comprobar_espacio(tmp_path, 1024)
    assert bien["ok"] is True and bien["libre"] > 0 and bien["motivo"] == ""
    monkeypatch.setattr(dl, "espacio_libre", lambda destino: 3 * 1024 ** 3)
    no = dl.comprobar_espacio(tmp_path, 5 * 1024 ** 3)
    assert no["ok"] is False and "no cabe" in no["motivo"] and no["necesario"] > no["libre"]
    monkeypatch.setattr(dl, "espacio_libre", lambda destino: None)
    raro = dl.comprobar_espacio(tmp_path, 1)
    assert raro["ok"] is True and "no se pudo mirar" in raro["motivo"]


def test_submit_refuses_when_the_disk_cannot_take_it(tmp_path, monkeypatch):
    from mpvd.jobs import JobQueue

    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path))
    mgr = dl.DownloadManager(JobQueue(), lambda: None, tmp_path)
    monkeypatch.setattr(dl, "espacio_libre", lambda destino: 1024 ** 3)
    spec = DownloadSpec(url="https://fake.test/v", perfil="video_720")
    spec.extra["bytes_estimados"] = 5 * 1024 ** 3
    with pytest.raises(ValueError) as exc:
        mgr.submit(spec)
    assert "no cabe" in str(exc.value)
    # con la guarda apagada se encola igual (el usuario sabe lo que hace)
    mgr.settings.guard_space = False
    assert mgr.submit(spec).status in ("queued", "running")
    asyncio.run(mgr.cancel_all())


# -- verificación ------------------------------------------------------------------------------


def media_file(media_dir: Path) -> Path:
    """Un fichero de vídeo real de los que genera ``tools/make_test_media.sh``."""
    manifest = json.loads((media_dir / "manifest.json").read_text(encoding="utf-8"))
    for nombre, row in manifest.items():
        if isinstance(row, dict) and row.get("video") and (media_dir / nombre).is_file():
            return media_dir / nombre
    return next(p for p in sorted(media_dir.glob("*")) if p.suffix in (".mkv", ".mp4", ".webm"))


def test_verificar_salida_good_file(media_dir):
    res = dl.verificar_salida(media_file(media_dir), {"audio": True, "video": True}, segundos=1)
    assert res["ok"] is True and res["duracion"] > 0 and res["pistas"]["video"] is True


def test_verificar_salida_detects_empty_missing_and_garbage(tmp_path, media_dir):
    vacio = tmp_path / "vacio.mp4"
    vacio.write_bytes(b"")
    assert dl.verificar_salida(vacio)["ok"] is False
    assert dl.verificar_salida(tmp_path / "no-existe.mp4")["ok"] is False
    basura = tmp_path / "basura.mp4"
    basura.write_bytes(b"no soy un video" * 500)
    res = dl.verificar_salida(basura)
    assert res["ok"] is False and res["motivo"]
    # pedir imagen en un fichero que solo tiene sonido también es un fallo
    audio = next((p for p in media_dir.glob("*.flac")), None)
    if audio is not None:
        assert dl.verificar_salida(audio, {"video": True, "audio": True})["ok"] is False


# -- archivo por defecto en listas -------------------------------------------------------------


def test_build_args_uses_archive_and_break_on_existing():
    spec = DownloadSpec(url="https://www.youtube.com/channel/UCx", playlist=True, archive=True)
    spec.extra.update({"archive_file": "/tmp/a.txt", "break_on_existing": True, "dateafter": "20250101"})
    argv = build_args(spec, "/tmp/salida")
    assert argv[argv.index("--download-archive") + 1] == "/tmp/a.txt"
    assert "--break-on-existing" in argv and argv[argv.index("--dateafter") + 1] == "20250101"
    # sin archive no se pasa el corte: yt-dlp no lo entendería
    otro = DownloadSpec(url="https://www.youtube.com/channel/UCx", playlist=True, archive=False)
    otro.extra.update({"archive_file": "/tmp/a.txt", "break_on_existing": True})
    assert "--break-on-existing" not in build_args(otro, "/tmp/salida")


# -- servidor con el yt-dlp falso ---------------------------------------------------------------


def make_settings(tmp_path: Path) -> Settings:
    return Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                    idle_timeout=0, workers=3)


def with_server(tmp_path: Path, fn):
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
    arglog = tmp_path / "argv.log"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_ARGLOG", str(arglog))
    monkeypatch.setenv("FAKE_YTDLP_MEDIA", str(media_dir))
    monkeypatch.setenv("FAKE_YTDLP_DELAY", "0.01")
    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    monkeypatch.setenv("MPV_UOS_YTDLP_SUBS", "0")
    return tmp_path, arglog


def urls_de(n: int) -> list[str]:
    return [f"https://fake.test/ejemplo{i}" for i in range(n)]


def test_batch_over_the_cap_asks_before_queueing(ytdl_env):
    tmp_path, _ = ytdl_env

    async def fn(server, c):
        with pytest.raises(RpcError) as exc:
            await c.call("ytdl.download.batch", {"urls": urls_de(55)})
        assert "55" in str(exc.value) and (exc.value.data or {}).get("asks") == "confirm"
        assert await c.call("ytdl.downloads.list", {}) == []       # no se encoló nada a ciegas

        # «los primeros N»: baja exactamente N
        r = await c.call("ytdl.download.batch", {"urls": urls_de(55), "first": 3})
        assert r["count"] == 3

    with_server(tmp_path, fn)


def test_batch_archive_by_default_and_no_archive_when_asked(ytdl_env):
    tmp_path, arglog = ytdl_env

    async def fn(server, c):
        await c.call("ytdl.download.batch", {"urls": ["https://www.youtube.com/playlist?list=PLprueba"],
                                             "options": {"playlist": True, "list_folder": False}})
        await c.call("ytdl.download.batch", {"urls": ["https://fake.test/sin-archivo"],
                                             "options": {"archive": False}})
        return True

    async def esperar(server, c):
        await fn(server, c)
        for _ in range(600):
            if not [d for d in await c.call("ytdl.downloads.list", {"include_finished": False})]:
                break
            await asyncio.sleep(0.05)
        return True

    with_server(tmp_path, esperar)
    lineas = [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()]
    con_lista = [a for a in lineas if any("PLprueba" in x for x in a)]
    sin_archivo = [a for a in lineas if any("sin-archivo" in x for x in a)]
    assert con_lista and "--download-archive" in con_lista[0] and "--yes-playlist" in con_lista[0]
    assert sin_archivo and "--download-archive" not in sin_archivo[0]


def test_report_names_the_failures(ytdl_env):
    tmp_path, _ = ytdl_env

    async def fn(server, c):
        bien = await c.call("ytdl.download", {"url": "https://fake.test/bueno"})
        mal = await c.call("ytdl.download", {"url": "https://fake.test/fail"})
        for item in (bien, mal):
            for _ in range(600):
                d = await c.call("ytdl.downloads.get", {"id": item["id"]})
                if d["status"] in ("done", "failed"):
                    break
                await asyncio.sleep(0.05)
        inf = await c.call("ytdl.downloads.report", {})
        assert inf["total"] == 2 and inf["ok"] == 1 and inf["failed"] == 1
        assert inf["failed_items"] and "fail" in inf["failed_items"][0]["url"]
        assert inf["failed_items"][0]["motivo"].strip() != ""
        return True

    with_server(tmp_path, fn)


def test_verify_failure_retries_once_and_then_fails(ytdl_env, monkeypatch):
    tmp_path, _ = ytdl_env
    llamadas = {"n": 0}
    original = dl.verificar_salida

    def roto(ruta, esperado=None, segundos=10.0):
        llamadas["n"] += 1
        return {"ok": False, "motivo": "el fichero no decodifica: simulado", "duracion": None, "pistas": {}}

    monkeypatch.setattr(dl, "verificar_salida", roto)

    async def fn(server, c):
        item = await c.call("ytdl.download", {"url": "https://fake.test/roto"})
        for _ in range(600):
            d = await c.call("ytdl.downloads.get", {"id": item["id"]})
            if d["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)
        assert d["status"] == "failed" and "decodifica" in d["error"]
        assert llamadas["n"] == 2          # un reintento, no un bucle
        return True

    with_server(tmp_path, fn)
    monkeypatch.setattr(dl, "verificar_salida", original)


def test_queue_pause_and_resume(ytdl_env):
    tmp_path, _ = ytdl_env

    async def fn(server, c):
        ids = [i["id"] for i in (await c.call("ytdl.download.batch", {"urls": urls_de(4)}))["items"]]
        pausa = await c.call("ytdl.queue.pause", {})
        assert pausa["paused"] >= 1
        estados = {d["status"] for d in await c.call("ytdl.downloads.list", {"include_finished": False})}
        assert estados <= {"paused"}
        reanuda = await c.call("ytdl.queue.resume", {})
        assert reanuda["resumed"] == pausa["paused"]
        for i in ids:
            for _ in range(600):
                d = await c.call("ytdl.downloads.get", {"id": i})
                if d["status"] in ("done", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.05)
            assert d["status"] == "done"
        return True

    with_server(tmp_path, fn)


def test_priority_is_accepted_and_validated(ytdl_env):
    tmp_path, _ = ytdl_env

    async def fn(server, c):
        item = await c.call("ytdl.download", {"url": "https://fake.test/prioridad"})
        d = await c.call("ytdl.queue.priority", {"id": item["id"], "prioridad": "alta"})
        assert d["spec"]["extra"]["priority"] == "high"
        with pytest.raises(RpcError):
            await c.call("ytdl.queue.priority", {"id": item["id"], "prioridad": "urgentísima"})
        with pytest.raises(RpcError):
            await c.call("ytdl.queue.priority", {"id": "noexiste"})
        return True

    with_server(tmp_path, fn)


def test_index_is_written_when_a_download_finishes(ytdl_env):
    tmp_path, _ = ytdl_env

    async def fn(server, c):
        item = await c.call("ytdl.download", {"url": "https://fake.test/indexado"})
        for _ in range(600):
            d = await c.call("ytdl.downloads.get", {"id": item["id"]})
            if d["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)
        hist = await c.call("ytdl.index.historial", {"n": 10})
        assert hist["total"]["items"] >= 1
        assert hist["items"] and hist["items"][0]["ruta"]
        faltan = await c.call("ytdl.index.faltan", {"ids": [hist["items"][0]["video_id"], "otro0000000"]})
        assert faltan["faltan"] == ["otro0000000"]
        return True

    with_server(tmp_path, fn)
