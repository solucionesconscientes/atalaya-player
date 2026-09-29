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
            raise RpcError(NOT_FOUND, f"no session {session_id}")
        return s
    if ctx_session is not None and ctx_session.connected:
        return ctx_session
    live = [s for s in server.sessions.all() if s.connected] if hasattr(server.sessions, "all") else []
    if not live:
        raise RpcError(UNAVAILABLE, "no hay ninguna instancia de mpv conectada")
    return max(live, key=lambda s: s.created_at)


async def confirm(session: Session, text: str, timeout: float = CONFIRM_TIMEOUT) -> bool:
    """Show a yes/no dialog (mu-menu ``mu-confirm``) and wait for ``user-data/mu/confirm`` to carry our token."""
    token = f"c{int(time.time() * 1000) % 10**9:09d}"
    try:
        await session.client.command("script-message-to", "mu_menu", "mu-confirm", token, text)
    except MpvIpcError as exc:
        raise RpcError(UNAVAILABLE, f"no se pudo mostrar la confirmación: {exc}") from exc
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


class NotesStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "notas"

    def path_for(self, key: str) -> Path:
        return self.dir / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".md")

    def add(self, key: str, title: str, media_path: str, text: str, time_pos: float | None) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path_for(key)
        new = not p.exists()
        with p.open("a", encoding="utf-8") as fh:
            if new:
                fh.write(f"# {title or media_path}\n\n`{media_path}`\n\n")
            stamp = time.strftime("%Y-%m-%d %H:%M")
            if time_pos is not None:
                link = f"[{hms(time_pos)}](mpv://seek?t={time_pos:.1f})"
                fh.write(f"- {link} · {stamp} — {text.strip()}\n")
            else:
                fh.write(f"- {stamp} — {text.strip()}\n")
        return {"file": str(p), "key": key, "new_file": new, "time_pos": time_pos}

    def read(self, key: str) -> str | None:
        p = self.path_for(key)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.glob("*.md"), key=lambda q: q.stat().st_mtime, reverse=True):
            first = p.read_text(encoding="utf-8").splitlines()[:1]
            out.append({"file": str(p), "key": p.stem, "title": first[0].lstrip("# ").strip() if first else p.stem,
                        "notes": sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- "))})
        return out


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
                    raise RpcError(INVALID_PARAMS, "no hay archivo: indica path") from None
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
            raise RpcError(NOT_FOUND, f"no notes for {key}")
        return {"key": key, "markdown": text}
