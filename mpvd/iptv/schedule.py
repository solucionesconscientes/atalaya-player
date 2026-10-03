"""Scheduled recordings of TV/radio channels (H21), made by mpvd with ffmpeg whatever mpv is playing.

Each recording keeps what it needs to run without the lists being loaded (channel id, name, URL and the HTTP
headers mu-iptv would send) plus start/stop and optional margins, in ``<data>/iptv-schedule.json`` (atomic
writes). States: ``scheduled`` → ``recording`` → ``done`` | ``failed``; ``missed`` when its time went by while
mpvd was not running (computer off, player closed and mpvd gone), ``cancelled`` by the user.

The recorder is ``ffmpeg -c copy`` (no re-encoding: a few % of one core) with ffmpeg's default stream choice (the
best video and one audio track), subtitles and data dropped, into ``.mkv`` (``.mka`` for radio) under
``<Vídeos>/MPV-UOS/Grabaciones``. When the stream drops before the end ffmpeg is started again into a new part
(``… (2).mkv``). Stopping sends ``q`` on ffmpeg's stdin (it closes the file properly on every platform).

mpvd pushes ``mu-event {"event":"schedule","item":{…}}`` to mu-iptv in every session when a recording starts and
ends, and shows a desktop notification at the end (``notify-send``/``osascript``; never with MPV_UOS_NO_NOTIFY).
While a recording is pending or running mpvd does not exit for inactivity (server._idle_watch → ``busy``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import datetime as dt
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.asr.audio import ffmpeg_path
from mpvd.power import WAKE_MARGIN, inhibit_prefix
from mpvd.iptv.model import HLS_LAVF_DEFAULTS, Channel
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.iptv.service import IptvService
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.iptv.schedule")

NOTIFY = "mu_iptv"
ACTIVE = ("scheduled", "recording", "playing")
FINAL = ("done", "failed", "missed", "cancelled")
LABELS = {"scheduled": "programada", "recording": "grabando", "playing": "sonando", "done": "hecha",
          "failed": "fallida", "missed": "perdida", "cancelled": "cancelada"}
MAX_DURATION = 12 * 3600.0
MAX_WAKE = 30.0  # the scheduler re-checks the clock at least this often (suspend, clock changes)
RETRY_DELAY = 5.0
PLAYER_WAIT = 60.0           # s esperando a que el reproductor recién abierto se registre (H57)
MAX_PARTS = 30
RW_TIMEOUT_US = 15_000_000


@dataclass
class Recording:
    id: str
    channel: dict[str, Any]  # {id, name, kind, url, headers, hls}
    title: str
    start: float
    stop: float
    margin_before: float = 0.0
    margin_after: float = 0.0
    status: str = "scheduled"
    dir: str = ""
    files: list[str] = field(default_factory=list)
    message: str = ""
    origin: str = "manual"  # "manual" | "epg"
    wake: bool = False      # H40/F1: poner el despertador del equipo 5 min antes
    after: str = "nothing"  # H40/F1: al terminar → nothing | suspend | shutdown
    # H57 · qué se hace en esa franja: grabar (lo de siempre), reproducir —que el equipo se encienda y ponga el
    # canal, la emisora o la lista— o las dos cosas a la vez.
    mode: str = "record"    # record | play | both
    programme: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None

    @property
    def begin(self) -> float:
        return self.start - self.margin_before

    @property
    def end(self) -> float:
        return self.stop + self.margin_after

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        ch = d.pop("channel")
        d["channel"] = {k: ch.get(k) for k in ("id", "name", "kind")}
        d["begin"], d["end"] = self.begin, self.end
        d["status_label"] = LABELS.get(self.status, self.status)
        d["file"] = self.files[-1] if self.files else ""
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recording:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def default_dir() -> Path:
    from mpvd.record import default_dir as record_dir  # noqa: PLC0415 - avoid importing ytdl at module load

    env = os.environ.get("MPV_UOS_RECORD_DIR")
    return Path(env) if env else record_dir()


def safe_name(text: str, limit: int = 80) -> str:
    """A file name part valid on Windows/macOS/Linux."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:limit].rstrip(" .") or "grabacion"


