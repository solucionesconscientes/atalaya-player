"""H44 · la sala sirve el fichero original y el relay empieza donde va el anfitrión.

- C1: el handler de rangos lee por trozos. Antes hacía `data = path.read_bytes()` y cortaba el rango sobre ese
  buffer: una película de 4 GB habrían sido 4 GB de RAM **por petición**. No se notaba porque solo pasaban por ahí
  segmentos HLS de 4 s.
- C2/C3: `GET /s/<sala>/file` sirve el original (y `file.m3u` lo envuelve para VLC o mpv), con la credencial del
  invitado en la query porque esos reproductores no mandan nuestra cookie.
- C5: el relay arranca en la posición del anfitrión, no en el segundo 0. Es la causa de los ~18 min de espera al
  entrar en el minuto 40.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mpvd.remote.http import Request
from mpvd.share import hls
from mpvd.share.service import FILE_CHUNK, ShareService
from tests.test_share_http import Guest, clip, share_env, _share  # noqa: F401 - fixtures


def rss_bytes() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    pytest.skip("sin /proc: no se puede medir la memoria")
    return 0


def req(headers: dict[str, str] | None = None) -> Request:
    return Request("GET", "/x", {}, headers or {}, b"", "127.0.0.1:1")


async def drain(stream) -> list[int]:  # noqa: ANN001
    return [len(chunk) async for chunk in stream]


def test_el_handler_lee_por_trozos_y_no_se_trae_el_fichero_a_memoria(tmp_path):
    """C1 · 300 MB servidos enteros sin que la memoria del proceso crezca: el código anterior habría sumado los
    300 MB de golpe. El fichero es disperso, así que no ocupa disco ni tarda en crearse."""
    big = tmp_path / "pelicula.bin"
    with big.open("wb") as fh:
        fh.truncate(300 * 1024 * 1024)

    resp = ShareService._file(big, "video/x-matroska", "no-cache", req())
    assert resp.status == 200 and resp.body == b"" and resp.stream is not None
    assert resp.headers["Content-Length"] == str(big.stat().st_size)
    assert resp.headers["Accept-Ranges"] == "bytes"

    before = rss_bytes()
    sizes = asyncio.run(drain(resp.stream))
    grown = rss_bytes() - before
    assert sum(sizes) == big.stat().st_size
    assert max(sizes) <= FILE_CHUNK
    assert grown < 32 * 1024 * 1024, f"la memoria creció {grown // (1024 * 1024)} MB sirviendo 300 MB"


def test_los_rangos_siguen_comportandose_igual(tmp_path):
    """C1 · el Range ya estaba bien; solo cambia de dónde salen los bytes. 206 con Content-Range, el sufijo
    «últimos N», 416 cuando no se puede satisfacer y el fichero entero sin cabecera Range."""
    f = tmp_path / "x.bin"
    f.write_bytes(bytes(range(256)) * 8)      # 2048 bytes
    size = f.stat().st_size

    r = ShareService._file(f, "application/octet-stream", "no-cache", req({"range": "bytes=10-19"}))
    assert r.status == 206 and r.headers["Content-Range"] == f"bytes 10-19/{size}"
    assert asyncio.run(drain(r.stream)) == [10]

    r = ShareService._file(f, "application/octet-stream", "no-cache", req({"range": "bytes=-100"}))
    assert r.status == 206 and r.headers["Content-Range"] == f"bytes {size - 100}-{size - 1}/{size}"

    r = ShareService._file(f, "application/octet-stream", "no-cache", req({"range": f"bytes={size + 5}-"}))
    assert r.status == 416 and r.headers["Content-Range"] == f"bytes */{size}"

    r = ShareService._file(f, "application/octet-stream", "no-cache", req())
    assert r.status == 200 and r.headers["Content-Length"] == str(size)

    with pytest.raises(Exception):
        ShareService._file(tmp_path / "no-existe", "application/octet-stream", "no-cache", req())


def test_un_mp4_con_el_moov_al_final_no_lo_lleva_el_navegador(tmp_path, media_dir):
    """C4 · «es un MP4, se verá» no vale: las grabaciones de esta casa llevan el `moov` al final y entonces el
    navegador tiene que bajarse el fichero entero antes del primer fotograma. Y un Matroska con H.264 + AAC tampoco
    lo abre (ffprobe lo llama `matroska,webm` igual que a un WebM de verdad)."""
    import subprocess

    src = media_dir / "video30.mkv"
    fast, slow = tmp_path / "fast.mp4", tmp_path / "slow.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy", "-movflags", "+faststart",
                    str(fast)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy", str(slow)], check=True)

    assert hls.moov_at_start(fast) is True and hls.moov_at_start(slow) is False
    assert hls.browser_playable(hls.probe(hls.Input(str(fast))), fast) is True
    assert hls.browser_playable(hls.probe(hls.Input(str(slow))), slow) is False
    assert hls.browser_playable(hls.probe(hls.Input(str(src))), src) is False   # mkv con H.264 + AAC


def test_el_relay_empieza_donde_va_el_anfitrion(share_env, clip):  # noqa: F811
    """C5 · con el anfitrión en el segundo 20 de una peli de 40, el relay no empaqueta los 40 desde el principio:
    arranca en el 20 y lo dice. Entrando en el minuto 40 de una película eso eran ~18 minutos de espera."""
    h, d = share_env
    h.command("loadfile", str(clip))
    h.wait_property("duration", lambda v: isinstance(v, (int, float)) and v > 30, timeout=30)
    h.command("seek", 20, "absolute+exact")
    h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and 19 < v < 22, timeout=20)

    res = d.call("share.create", {"ttl_hours": 1})
    base, rest = res["url"].split("/s/", 1)
    room, token = rest.split("#k=")
    ana = Guest(base, room)
    ana.req("api/join", {"token": token, "name": "Ana"})
    ana.listen()
    media = ana.wait(lambda e, x: e == "media" and x.get("kind") == "file" and x.get("relay_complete"), timeout=90)
    assert 19 <= media["relay_offset"] <= 22, media["relay_offset"]
    # el relay lleva solo lo que queda desde ahí: ~20 s, no los 40 de la peli
    assert 15 <= media["relay_ready"] <= 26, media["relay_ready"]
    d.call("share.close")
