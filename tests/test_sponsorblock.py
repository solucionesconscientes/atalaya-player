"""H39/E3 · SponsorBlock al reproducir, sin decirle a nadie qué estás viendo.

Lo que se comprueba: que la petición lleva solo 4 caracteres del hash del id (nunca el id ni la URL), que de la
respuesta se queda con los tramos del vídeo pedido y tira los de los demás, que las categorías se respetan, y que un
vídeo que no es de YouTube se contesta con una explicación en vez de con un error.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import threading

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer
from mpvd.sponsorblock import CATEGORIES, DEFAULT_CATEGORIES, hash_prefix, parse, video_id

VID = "aqz-KE-bpKQ"


def fake_api(payload):
    """Servidor que guarda lo que se le pidió: así se puede comprobar que NO se manda el id del vídeo."""
    pedidos: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            pedidos.append(self.path)
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, pedidos


def test_el_id_del_video_no_sale_del_equipo():
    assert video_id("https://www.youtube.com/watch?v=" + VID) == VID
    assert video_id("https://youtu.be/" + VID) == VID
    assert video_id("https://www.youtube.com/shorts/" + VID) == VID
    assert video_id("https://vimeo.com/123") is None and video_id("") is None
    p = hash_prefix(VID)
    assert len(p) == 4 and all(c in "0123456789abcdef" for c in p)
    assert VID not in p


def test_se_queda_solo_con_los_tramos_de_ese_video():
    payload = [
        {"videoID": "otro-video-1", "segments": [{"category": "sponsor", "actionType": "skip", "segment": [1, 50]}]},
        {"videoID": VID, "segments": [
            {"category": "sponsor", "actionType": "skip", "segment": [30.5, 45.25], "votes": 7, "UUID": "a"},
            {"category": "intro", "actionType": "skip", "segment": [0, 10]},          # categoría no pedida
            {"category": "sponsor", "actionType": "mute", "segment": [60, 70]},       # no se salta: no se inventa
            {"category": "sponsor", "actionType": "skip", "segment": [45.3, 50]},     # pegado al primero: se une
            {"category": "sponsor", "actionType": "skip", "segment": [80, 80.2]},     # demasiado corto
        ]},
    ]
    segs = parse(payload, VID, ["sponsor"])
    assert [(s["start"], s["end"]) for s in segs] == [(30.5, 50.0)]
    assert segs[0]["label"] == "patrocinio" and segs[0]["votes"] == 7
    assert parse(payload, "no-existe", ["sponsor"]) == []
    assert all(c in CATEGORIES for c in DEFAULT_CATEGORIES)


def test_rpc_pide_por_prefijo_y_contesta_lo_de_este_video(tmp_path, monkeypatch):
    payload = [
        {"videoID": "ruido-ruido", "segments": [{"category": "sponsor", "actionType": "skip", "segment": [0, 99]}]},
        {"videoID": VID, "segments": [{"category": "selfpromo", "actionType": "skip", "segment": [12, 20]}]},
    ]
    httpd, pedidos = fake_api(payload)
    monkeypatch.setenv("MPV_UOS_SPONSORBLOCK_URL", f"http://127.0.0.1:{httpd.server_port}/api/skipSegments")
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)

    async def go():
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                res = await c.call("sponsorblock.segments", {"url": f"https://www.youtube.com/watch?v={VID}"})
                assert res["video_id"] == VID and res["prefix"] == hash_prefix(VID)
                assert [(s["start"], s["end"], s["category"]) for s in res["segments"]] == [(12.0, 20.0, "selfpromo")]
                # lo único que viajó: el prefijo del hash y las categorías
                assert len(pedidos) == 1 and hash_prefix(VID) in pedidos[0]
                assert VID not in pedidos[0] and "youtube" not in pedidos[0]
                # la segunda vez sale de la caché: ni una petición más
                await c.call("sponsorblock.segments", {"url": f"https://youtu.be/{VID}"})
                assert len(pedidos) == 1

                # un vídeo que no es de YouTube: se explica, no se revienta
                fuera = await c.call("sponsorblock.segments", {"url": "https://vimeo.com/123"})
                assert fuera["supported"] is False and "YouTube" in fuera["reason"] and fuera["segments"] == []

                # categorías: solo las conocidas, y pedir basura es un error de parámetros
                cats = await c.call("sponsorblock.categories")
                assert {c["id"] for c in cats["categories"]} == set(CATEGORIES)
                with pytest.raises(RpcError):
                    await c.call("sponsorblock.segments", {"url": f"https://youtu.be/{VID}",
                                                           "categories": ["inventada"]})
        finally:
            await server.stop()
            httpd.shutdown()

    asyncio.run(go())


@pytest.mark.network
def test_contra_el_servicio_real_la_forma_es_la_que_esperamos(tmp_path):
    """La API pública cambia sin avisar: esto comprueba la forma de la respuesta, no su contenido."""
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)

    async def go():
        server = MpvdServer(settings)
        res = await server.sponsorblock.segments(f"https://www.youtube.com/watch?v={VID}")
        assert res["supported"] is True and res["video_id"] == VID
        assert isinstance(res["segments"], list)
        for s in res["segments"]:
            assert s["end"] > s["start"] and s["category"] in CATEGORIES and s["label"]

    asyncio.run(go())