def ffmpeg_args(ch: dict[str, Any], out: Path, seconds: float) -> list[str]:
    """``ffmpeg … -i URL -c copy -t S OUT`` with the channel's headers (as mpv sends them, see Channel.mpv_options).

    ``-t`` is only a ceiling (the scheduler stops ffmpeg with ``q`` at the end time); callers pass the time left
    plus a minute."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostats", "-loglevel", "error", "-rw_timeout", str(RW_TIMEOUT_US)]
    headers = dict(ch.get("headers") or {})
    ua = headers.pop("User-Agent", None)
    ref = headers.pop("Referer", None)
    if ua:
        cmd += ["-user_agent", ua]
    if ref:
        cmd += ["-referer", ref]
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    if ch.get("hls"):
        for k, v in HLS_LAVF_DEFAULTS.items():
            cmd += ["-" + k, v]
    cmd += ["-i", ch["url"]]
    if ch.get("kind") == "radio":
        cmd += ["-vn"]
    cmd += ["-sn", "-dn", "-c", "copy", "-t", f"{max(1.0, seconds):.3f}", "-f", "matroska", str(out)]
    return cmd


LIST_SUFFIXES = {".m3u", ".m3u8", ".pls", ".xspf"}


def is_playlist_file(url: str) -> bool:
    """Una lista de este equipo (M3U/PLS/XSPF): se carga con ``loadlist``, no con ``loadfile``.

    Solo ficheros locales: un ``.m3u8`` remoto es HLS —un canal—, no una lista de canciones."""
    if re.match(r"^[a-zA-Z][\w+.-]*://", url):
        return False
    return Path(url).suffix.lower() in LIST_SUFFIXES


def media_channel(path: str, title: str | None = None) -> Channel:
    """H57 · una canción, una carpeta, una lista o una dirección, envuelta como canal para poder programarla.

    Así el despertador, la franja horaria, el «suspender al terminar» y la lista de programaciones funcionan igual
    para la radio de las 7:00 que para la lista que quieres oír mientras cenas, sin duplicar nada."""
    raw = os.path.expanduser(str(path).strip())
    if not raw:
        raise RpcError(INVALID_PARAMS, "no hay nada que reproducir")
    local = Path(raw)
    if not re.match(r"^[a-zA-Z][\w+.-]*://", raw) and not local.exists():
        raise RpcError(INVALID_PARAMS, f"no existe: {raw}")
    nombre = (title or (local.name if local.exists() else raw)).strip() or "Programado"
    return Channel(id="media:" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12],
                   name=nombre[:120], url=raw, kind="media", source="manual")


def channel_snapshot(ch: Channel) -> dict[str, Any]:
    return {"id": ch.id, "name": ch.name, "kind": ch.kind, "url": ch.url, "headers": ch.request_headers(),
            "hls": ch.is_hls()}


# -- manual entry ("21:30 22:15", "21:30 90", "mañana 9:00 1h30", "ahora 30") ------------------------------------

_TIME_TOKEN = re.compile(r"^(\d{1,2})(?:[:.h](\d{2})?)$")
_DURATION = re.compile(r"^\+?(?:(\d+)\s*h\s*(\d+)?\s*(?:m|min)?|(\d+)\s*(?:m|min)?)$")
_DAYS = {"hoy": 0, "mañana": 1, "manana": 1, "pasado": 2}


def _clock(token: str) -> tuple[int, int] | None:
    m = _TIME_TOKEN.match(token)
    if not m or (":" not in token and "." not in token and "h" not in token):
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return (h, mi) if h < 24 and mi < 60 else None


def _minutes(token: str) -> int | None:
    m = _DURATION.match(token.replace(" ", ""))
    if not m:
        return None
    if m.group(3) is not None:
        return int(m.group(3))
    return int(m.group(1)) * 60 + int(m.group(2) or 0)


def _day_label(when: dt.datetime, now: dt.datetime) -> str:
    delta = (when.date() - now.date()).days
    if delta == 0:
        return "hoy"
    if delta == 1:
        return "mañana"
    return when.strftime("%d/%m")


def describe(start: float, stop: float, now: float | None = None) -> str:
    """ "hoy 21:30–22:15 (45 min)" """
    now_dt = dt.datetime.fromtimestamp(time.time() if now is None else now)
    a, b = dt.datetime.fromtimestamp(start), dt.datetime.fromtimestamp(stop)
    mins = round((stop - start) / 60)
    dur = f"{mins} min" if mins < 60 else f"{mins // 60} h" + (f" {mins % 60:02d}" if mins % 60 else "")
    return f"{_day_label(a, now_dt)} {a:%H:%M}–{b:%H:%M} ({dur})"


def parse_when(text: str, now: float | None = None, default_minutes: int = 60) -> dict[str, Any]:
    """Start/stop from what the user types. Unknown words make an error (shown as the palette's hint).

    ``<inicio> [<fin>|<duración>]`` with inicio = ``21:30``/``21.30``/``21h``/``ahora`` optionally after
    ``hoy``/``mañana``/``pasado``; fin = another hour (``22:15``, ``-22:15``, ``a 22:15``); duración = ``90``,
    ``90m``, ``1h``, ``1h30``. An hour already gone today means tomorrow unless its end is still ahead."""
    now = time.time() if now is None else now
    now_dt = dt.datetime.fromtimestamp(now)
    raw = (text or "").strip().lower()
    tokens = [t for t in re.split(r"\s+|(?<=\d)-(?=\d)|\s*-\s*|\s+a\s+", raw) if t and t not in ("a", "de", "-", "hasta")]
    if not tokens:
        return {"error": "Escribe la hora de inicio y la duración o la hora de fin: 21:30 22:15 · 21:30 90"}
    day = None
    if tokens[0] in _DAYS:
        day = _DAYS[tokens.pop(0)]
        if day == 2 and tokens and tokens[0] in ("mañana", "manana"):
            tokens.pop(0)
    if not tokens:
        return {"error": "Falta la hora de inicio"}
    first = tokens.pop(0)
    if first in ("ahora", "ya", "now"):
        start_dt = now_dt
        explicit_day = True
    else:
        hm = _clock(first)
        if hm is None:
            return {"error": f"No entiendo la hora «{first}» (usa 21:30)"}
        start_dt = now_dt.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0) + dt.timedelta(days=day or 0)
        explicit_day = day is not None
    stop_dt = None
    if tokens:
        second = tokens.pop(0)
        # an end hour needs ":" or "." ("22:15"); "1h30"/"90"/"45m" are durations
        hm = _clock(second) if (":" in second or "." in second) else None
        if hm is not None:
            stop_dt = start_dt.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            if stop_dt <= start_dt:
                stop_dt += dt.timedelta(days=1)
        else:
            mins = _minutes(second + ("".join(tokens) if tokens else ""))
            tokens = []
            if mins is None or mins <= 0:
                return {"error": f"No entiendo «{second}»: pon la hora de fin (22:15) o los minutos (90)"}
            stop_dt = start_dt + dt.timedelta(minutes=mins)
    if tokens:
        return {"error": f"Sobra «{' '.join(tokens)}»"}
    if stop_dt is None:
        stop_dt = start_dt + dt.timedelta(minutes=default_minutes)
    if not explicit_day and stop_dt.timestamp() <= now:
        start_dt += dt.timedelta(days=1)
        stop_dt += dt.timedelta(days=1)
    start, stop = start_dt.timestamp(), stop_dt.timestamp()
    if stop <= now:
        return {"error": "Esa hora ya ha pasado"}
    if stop - start > MAX_DURATION:
        return {"error": "Como mucho 12 horas seguidas"}
    return {"start": start, "stop": stop, "label": describe(max(start, now) if start < now else start, stop, now),
            "now": start <= now}


# -- desktop notification ----------------------------------------------------------------------------------------

async def desktop_notify(title: str, body: str) -> None:
    """Best effort, never raises; silent with MPV_UOS_NO_NOTIFY (tests) or without a helper."""
    if os.environ.get("MPV_UOS_NO_NOTIFY"):
        return
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
        cmd = ["osascript", "-e", script]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "-a", "MPV-UOS", "-i", "media-record", title, body]
    else:
        return
    with contextlib.suppress(OSError, asyncio.TimeoutError):
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 10)


# -- service ------------------------------------------------------------------------------------------------------


class ScheduleService:
    def __init__(self, server: MpvdServer, iptv: IptvService, path: Path | None = None):
        self.server = server
        self.iptv = iptv
        self.path = path or server.settings.data_dir / "iptv-schedule.json"
        self.log_dir = server.settings.cache_dir / "recordings"
        self.items: dict[str, Recording] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._stopping: set[str] = set()  # ids whose recording the user stopped
        self._loop_task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._closing = False
        self._load()

    # -- persistence --------------------------------------------------------------------------

    # H40/F1 · lo que se aplica a las grabaciones nuevas cuando quien las programa no dice otra cosa. Va en un fichero
    # aparte para no cambiar la forma del de las grabaciones (y su recuperación al arrancar).
    def _defaults_path(self) -> Path:
        return self.path.with_name(self.path.stem + "-defaults.json")

    def load_defaults(self) -> dict[str, Any]:
        out = {"wake": False, "after": "nothing"}
        try:
            data = json.loads(self._defaults_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return out
        if isinstance(data, dict):
            if isinstance(data.get("wake"), bool):
                out["wake"] = data["wake"]
            if data.get("after") in ("nothing", "suspend", "shutdown"):
                out["after"] = data["after"]
        return out

    def set_defaults(self, wake: bool | None = None, after: str | None = None) -> dict[str, Any]:
        cur = self.load_defaults()
        if wake is not None:
            cur["wake"] = bool(wake)
        if after is not None:
            if after not in ("nothing", "suspend", "shutdown"):
                raise RpcError(INVALID_PARAMS, "al terminar: nothing, suspend o shutdown")
            cur["after"] = after
        path = self._defaults_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return cur

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("schedule %s unreadable (%s): starting empty", self.path, exc)
            with contextlib.suppress(OSError):
                self.path.replace(self.path.with_suffix(f".bad-{int(time.time())}"))
            return
        for row in rows if isinstance(rows, list) else []:
            try:
                rec = Recording.from_dict(row)
            except TypeError:
                continue
            self.items[rec.id] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        rows = [asdict(r) for r in sorted(self.items.values(), key=lambda r: r.start)]
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- lifecycle ----------------------------------------------------------------------------

    def recover(self, now: float | None = None) -> None:
        """At start-up: what was due while mpvd was not running is missed; an interrupted recording goes on."""
        now = time.time() if now is None else now
        changed = False
        for rec in self.items.values():
            if rec.status == "recording":
                if rec.end > now + 1:
                    rec.status = "scheduled"
                    rec.message = "se retoma tras un cierre de mpvd"
                else:
                    rec.status = "done" if self._has_output(rec) else "missed"
                    rec.message = "incompleta: mpvd se cerró durante la grabación"
                    rec.ended_at = rec.ended_at or now
                changed = True
            elif rec.status == "scheduled" and rec.end <= now:
                rec.status = "missed"
                rec.message = "el equipo o mpvd no estaban en marcha a esa hora"
                rec.ended_at = now
                changed = True
        if changed:
            self.save()

    async def start(self) -> None:
        self.recover()
        self._wake = asyncio.Event()
        self._loop_task = asyncio.create_task(self._loop(), name="mpvd-schedule")
        # if it ever dies anyway, say so in the log instead of «Task exception was never retrieved» at collection time
        self._loop_task.add_done_callback(self._loop_died)

    async def close(self) -> None:
        """mpvd stops: running recordings are closed properly and stay «recording» (resumed at the next start)."""
        self._closing = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        await asyncio.gather(*(self._stop_proc(rid) for rid in list(self._procs)), return_exceptions=True)
        tasks = list(self._tasks.values())
        if tasks:  # let them note the part just closed, then cancel what still waits (a retry delay)
            _, pending = await asyncio.wait(tasks, timeout=5)
            for t in pending:
                t.cancel()
            for t in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        self.save()

    def busy(self) -> bool:
        """Something is waiting or recording: mpvd must not exit for inactivity."""
        return any(r.status in ACTIVE for r in self.items.values())

    @staticmethod
    def _loop_died(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("schedule: el bucle del programador murió: %r", task.exception())

    def _poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            wait = MAX_WAKE
            # one bad recording (or a full disk while saving the list) must not kill the watcher: without this every
            # scheduled recording stopped for ever, none turned into "missed", and nobody was told
            try:
                now = time.time()
                for rec in list(self.items.values()):
                    if rec.status != "scheduled" or rec.id in self._tasks:
                        continue
                    if rec.end <= now:
                        self._finish(rec, "missed", "no se pudo empezar a tiempo")
                    elif rec.begin <= now:
                        self._tasks[rec.id] = asyncio.create_task(self._run(rec), name=f"mpvd-rec-{rec.id}")
                    else:
                        wait = min(wait, rec.begin - now)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the watcher survives anything a single recording can throw
                log.exception("schedule: fallo en el bucle del programador")
                wait = min(wait, 5.0)
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), max(0.05, wait))

    # -- recording ----------------------------------------------------------------------------

    @staticmethod
    def _has_output(rec: Recording) -> bool:
        return any(Path(f).is_file() and Path(f).stat().st_size > 0 for f in rec.files)

    def _out_path(self, rec: Recording) -> Path:
        folder = Path(rec.dir).expanduser() if rec.dir else default_dir()
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".mka" if rec.channel.get("kind") == "radio" else ".mkv"
        when = dt.datetime.fromtimestamp(rec.start).strftime("%Y-%m-%d %H.%M")
        name = safe_name(rec.channel.get("name") or "Canal", 40)
        if rec.title and rec.title != rec.channel.get("name"):
            name += " - " + safe_name(rec.title, 60)
        base = f"{name} - {when}"
        out = folder / (base + ext)
        n = 2
        while out.exists():
            out = folder / f"{base} ({n}){ext}"
            n += 1
        return out

    async def _run(self, rec: Recording) -> None:
        try:
            if rec.mode == "play":
                await self._play(rec)
            elif rec.mode == "both":
                # grabar y ver a la vez: la grabación manda (es la que puede fallar y la que deja un archivo)
                tocar = asyncio.create_task(self._play(rec), name=f"mpvd-play-{rec.id}")
                try:
                    await self._record(rec)
                finally:
                    tocar.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await tocar
            else:
                await self._record(rec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never kill the scheduler
            log.exception("recording %s failed", rec.id)
            self._finish(rec, "done" if self._has_output(rec) else "failed", str(exc)[:300])
        finally:
            self._tasks.pop(rec.id, None)
            self._procs.pop(rec.id, None)
            self._poke()

    # -- H57 · reproducir en una franja ------------------------------------------------------------------

    async def _player(self) -> Any:
        """Un reproductor donde poner esto: el que esté abierto o, si no hay ninguno, uno nuevo.

        Abrirlo importa: lo que da sentido a «a las 7:00 que suene la radio» es que el equipo esté suspendido, se
        despierte con el despertador del propio programa (H40) y encuentre que no hay ninguna ventana abierta."""
        sessions = [x for x in self.server.sessions.all() if x.connected]
        if sessions:
            return sessions[0]
        launcher = Path(self.server.root) / "bin" / ("mpv-uos.ps1" if sys.platform == "win32" else "mpv-uos")
        if not launcher.is_file():
            raise RpcError(UNAVAILABLE, "no hay ningún reproductor abierto y no se encuentra bin/mpv-uos")
        cmd = ["pwsh", "-NoProfile", "-File", str(launcher)] if sys.platform == "win32" else [str(launcher)]
        log.info("schedule: no hay reproductor abierto, se abre uno (%s)", launcher)
        await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        fin = time.monotonic() + PLAYER_WAIT
        while time.monotonic() < fin:
            await asyncio.sleep(0.5)
            abiertos = [x for x in self.server.sessions.all() if x.connected]
            if abiertos:
                return abiertos[0]
        raise RpcError(UNAVAILABLE, "el reproductor no ha llegado a abrirse")

    async def _play(self, rec: Recording) -> None:
        """Pone lo programado y lo deja sonando hasta la hora de fin."""
        rec.status = "playing" if rec.mode == "play" else rec.status
        rec.started_at = rec.started_at or time.time()
        self.save()
        self._push(rec)
        s = await self._player()
        url = str(rec.channel.get("url") or "")
        if not url:
            raise RpcError(INVALID_PARAMS, "no hay nada que reproducir")
        # J5 · una lista guardada se carga con `loadlist` (`loadfile` intentaría demuxear el .m3u8) y se repite
        # mientras dure la franja: «música de 21:00 a 23:00» con una lista de veinte minutos, si no, se acaba a y
        # veinte. Se guarda el valor que hubiera para devolverlo al terminar.
        lista = is_playlist_file(url)
        loop_before: Any = None
        if lista:
            with contextlib.suppress(Exception):
                loop_before = await s.client.get_property("loop-playlist", timeout=10)
            await s.client.command("loadlist", url, "replace", timeout=20)
            with contextlib.suppress(Exception):
                await s.client.set_property("loop-playlist", "inf", timeout=10)
        else:
            await s.client.command("loadfile", url, "replace", timeout=20)
        await s.client.set_property("pause", False, timeout=10)
        while not self._closing and rec.id not in self._stopping:
            queda = rec.end - time.time()
            if queda <= 0:
                break
            await asyncio.sleep(min(queda, 1.0))
        with contextlib.suppress(Exception):
            # al acabar la franja se para, que es lo que se ha pedido; si además había que suspender o apagar, de
            # eso se encarga `after` como en cualquier grabación
            if lista:
                await s.client.set_property("loop-playlist", loop_before if loop_before is not None else "no",
                                            timeout=10)
            await s.client.command("stop", timeout=10)
        if rec.mode == "play":
            self._finish(rec, "done", "")

    async def _record(self, rec: Recording) -> None:
        rec.status = "recording"
        rec.started_at = rec.started_at or time.time()
        self.save()
        self._push(rec)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{rec.id}.log"
        parts = 0
        last_error = ""
        while not self._closing and rec.id not in self._stopping:
            remaining = rec.end - time.time()
            if remaining < 1 or parts >= MAX_PARTS:
                break
            out = self._out_path(rec)
            cmd = [*inhibit_prefix(f"grabando «{rec.title}»"), *ffmpeg_args(rec.channel, out, remaining + 60)]
            log.info("recording %s: %s", rec.id, " ".join(cmd[:-1]) + " " + out.name)
            with open(log_path, "ab") as err:
                proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE,
                                                            stdout=asyncio.subprocess.DEVNULL, stderr=err)
            self._procs[rec.id] = proc
            parts += 1
            # stopped by the wall clock, not by -t: a live HLS starts a few segments back and reads them at once,
            # so the media time runs ahead of the clock (-t alone would end early and leave a tiny second part)
            try:
                await asyncio.wait_for(proc.wait(), max(0.5, rec.end - time.time()))
            except asyncio.TimeoutError:
                await self._stop_proc(rec.id)
            self._procs.pop(rec.id, None)
            if out.is_file() and out.stat().st_size > 0:
                rec.files.append(str(out))
                self.save()
            else:
                out.unlink(missing_ok=True)
                last_error = _tail(log_path) or f"ffmpeg terminó con código {proc.returncode}"
            if self._closing or rec.id in self._stopping or rec.end - time.time() < 2:
                break
            # the stream ended or dropped before the end: try again into a new part
            rec.message = "reconectando…"
            log.info("recording %s: stream ended early, retrying (%s)", rec.id, last_error[-200:])
            await asyncio.sleep(min(RETRY_DELAY, max(0.0, rec.end - time.time())))
        if self._closing:
            return  # stays «recording»: resumed at the next start (see recover)
        stopped = rec.id in self._stopping
        self._stopping.discard(rec.id)
        if self._has_output(rec):
            self._finish(rec, "done", "detenida antes de tiempo" if stopped else
                         ("en varias partes" if len(rec.files) > 1 else ""))
        elif stopped:
            self._finish(rec, "cancelled", "cancelada")
        else:
            self._finish(rec, "failed", last_error[-300:] or "no se pudo grabar")

    async def _stop_proc(self, rid: str) -> None:
        proc = self._procs.get(rid)
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.write(b"q")
                await proc.stdin.drain()
                proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 8)
            return
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    def _finish(self, rec: Recording, status: str, message: str = "") -> None:
        rec.status = status
        rec.message = message
        rec.ended_at = time.time()
        self.save()
        self._push(rec)
        if status in ("done", "failed"):
            name = rec.channel.get("name") or ""
            what = rec.title if rec.title and rec.title != name else name
            body = f"{what} ({name})" if what != name else what
            title = "Grabación terminada" if status == "done" else "La grabación ha fallado"
            asyncio.get_running_loop().create_task(desktop_notify(title, body + (f": {message}" if status == "failed"
                                                                                   and message else "")))
            power = getattr(self.server, "power", None)
            if power is not None and rec.after != "nothing":
                power.arm(rec.after, rec.id)
                asyncio.get_running_loop().create_task(power.on_recording_finished(rec.id))
            self.sync_wake()

    def _push(self, rec: Recording) -> None:
        payload = {"event": "schedule", "item": rec.public()}
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event(NOTIFY, "schedule:" + rec.id, rec.status, payload, final=rec.status in FINAL)

    # -- API ----------------------------------------------------------------------------------

    def add(self, ch: Channel, start: float, stop: float, title: str | None = None, margin_before: float = 0.0,
            margin_after: float = 0.0, programme: dict[str, Any] | None = None, folder: str | None = None,
            now: float | None = None, wake: bool | None = None, after: str | None = None,
            mode: str = "record") -> Recording:
        now = time.time() if now is None else now
        start, stop = float(start), float(stop)
        margin_before = max(0.0, min(float(margin_before or 0), 3600.0))
        margin_after = max(0.0, min(float(margin_after or 0), 3600.0))
        if stop <= start:
            raise RpcError(INVALID_PARAMS, "la hora de fin debe ser posterior a la de inicio")
        if stop + margin_after <= now:
            raise RpcError(INVALID_PARAMS, "esa hora ya ha pasado")
        if stop - start > MAX_DURATION:
            raise RpcError(INVALID_PARAMS, "como mucho 12 horas seguidas")
        if mode not in ("record", "play", "both"):
            raise RpcError(INVALID_PARAMS, "modo: record, play o both")
        if ch.drm and mode != "play":
            raise RpcError(INVALID_PARAMS, "este canal está protegido (DRM) y no se puede grabar")
        for other in self.items.values():  # the same programme twice
            if other.status in ACTIVE and other.channel.get("id") == ch.id and abs(other.start - start) < 1 \
                    and abs(other.stop - stop) < 1:
                return other
        defaults = self.load_defaults()
        wake = defaults["wake"] if wake is None else bool(wake)
        after = defaults["after"] if after is None else after
        if after not in ("nothing", "suspend", "shutdown"):
            raise RpcError(INVALID_PARAMS, "al terminar: nothing, suspend o shutdown")
        rec = Recording(id=uuid.uuid4().hex[:10], channel=channel_snapshot(ch), title=(title or ch.name).strip(),
                        start=start, stop=stop, margin_before=margin_before, margin_after=margin_after,
                        dir=folder or "", origin="epg" if programme else "manual", programme=programme,
                        wake=bool(wake), after=after, mode=mode)
        self.items[rec.id] = rec
        self.save()
        self._poke()
        self.sync_wake()
        return rec

    def sync_wake(self) -> None:
        """H40/F1: el despertador se pone para la PRIMERA grabación pendiente que lo pida. Si el equipo no puede
        (hace falta una regla de sudo, ver mpvd/power.py), no se pierde la grabación: se anota el motivo y se sigue."""
        soonest: Recording | None = None
        for rec in self.items.values():
            if rec.wake and rec.status == "scheduled" and (soonest is None or rec.begin < soonest.begin):
                soonest = rec
        if soonest is None:
            return
        rec = soonest
        power = getattr(self.server, "power", None)
        if power is None:
            return
        at = rec.begin - WAKE_MARGIN

        async def body() -> None:
            try:
                await power.schedule_wake(at)
            except RpcError as exc:
                rec.message = f"sin despertador: {exc.message}"
                self.save()
                self._push(rec)

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(body())

    def get(self, rid: str) -> Recording:
        rec = self.items.get(rid)
        if rec is None:
            raise RpcError(NOT_FOUND, f"no existe la grabación {rid}")
        return rec

    async def cancel(self, rid: str) -> Recording:
        rec = self.get(rid)
        if rec.status == "scheduled":
            task = self._tasks.get(rid)
            if task is None:
                self._finish(rec, "cancelled", "cancelada")
                return rec
        if rec.status in ("scheduled", "recording"):
            self._stopping.add(rid)
            await self._stop_proc(rid)
            task = self._tasks.get(rid)
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(asyncio.shield(task), 20)
        return rec

    async def remove(self, rid: str) -> bool:
        rec = self.get(rid)
        if rec.status in ACTIVE:
            await self.cancel(rid)
        self.items.pop(rid, None)
        self.save()
        return True

    def covers(self, channel_id: str, start: float, stop: float) -> bool:
        """A pending/running recording of this channel spans (most of) this programme."""
        for rec in self.items.values():
            if rec.status in ACTIVE and rec.channel.get("id") == channel_id and rec.begin <= start + 120 \
                    and rec.end >= stop - 120:
                return True
        return False

    def listing(self) -> list[dict[str, Any]]:
        active = sorted((r for r in self.items.values() if r.status in ACTIVE),
                        key=lambda r: (r.status != "recording", r.begin))
        done = sorted((r for r in self.items.values() if r.status not in ACTIVE),
                      key=lambda r: -(r.ended_at or r.stop))
        return [r.public() | {"label": describe(r.start, r.stop)} for r in active + done]


def _tail(path: Path, limit: int = 400) -> str:
    try:
        text = path.read_bytes()[-4000:].decode("utf-8", "replace").strip()
    except OSError:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[:limit]


def register(server: MpvdServer, service: ScheduleService) -> None:
    d = server.dispatcher

    async def resolve(channel: str) -> Channel:
        await service.iptv.ensure_loaded()
        return service.iptv.get(channel)

    @d.method("iptv.schedule.add")
    async def add(ctx: RpcContext, channel: str | None = None, start: float = 0.0, stop: float = 0.0,
                  title: str | None = None, margin_before: float = 0.0, margin_after: float = 0.0,
                  programme: dict[str, Any] | None = None, dir: str | None = None, wake: bool | None = None,
                  after: str | None = None, mode: str = "record",
                  media: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Schedule a recording of a channel (epoch seconds; optional margins in seconds and folder).

        Sin ``wake``/``after``, se usan los de `iptv.schedule.defaults`.
        ``wake``: poner el despertador del equipo 5 min antes (solo despierta de la suspensión; si hace falta una regla
        de sudo, la grabación se programa igual y se dice por qué no hay despertador). ``after``: nothing | suspend |
        shutdown al terminar, con los tres seguros y el aviso cancelable de mpvd/power.py."""
        # H57 · `media` es una canción, una carpeta o una lista de este equipo (o una dirección): se envuelve como
        # un canal de pega para reutilizar todo lo que ya sabe el programador (franja, despertador, apagado).
        if media:
            if mode == "record":
                mode = "play"
            ch = media_channel(str(media), title)
        elif channel:
            ch = await resolve(channel)
        else:
            raise RpcError(INVALID_PARAMS, "hace falta un canal o algo que reproducir")
        rec = service.add(ch, start, stop, title, margin_before, margin_after, programme, dir, wake=wake,
                          after=after, mode=mode)
        return rec.public() | {"label": describe(rec.start, rec.stop)}

    @d.method("iptv.schedule.defaults")
    async def defaults(ctx: RpcContext, wake: bool | None = None, after: str | None = None) -> dict[str, Any]:
        """Lo que se aplica a las grabaciones nuevas: ``wake`` (despertar el equipo 5 min antes) y ``after``
        (nothing | suspend | shutdown). Sin argumentos, solo lo consulta; incluye lo que puede hacer el equipo."""
        cur = service.set_defaults(wake, after) if (wake is not None or after is not None) else service.load_defaults()
        power = getattr(service.server, "power", None)
        return {**cur, "power": power.capabilities() if power is not None else {}}

    @d.method("iptv.schedule.list")
    async def listing(ctx: RpcContext) -> dict[str, Any]:
        """Scheduled, running and finished recordings (pending first) and the default folder."""
        return {"items": service.listing(), "dir": str(default_dir())}

    @d.method("iptv.schedule.cancel")
    async def cancel(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Cancel a pending recording or stop a running one (what was recorded is kept)."""
        return (await service.cancel(id)).public()

    @d.method("iptv.schedule.remove")
    async def remove(ctx: RpcContext, id: str) -> dict[str, Any]:  # noqa: A002
        """Forget a recording from the list (the files stay on disk); a running one is stopped first."""
        return {"removed": await service.remove(id)}

    @d.method("iptv.schedule.parse")
    async def parse(ctx: RpcContext, text: str, now: float | None = None) -> dict[str, Any]:
        """Preview of a manual time entry («21:30 22:15», «21:30 90», «mañana 9:00 1h», «ahora 30»)."""
        return parse_when(text, now)
