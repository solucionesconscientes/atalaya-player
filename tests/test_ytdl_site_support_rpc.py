"""H37/D4 · `ytdl.site_support` contestado por el daemon: lo mismo que sabe `mpvd/ytdl/sites.py`, pero con la lista de
extractores rotos leída del binario instalado, que es lo que el menú necesita para avisar antes de intentarlo."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mpvd.client import MpvdClient
from mpvd.config import Settings
from mpvd.rpc import RpcError
from mpvd.server import MpvdServer


def run(settings: Settings, fn):
    async def go():
        server = MpvdServer(settings)
        await server.start()
        try:
            async with MpvdClient(str(settings.socket_path)) as c:
                return await fn(c, server)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_site_support_dice_lo_que_se_puede_y_lo_que_no(tmp_path):
    settings = Settings(runtime_dir=tmp_path / "rt", cache_dir=tmp_path / "cache", data_dir=tmp_path / "data",
                        idle_timeout=0, workers=1)

    async def go(c, server):
        guardados = await c.call("ytdl.site_support", {"url": "https://www.instagram.com/sc.tecnico/saved/all-posts/"})
        assert guardados["supported"] is False and guardados["kind"] == "saved"
        assert "guardados" in guardados["message"] and guardados["alternative"]
        assert guardados["url"].endswith("/saved/all-posts/")

        normal = await c.call("ytdl.site_support", {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"})
        assert normal["supported"] is True and normal["kind"] == "video" and normal["message"] == ""

        perfil = await c.call("ytdl.site_support", {"url": "https://www.tiktok.com/@alguien"})
        assert perfil["supported"] is True and perfil["kind"] == "profile"
        assert perfil["private"] is True and "navegador" in perfil["private_hint"]

        # el aviso de «roto» sale del binario instalado: si no hay binario, no se inventa
        insta = await c.call("ytdl.site_support", {"url": "https://www.instagram.com/sc.tecnico/"})
        if server.ytdl.binary() is not None:
            assert insta["warning"], "yt-dlp 2026.08.19 marca instagram:user como roto"
        assert insta["supported"] is True

        with pytest.raises(RpcError):
            await c.call("ytdl.site_support", {"url": "  "})

    run(settings, go)
