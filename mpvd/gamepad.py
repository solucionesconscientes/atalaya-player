"""Gamepad for the «modo salón» (H27, ADR-058): the Linux joystick API (``/dev/input/js*``, the kernel's ``joydev``;
no SDL, the system mpv is built without ``--input-gamepad``) read in a thread; buttons become actions pushed to the
mu-modes script of the session that turned the mode on (``mu-event {"event":"gamepad","action":…}``).

``struct js_event { __u32 time; __s16 value; __u8 type; __u8 number; }`` — type 0x01 button, 0x02 axis, 0x80 = the
initial state burst sent on open (ignored). Layout of the common "xpad" driver (Xbox and most clones): buttons
A=0 B=1 X=2 Y=3 LB=4 RB=5 Back=6 Start=7 Guide=8; the d-pad is axes 6 (x) and 7 (y), sticks 0/1.
``$MPV_UOS_GAMEPAD_DEVICE`` points to another device (tests use a FIFO).
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import logging
import os
import struct
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.sessions import Session

log = logging.getLogger("mpvd.gamepad")

EVENT = struct.Struct("<IhBB")
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80
AXIS_THRESHOLD = 16000
BUTTONS = {0: "play_pause", 1: "close", 2: "subtitles", 4: "prev", 5: "next", 6: "close", 7: "menu", 8: "menu"}
# (axis, sign) → action: d-pad (6/7) and left stick (0/1); up is negative on the y axes
AXES = {(6, -1): "back", (6, 1): "forward", (7, -1): "volume_up", (7, 1): "volume_down",
        (0, -1): "back", (0, 1): "forward", (1, -1): "volume_up", (1, 1): "volume_down"}


def decode(data: bytes) -> list[tuple[int, int, int]]:
    """Raw bytes → ``[(type, number, value)]`` (whole events only)."""
    out = []
    for i in range(len(data) // EVENT.size):
        _time, value, etype, number = EVENT.unpack_from(data, i * EVENT.size)
        out.append((etype, number, value))
    return out


class Mapper:
    """Joystick events → actions: a button on press, an axis once when it crosses the threshold (again only after it
    comes back to the centre)."""

    def __init__(self) -> None:
        self.held: dict[int, int] = {}

    def feed(self, etype: int, number: int, value: int) -> str | None:
        if etype & JS_INIT:
            return None
        if etype == JS_BUTTON:
            return BUTTONS.get(number) if value else None
        if etype == JS_AXIS:
            sign = 1 if value > AXIS_THRESHOLD else -1 if value < -AXIS_THRESHOLD else 0
            before = self.held.get(number, 0)
            self.held[number] = sign
            if sign and sign != before:
                return AXES.get((number, sign))
        return None


def find_device() -> str:
    env = os.environ.get("MPV_UOS_GAMEPAD_DEVICE")
    if env:
        return env if os.path.exists(env) else ""
    devices = sorted(glob.glob("/dev/input/js[0-9]*"))
    return devices[0] if devices else ""


class GamepadService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.device = ""
        self.target: tuple[str, str] | None = None     # (session id, script)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        server.services["gamepad"] = True

    def start(self, session: Session | None, notify: str) -> dict[str, Any]:
        if session is not None:
            self.target = (session.id, notify)
        if self._thread is not None and self._thread.is_alive():
            return {"device": self.device, "running": True}
        self.device = find_device()
        if not self.device:
            return {"device": "", "running": False}
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, args=(self.device,), name="mpvd-gamepad", daemon=True)
        self._thread.start()
        log.info("gamepad: reading %s", self.device)
        return {"device": self.device, "running": True}

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self.target = None
        return {"running": False}

    def _read(self, path: str) -> None:
        mapper = Mapper()
        buf = b""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            log.warning("gamepad: cannot open %s: %s", path, exc)
            self._emit({"status": "lost"})
            return
        try:
            import select  # noqa: PLC0415
            while not self._stop.is_set():
                r, _, _ = select.select([fd], [], [], 0.25)
                if not r:
                    continue
                try:
                    chunk = os.read(fd, 64 * EVENT.size)
                except BlockingIOError:
                    continue
                if not chunk:        # unplugged (or the FIFO writer went away)
                    if not os.path.exists(path) or path.startswith("/dev/"):
                        self._emit({"status": "lost"})
                        return
                    self._stop.wait(0.1)
                    continue
                buf += chunk
                n = len(buf) // EVENT.size * EVENT.size
                for etype, number, value in decode(buf[:n]):
                    action = mapper.feed(etype, number, value)
                    if action:
                        self._emit({"status": "input", "action": action})
                buf = buf[n:]
        except OSError as exc:
            log.info("gamepad: %s gone (%s)", path, exc)
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
                session.push_event(target[1], "gamepad", payload.get("status", ""), {"event": "gamepad", **payload},
                                   min_interval=0)
        loop.call_soon_threadsafe(push)


def register(server: MpvdServer, service: GamepadService) -> None:
    d = server.dispatcher

    @d.method("gamepad.start")
    async def start(ctx: RpcContext, notify: str = "mu_modes") -> dict[str, Any]:
        """Read the first joystick (``/dev/input/js*``) and push its buttons to ``notify`` of the calling session
        (modo salón). ``device`` is empty when none is connected."""
        return service.start(ctx.session, notify)

    @d.method("gamepad.stop")
    async def stop(ctx: RpcContext) -> dict[str, Any]:
        """Stop reading the gamepad."""
        return service.stop()
