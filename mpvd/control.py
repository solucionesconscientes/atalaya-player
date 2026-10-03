"""``session.*`` (drive one mpv instance through its IPC: get/set properties, run commands, ask the viewer to confirm)
and ``notes.*`` (Markdown notes with time links, one file per media key). Used by the MCP server (``mpvd/mcp.py``)."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.hashing import file_hash
from mpvd.mpvipc import MpvIpcError
from mpvd.notes import NotesStore
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext
    from mpvd.sessions import Session

CONFIRM_PROP = "user-data/mu/confirm"
CONFIRM_TIMEOUT = 15.0


def pick_session(server: MpvdServer, session_id: str | None, ctx_session: Session | None = None) -> Session:
    """The session asked for, the caller's own, or the most recently created connected one."""
    if session_id:
        s = server.sessions.get(session_id)
        if s is None or not s.connected:
            raise RpcError(NOT_FOUND, t("no session %s") % (session_id,))
        return s
    if ctx_session is not None and ctx_session.connected:
        return ctx_session
    live = [s for s in server.sessions.all() if s.connected] if hasattr(server.sessions, "all") else []
    if not live:
        raise RpcError(UNAVAILABLE, t("no hay ninguna instancia de mpv conectada"))
    return max(live, key=lambda s: s.created_at)


async def confirm(session: Session, text: str, timeout: float = CONFIRM_TIMEOUT) -> bool:
    """Show a yes/no dialog (mu-menu ``mu-confirm``) and wait for ``user-data/mu/confirm`` to carry our token."""
    token = f"c{int(time.time() * 1000) % 10**9:09d}"
    try:
        await session.client.command("script-message-to", "mu_menu", "mu-confirm", token, text)
    except MpvIpcError as exc:
        raise RpcError(UNAVAILABLE, t("no se pudo mostrar la confirmación: %s") % (exc,)) from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = await session.client.get_property(CONFIRM_PROP)
        except MpvIpcError:
            value = None
        if isinstance(value, dict) and value.get("token") == token:
            return value.get("answer") == "yes"
        await asyncio.sleep(0.2)
    with_timeout = {"token": token, "answer": "timeout"}
    try:
        await session.client.set_property(CONFIRM_PROP, with_timeout)
        await session.client.command("script-message-to", "mu_menu", "mu-confirm-close", token)
    except MpvIpcError:
        pass
    return False


def media_key(path: str | None) -> str | None:
    if not path:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not path.startswith("file://"):
        return "url:" + re.sub(r"[^A-Za-z0-9]+", "_", path)[:80]
    p = Path(path.removeprefix("file://"))
    try:
        return file_hash(p).key
    except OSError:
        return None


