"""What keeps working behind the player's back, and what to do about it when the window closes (H36/C8).

mpvd outlives mpv on purpose: a transcription at 60 % finishes instead of being thrown away. The problem was that
nobody was told. This gathers the three kinds of work that do survive — AI subtitles, downloads and conversions —
into one summary that the player can always show («qué se está haciendo por detrás»), reminds whoever opens the
player again, and, when the last window closes with subtitles still going, puts a desktop notice up with a *Parar*
button.

Scheduled recordings are listed but never touched: they are an appointment with a time, and stopping one silently
would lose the programme. Stopping a transcription loses nothing: it resumes where it was the next time the file is
opened (the per-chunk cache).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from mpvd import notify

if TYPE_CHECKING:
    from mpvd.server import MpvdServer
    from mpvd.sessions import Session

log = logging.getLogger("mpvd.pending")

POLL = 5.0          # seconds between summaries while there is anything running (the loop sleeps otherwise)
NOTICE_SECONDS = 600.0
ALIVE = ("queued", "running")


class PendingService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self._loop_task: asyncio.Task[None] | None = None
        self._notice: asyncio.Task[None] | None = None
        self._last: dict[str, Any] | None = None
        server.session_listeners.append(self._session_event)

    # -- what is running ------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """The three queues plus the recordings, already turned into one Spanish line for the menu."""
        subs = [{"id": t.id, "path": t.path, "progress": round(t.progress, 3), "model": t.model,
                 "remaining": t.remaining_seconds()}
                for t in self.server.asr.tasks.values() if not t.complete and t.status in ALIVE]
        downloads = [r for r in self.server.ytdl.downloads.list(include_finished=False) if r.get("status") in ALIVE]
        converts = [r for r in self.server.convert.list(include_finished=False) if r.get("status") in ALIVE]
        recordings = [r for r in self.server.schedule.items.values() if r.status == "recording"]
        out = {"subs": subs, "downloads": len(downloads), "converts": len(converts),
               "recordings": len(recordings), "busy": bool(subs or downloads or converts or recordings)}
        out["text"] = describe(out)
        return out

    def stoppable(self) -> list[str]:
        """Task ids that may be stopped on the user's behalf: the transcriptions, never a recording."""
        return [s["id"] for s in self.summary()["subs"]]

    def stop_subs(self) -> int:
        """Returns how many really stopped: a task whose job had already finished does not count as stopped."""
        stopped = 0
        for task_id in self.stoppable():
            with contextlib.suppress(Exception):
                task = self.server.asr.stop(task_id)
                if task.status not in ALIVE:
                    stopped += 1
        return stopped

    # -- telling the player ---------------------------------------------------------------------

    def _session_event(self, kind: str, session: Session) -> None:
        if kind == "open":
            self._cancel_notice()
            self._push_one(session)
            self._ensure_loop()
        elif kind == "closed" and not len(self.server.sessions):
            self._offer_to_stop()

    def _push_one(self, session: Session) -> None:
        """Reminder for whoever just opened the player: this is what was left running."""
        data = self.summary()
        if not data["busy"]:
            return
        session.push_event("mu_core", "pending", data["text"], {"event": "pending", "pending": data},
                           min_interval=0.0)

    def _broadcast(self, data: dict[str, Any]) -> None:
        payload = {"event": "pending", "pending": data}
        for session in self.server.sessions.all():
            if session.connected:
                session.push_event("mu_core", "pending", data["text"], payload, min_interval=0.0)

    def _ensure_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run(), name="mpvd-pending")

    async def _run(self) -> None:
        """While there is work, push a summary when it changes; stop looping when everything is finished."""
        try:
            while True:
                data = self.summary()
                text = data["text"]
                if text != (self._last or {}).get("text"):
                    self._last = data
                    self._broadcast(data)
                if not data["busy"] or not len(self.server.sessions):
                    return
                await asyncio.sleep(POLL)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("pending loop failed")

    # -- the desktop notice ---------------------------------------------------------------------

    def _cancel_notice(self) -> None:
        if self._notice is not None and not self._notice.done():
            self._notice.cancel()
        self._notice = None

    def _offer_to_stop(self) -> None:
        data = self.summary()
        if not data["subs"] or not notify.available():
            return
        self._cancel_notice()
        self._notice = asyncio.create_task(self._notice_body(data), name="mpvd-pending-notice")

    async def _notice_body(self, data: dict[str, Any]) -> None:
        try:
            answer = await notify.ask("Sigo con los subtítulos", data["text"], {"parar": "Parar"},
                                      timeout=NOTICE_SECONDS)
            if answer == "parar":
                n = self.stop_subs()
                await notify.notify("Subtítulos detenidos",
                                    "Lo hecho se guarda: seguirá donde iba cuando vuelvas a abrir el archivo."
                                    if n else "Ya habían terminado.")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("pending notice failed")


def describe(data: dict[str, Any]) -> str:
    """One line in Spanish: «Subtítulos de peli.mkv (62 %, 4 min) · 2 descargas»."""
    parts: list[str] = []
    for s in data["subs"]:
        name = s["path"].replace("\\", "/").rsplit("/", 1)[-1] or "este archivo"
        pct = round((s.get("progress") or 0) * 100)
        left = s.get("remaining")
        detail = f"{pct} %" + (f", {human(left)}" if left else "")
        parts.append(f"Subtítulos de {name} ({detail})")
    if data["downloads"]:
        parts.append(plural(data["downloads"], "descarga", "descargas"))
    if data["converts"]:
        parts.append(plural(data["converts"], "conversión", "conversiones"))
    if data["recordings"]:
        parts.append(plural(data["recordings"], "grabación programada", "grabaciones programadas"))
    return " · ".join(parts)


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def human(seconds: float) -> str:
    n = max(0, int(seconds))
    if n < 60:
        return f"{n} s"
    if n < 3600:
        return f"{round(n / 60)} min"
    horas, resto = divmod(n, 3600)
    minutos = round(resto / 60)
    return f"{horas} h {minutos} min" if minutos else f"{horas} h"


def register(server: MpvdServer, service: PendingService) -> None:
    d = server.dispatcher

    @d.method("pending.status")
    async def status(ctx: Any) -> dict[str, Any]:
        """What keeps working behind the player: AI subtitles, downloads, conversions and recordings (C8)."""
        return service.summary()

    @d.method("pending.stop_subs")
    async def stop_subs(ctx: Any) -> dict[str, Any]:
        """Stop the unfinished transcriptions (what is done stays cached and resumes later). Recordings untouched."""
        return {"stopped": service.stop_subs()}
