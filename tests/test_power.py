"""H40 · despertar para grabar y apagar al terminar, sin apagarle el ordenador a nadie por error.

Nada de esto toca el equipo de verdad: `MPVD_POWER_FAKE` sustituye TODAS las órdenes de energía por un programa que
apunta lo que se le pidió. Lo que se comprueba es la aritmética (el despertador 5 min antes), la orden exacta que se
ejecutaría en esta plataforma, los tres seguros y que el aviso cancelable manda.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from mpvd import notify, power
from mpvd.config import Settings
from mpvd.iptv.model import Channel
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer

FAKE = """#!/usr/bin/env python3
import sys, pathlib
pathlib.Path(sys.argv[0] + ".log").open("a").write(" ".join(sys.argv[1:]) + "\\n")
print("ok")
"""


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    fake = tmp_path / "fake-power"
    fake.write_text(FAKE, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("MPVD_POWER_FAKE", str(fake))
    monkeypatch.setenv("MPV_UOS_NO_NOTIFY", "1")        # sin avisos de escritorio en los tests
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)
    server = MpvdServer(settings)
    return server, fake


def ordenes(fake: Path) -> list[str]:
    log = Path(str(fake) + ".log")
    return log.read_text(encoding="utf-8").strip().splitlines() if log.exists() else []


def test_lo_que_puede_hacer_este_equipo_se_dice_sin_inventar(tmp_path):
    """Sin el programa falso: en ESTE equipo el despertador necesita sudo, y eso se dice con la orden exacta."""
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)
    caps = MpvdServer(settings).power.capabilities()
    assert caps["platform"] == sys.platform and caps["fake"] is False
    assert set(caps["actions"]) == {"nothing", "suspend", "shutdown"}
    if not caps["can_wake"]:
        assert caps["reason"] and caps["install_hint"], caps
        assert "sudo" in caps["install_hint"]
    if sys.platform == "linux":
        # comprobado el 2026-10-01: logind deja suspender y apagar al usuario local, rtcwake no sin una regla de sudo
        assert caps["inhibit"] is True


def test_el_despertador_se_pone_cinco_minutos_antes_y_con_la_orden_de_esta_plataforma(entorno):
    server, fake = entorno
    at = time.time() + 3600

    async def go():
        res = await server.power.schedule_wake(at)
        assert abs(res["wake_at"] - at) < 1 and server.power.wake_at == res["wake_at"]
        # una hora que ya pasó no se acepta
        with pytest.raises(RpcError):
            await server.power.schedule_wake(time.time() - 10)
        await server.power.cancel_wake()
        assert server.power.wake_at is None

    asyncio.run(go())
    hechas = ordenes(fake)
    assert len(hechas) == 2, hechas
    if sys.platform == "linux":
        # H72 · con `bin/wake` al lado —va en el repositorio y en los paquetes— la orden es la del ayudante, que
        # es justo lo que autoriza la regla de sudoers del .deb; sin él, el `rtcwake` de siempre. Las dos valen.
        if "wake" in hechas[0] and "set" in hechas[0]:
            assert str(int(at)) in hechas[0], hechas
            assert "clear" in hechas[1], hechas
        else:
            assert "rtcwake" in hechas[0] and "-m no" in hechas[0] and str(int(at)) in hechas[0]
            assert "disable" in hechas[1]


def test_los_tres_seguros_y_el_aviso_cancelable(entorno, monkeypatch):
    server, fake = entorno

    # 1. nada en marcha: se puede
    assert server.power.blockers() == []

    # 2. una grabación cerca lo impide (y una lejana no)
    ch = Channel(id="c1", name="Canal", url="http://127.0.0.1/x.m3u8", kind="tv", source="s")
    rec = server.schedule.add(ch, time.time() + 300, time.time() + 900)
    assert any("grabación" in b for b in server.power.blockers()), server.power.blockers()
    rec.start, rec.stop = time.time() + 7200, time.time() + 7800
    assert server.power.blockers() == []

    # 3. algo descargando lo impide
    class FalsaDescarga:
        status = "running"

        def to_dict(self):
            return {"status": "running"}

    monkeypatch.setattr(server.ytdl.downloads, "list", lambda include_finished=True: [{"status": "running"}])
    assert any("descarga" in b for b in server.power.blockers())
    monkeypatch.setattr(server.ytdl.downloads, "list", lambda include_finished=True: [])

    async def go():
        # con un seguro puesto NO se suspende, y se dice cuál
        monkeypatch.setattr(server.ytdl.downloads, "list", lambda include_finished=True: [{"status": "running"}])
        res = await server.power.run_action("suspend", notice=0)
        assert res["done"] is False and res["blockers"]
        assert not ordenes(fake), "no se ejecutó nada: eso es lo importante"
        monkeypatch.setattr(server.ytdl.downloads, "list", lambda include_finished=True: [])

        # el aviso cancelable manda: si se pulsa «Cancelar», no se suspende
        async def fake_ask(title, body, actions, timeout=0, icon=""):
            return "cancelar"

        monkeypatch.setattr(notify, "ask", fake_ask)
        res = await server.power.run_action("suspend", notice=5)
        assert res["done"] is False and res.get("cancelled") is True and not ordenes(fake)

        # sin cancelar, se suspende de verdad (aquí, el programa falso)
        async def ask_nada(title, body, actions, timeout=0, icon=""):
            return None

        monkeypatch.setattr(notify, "ask", ask_nada)
        res = await server.power.run_action("suspend", notice=5)
        assert res["done"] is True and res["blockers"] == []
        assert ordenes(fake), ordenes(fake)
        if sys.platform == "linux":
            assert "systemctl suspend" in ordenes(fake)[-1]

        # «nada» no hace nada y no es un error
        assert (await server.power.run_action("nothing"))["done"] is False
        with pytest.raises(RpcError):
            await server.power.run_action("hibernar")

    asyncio.run(go())


def test_la_grabacion_programada_pide_despertador_y_que_hacer_despues(entorno):
    server, fake = entorno
    ch = Channel(id="c1", name="Canal", url="http://127.0.0.1/x.m3u8", kind="tv", source="s")
    start = time.time() + 3600

    async def go():
        rec = server.schedule.add(ch, start, start + 600, wake=True, after="suspend")
        assert rec.wake is True and rec.after == "suspend"
        assert rec.public()["after"] == "suspend"
        await asyncio.sleep(0.4)       # sync_wake lanza la orden en segundo plano
        hechas = ordenes(fake)
        assert hechas, "la grabación con despertador tiene que poner la alarma"
        esperado = int(rec.begin - power.WAKE_MARGIN)
        if sys.platform == "linux":
            assert str(esperado) in hechas[-1], hechas
        # y lo de «al terminar» queda armado para esa grabación
        server.power.arm(rec.after, rec.id)
        assert server.power.pending == {"action": "suspend", "recording": rec.id}
        # con un seguro puesto (la propia grabación está cerca) no se suspende nada
        rec.start, rec.stop = time.time() + 60, time.time() + 120
        res = await server.power.on_recording_finished(rec.id)
        assert res is not None and res["done"] is False and res["blockers"]

    asyncio.run(go())


def test_mientras_graba_el_equipo_no_se_duerme(monkeypatch):
    """F2: el ffmpeg de la grabación va envuelto en un inhibidor; con el programa falso, en ninguno."""
    monkeypatch.delenv("MPVD_POWER_FAKE", raising=False)
    prefijo = power.inhibit_prefix("grabando «Telediario»")
    if sys.platform == "linux" and os.path.exists("/usr/bin/systemd-inhibit"):
        assert prefijo[0].endswith("systemd-inhibit")
        assert "--what=sleep:idle" in prefijo and any("grabando" in p for p in prefijo)
    monkeypatch.setenv("MPVD_POWER_FAKE", "/bin/true")
    assert power.inhibit_prefix("x") == []
