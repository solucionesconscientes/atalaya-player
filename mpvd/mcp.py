"""Minimal MCP (Model Context Protocol) server over stdio, no third-party dependencies (ADR-028).

Speaks JSON-RPC 2.0, one JSON object per line, following the 2025-06-18 spec subset that clients actually use:
``initialize``, ``notifications/initialized``, ``ping``, ``tools/list``, ``tools/call``, ``resources/list``,
``resources/read``, ``prompts/list``. Every tool is a thin wrapper over mpvd's own JSON-RPC methods, so the server
only needs the daemon socket. Disruptive actions (play, seek, play_channel, download) ask the viewer to confirm in
the OSD (``session.confirm`` → uosc dialog) unless ``--yes`` / ``MPVD_MCP_AUTOCONFIRM=1``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from mpvd import __version__
from mpvd.brand import app_name
from mpvd.client import MpvdClient
from mpvd.rpc import RpcError

log = logging.getLogger("mpvd.mcp")

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PLAY_MODES = ("replace", "append", "append-play")

TOOLS: list[dict[str, Any]] = [
    {"name": "status", "description": "Estado del reproductor: archivo/URL actual, título, posición, duración, pausa, "
                                      "pistas de subtítulos, sesiones de mpv conectadas y daemon.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "play", "description": "Reproduce un archivo local o URL (yt-dlp) en mpv. mode=replace sustituye lo actual "
                                    "(pide confirmación en pantalla si algo se está reproduciendo); append añade a la lista.",
     "inputSchema": {"type": "object", "required": ["target"], "properties": {
         "target": {"type": "string", "description": "ruta local o URL"},
         "mode": {"type": "string", "enum": list(PLAY_MODES), "default": "replace"}}}},
    {"name": "pause", "description": "Pausa la reproducción.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "resume", "description": "Reanuda la reproducción.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "seek", "description": "Salta en el tiempo (segundos relativos por defecto, o absolutos). Pide confirmación.",
     "inputSchema": {"type": "object", "required": ["seconds"], "properties": {
         "seconds": {"type": "number"}, "mode": {"type": "string", "enum": ["relative", "absolute"], "default": "relative"}}}},
    {"name": "search_dialogue", "description": "Busca una frase en la transcripción IA (whisper) del archivo actual o del "
                                               "indicado; devuelve los momentos (segundos) donde se dice. Si no hay "
                                               "transcripción la pone en marcha en segundo plano.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "path": {"type": "string", "description": "archivo (por defecto el actual)"},
         "limit": {"type": "integer", "default": 20}}}},
    {"name": "list_channels", "description": "Busca canales de TV y emisoras de radio (TDTChannels, iptv-org, Radio Browser, "
                                             "listas propias) por nombre, grupo o país.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer", "default": 20},
         "kind": {"type": "string", "enum": ["tv", "radio"]}}}},
    {"name": "play_channel", "description": "Sintoniza un canal por su id (de list_channels). Pide confirmación.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
    {"name": "download", "description": "Descarga una URL con yt-dlp usando un preset (video_best, video_1080, video_720, "
                                        "video_480, video_360, audio_original, audio_mp3_128, audio_mp3_192, audio_mp3_320, "
                                        "audio_mp3_vbr, audio_opus_128, audio_m4a_192, audio_flac, audio_wav…). Pide confirmación.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string"}, "preset": {"type": "string", "default": "video_best"}}}},
    {"name": "add_note", "description": "Guarda una nota en Markdown sobre lo que se está viendo, con enlace al minuto actual "
                                        "(<datos>/notas/<título del vídeo>.md).",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {
         "text": {"type": "string"}, "time_pos": {"type": "number", "description": "segundos (por defecto la posición actual)"}}}},
    {"name": "subtitles_ai", "description": "Subtítulos IA en vivo (whisper) del archivo actual: start, stop o status.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["start", "stop", "status"], "default": "status"},
         "language": {"type": "string", "description": "ISO 639-1 o auto"}}}},
]

INSTRUCTIONS = (f"{app_name()}: controla el reproductor mpv del usuario. Empieza por `status`. Las acciones que interrumpen lo que "
                "se está viendo (play, seek, play_channel, download) muestran una confirmación en pantalla que el usuario "
                "debe aceptar; si la rechaza o no contesta, la herramienta devuelve confirmed=false. Los tiempos son segundos.")


def text_content(data: Any) -> list[dict[str, Any]]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
    return [{"type": "text", "text": text}]


class McpServer:
    def __init__(self, socket_path: str, session_id: str | None = None, autoconfirm: bool = False):
        self.socket_path = socket_path
        self.session_id = session_id
        self.autoconfirm = autoconfirm
        self.client = MpvdClient(socket_path)
        self.initialized = False
        self._out: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    # -- daemon helpers -------------------------------------------------------------------

    async def call(self, method: str, params: Any = None, timeout: float = 60.0) -> Any:
        async with self._lock:
            return await self.client.call(method, params, timeout=timeout)

    async def prop(self, name: str) -> Any:
        try:
            r = await self.call("session.get", {"property": name, "session": self.session_id})
            return r.get("value")
        except RpcError:
            return None

    async def confirm(self, text: str) -> bool:
        if self.autoconfirm:
            return True
        r = await self.call("session.confirm", {"text": text, "session": self.session_id}, timeout=40.0)
        return bool(r.get("confirmed"))

    async def current_path(self) -> str | None:
        return await self.prop("path")

    # -- tools -----------------------------------------------------------------------------

    async def tool_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        sessions = await self.call("sessions.list")
        out: dict[str, Any] = {"daemon": await self.call("version"), "sessions": sessions}
        if sessions:
            props = {}
            for name in ("path", "media-title", "time-pos", "duration", "pause", "speed", "volume", "sid", "aid",
                         "playlist-pos", "playlist-count", "idle-active"):
                props[name] = await self.prop(name)
            tracks = await self.prop("track-list") or []
            props["subtitles"] = [{"id": t.get("id"), "title": t.get("title"), "lang": t.get("lang"),
                                   "selected": t.get("selected"), "external": t.get("external")}
                                  for t in tracks if t.get("type") == "sub"]
            out["player"] = props
            subs = await self.call("asr.status")
            out["ai_subtitles"] = [t for t in subs.get("tasks", []) if t.get("path") == props.get("path")]
        else:
            out["player"] = None
            out["hint"] = "no hay ninguna instancia de mpv conectada: abre una con bin/mpv-uos"
        return out

    async def tool_play(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target") or "").strip()
        mode = str(args.get("mode") or "replace")
        if not target:
            raise ValueError("target vacío")
        if mode not in PLAY_MODES:
            raise ValueError(f"mode debe ser uno de {PLAY_MODES}")
        current = await self.current_path()
        if mode == "replace" and current:
            ok = await self.confirm(f"Reproducir ahora: {target[-70:]}")
            if not ok:
                return {"confirmed": False, "message": "el usuario no confirmó"}
        r = await self.call("session.command", {"command": ["loadfile", target, mode], "session": self.session_id})
        return {"confirmed": True, "loaded": target, "mode": mode, "result": r.get("result")}

    async def tool_pause(self, _args: dict[str, Any]) -> dict[str, Any]:
        await self.call("session.set", {"property": "pause", "value": True, "session": self.session_id})
        return {"pause": True}

    async def tool_resume(self, _args: dict[str, Any]) -> dict[str, Any]:
        await self.call("session.set", {"property": "pause", "value": False, "session": self.session_id})
        return {"pause": False}

    async def tool_seek(self, args: dict[str, Any]) -> dict[str, Any]:
        seconds = float(args.get("seconds", 0))
        mode = str(args.get("mode") or "relative")
        if mode not in ("relative", "absolute"):
            raise ValueError("mode debe ser relative o absolute")
        label = f"Saltar a {seconds:.0f} s" if mode == "absolute" else f"Saltar {seconds:+.0f} s"
        if not await self.confirm(label):
            return {"confirmed": False, "message": "el usuario no confirmó"}
        await self.call("session.command", {"command": ["seek", seconds, mode], "session": self.session_id})
        await asyncio.sleep(0.2)
        return {"confirmed": True, "time_pos": await self.prop("time-pos")}

    async def tool_search_dialogue(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query vacía")
        path = args.get("path") or await self.current_path()
        limit = int(args.get("limit") or 20)
        res = await self.call("asr.search", {"q": query, "path": path, "limit": limit})
        if res.get("tasks") == 0 and path and "://" not in str(path):
            try:
                t = await self.call("asr.precompute", {"path": path})
                res["transcription"] = {"started": True, "task": t.get("id"), "status": t.get("status"),
                                        "message": "no había transcripción: se está generando, vuelve a buscar en un momento"}
            except RpcError as exc:
                res["transcription"] = {"started": False, "error": exc.args[0]}
        return res

    async def tool_list_channels(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        rows = await self.call("iptv.search", {"q": query, "limit": int(args.get("limit") or 20), "kind": args.get("kind"),
                                               "compact": True}, timeout=120)
        return {"query": query, "channels": rows}

    async def tool_play_channel(self, args: dict[str, Any]) -> dict[str, Any]:
        cid = str(args.get("id") or "").strip()
        if not cid:
            raise ValueError("id vacío")
        ch = await self.call("iptv.channel", {"id": cid})
        name = ch.get("name") if isinstance(ch, dict) else cid
        if not await self.confirm(f"Sintonizar {name}"):
            return {"confirmed": False, "message": "el usuario no confirmó"}
        await self.call("session.command", {"command": ["script-message-to", "mu_iptv", "mu-iptv-play", cid],
                                            "session": self.session_id})
        return {"confirmed": True, "channel": ch}

    async def tool_download(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url") or "").strip()
        preset = str(args.get("preset") or "video_best")
        if not url:
            raise ValueError("url vacía")
        if not await self.confirm(f"Descargar ({preset}): {url[-60:]}"):
            return {"confirmed": False, "message": "el usuario no confirmó"}
        item = await self.call("ytdl.download", {"url": url, "preset": preset})
        return {"confirmed": True, "download": item}

    async def tool_add_note(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("text vacío")
        params: dict[str, Any] = {"text": text, "session": self.session_id}
        if args.get("time_pos") is not None:
            params["time_pos"] = float(args["time_pos"])
        return await self.call("notes.add", params)

    async def tool_subtitles_ai(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "status")
        if action == "start":
            lang = str(args.get("language") or "auto")
            await self.call("session.command", {"command": ["script-message-to", "mu_subs", "mu-subs-set", "language", lang],
                                                "session": self.session_id})
            await self.call("session.command", {"command": ["script-message-to", "mu_subs", "mu-subs-start"],
                                                "session": self.session_id})
            await asyncio.sleep(0.5)
        elif action == "stop":
            await self.call("session.command", {"command": ["script-message-to", "mu_subs", "mu-subs-stop"],
                                                "session": self.session_id})
        st = await self.prop("user-data/mu/subs")
        return {"action": action, "state": st}

    # -- resources -------------------------------------------------------------------------

    async def resources_list(self) -> list[dict[str, Any]]:
        st = await self.call("asr.status")
        out = []
        for t in st.get("tasks", []):
            name = os.path.basename(t.get("path") or t.get("id"))
            out.append({"uri": f"mpv://transcript/{t['id']}", "name": f"Transcripción de {name}",
                        "description": f"{t.get('cues', 0)} cues · {t.get('status')} · {t.get('model')} {t.get('detected') or t.get('language')}",
                        "mimeType": "application/x-subrip"})
        notes = await self.call("notes.list")
        for n in notes:
            out.append({"uri": f"mpv://notes/{n['key']}", "name": f"Notas: {n['title']}", "mimeType": "text/markdown"})
        return out

    async def resources_read(self, uri: str) -> list[dict[str, Any]]:
        if uri.startswith("mpv://transcript/"):
            task_id = uri.rsplit("/", 1)[-1]
            t = await self.call("asr.status", {"id": task_id})
            text = ""
            if t.get("srt") and os.path.isfile(t["srt"]):
                with open(t["srt"], encoding="utf-8") as fh:
                    text = fh.read()
            return [{"uri": uri, "mimeType": "application/x-subrip", "text": text}]
        if uri.startswith("mpv://notes/"):
            key = uri.rsplit("/", 1)[-1]
            n = await self.call("notes.read", {"key": key})
            return [{"uri": uri, "mimeType": "text/markdown", "text": n["markdown"]}]
        raise ValueError(f"recurso desconocido: {uri}")

    # -- protocol --------------------------------------------------------------------------

    def tool_handler(self, name: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None:
        return getattr(self, f"tool_{name}", None) if any(t["name"] == name for t in TOOLS) else None

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        params = msg.get("params") or {}
        mid = msg.get("id")
        if method is None:
            return None  # a response to something we never sent
        try:
            if method == "initialize":
                requested = str(params.get("protocolVersion") or PROTOCOL_VERSIONS[0])
                version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result: Any = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False},
                               "resources": {"subscribe": False, "listChanged": False}, "prompts": {"listChanged": False}},
                               "serverInfo": {"name": "mpv-uos", "version": __version__}, "instructions": INSTRUCTIONS}
                self.initialized = True
            elif method == "notifications/initialized" or method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name = str(params.get("name") or "")
                handler = self.tool_handler(name)
                try:
                    if handler is None:
                        raise ValueError(f"herramienta desconocida: {name}")
                    data = await handler(params.get("arguments") or {})
                    result = {"content": text_content(data), "structuredContent": data, "isError": False}
                except (RpcError, ValueError, TypeError) as exc:
                    detail = getattr(exc, "data", None)
                    result = {"content": text_content({"error": str(exc), "data": detail}), "isError": True}
            elif method == "resources/list":
                result = {"resources": await self.resources_list()}
            elif method == "resources/read":
                result = {"contents": await self.resources_read(str(params.get("uri") or ""))}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except (RpcError, ValueError, ConnectionError) as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(exc)}}
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
        self._out = asyncio.StreamWriter(w_transport, w_protocol, None, loop)
        await self.client.__aenter__()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    await self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                    continue
                if isinstance(msg, list):
                    for m in msg:
                        resp = await self.handle(m)
                        if resp is not None:
                            await self.send(resp)
                    continue
                resp = await self.handle(msg)
                if resp is not None:
                    await self.send(resp)
        finally:
            await self.client.__aexit__(None, None, None)
        return 0

    async def send(self, obj: dict[str, Any]) -> None:
        assert self._out is not None
        self._out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._out.drain()


async def serve(socket_path: str, session_id: str | None = None, autoconfirm: bool = False) -> int:
    return await McpServer(socket_path, session_id, autoconfirm).run()
