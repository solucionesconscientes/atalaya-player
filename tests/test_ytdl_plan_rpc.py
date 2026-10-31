"""F3 · `ytdl.plan` y `ytdl.pistas` contestados por el daemon: la ficha previa antes de descargar.

Lo que se comprueba aquí es lo que ve el menú: qué pistas se van a pedir, en qué contenedor, cuánto va a ocupar y
cuánto va a tardar. El ``-J`` es el fixturado de YouTube (``tests/fixtures/ytdlp/youtube_bbb.json``) servido por el
yt-dlp falso: sin red y con los códecs reales (VP9, AV1, H.264, Opus y AAC, incluidos los ``-drc`` y el storyboard).

Un plan **no descarga nada**: por eso también se comprueba que la cola sigue vacía después de pedir las fichas.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer

FIX = Path(__file__).parent / "fixtures" / "ytdlp"
FAKE = FIX / "fake_ytdlp.py"
URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.fixture
def plan_env(tmp_path, media_dir, monkeypatch):
    arglog = tmp_path / "argv.log"
    monkeypatch.setenv("MPV_UOS_YTDLP", str(FAKE))
    monkeypatch.setenv("FAKE_YTDLP_ARGLOG", str(arglog))
    monkeypatch.setenv("FAKE_YTDLP_MEDIA", str(media_dir))
    monkeypatch.setenv("MPV_UOS_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("MPV_UOS_YTDLP_AUTO_UPDATE", "0")
    return tmp_path, arglog


def with_server(tmp_path: Path, fn):
    async def go():
        server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                     data_dir=tmp_path / "data", idle_timeout=0, workers=2))
        await server.start()
        try:
            async with MpvdClient(str(server.settings.socket_path)) as c:
                return await fn(server, c)
        finally:
            await server.stop()

    return asyncio.run(go())


def argv_lines(arglog: Path) -> list[list[str]]:
    if not arglog.exists():
        return []
    return [json.loads(ln) for ln in arglog.read_text().splitlines() if ln.strip()]


# ------------------------------------------------------------------ la ficha de los tres perfiles

def test_plan_contesta_la_ficha_de_cada_perfil(plan_env):
    tmp_path, arglog = plan_env

    async def fn(server, c):
        opus = await c.call("ytdl.plan", {"url": URL, "perfil": "opus_origen"})
        # voz (el fixturado no es música): el Opus más bajo del origen, sin recodificar
        assert opus["video_id"] is None and opus["audio_id"] == "249"
        assert opus["contenedor"] == "opus" and opus["recodificar"] is False
        assert opus["bytes_estimados"] and opus["bytes"] == opus["bytes_estimados"]
        assert opus["segundos"] >= 1 and opus["kbps"] > 0
        assert "Opus" in opus["nota"] and opus["duration"] == 635

        v720 = await c.call("ytdl.plan", {"url": URL, "perfil": "video_720"})
        assert (v720["video_id"], v720["audio_id"], v720["contenedor"]) == ("302", "249", "mkv")
        assert v720["recodificar"] is False and v720["descripcion"]
        assert v720["bytes"] and v720["segundos"] and v720["url"] == URL

        # «compatible» pide H.264 + AAC en MP4; el fixturado sí los tiene
        comp = await c.call("ytdl.plan", {"url": URL, "perfil": "compatible"})
        assert comp["contenedor"] == "mp4" and comp["recodificar"] is False

        # pistas a mano: la pareja se valida en el mismo sitio donde se pinta la ficha
        pistas = await c.call("ytdl.plan", {"url": URL, "perfil": "pistas", "video_id": "302", "audio_id": "251"})
        assert (pistas["video_id"], pistas["audio_id"], pistas["contenedor"]) == ("302", "251", "mkv")

        # un plan no encola nada: la cola sigue vacía
        assert await c.call("ytdl.downloads.list") == []
        # y no se baja nada: el único yt-dlp que se ejecutó fue el -J (una vez, cacheado)
        js = [a for a in argv_lines(arglog) if "-J" in a]
        assert len(js) == 1 and js[0][-1] == URL

    with_server(tmp_path, fn)


def test_plan_rechaza_con_el_motivo_y_no_inventa(plan_env):
    tmp_path, arglog = plan_env

    async def fn(server, c):
        with pytest.raises(RpcError) as e1:
            await c.call("ytdl.plan", {"url": URL, "perfil": "pistas", "video_id": "251"})
        assert "es de audio" in str(e1.value)

        with pytest.raises(RpcError) as e2:
            await c.call("ytdl.plan", {"url": URL, "perfil": "pistas", "audio_id": "999"})
        assert "ya no está en el origen" in str(e2.value)

        with pytest.raises(RpcError) as e3:
            await c.call("ytdl.plan", {"url": URL, "perfil": "pistas"})
        assert "al menos una pista" in str(e3.value)

        with pytest.raises(RpcError) as e4:
            await c.call("ytdl.plan", {"url": URL, "perfil": "inventado"})
        assert "desconocido" in str(e4.value)

    with_server(tmp_path, fn)


# ------------------------------------------------------------------ las pistas del origen

def test_pistas_trae_las_dos_listas_con_lo_que_hace_falta_para_elegir(plan_env):
    tmp_path, arglog = plan_env

    async def fn(server, c):
        res = await c.call("ytdl.pistas", {"url": URL})
        assert res["title"].startswith("Big Buck Bunny")
        assert res["counts"] == {"video": len(res["video"]), "audio": len(res["audio"])}
        assert res["counts"]["video"] >= 5 and res["counts"]["audio"] >= 3
        v = res["video"][0]
        for campo in ("id", "codec", "familia", "altura", "ancho", "fps", "kbps", "bytes", "idioma", "ext"):
            assert campo in v, campo
        assert v["altura"] >= 720 and v["codec"]
        a = res["audio"][0]
        for campo in ("id", "codec", "kbps", "canales", "asr", "idioma", "ext", "bytes"):
            assert campo in a, campo
        # el storyboard y las copias -drc no son pistas que se puedan bajar
        ids = [r["id"] for r in res["video"] + res["audio"]]
        assert not [i for i in ids if i.startswith("sb")], ids
        assert "251-drc" not in ids and "251" in ids
        # la pareja automática es la de la política, no la primera fila de la lista
        assert res["auto"]["video_id"] == "302" and res["auto"]["audio_id"] == "249"
        assert res["auto"]["contenedor"] == "mkv"

    with_server(tmp_path, fn)


def test_pistas_sin_pistas_utilizables_dice_el_motivo(plan_env):
    """El fixturado de archive.org no declara ni códec ni pistas separadas: se cuenta, no se finge una pareja."""
    tmp_path, arglog = plan_env

    async def fn(server, c):
        res = await c.call("ytdl.pistas", {"url": "https://archive.org/details/prueba"})
        assert res["video"] == [] and [a["codec"] for a in res["audio"]] == ["MP3"]
        assert res["auto"] is None and "no ofrece ninguna pista de vídeo" in res["motivo"]

    with_server(tmp_path, fn)


# ------------------------------------------------------------------ convertir (la ficha del catálogo)

def test_la_ficha_de_convertir_dice_el_resultado_y_cuanto_ocupa(plan_env):
    tmp_path, arglog = plan_env

    async def fn(server, c):
        res = await c.call("ytdl.plan", {"url": URL, "perfil": "convertir",
                                         "destino": {"vcod": "hevc", "acod": "aac", "altura": 720, "crf": 28}})
        assert res["recodificar"] is True and res["contenedor"] == "mp4"
        assert res["video_id"] and res["audio_id"]
        assert "H.265" in res["nota"] and "MP4" in res["nota"]
        assert res["descripcion"].startswith("H.265 CRF 28")
        assert res["bytes"] > 0 and res["segundos"] >= 1
        # el tamaño del resultado no es el del origen: se está recodificando
        assert res["bytes"] != res["bytes_origen"]

        # solo audio: no se baja vídeo, y el contenedor es el del códec
        solo = await c.call("ytdl.plan", {"url": URL, "perfil": "convertir",
                                          "destino": {"acod": "mp3", "audio_bitrate": 128}})
        assert solo["video_id"] is None and solo["contenedor"] == "mp3"
        assert solo["descripcion"].startswith("MP3 128k")

        with pytest.raises(RpcError) as e:
            await c.call("ytdl.plan", {"url": URL, "perfil": "convertir", "destino": {"vcod": "mpeg2"}})
        assert "no se puede convertir" in str(e.value)

    with_server(tmp_path, fn)


def test_los_catalogo_y_los_presets_llegan_en_la_misma_llamada(plan_env):
    tmp_path, arglog = plan_env

    async def fn(server, c):
        res = await c.call("ytdl.presets")
        cat = res["catalogo"]
        assert [a["id"] for a in cat["audio"]][:3] == ["opus", "aac", "alac"]
        assert "h264" in [v["id"] for v in cat["video"]]
        assert cat["canales"] == ["conservar", "estereo", "mono"] and cat["loudness"] == ["ninguna", "-18", "-14"]
        assert 720 in cat["resoluciones"] and 30 in cat["fps"]
        assert isinstance(cat["hardware"], dict) and "disponible" in cat["hardware"]
        # los presets de siempre siguen ahí y la conversión sigue fuera del defecto
        ids = [p["id"] for p in res["presets"]]
        assert "audio_opus_origen" in ids and "video_720" in ids and "compatible" in ids

    with_server(tmp_path, fn)


# ------------------------------------------------------------------ convertir al bajar, de verdad

async def _esperar_conversion(c, item_id: str, timeout: float = 180.0) -> dict:
    """El trabajo acaba en «done» y la conversión va después, en su propia tarea: se espera a que deje su parte."""
    import time

    deadline = time.monotonic() + timeout
    d: dict = {}
    while time.monotonic() < deadline:
        d = await c.call("ytdl.downloads.get", {"id": item_id})
        if (d.get("post") or {}).get("conversion"):
            return d
        if d["status"] == "failed":
            raise AssertionError(f"la descarga falló: {d.get('error')}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"la conversión no terminó: {d}")


def test_una_conversion_de_audio_al_bajar_deja_el_mp3_y_quita_el_intermedio(plan_env, media_dir):
    """De un FLAC de verdad a un MP3 de verdad: yt-dlp baja el original y ffmpeg lo convierte (comprobado con ffprobe)."""
    tmp_path, arglog = plan_env

    async def fn(server, c):
        item = await c.call("ytdl.download", {"url": "https://fake.test/clip", "kind": "audio_original",
                                              "acod_destino": "mp3", "audio_bitrate": 192,
                                              "audio_canales": "mono"})
        d = await _esperar_conversion(c, item["id"])
        conv = d["post"]["conversion"]
        assert conv["ok"] is True and conv["acodec"] == "mp3" and conv["contenedor"] == "mp3"
        salida = Path(d["outputs"][0])
        assert salida.suffix == ".mp3" and salida.is_file() and salida.stat().st_size > 0
        # el original (el .flac que se descargó) no se queda: era el paso intermedio
        assert [p.suffix for p in Path(d["out_dir"]).iterdir()] == [".mp3"]
        assert d["message"].startswith("listo")
        # la orden que se le dio a yt-dlp NO recodifica: baja el original tal cual (nada de --audio-format)
        argv = [a for a in argv_lines(arglog) if a[-1] == "https://fake.test/clip"][-1]
        assert "-x" in argv and "--audio-format" not in argv

    with_server(tmp_path, fn)


def test_una_conversion_de_video_al_bajar_sale_en_mp4_con_su_codec(plan_env, media_dir):
    tmp_path, arglog = plan_env

    async def fn(server, c):
        item = await c.call("ytdl.download", {"url": "https://fake.test/clip", "kind": "video",
                                              "vcod_destino": "h264", "acod_destino": "aac", "altura": 180,
                                              "audio_bitrate": 128})
        d = await _esperar_conversion(c, item["id"])
        conv = d["post"]["conversion"]
        assert conv["ok"] is True and conv["vcodec"] == "h264" and conv["acodec"] == "aac"
        salida = Path(d["outputs"][0])
        assert salida.suffix == ".mp4" and salida.is_file()
        assert conv["duracion"] > 0 and conv["avisos"] == []
        # el intermedio se bajó en MKV (acepta cualquier códec) y no se recodificó dentro de yt-dlp
        argv = [a for a in argv_lines(arglog) if a[-1] == "https://fake.test/clip"][-1]
        assert "--merge-output-format" in argv and argv[argv.index("--merge-output-format") + 1] == "mkv"
        assert "--recode-video" not in argv and "--audio-format" not in argv

    with_server(tmp_path, fn)
