"""H61 · el mando del televisor por HDMI-CEC, para una Raspberry conectada a la tele.

Aquí no hay televisor ni CEC, así que se prueba todo menos la última milla: se le dan al demonio los mismos
`struct cec_msg` que entregaría el kernel, por un FIFO, igual que se prueba el gamepad. Que el televisor pase sus
teclas (depende de que implemente «remote control pass through» y de que seamos la fuente activa) solo se puede
comprobar en la Pi, y está en NEEDS_HUMAN.md.
"""

from __future__ import annotations

import os
import struct

import pytest

from mpvd.cec import LEN_OFFSET, MSG_OFFSET, MSG_SIZE, USER_CONTROL_PRESSED, parse_key
from tests.conftest import start_mpv

MU_OPTS = "--script-opts=mu-core-watchdog_seconds=2,mu-core-retry_seconds=1,mu-core-rpc_timeout=10"


def cec_msg(*payload: int) -> bytes:
    """Un `struct cec_msg` como el que devuelve el kernel: 56 bytes, `len` en 16 y `msg` en 32."""
    raw = bytearray(MSG_SIZE)
    struct.pack_into("<I", raw, LEN_OFFSET, len(payload))
    raw[MSG_OFFSET:MSG_OFFSET + len(payload)] = bytes(payload)
    return bytes(raw)


def key(code: int) -> bytes:
    return cec_msg(0x05, USER_CONTROL_PRESSED, code)


def test_las_teclas_del_mando_se_leen_del_struct_del_kernel():
    assert parse_key(key(0x44)) == "play_pause"     # Play
    assert parse_key(key(0x46)) == "play_pause"     # Pause
    assert parse_key(key(0x49)) == "forward"        # Fast forward
    assert parse_key(key(0x03)) == "back"           # Left
    assert parse_key(key(0x09)) == "menu"           # Device root menu
    assert parse_key(key(0x0D)) == "close"          # Back
    assert parse_key(cec_msg(0x05, 0x45, 0x44)) is None    # «released» no es «pressed»
    assert parse_key(key(0x77)) is None                    # una tecla que no usamos
    assert parse_key(cec_msg(0x05)) is None                # un mensaje corto no es una tecla
    assert parse_key(b"") is None


@pytest.fixture
def cec_mpv(daemon_env, media_dir):
    fifo = daemon_env.runtime_dir / "cec0"
    os.mkfifo(fifo)
    daemon_env.extra_env["MPV_UOS_CEC_DEVICE"] = str(fifo)
    h = start_mpv(daemon_env.runtime_dir, [MU_OPTS, "--keep-open=yes", "--pause=yes",
                                           str(media_dir / "video30.mkv")], env=daemon_env.env)
    try:
        h.wait_property("user-data/mu/core", lambda v: bool(v) and v.get("mpvd") == "connected", timeout=40)
        yield h, daemon_env, fifo
    finally:
        h.stop()


def test_el_mando_del_televisor_maneja_el_reproductor(cec_mpv):
    h, _d, fifo = cec_mpv
    # no hace falta pedirlo ni entrar en modo salón: un mando físico manda siempre
    h.wait_property("user-data/mu/modes", lambda v: bool(v) and v.get("cec") is True, timeout=25)
    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    try:
        assert h.get("pause") is True
        os.write(fd, key(0x44))                                   # Play
        h.wait_property("pause", lambda v: v is False, timeout=10)
        os.write(fd, key(0x46))                                   # Pause
        h.wait_property("pause", lambda v: v is True, timeout=10)

        pos = h.get("time-pos") or 0
        os.write(fd, key(0x49))                                   # Fast forward → +10 s
        h.wait_property("time-pos", lambda v: isinstance(v, (int, float)) and v >= pos + 9, timeout=10)

        vol = h.get("volume")
        os.write(fd, key(0x01))                                   # Up → volumen
        h.wait_property("volume", lambda v: v == pytest.approx(min(vol + 5, 130)), timeout=10)

        os.write(fd, key(0x09))                                   # menú raíz del aparato → nuestro menú
        h.wait_property("user-data/uosc/menu/type", lambda v: v == "mu-menu", timeout=10)
        os.write(fd, key(0x0D))                                   # Back → cerrar
        h.wait_property("user-data/uosc/menu/type", lambda v: not v, timeout=10)
    finally:
        os.close(fd)
    assert h.script_errors() == [], h.script_errors()
