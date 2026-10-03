"""The television's own remote, over HDMI-CEC (H61): the only remote already in the hand of whoever is watching.

For a Raspberry Pi plugged into a TV —the point of «modo salón»— there is no keyboard, and this is how Kodi is
driven there. We read the **kernel CEC API** (``/dev/cec0``) and not ``cec-client``: it is a documented binary
interface, needs no package installed, and is the same shape as the joystick the gamepad already reads. Every
constant and the struct layout come from ``/usr/include/linux/cec.h`` (checked with ctypes, not guessed):
``sizeof(struct cec_msg)`` is 56, ``msg`` sits at offset 32, ``CEC_RECEIVE`` is ``_IOWR('a', 6, struct cec_msg)``
and ``CEC_S_MODE`` is ``_IOW('a', 9, __u32)``.

A key press arrives as ``USER_CONTROL_PRESSED`` (0x44) with the key in the next byte, and it becomes one of the
**same actions the gamepad sends** (``play_pause``, ``back``, ``forward``, ``menu``…), so the player side keeps one
map and not two. Unlike the gamepad it is not tied to «modo salón»: a physical remote that does nothing when
pressed is the kind of silent failure we keep removing.

Not testable against real hardware here (no TV, and this laptop has no CEC): the parsing and the mapping are
tested by feeding whole ``cec_msg`` structs through a FIFO in ``MPV_UOS_CEC_DEVICE``, exactly as the gamepad is
tested. The last mile —that the television passes its keys through at all— only shows up on the Pi.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import glob
import logging
import os
import stat
import struct
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.sessions import Session

log = logging.getLogger("mpvd.cec")

MSG_SIZE = 56               # sizeof(struct cec_msg)
MSG_OFFSET = 32             # offset of msg[16]
LEN_OFFSET = 16             # offset of len
CEC_RECEIVE = 0xC0386106    # _IOWR('a', 6, struct cec_msg)
CEC_S_MODE = 0x40046109     # _IOW('a', 9, __u32)
CEC_MODE_FOLLOWER = 0x10

USER_CONTROL_PRESSED = 0x44

# Las teclas de un mando de televisión, con el nombre que ya usa el gamepad. Arriba y abajo van al volumen porque
# las teclas de volumen del mando casi nunca llegan hasta aquí: el televisor se las queda para sus altavoces.
KEYS: dict[int, str] = {
    0x00: "play_pause",     # Select
    0x01: "volume_up",      # Up
    0x02: "volume_down",    # Down
    0x03: "back",           # Left
    0x04: "forward",        # Right
    0x09: "menu",           # Device root menu
    0x0A: "menu",           # Device setup menu
    0x0B: "menu",           # Contents menu
    0x0D: "close",          # Back
    0x10: "menu",           # Media top menu
    0x41: "volume_up",
    0x42: "volume_down",
    0x44: "play_pause",     # Play
    0x45: "close",          # Stop
    0x46: "play_pause",     # Pause
    0x48: "back",           # Rewind
    0x49: "forward",        # Fast forward
    0x60: "play_pause",     # Play function
    0x61: "play_pause",     # Pause-play function
    0x64: "close",          # Stop function
}


def parse_key(buf: bytes) -> str | None:
    """One ``struct cec_msg`` → the action its key press means, or ``None`` for anything else."""
    if len(buf) < MSG_SIZE:
        return None
    (length,) = struct.unpack_from("<I", buf, LEN_OFFSET)
    msg = buf[MSG_OFFSET:MSG_OFFSET + 16]
    if length < 3 or msg[1] != USER_CONTROL_PRESSED:
        return None
    return KEYS.get(msg[2])


def find_device() -> str:
    env = os.environ.get("MPV_UOS_CEC_DEVICE")
    if env:
        return env if os.path.exists(env) else ""
    devices = sorted(glob.glob("/dev/cec[0-9]*"))
    return devices[0] if devices else ""


class CecService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.device = ""
        self.target: tuple[str, str] | None = None     # (session id, script)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        server.services["cec"] = bool(find_device())

    def status(self) -> dict[str, Any]:
        running = self._thread is not None and self._thread.is_alive()
        return {"device": self.device, "running": running, "available": bool(find_device())}

    def start(self, session: Session | None, notify: str) -> dict[str, Any]:
        if session is not None:
            self.target = (session.id, notify)
        if self._thread is not None and self._thread.is_alive():
            return self.status()
        self.device = find_device()
        if not self.device:
            return {"device": "", "running": False, "available": False}
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, args=(self.device,), name="mpvd-cec", daemon=True)
        self._thread.start()
        log.info("cec: reading %s", self.device)
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self.target = None
        return {"running": False}

    def _read(self, path: str) -> None:
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK) if _is_chardev(path) \
                else os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            log.warning("cec: cannot open %s: %s", path, exc)
            self._emit({"status": "lost"})
            return
        chardev = _is_chardev(path)
        if chardev:
            # hacerse «follower»: sin esto el kernel no entrega los mensajes dirigidos a este aparato
            with contextlib.suppress(OSError):
                fcntl.ioctl(fd, CEC_S_MODE, struct.pack("<I", CEC_MODE_FOLLOWER))
        buf = b""
        try:
            import select  # noqa: PLC0415
            while not self._stop.is_set():
                r, _, _ = select.select([fd], [], [], 0.25)
                if not r:
                    continue
                if chardev:
                    raw = bytearray(MSG_SIZE)
                    try:
                        fcntl.ioctl(fd, CEC_RECEIVE, raw)
                    except OSError:
                        continue
                    action = parse_key(bytes(raw))
                    if action:
                        self._emit({"status": "input", "action": action})
                    continue
                try:
                    chunk = os.read(fd, 16 * MSG_SIZE)
                except BlockingIOError:
                    continue
                if not chunk:                      # el que escribía en el FIFO se fue
                    self._stop.wait(0.1)
                    continue
                buf += chunk
                n = len(buf) // MSG_SIZE * MSG_SIZE
                for i in range(0, n, MSG_SIZE):
                    action = parse_key(buf[i:i + MSG_SIZE])
                    if action:
                        self._emit({"status": "input", "action": action})
                buf = buf[n:]
        except OSError as exc:
            log.info("cec: %s gone (%s)", path, exc)
            self._emit({"status": "lost"})
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _emit(self, payload: dict[str, Any]) -> None:
        loop, target = self._loop, self.target
        if loop is None or target is None:
            return

        def push() -> None:
            session = self.server.sessions.get(target[0])
            if session is not None and session.connected:
                session.push_event(target[1], "cec", payload.get("status", ""), {"event": "cec", **payload},
                                   min_interval=0)
        loop.call_soon_threadsafe(push)


def _is_chardev(path: str) -> bool:
    try:
        return stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def register(server: MpvdServer, service: CecService) -> None:
    d = server.dispatcher

    @d.method("cec.start")
    async def start(ctx: RpcContext, notify: str = "mu_modes") -> dict[str, Any]:
        """Read the TV's remote over HDMI-CEC (``/dev/cec*``) and push its keys to ``notify`` of the calling
        session. ``available`` is false where there is no CEC device, and then nothing is started."""
        return service.start(ctx.session, notify)

    @d.method("cec.stop")
    async def stop(ctx: RpcContext) -> dict[str, Any]:
        """Stop reading the TV's remote."""
        return service.stop()

    @d.method("cec.status")
    async def status(ctx: RpcContext) -> dict[str, Any]:
        """Whether there is a CEC device and whether we are reading it."""
        return service.status()
