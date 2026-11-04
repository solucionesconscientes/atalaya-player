"""F2 · el enlace no se da por bueno hasta que enruta de verdad (H25).

cloudflared entrega la dirección a los 6-8 s, pero la dirección no enruta hasta pasados ~60 s. F2 añade la
comprobación por DNS-over-HTTPS (dos de tres resolvedores públicos) y, solo después, una petición HTTP a la raíz
del túnel. Estas pruebas fijan el orden y el contrato: con DNS pero sin HTTP no se entrega nada; sin DNS tampoco,
y la sala lo dice.
"""

from __future__ import annotations

import asyncio
import logging
import time

from mpvd.config import Settings
from mpvd.server import MpvdServer
from mpvd.share import service as service_mod
from mpvd.share import tunnel as tunnel_mod
from mpvd.share.rooms import Room
from mpvd.share.service import RoomRuntime, ShareService

URL = "https://sala-de-prueba.trycloudflare.com"


def make_service(tmp_path) -> ShareService:  # type: ignore[no-untyped-def]
    server = MpvdServer(Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache",
                                 data_dir=tmp_path / "data", idle_timeout=0, workers=1))
    return server.share


def make_runtime(tmp_path) -> RoomRuntime:  # type: ignore[no-untyped-def]
    return RoomRuntime(room=Room.new("sesion", 600), session_id=None, dir=tmp_path / "sala")


class FakeTunnel:
    name = "cloudflared"

    def __init__(self, url: str = URL):
        self.url = url
        self.started = False

    async def start(self, local_port: int) -> str:
        self.started = True
        return self.url

    async def stop(self) -> None:
        pass


def dns_result(answered: tuple[str, ...], addresses: tuple[str, ...] = ()) -> tunnel_mod.DnsResult:
    return tunnel_mod.DnsResult(host="sala-de-prueba.trycloudflare.com", answered=answered, at=time.time(),
                                addresses=addresses)


def fast(monkeypatch, seconds: float = 0.6) -> None:
    monkeypatch.setattr(service_mod, "TUNNEL_WARM_MAX", seconds)
    monkeypatch.setattr(service_mod, "TUNNEL_PROBE_EVERY", 0.05)
    monkeypatch.delenv("MPVD_SHARE_TUNNEL_PROBE", raising=False)


def test_dns_dice_que_si_pero_el_http_todavia_no_y_el_enlace_no_se_entrega(tmp_path, monkeypatch):
    """El caso que importa: el nombre ya resuelve (Cloudflare lo publica al crear el túnel) pero la raíz todavía
    no contesta porque el túnel aún no enruta. No se copia ni se anuncia el enlace."""
    svc = make_service(tmp_path)
    rt = make_runtime(tmp_path)
    svc.rt = rt
    svc.tunnel = FakeTunnel()
    svc.tunnel_requested = time.monotonic()
    fast(monkeypatch)
    events = []

    async def dns_ok(host, *args, **kwargs):
        return dns_result(("1.1.1.1", "8.8.8.8", "9.9.9.9"))

    monkeypatch.setattr(tunnel_mod, "resolve_public", dns_ok)
    monkeypatch.setattr(service_mod, "url_answers", lambda url, address=None: False)
    svc._host_push = lambda rt_, kind, text="", **extra: events.append(kind)

    asyncio.run(svc._open_tunnel(rt))

    assert svc.tunnel_state == "failed"
    assert svc.public_url is None and svc.pending_url is None
    assert svc.base_url().startswith("http://"), "mientras no enruta no se reparte la dirección pública"
    assert "link-ready" not in events and rt.invited is False
    assert "contestar" in svc.tunnel_error


