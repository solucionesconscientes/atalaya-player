"""Downloads panel (H23): a web page served by the remote-control HTTP server (same port, same pairing cookie) with
the «Tareas» of mpvd — downloads and conversions — live, several at a time, plus a box to paste or drop links.

Routes (all but the page itself need the paired cookie of the remote):

* ``GET /downloads`` and ``/downloads.js``: the page (static, like the PWA).
* ``GET /api/tasks``: ``tasks.list`` rows + free disk space of the download folders.
* ``GET /events/tasks``: the same payload as server-sent events, only when something changed (at most 1 per second).
* ``POST /api/tasks/action`` ``{action: cancel|retry|remove|play, items: [{type, id}]}``.
* ``POST /api/tasks/clear``: forget every finished task (files are kept).
* ``GET /api/downloads/presets``, ``POST /api/downloads/add`` ``{text, preset}`` → ``ytdl.download.batch``.
* ``GET /api/disk``.

Nothing arbitrary reaches mpvd: the actions map to a fixed list of RPC methods and «play» only opens a file that a
finished task wrote."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd import __version__
from mpvd.remote.http import HttpError, Request, Response, sse_event

if TYPE_CHECKING:
    from mpvd.remote.service import RemoteService

log = logging.getLogger("mpvd.remote.downloads")

DEFAULT_PRESET = "video_best"   # same as «Descargar varios enlaces» in mu-ytdl
MAX_ITEMS = 500                 # items per bulk action
MAX_ROWS = 300                  # rows sent to the page (active first, then the newest history)
POLL = 1.0                      # seconds between checks of the SSE stream (≤ 1 Hz, like the brief asks)
DISK_TTL = 10.0
ACTIONS: dict[tuple[str, str], str] = {
    ("download", "cancel"): "ytdl.downloads.cancel",
    ("download", "retry"): "ytdl.downloads.retry",
    ("download", "remove"): "ytdl.downloads.remove",
    ("convert", "cancel"): "convert.cancel",
    ("convert", "retry"): "convert.retry",
    ("convert", "remove"): "convert.remove",
}


def _existing(path: Path) -> Path | None:
    """The folder itself or its nearest existing parent (the download folder is created on the first download)."""
    p = path
    for _ in range(64):
        if p.exists():
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def disk_usage(folders: dict[str, Path]) -> list[dict[str, Any]]:
    """Free space where each folder lives; folders on the same filesystem are reported once (kinds joined)."""
    out: list[dict[str, Any]] = []
    seen: dict[int, dict[str, Any]] = {}
    for kind, folder in folders.items():
        base = _existing(folder)
        if base is None:
            continue
        try:
            dev = base.stat().st_dev
            usage = shutil.disk_usage(base)
        except OSError:
            continue
        if dev in seen:
            seen[dev]["kinds"].append(kind)
            continue
        row = {"kinds": [kind], "path": str(folder), "free": usage.free, "total": usage.total, "used": usage.used,
               "percent_free": round(100.0 * usage.free / usage.total, 1) if usage.total else 0.0}
        seen[dev] = row
        out.append(row)
    return out


class DownloadsPanel:
    def __init__(self, remote: RemoteService):
        self.remote = remote
        self.server = remote.server
        self._disk: tuple[float, list[dict[str, Any]]] = (0.0, [])

    # -- data -----------------------------------------------------------------------------------------

    def folders(self) -> dict[str, Path]:
        ytdl = getattr(self.server, "ytdl", None)
        if ytdl is None:
            return {}
        s = ytdl.downloads.settings
        return {"video": s.resolved_dir("video"), "audio": s.resolved_dir("audio")}

    async def disk(self, force: bool = False) -> list[dict[str, Any]]:
        at, value = self._disk
        if force or time.monotonic() - at > DISK_TTL:
            value = await asyncio.to_thread(disk_usage, self.folders())
            self._disk = (time.monotonic(), value)
        return value

    async def tasks(self) -> dict[str, Any]:
        res = await self.remote._rpc("tasks.list", {"include_finished": True})
        rows = list(res.get("tasks") or [])
        history = sum(1 for r in rows if r["status"] not in ("queued", "running"))
        return {"tasks": rows[:MAX_ROWS], "active": res.get("active", 0), "history": history,
                "disk": await self.disk(), "ts": round(time.time(), 2)}

    async def presets(self) -> dict[str, Any]:
        res = await self.remote._rpc("ytdl.presets")
        rows = [{"id": p["id"], "title": p["title"], "group": p.get("group", "")} for p in res.get("presets") or []]
        return {"presets": rows, "default": DEFAULT_PRESET}

    async def add(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HttpError(400, "se esperaba un objeto")
        text = str(body.get("text") or "")
        urls = [str(u) for u in body.get("urls") or [] if isinstance(u, str)] if isinstance(body.get("urls"), list) else []
        if not text.strip() and not urls:
            raise HttpError(400, "pega o arrastra algún enlace")
        preset = str(body.get("preset") or DEFAULT_PRESET)
        known = {p["id"] for p in (await self.presets())["presets"]}
        if preset not in known:
            raise HttpError(400, f"formato desconocido: {preset}")
        res = await self.remote._rpc("ytdl.download.batch", {"text": text[:200_000], "urls": urls[:MAX_ITEMS],
                                                             "preset": preset})
        return {"count": res.get("count", 0), "ids": [i.get("id") for i in res.get("items") or []], "preset": preset}

    async def action(self, body: Any, row: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HttpError(400, "se esperaba un objeto")
        action = str(body.get("action") or "")
        items = body.get("items")
        if action not in ("cancel", "retry", "remove", "play"):
            raise HttpError(400, f"acción desconocida: {action}")
        if not isinstance(items, list) or not items:
            raise HttpError(400, "no hay nada seleccionado")
        if len(items) > MAX_ITEMS:
            raise HttpError(400, f"como mucho {MAX_ITEMS} a la vez")
        if action == "play":
            return await self._play(items[0], row)
        results = []
        for it in items:
            kind = str(it.get("type") or "") if isinstance(it, dict) else ""
            tid = str(it.get("id") or "") if isinstance(it, dict) else ""
            method = ACTIONS.get((kind, action))
            if method is None or not tid:
                results.append({"type": kind, "id": tid, "ok": False, "error": "tarea no válida"})
                continue
            try:
                res = await self.remote._rpc(method, {"id": tid})
            except HttpError as exc:
                results.append({"type": kind, "id": tid, "ok": False, "error": exc.message})
                continue
            ok = bool(res.get("cancelled") or res.get("removed")) if action != "retry" else bool(res.get("id"))
            results.append({"type": kind, "id": tid, "ok": ok, "error": "" if ok else "no se puede ahora"})
        return {"action": action, "done": sum(1 for r in results if r["ok"]), "results": results}

    async def _play(self, item: Any, row: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise HttpError(400, "tarea no válida")
        rows = (await self.remote._rpc("tasks.list", {"include_finished": True})).get("tasks") or []
        task = next((r for r in rows if r["type"] == item.get("type") and r["id"] == item.get("id")), None)
        if task is None or task["status"] != "done" or not task.get("outputs"):
            raise HttpError(400, "esa tarea no tiene ningún archivo terminado")
        target = next((o for o in task["outputs"] if not o.endswith((".srt", ".vtt", ".ass"))), task["outputs"][0])
        if not Path(target).exists():
            raise HttpError(400, "el archivo ya no está: " + target)
        session = self.remote._session_for(row)
        await self.remote.command(session, "play", {"target": target})
        return {"action": "play", "done": 1, "target": target}

    async def clear(self) -> dict[str, Any]:
        return await self.remote._rpc("tasks.clear")

    async def events(self, row: dict[str, Any]) -> AsyncIterator[bytes]:
        self.remote.clients += 1
        last = ""
        beat = time.monotonic()
        try:
            yield sse_event("hello", {"version": __version__, "name": row["name"]})
            while True:
                try:
                    payload = await self.tasks()
                except HttpError as exc:
                    payload = {"error": exc.message, "tasks": [], "active": 0, "history": 0, "disk": []}
                key = json.dumps({k: v for k, v in payload.items() if k != "ts"}, sort_keys=True, default=str)
                if key != last:
                    yield sse_event("tasks", payload)
                    last = key
                    beat = time.monotonic()
                elif time.monotonic() - beat > 15:
                    yield b": ping\n\n"
                    beat = time.monotonic()
                await asyncio.sleep(POLL)
        finally:
            self.remote.clients -= 1

    # -- HTTP -------------------------------------------------------------------------------------------

    async def handle(self, req: Request, row: dict[str, Any]) -> Response | None:
        """Authenticated routes of the panel; None when the path is not ours."""
        path, method = req.path, req.method
        if path == "/events/tasks":
            return Response.sse(self.events(row))
        if path == "/api/tasks" and method == "GET":
            return Response.json(await self.tasks())
        if path == "/api/tasks/action" and method == "POST":
            return Response.json(await self.action(req.json(), row))
        if path == "/api/tasks/clear" and method == "POST":
            return Response.json(await self.clear())
        if path == "/api/downloads/presets" and method == "GET":
            return Response.json(await self.presets())
        if path == "/api/downloads/add" and method == "POST":
            return Response.json(await self.add(req.json()))
        if path == "/api/disk" and method == "GET":
            return Response.json({"disk": await self.disk(force=True)})
        return None