def hms(seconds: float) -> str:
    s = int(max(0.0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def register(server: MpvdServer) -> None:
    d = server.dispatcher
    notes = NotesStore(server.settings.data_dir)
    server.notes = notes  # type: ignore[attr-defined]

    def _pick(ctx: RpcContext, session_id: str | None) -> Session:
        return pick_session(server, session_id, ctx.session)

    @d.method("session.get")
    async def session_get(ctx: RpcContext, property: str, session: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Read an mpv property from a session (the caller's, the given id, or the most recent one)."""
        s = _pick(ctx, session)
        try:
            value = await s.client.get_property(property)
        except MpvIpcError as exc:
            if "unavailable" in str(exc):
                value = None
            else:
                raise RpcError(INVALID_PARAMS, str(exc)) from exc
        return {"session": s.id, "property": property, "value": value}

    @d.method("session.set")
    async def session_set(ctx: RpcContext, property: str, value: Any, session: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Set an mpv property in a session."""
        s = _pick(ctx, session)
        try:
            await s.client.set_property(property, value)
        except MpvIpcError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        return {"session": s.id, "property": property, "value": value}

    @d.method("session.command")
    async def session_command(ctx: RpcContext, command: list[Any], session: str | None = None) -> dict[str, Any]:
        """Run an mpv command (list form) in a session."""
        if not isinstance(command, list) or not command:
            raise RpcError(INVALID_PARAMS, "command must be a non-empty list")
        s = _pick(ctx, session)
        try:
            result = await s.client.command(*command)
        except MpvIpcError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        return {"session": s.id, "result": result}

    @d.method("session.confirm")
    async def session_confirm(ctx: RpcContext, text: str, session: str | None = None,
                              timeout: float = CONFIRM_TIMEOUT) -> dict[str, Any]:
        """Ask the viewer (uosc dialog) and wait for the answer."""
        s = _pick(ctx, session)
        ok = await confirm(s, text, timeout)
        return {"session": s.id, "confirmed": ok}

    @d.method("notes.add")
    async def notes_add(ctx: RpcContext, text: str, path: str | None = None, time_pos: float | None = None,
                        title: str | None = None, session: str | None = None) -> dict[str, Any]:
        """Append a note (with a time link) to the Markdown file of the current or given media."""
        if not text or not text.strip():
            raise RpcError(INVALID_PARAMS, "empty note")
        if path is None or time_pos is None or title is None:
            try:
                s = _pick(ctx, session)
                if path is None:
                    path = await s.client.get_property("path")
                if time_pos is None:
                    try:
                        time_pos = await s.client.get_property("time-pos")
                    except MpvIpcError:
                        time_pos = None
                if title is None:
                    try:
                        title = await s.client.get_property("media-title")
                    except MpvIpcError:
                        title = None
            except RpcError:
                if path is None:
                    raise RpcError(INVALID_PARAMS, t("no hay archivo: indica path")) from None
        key = media_key(path) or "sin-archivo"
        return await asyncio.to_thread(notes.add, key, title or "", path or "", text, time_pos)

    @d.method("notes.list")
    async def notes_list(ctx: RpcContext) -> list[dict[str, Any]]:
        """Note files (one per media), most recent first."""
        return await asyncio.to_thread(notes.list)

    @d.method("notes.read")
    async def notes_read(ctx: RpcContext, key: str) -> dict[str, Any]:
        """Markdown of a note file."""
        text = await asyncio.to_thread(notes.read, key)
        if text is None:
            raise RpcError(NOT_FOUND, t("no notes for %s") % (key,))
        return {"key": key, "markdown": text}

    async def _key(key: str | None, path: str | None) -> str:
        if key:
            return key
        k = await asyncio.to_thread(media_key, path) if path else None
        if not k:
            raise RpcError(INVALID_PARAMS, "indica key o path")
        return k

    @d.method("notes.get")
    async def notes_get(ctx: RpcContext, key: str | None = None, path: str | None = None) -> dict[str, Any]:
        """The notes of one video (by key, or by path: the content key is computed), each with its index."""
        k = await _key(key, path)
        got = await asyncio.to_thread(notes.get, k)
        return got if got is not None else {"key": k, "title": "", "path": path or "", "file": "", "notes": 0,
                                            "items": []}

    @d.method("notes.edit")
    async def notes_edit(ctx: RpcContext, key: str, index: int, text: str) -> dict[str, Any]:
        """Replace the text of a note."""
        if not text or not text.strip():
            raise RpcError(INVALID_PARAMS, "empty note")
        try:
            return await asyncio.to_thread(notes.edit, key, int(index), text)
        except KeyError as exc:
            raise RpcError(NOT_FOUND, str(exc)) from None

    @d.method("notes.delete")
    async def notes_delete(ctx: RpcContext, key: str, index: int) -> dict[str, Any]:
        """Delete a note (the file goes with the last one)."""
        try:
            return await asyncio.to_thread(notes.delete, key, int(index))
        except KeyError as exc:
            raise RpcError(NOT_FOUND, str(exc)) from None

    @d.method("notes.export")
    async def notes_export(ctx: RpcContext, key: str, folder: str | None = None) -> dict[str, Any]:
        """Copy the notes next to the video (no folder) or into a folder (e.g. an Obsidian vault)."""
        try:
            return await asyncio.to_thread(notes.export, key, folder)
        except KeyError as exc:
            raise RpcError(NOT_FOUND, str(exc)) from None
        except (ValueError, OSError) as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None
