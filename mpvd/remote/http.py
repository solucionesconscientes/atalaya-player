"""Minimal HTTP/1.1 server on asyncio (no aiohttp): request parsing with size limits, JSON/static/redirect
responses and server-sent events. One request per connection (``Connection: close``); browsers cope fine and
it keeps the code small. Only what the remote-control PWA needs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

log = logging.getLogger("mpvd.remote.http")

MAX_HEAD = 16 * 1024
MAX_BODY = 1024 * 1024
STATUS_TEXT = {200: "OK", 204: "No Content", 206: "Partial Content", 302: "Found", 304: "Not Modified",
               400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
               410: "Gone", 413: "Payload Too Large", 415: "Unsupported Media Type", 416: "Range Not Satisfiable",
               429: "Too Many Requests", 500: "Internal Server Error", 503: "Service Unavailable"}


class HttpError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message or STATUS_TEXT.get(status, str(status)))
        self.status = status
        self.message = message or STATUS_TEXT.get(status, str(status))


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    peer: str

    @property
    def cookies(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in self.headers.get("cookie", "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def json(self) -> Any:
        ctype = self.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HttpError(415, "se esperaba application/json")
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError) as exc:
            raise HttpError(400, f"JSON inválido: {exc}") from exc


@dataclass
class Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    stream: AsyncIterator[bytes] | None = None  # when set, ``body`` is ignored and chunks are written as they come

    @classmethod
    def json(cls, data: Any, status: int = 200, **headers: str) -> Response:
        return cls(status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", **headers},
                   json.dumps(data, ensure_ascii=False).encode("utf-8"))

    @classmethod
    def text(cls, text: str, status: int = 200, **headers: str) -> Response:
        return cls(status, {"Content-Type": "text/plain; charset=utf-8", **headers}, text.encode("utf-8"))

    @classmethod
    def redirect(cls, location: str, status: int = 302) -> Response:
        return cls(status, {"Location": location, "Cache-Control": "no-store"}, b"")

    @classmethod
    def file(cls, path: Path, cache: str = "no-cache") -> Response:
        if not path.is_file():
            raise HttpError(404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        elif path.suffix == ".js":
            ctype = "text/javascript"
        if ctype.startswith("text/") or ctype in ("application/manifest+json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        return cls(200, {"Content-Type": ctype, "Cache-Control": cache}, path.read_bytes())

    @classmethod
    def sse(cls, events: AsyncIterator[bytes]) -> Response:
        return cls(200, {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store",
                         "X-Accel-Buffering": "no"}, b"", stream=events)


def sse_event(event: str, data: Any, event_id: str | None = None) -> bytes:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


Handler = Callable[[Request], Awaitable[Response]]


class HttpServer:
    def __init__(self, handler: Handler, name: str = "mpvd"):
        self.handler = handler
        self.name = name
        self._server: asyncio.AbstractServer | None = None
        self._conns: set[asyncio.Task[None]] = set()
        self.host = ""
        self.port = 0

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self, host: str = "0.0.0.0", port: int = 0) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._client, host=host, port=port, reuse_address=True, limit=MAX_HEAD)
        sock = self._server.sockets[0]
        self.host, self.port = host, int(sock.getsockname()[1])
        log.info("http listening on %s:%d", self.host, self.port)
        return self.host, self.port

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        for t in list(self._conns):
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), 2)
        self._server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError, asyncio.LimitOverrunError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("http connection failed")
        finally:
            if task is not None:
                self._conns.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_s = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)
        try:
            request = await asyncio.wait_for(self._read_request(reader, peer_s), 30)
        except HttpError as exc:
            await self._write(writer, Response.text(exc.message, exc.status))
            await self._linger(reader, writer)
            return
        except (asyncio.TimeoutError, ValueError):
            await self._write(writer, Response.text("Bad Request", 400))
            await self._linger(reader, writer)
            return
        if request is None:
            return
        started = time.monotonic()
        try:
            response = await self.handler(request)
        except HttpError as exc:
            response = Response.json({"error": exc.message}, exc.status)
        except Exception:  # noqa: BLE001
            # never hand the phone (or whoever is on the LAN) the exception text: it leaked absolute paths of the
            # project's cache and www folders. The detail stays in the log.
            log.exception("handler failed for %s %s", request.method, request.path)
            response = Response.json({"error": "error interno"}, 500)
        log.debug("%s %s -> %d (%.0f ms)", request.method, request.path, response.status,
                  (time.monotonic() - started) * 1000)
        await self._write(writer, response, head_only=request.method == "HEAD")

    async def _read_request(self, reader: asyncio.StreamReader, peer: str) -> Request | None:
        line = await reader.readline()
        if not line:
            return None
        if len(line) > MAX_HEAD:
            raise HttpError(413)
        parts = line.decode("latin-1").strip().split()
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise HttpError(400, "request line")
        method, target, _ = parts
        headers: dict[str, str] = {}
        total = len(line)
        while True:
            h = await reader.readline()
            total += len(h)
            if total > MAX_HEAD:
                raise HttpError(413)
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0") or 0)
        if length > MAX_BODY:
            raise HttpError(413)
        body = await reader.readexactly(length) if length else b""
        url = urlsplit(target)
        query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        return Request(method.upper(), unquote(url.path) or "/", query, headers, body, peer)

    @staticmethod
    async def _linger(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limit: int = 4 * MAX_BODY,
                      timeout: float = 1.0) -> None:
        """After rejecting a request that was not fully read (e.g. 413 on a big body), half-close and discard what the
        client is still sending: closing with unread input makes the kernel send a RST, and the client then sees
        "connection reset" instead of our error response."""
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()
            total = 0
            while total < limit:
                chunk = await asyncio.wait_for(reader.read(65536), timeout)
                if not chunk:
                    break
                total += len(chunk)

    async def _write(self, writer: asyncio.StreamWriter, response: Response, head_only: bool = False) -> None:
        status = response.status
        headers = {"Server": self.name, "Connection": "close", **response.headers}
        if response.stream is None:
            headers.setdefault("Content-Length", str(len(response.body)))
        head = f"HTTP/1.1 {status} {STATUS_TEXT.get(status, '')}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        writer.write(head.encode("latin-1") + b"\r\n")
        if head_only:
            await writer.drain()
            return
        if response.stream is None:
            writer.write(response.body)
            await writer.drain()
            return
        await writer.drain()
        async for chunk in response.stream:
            if writer.is_closing():
                break
            writer.write(chunk)
            await writer.drain()