def test_los_tres_resolvedores_dicen_que_no_y_se_dice(tmp_path, monkeypatch):
    """Sin DNS no hay ni una petición HTTP: se espera al corte y la sala lo cuenta, en vez de entregar un enlace
    que nadie va a poder abrir."""
    svc = make_service(tmp_path)
    rt = make_runtime(tmp_path)
    svc.rt = rt
    svc.tunnel = FakeTunnel()
    svc.tunnel_requested = time.monotonic()
    fast(monkeypatch)
    events = []
    probed = []

    async def dns_no(host, *args, **kwargs):
        return dns_result(())

    monkeypatch.setattr(tunnel_mod, "resolve_public", dns_no)
    monkeypatch.setattr(service_mod, "url_answers", lambda url, address=None: probed.append(url) or True)
    svc._host_push = lambda rt_, kind, text="", **extra: events.append(kind)

    asyncio.run(svc._open_tunnel(rt))

    assert svc.tunnel_state == "failed"
    assert svc.public_url is None and rt.invited is False
    assert "link-ready" not in events
    assert "resolvedor" in svc.tunnel_error
    assert probed == [], "sin resolver no se prueba el HTTP: sería una petición a ciegas"


def test_con_dns_y_http_el_enlace_queda_listo_y_se_mide(tmp_path, monkeypatch, caplog):
    """Camino bueno: dos de tres resolvedores contestan, la raíz contesta y solo entonces se anuncia el enlace,
    con el tiempo total y el de cada etapa en el log (campos, no una línea suelta)."""
    svc = make_service(tmp_path)
    rt = make_runtime(tmp_path)
    svc.rt = rt
    svc.tunnel = FakeTunnel()
    svc.tunnel_requested = time.monotonic()
    fast(monkeypatch)
    events = []

    async def dns_ok(host, *args, **kwargs):
        return dns_result(("1.1.1.1", "8.8.8.8"), ("104.16.0.1",))

    seen: list[str | None] = []

    def http(url, address=None):
        seen.append(address)
        return True

    monkeypatch.setattr(tunnel_mod, "resolve_public", dns_ok)
    monkeypatch.setattr(service_mod, "url_answers", http)
    svc._host_push = lambda rt_, kind, text="", **extra: events.append(kind)

    with caplog.at_level(logging.INFO, logger="mpvd.share"):
        asyncio.run(svc._open_tunnel(rt))

    assert svc.tunnel_state == "ready"
    assert svc.public_url == URL and svc.pending_url is None
    assert isinstance(svc.tunnel_ready_s, int) and svc.tunnel_ready_s >= 0
    assert "link-ready" in events and rt.invited is True
    record = next(r for r in caplog.records if getattr(r, "tunnel_total_s", None) is not None)
    assert record.tunnel_start_s >= 0 and record.tunnel_dns_s >= 0 and record.tunnel_http_s >= 0
    assert record.tunnel_dns_resolvers == ["1.1.1.1", "8.8.8.8"]
    assert seen == ["104.16.0.1"], "el HTTP va a la IP que dio el DoH, no al DNS del sistema"


def test_el_tope_de_peticiones_en_vuelo_descarta_con_429_y_libera_la_plaza():
    """F3 · con el invitado sondeando por el túnel, el servidor no deja crecer las peticiones en vuelo sin límite:
    la que llega con el tope lleno se descarta (429, lo que la página convierte en aviso) y la plaza vuelve al
    terminar la respuesta que la ocupaba."""
    from mpvd.remote.http import HttpServer, Response

    gate = asyncio.Event()

    async def handler(req):  # type: ignore[no-untyped-def]
        if req.path == "/stream":
            async def chunks():
                await gate.wait()
                yield b"ok"
            return Response(200, {}, b"", stream=chunks())
        return Response.text("ok")

    async def run() -> None:
        server = HttpServer(handler, name="test", max_inflight=1)
        await server.start("127.0.0.1", 0)
        try:
            first, w1 = await asyncio.open_connection("127.0.0.1", server.port)
            w1.write(b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n")
            await w1.drain()
            assert b"200" in await first.readuntil(b"\r\n\r\n")

            second, w2 = await asyncio.open_connection("127.0.0.1", server.port)
            w2.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            await w2.drain()
            assert b"429" in await second.readuntil(b"\r\n\r\n")
            assert b'"busy": true' in await second.read()
            w2.close()

            gate.set()
            assert await first.read() == b"ok"
            w1.close()
            await w1.wait_closed()

            third, w3 = await asyncio.open_connection("127.0.0.1", server.port)
            w3.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            await w3.drain()
            assert b"200" in await third.readuntil(b"\r\n\r\n")
            w3.close()
            await w3.wait_closed()
        finally:
            await server.stop()

    asyncio.run(run())
