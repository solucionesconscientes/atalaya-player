"""MPRIS for every attached mpv (H24, ADR-051): KDE/GNOME media controls, media keys, headsets and the lock screen.

One D-Bus connection per mpv session owns ``org.mpris.MediaPlayer2.mpv_uos.instance<pid>`` and exports
``/org/mpris/MediaPlayer2`` (``org.mpris.MediaPlayer2`` + ``.Player``, Properties, Introspectable). State comes from a
second JSON IPC connection to that mpv with its own property observers, so the session loop is untouched.
Needs ``jeepney`` (extra ``desktop``, pure Python) and a session bus; ``MPV_UOS_MPRIS=0`` turns it off (tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from mpvd import brand
from mpvd.mpvipc import DISCONNECTED_EVENT, MpvIpcClient, MpvIpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer
    from mpvd.sessions import Session

log = logging.getLogger("mpvd.mpris")

OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
INTROSPECT_IFACE = "org.freedesktop.DBus.Introspectable"
PEER_IFACE = "org.freedesktop.DBus.Peer"
BUS_PREFIX = "org.mpris.MediaPlayer2.mpv_uos"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
APP_NAME = brand.app_name()
DESKTOP_ENTRY = brand.app_id()

OBSERVE = ("pause", "idle-active", "media-title", "metadata", "duration", "path", "volume", "speed", "loop-file",
           "loop-playlist", "shuffle", "playlist-pos", "playlist-count", "seekable", "fullscreen", "eof-reached")
URI_SCHEMES = ["file", "http", "https", "rtmp", "rtsp", "ytdl", "mpv-uos"]
MIME_TYPES = ["video/mp4", "video/x-matroska", "video/webm", "video/quicktime", "video/x-msvideo", "video/mpeg",
              "audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav", "audio/mp4", "audio/aac", "audio/opus",
              "application/x-mpegurl", "application/vnd.apple.mpegurl", "audio/x-mpegurl"]

INTROSPECTION = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
 <interface name="org.freedesktop.DBus.Introspectable">
  <method name="Introspect"><arg name="xml" type="s" direction="out"/></method>
 </interface>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
  <method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/></method>
  <signal name="PropertiesChanged"><arg type="s"/><arg type="a{sv}"/><arg type="as"/></signal>
 </interface>
 <interface name="org.mpris.MediaPlayer2">
  <method name="Raise"/><method name="Quit"/>
  <property name="CanQuit" type="b" access="read"/>
  <property name="Fullscreen" type="b" access="readwrite"/>
  <property name="CanSetFullscreen" type="b" access="read"/>
  <property name="CanRaise" type="b" access="read"/>
  <property name="HasTrackList" type="b" access="read"/>
  <property name="Identity" type="s" access="read"/>
  <property name="DesktopEntry" type="s" access="read"/>
  <property name="SupportedUriSchemes" type="as" access="read"/>
  <property name="SupportedMimeTypes" type="as" access="read"/>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/><method name="Stop"/>
  <method name="Play"/>
  <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
  <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
  <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
  <signal name="Seeked"><arg name="Position" type="x"/></signal>
  <property name="PlaybackStatus" type="s" access="read"/>
  <property name="LoopStatus" type="s" access="readwrite"/>
  <property name="Rate" type="d" access="readwrite"/>
  <property name="Shuffle" type="b" access="readwrite"/>
  <property name="Metadata" type="a{sv}" access="read"/>
  <property name="Volume" type="d" access="readwrite"/>
  <property name="Position" type="x" access="read"/>
  <property name="MinimumRate" type="d" access="read"/>
  <property name="MaximumRate" type="d" access="read"/>
  <property name="CanGoNext" type="b" access="read"/>
  <property name="CanGoPrevious" type="b" access="read"/>
  <property name="CanPlay" type="b" access="read"/>
  <property name="CanPause" type="b" access="read"/>
  <property name="CanSeek" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/>
 </interface>
</node>
"""


def available() -> tuple[bool, str]:
    """(usable, reason): Linux, jeepney importable, a session bus address, not disabled by MPV_UOS_MPRIS=0."""
    if os.environ.get("MPV_UOS_MPRIS", "1") == "0":
        return False, "disabled (MPV_UOS_MPRIS=0)"
    if not sys.platform.startswith("linux"):
        return False, "MPRIS is Linux only"
    try:
        import jeepney  # noqa: F401, PLC0415
    except ImportError:
        return False, "jeepney missing (uv sync --extra desktop)"
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not addr:
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not (runtime and Path(runtime, "bus").exists()):
            return False, "no session D-Bus"
    return True, ""


