"""Local stream transport of mpvd (H28): a Unix socket on Linux/macOS, a named pipe (``\\\\.\\pipe\\<name>``) on
Windows — for mpvd's own JSON-RPC endpoint and for mpv's ``--input-ipc-server`` (mpv uses a named pipe there too).

Windows uses the Proactor event loop's pipe support (``loop.start_serving_pipe`` / ``loop.create_pipe_connection``,
the same primitives asyncio's own subprocess code relies on); they only exist on Windows, so every entry point here
takes the loop's methods by name and the tests drive the pipe branch with a loop that provides them over Unix sockets.
Not tried on a real Windows machine yet (docs/PLATAFORMAS.md).
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from typing import Any, Awaitable, Callable

PIPE_PREFIX = "\\\\.\\pipe\\"

ClientCallback = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def is_pipe(path: os.PathLike[str] | str) -> bool:
    return str(path).startswith(PIPE_PREFIX)


def pipe_name(name: str) -> str:
    """``mpvd-<user>`` → ``\\\\.\\pipe\\mpv-uos-mpvd-<user>`` (pipes live in one global namespace: the user name keeps
    two accounts on the same Windows machine apart)."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"mpv-uos-{name}-{user}")
    return PIPE_PREFIX + safe


def default_endpoint(socket_path: os.PathLike[str] | str) -> str:
    """Where mpvd listens: the Unix socket path, or a named pipe on Windows."""
    if sys.platform == "win32":
        return pipe_name("mpvd")
    return str(socket_path)


class _PipeServer:
    """What ``start_server`` returns for a pipe: the ``close``/``wait_closed`` surface of ``asyncio.Server``."""

    def __init__(self, servers: list[Any]):
        self._servers = servers

    def close(self) -> None:
        for s in self._servers:
            s.close()

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


async def start_server(cb: ClientCallback, path: str, limit: int, api: Any = None) -> Any:
    """``api``: what provides the pipe methods (the running loop; tests pass a stand-in)."""
    loop = asyncio.get_running_loop()
    if not is_pipe(path):
        return await asyncio.start_unix_server(cb, path=path, limit=limit)
    serve = getattr(api or loop, "start_serving_pipe", None)
    if serve is None:
        raise OSError("named pipes need the Windows Proactor event loop")

    def factory() -> asyncio.StreamReaderProtocol:
        reader = asyncio.StreamReader(limit=limit, loop=loop)
        return asyncio.StreamReaderProtocol(reader, cb, loop=loop)

    servers = await serve(factory, path)
    return _PipeServer(servers if isinstance(servers, list) else [servers])


async def open_connection(path: str, limit: int, api: Any = None) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    if not is_pipe(path):
        return await asyncio.open_unix_connection(path, limit=limit)
    connect = getattr(api or loop, "create_pipe_connection", None)
    if connect is None:
        raise OSError("named pipes need the Windows Proactor event loop")
    reader = asyncio.StreamReader(limit=limit, loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    try:
        transport, _ = await connect(lambda: protocol, path)
    except FileNotFoundError:
        raise
    except OSError as exc:        # ERROR_PIPE_BUSY and friends: the caller retries like a refused socket
        raise ConnectionRefusedError(str(exc)) from exc
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer


def endpoint_alive(path: os.PathLike[str] | str, timeout: float = 1.0) -> bool:
    """True if something accepts connections at ``path`` (a Unix socket or a named pipe)."""
    if is_pipe(path):
        try:
            with open(str(path), "r+b", buffering=0):   # Windows: opening a pipe connects to it
                return True
        except OSError:
            return False
    if not os.path.exists(path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def remove_stale(path: os.PathLike[str] | str) -> None:
    """A dead Unix socket file blocks ``bind``; a pipe disappears with its last handle."""
    if not is_pipe(path) and os.path.exists(path):
        os.unlink(path)