def track_id(pos: Any) -> str:
    return f"/org/mpris/MediaPlayer2/mpv_uos/track/{int(pos)}" if isinstance(pos, int) and pos >= 0 else NO_TRACK


def to_uri(path: str) -> str:
    if "://" in path:
        return path
    return "file://" + quote(str(Path(path).resolve()))


def _meta_get(meta: dict[str, Any], *keys: str) -> str:
    lower = {str(k).lower(): v for k, v in meta.items()}
    for k in keys:
        v = lower.get(k)
        if v:
            return str(v)
    return ""


def metadata(props: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    """MPRIS ``Metadata`` (a{sv} as jeepney (signature, value) pairs) from mpv's properties."""
    pos = props.get("playlist-pos")
    path = props.get("path")
    if not path or props.get("idle-active"):
        return {"mpris:trackid": ("o", NO_TRACK)}
    meta = props.get("metadata") if isinstance(props.get("metadata"), dict) else {}
    out: dict[str, tuple[str, Any]] = {"mpris:trackid": ("o", track_id(pos))}
    title = props.get("media-title") or _meta_get(meta, "title") or Path(str(path)).name
    out["xesam:title"] = ("s", str(title))
    artist = _meta_get(meta, "artist", "album_artist", "uploader", "icy-name")
    if artist:
        out["xesam:artist"] = ("as", [artist])
    album = _meta_get(meta, "album")
    if album:
        out["xesam:album"] = ("s", album)
    dur = props.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        out["mpris:length"] = ("x", int(dur * 1_000_000))
    out["xesam:url"] = ("s", to_uri(str(path)))
    return out


def playback_status(props: dict[str, Any]) -> str:
    if props.get("idle-active") or not props.get("path"):
        return "Stopped"
    return "Paused" if props.get("pause") else "Playing"


def loop_status(props: dict[str, Any]) -> str:
    lf, lp = props.get("loop-file"), props.get("loop-playlist")
    if lf not in (None, False, "no"):
        return "Track"
    if lp not in (None, False, "no"):
        return "Playlist"
    return "None"


def player_props(props: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    count = props.get("playlist-count") or 0
    pos = props.get("playlist-pos")
    pos = pos if isinstance(pos, int) else -1
    has_media = bool(props.get("path")) and not props.get("idle-active")
    loop_pl = props.get("loop-playlist") not in (None, False, "no")
    vol = props.get("volume")
    return {
        "PlaybackStatus": ("s", playback_status(props)),
        "LoopStatus": ("s", loop_status(props)),
        "Rate": ("d", float(props.get("speed") or 1.0)),
        "Shuffle": ("b", bool(props.get("shuffle"))),
        "Metadata": ("a{sv}", metadata(props)),
        "Volume": ("d", max(0.0, float(vol) / 100.0) if isinstance(vol, (int, float)) else 1.0),
        "MinimumRate": ("d", 0.01),
        "MaximumRate": ("d", 100.0),
        "CanGoNext": ("b", count > 1 and (pos < count - 1 or loop_pl)),
        "CanGoPrevious": ("b", count > 1 and (pos > 0 or loop_pl)),
        "CanPlay": ("b", has_media or count > 0),
        "CanPause": ("b", has_media),
        "CanSeek": ("b", has_media and bool(props.get("seekable", True))),
        "CanControl": ("b", True),
    }


def root_props(props: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    return {
        "CanQuit": ("b", True),
        "Fullscreen": ("b", bool(props.get("fullscreen"))),
        "CanSetFullscreen": ("b", True),
        "CanRaise": ("b", False),
        "HasTrackList": ("b", False),
        "Identity": ("s", APP_NAME),
        "DesktopEntry": ("s", DESKTOP_ENTRY),
        "SupportedUriSchemes": ("as", URI_SCHEMES),
        "SupportedMimeTypes": ("as", MIME_TYPES),
    }


# mpv property → (interface, MPRIS properties it changes); Position is never signalled (MPRIS spec), Seeked is
AFFECTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pause": (PLAYER_IFACE, ("PlaybackStatus", "CanPause", "CanSeek")),
    "idle-active": (PLAYER_IFACE, ("PlaybackStatus", "Metadata", "CanPlay", "CanPause", "CanSeek")),
    "path": (PLAYER_IFACE, ("PlaybackStatus", "Metadata", "CanPlay", "CanPause", "CanSeek")),
    "media-title": (PLAYER_IFACE, ("Metadata",)),
    "metadata": (PLAYER_IFACE, ("Metadata",)),
    "duration": (PLAYER_IFACE, ("Metadata",)),
    "volume": (PLAYER_IFACE, ("Volume",)),
    "speed": (PLAYER_IFACE, ("Rate",)),
    "loop-file": (PLAYER_IFACE, ("LoopStatus",)),
    "loop-playlist": (PLAYER_IFACE, ("LoopStatus", "CanGoNext", "CanGoPrevious")),
    "shuffle": (PLAYER_IFACE, ("Shuffle",)),
    "playlist-pos": (PLAYER_IFACE, ("Metadata", "CanGoNext", "CanGoPrevious")),
    "playlist-count": (PLAYER_IFACE, ("CanGoNext", "CanGoPrevious", "CanPlay")),
    "seekable": (PLAYER_IFACE, ("CanSeek",)),
    "fullscreen": (ROOT_IFACE, ("Fullscreen",)),
}


class DBusError(Exception):
    def __init__(self, name: str, text: str):
        super().__init__(text)
        self.name = name


class MprisPlayer:
    """The MPRIS face of one mpv session."""

    def __init__(self, session: Session):
        self.session = session
        self.props: dict[str, Any] = {}
        self.bus_name = f"{BUS_PREFIX}.instance{session.pid}" if session.pid else f"{BUS_PREFIX}.session_{session.id}"
        self.ipc: MpvIpcClient | None = None
        self.router: Any = None
        self._conn: Any = None
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[str, set[str]] = {}
        self._flush: asyncio.TimerHandle | None = None
        self.ready = asyncio.Event()
        self.error = ""

    def start(self) -> None:
        self._task = asyncio.create_task(self._main(), name=f"mpris-{self.session.id}")

    async def stop(self) -> None:
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._close()

    async def _close(self) -> None:
        if self._flush is not None:
            self._flush.cancel()
        if self.router is not None:
            with contextlib.suppress(Exception):
                await self.router.__aexit__(None, None, None)
            self.router = None
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None
        if self.ipc is not None:
            with contextlib.suppress(Exception):
                await self.ipc.close()
            self.ipc = None

    # -- main loop ----------------------------------------------------------------------------------------------

    async def _main(self) -> None:
        from jeepney import MatchRule  # noqa: PLC0415
        from jeepney.bus_messages import message_bus  # noqa: PLC0415
        from jeepney.io.asyncio import DBusRouter, Proxy, open_dbus_connection  # noqa: PLC0415

        try:
            self.ipc = MpvIpcClient(self.session.ipc_path)
            await self.ipc.connect(timeout=5)
            for i, name in enumerate(OBSERVE, start=100):
                await self.ipc.command("observe_property", i, name)
            self._conn = await open_dbus_connection("SESSION")
            self.router = DBusRouter(self._conn)
            rule = MatchRule(type="method_call")
            with self.router.filter(rule, bufsize=64) as calls:
                reply = await Proxy(message_bus, self.router).RequestName(self.bus_name, 4)  # DO_NOT_QUEUE
                if reply[0] not in (1, 4):  # primary owner / already owner
                    raise RuntimeError(f"bus name {self.bus_name} taken ({reply[0]})")
                log.info("MPRIS: %s for session %s", self.bus_name, self.session.id)
                self.ready.set()
                events = asyncio.create_task(self._mpv_events())
                try:
                    while self.ipc.connected:
                        getter = asyncio.create_task(calls.get())
                        done, _ = await asyncio.wait({getter, events}, return_when=asyncio.FIRST_COMPLETED)
                        if getter not in done:
                            getter.cancel()
                            break
                        await self._serve(getter.result())
                finally:
                    events.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await events
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - MPRIS is a nicety: never take the daemon down
            self.error = str(exc)
            log.warning("MPRIS for session %s unavailable: %s", self.session.id, exc)
        finally:
            self.ready.set()
            await self._close()

    async def _mpv_events(self) -> None:
        assert self.ipc is not None
        while True:
            ev = await self.ipc.events.get()
            kind = ev.get("event")
            if kind == "property-change":
                name = ev.get("name", "")
                self.props[name] = ev.get("data")
                if name in AFFECTS:
                    iface, names = AFFECTS[name]
                    self._pending.setdefault(iface, set()).update(names)
                    self._schedule_flush()
            elif kind == "playback-restart" and self.props.get("path"):
                await self._emit_seeked()
            elif kind in ("shutdown", DISCONNECTED_EVENT):
                return

    def _schedule_flush(self) -> None:
        # coalesce bursts (a file load changes a dozen properties) into one PropertiesChanged per interface
        if self._flush is None:
            self._flush = asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(self._flush_changes()))

    async def _flush_changes(self) -> None:
        from jeepney import DBusAddress, new_signal  # noqa: PLC0415

        self._flush = None
        pending, self._pending = self._pending, {}
        if self.router is None:
            return
        emitter = DBusAddress(OBJECT_PATH, interface=PROPS_IFACE)
        for iface, names in pending.items():
            allp = player_props(self.props) if iface == PLAYER_IFACE else root_props(self.props)
            changed = {n: allp[n] for n in sorted(names) if n in allp}
            with contextlib.suppress(Exception):
                await self.router.send(new_signal(emitter, "PropertiesChanged", "sa{sv}as", (iface, changed, [])))

    async def _emit_seeked(self) -> None:
        from jeepney import DBusAddress, new_signal  # noqa: PLC0415

        if self.router is None:
            return
        pos = await self._position()
        with contextlib.suppress(Exception):
            await self.router.send(new_signal(DBusAddress(OBJECT_PATH, interface=PLAYER_IFACE), "Seeked", "x", (pos,)))

    async def _position(self) -> int:
        assert self.ipc is not None
        try:
            v = await self.ipc.get_property("time-pos", timeout=2)
        except (MpvIpcError, TimeoutError, ConnectionError):
            return 0
        return int(float(v) * 1_000_000) if isinstance(v, (int, float)) else 0

    # -- D-Bus method calls ---------------------------------------------------------------------------------------

    async def _serve(self, msg: Any) -> None:
        from jeepney import HeaderFields, new_error, new_method_return  # noqa: PLC0415

        hdr = msg.header.fields
        path = hdr.get(HeaderFields.path)
        iface = hdr.get(HeaderFields.interface)
        member = hdr.get(HeaderFields.member)
        if hdr.get(HeaderFields.destination) not in (self.bus_name, self._conn.unique_name if self._conn else None):
            return
        try:
            if path != OBJECT_PATH and not (iface == INTROSPECT_IFACE and path in ("/", "/org", "/org/mpris")):
                raise DBusError("org.freedesktop.DBus.Error.UnknownObject", f"no object {path}")
            sig, body = await self._call(iface, member, msg.body, path)
            reply = new_method_return(msg, sig, body)
        except DBusError as exc:
            reply = new_error(msg, exc.name, "s", (str(exc),))
        except (MpvIpcError, TimeoutError, ConnectionError) as exc:
            reply = new_error(msg, "org.freedesktop.DBus.Error.Failed", "s", (f"mpv: {exc}",))
        with contextlib.suppress(Exception):
            await self.router.send(reply)

    async def _call(self, iface: str | None, member: str | None, body: tuple[Any, ...],
                    path: str | None) -> tuple[str | None, tuple[Any, ...]]:
        if iface == INTROSPECT_IFACE and member == "Introspect":
            if path != OBJECT_PATH:
                child = {"/": "org", "/org": "mpris", "/org/mpris": "MediaPlayer2"}[str(path)]
                return "s", (f'<node><node name="{child}"/></node>',)
            return "s", (INTROSPECTION,)
        if iface == PEER_IFACE and member == "Ping":
            return None, ()
        if iface == PROPS_IFACE:
            return await self._properties(member, body)
        if iface in (ROOT_IFACE, None) and member in ("Raise", "Quit"):
            if member == "Quit":
                await self._cmd("quit")
            return None, ()
        if iface in (PLAYER_IFACE, None):
            return await self._player(member, body)
        raise DBusError("org.freedesktop.DBus.Error.UnknownMethod", f"{iface}.{member}")

    async def _cmd(self, *args: Any) -> Any:
        assert self.ipc is not None
        return await self.ipc.command(*args, timeout=5)

    async def _player(self, member: str | None, body: tuple[Any, ...]) -> tuple[str | None, tuple[Any, ...]]:
        if member == "PlayPause":
            await self._cmd("cycle", "pause")
        elif member == "Play":
            if self.props.get("idle-active") and (self.props.get("playlist-count") or 0) > 0:
                await self._cmd("playlist-play-index", "current")
            await self._cmd("set", "pause", "no")
        elif member == "Pause":
            await self._cmd("set", "pause", "yes")
        elif member == "Stop":
            await self._cmd("stop")
        elif member == "Next":
            with contextlib.suppress(MpvIpcError):   # at the end of the playlist: nothing to do (spec)
                await self._cmd("playlist-next")
        elif member == "Previous":
            with contextlib.suppress(MpvIpcError):
                await self._cmd("playlist-prev")
        elif member == "Seek":
            await self._cmd("seek", float(body[0]) / 1_000_000, "relative", "exact")
        elif member == "SetPosition":
            tid, pos = body[0], int(body[1])
            dur = self.props.get("duration")
            if tid != track_id(self.props.get("playlist-pos")) or pos < 0 or \
                    (isinstance(dur, (int, float)) and pos > dur * 1_000_000):
                return None, ()   # stale track id or out of range: ignored (spec)
            await self._cmd("seek", pos / 1_000_000, "absolute", "exact")
        elif member == "OpenUri":
            await self._cmd("loadfile", str(body[0]), "replace")
        else:
            raise DBusError("org.freedesktop.DBus.Error.UnknownMethod", f"Player.{member}")
        return None, ()

    async def _properties(self, member: str | None, body: tuple[Any, ...]) -> tuple[str | None, tuple[Any, ...]]:
        iface = body[0] if body else ""
        if member == "GetAll":
            if iface == PLAYER_IFACE:
                props = player_props(self.props)
                props["Position"] = ("x", await self._position())
            elif iface == ROOT_IFACE:
                props = root_props(self.props)
            else:
                props = {}
            return "a{sv}", (props,)
        if member == "Get":
            name = body[1]
            if iface == PLAYER_IFACE and name == "Position":
                return "v", (("x", await self._position()),)
            allp = player_props(self.props) if iface == PLAYER_IFACE else root_props(self.props)
            if name not in allp:
                raise DBusError("org.freedesktop.DBus.Error.InvalidArgs", f"no property {iface}.{name}")
            return "v", (allp[name],)
        if member == "Set":
            name, (_sig, value) = body[1], body[2]
            await self._set(iface, name, value)
            return None, ()
        raise DBusError("org.freedesktop.DBus.Error.UnknownMethod", f"Properties.{member}")

    async def _set(self, iface: str, name: str, value: Any) -> None:
        if iface == PLAYER_IFACE and name == "Volume":
            await self._cmd("set", "volume", str(max(0.0, min(float(value), 1.3)) * 100))
        elif iface == PLAYER_IFACE and name == "Rate":
            if float(value) <= 0:
                await self._cmd("set", "pause", "yes")   # the spec: Rate 0 is a pause
            else:
                await self._cmd("set", "speed", str(max(0.01, min(float(value), 100.0))))
        elif iface == PLAYER_IFACE and name == "Shuffle":
            await self._cmd("set", "shuffle", "yes" if value else "no")
        elif iface == PLAYER_IFACE and name == "LoopStatus":
            loops = {"None": ("no", "no"), "Track": ("inf", "no"), "Playlist": ("no", "inf")}
            if value not in loops:
                raise DBusError("org.freedesktop.DBus.Error.InvalidArgs", f"LoopStatus {value}")
            await self._cmd("set", "loop-file", loops[value][0])
            await self._cmd("set", "loop-playlist", loops[value][1])
        elif iface == ROOT_IFACE and name == "Fullscreen":
            await self._cmd("set", "fullscreen", "yes" if value else "no")
        else:
            raise DBusError("org.freedesktop.DBus.Error.PropertyReadOnly", f"{iface}.{name} is read-only")


class MprisService:
    """Starts an :class:`MprisPlayer` for each session that opens, stops it when the session closes."""

    def __init__(self, server: MpvdServer):
        self.server = server
        self.enabled, self.reason = available()
        self.players: dict[str, MprisPlayer] = {}
        server.services["mpris"] = self.enabled
        if self.enabled:
            server.session_listeners.append(self._session_event)

    def _session_event(self, kind: str, session: Session) -> None:
        if kind == "open" and session.id not in self.players:
            player = MprisPlayer(session)
            self.players[session.id] = player
            player.start()
        elif kind == "closed":
            player = self.players.pop(session.id, None)
            if player is not None:
                asyncio.ensure_future(player.stop())

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "reason": self.reason,
                "players": [{"session": sid, "bus_name": p.bus_name, "ready": p.ready.is_set() and not p.error,
                             "error": p.error} for sid, p in self.players.items()]}


def register(server: MpvdServer, service: MprisService) -> None:
    d = server.dispatcher

    @d.method("mpris.status")
    async def status(ctx: Any) -> dict[str, Any]:
        """MPRIS bridge: enabled, why not, and the D-Bus name of each attached mpv."""
        return service.status()
