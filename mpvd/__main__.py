"""mpvd command line.

  mpvd serve                      run the daemon in the foreground (mu-core spawns it detached)
  mpvd ensure --attach <ipc>      start the daemon if needed and attach an mpv instance (used by mu-core)
  mpvd call <method> [json]       call a method on the running daemon
  mpvd link <mpv-uos://download?url=…>   queue a download sent from the browser (bin/mpv-uos, H23)
  mpvd status | stop | --version
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mpvd import __version__
from mpvd.client import rpc_call
from mpvd.config import Settings
from mpvd.rpc import RpcError


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env(
        runtime_dir=Path(args.runtime_dir) if getattr(args, "runtime_dir", None) else None,
        cache_dir=Path(args.cache_dir) if getattr(args, "cache_dir", None) else None,
        idle_timeout=getattr(args, "idle_timeout", None),
        workers=getattr(args, "workers", None),
        log_level=getattr(args, "log_level", None),
    )


def _setup_logging(settings: Settings, foreground: bool) -> None:
    settings.ensure_dirs()
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(settings.log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if foreground:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_serve(args: argparse.Namespace) -> int:
    from mpvd.server import MpvdServer  # noqa: PLC0415

    settings = _settings(args)
    _setup_logging(settings, foreground=sys.stderr.isatty() or args.foreground)
    server = MpvdServer(settings)
    try:
        asyncio.run(server.run())
    except RuntimeError as exc:
        logging.getLogger("mpvd").error("%s", exc)
        return 3
    except KeyboardInterrupt:
        pass
    return 0


def _ping(settings: Settings, timeout: float = 2.0) -> bool:
    try:
        return bool(rpc_call(str(settings.socket_path), "ping", timeout=timeout).get("pong"))
    except (OSError, RpcError, asyncio.TimeoutError, ValueError):
        return False


def spawn_daemon(settings: Settings, root: str | None = None) -> subprocess.Popen[bytes]:
    """Start `mpvd serve` detached from the caller (own session, stdio to the log file)."""
    settings.ensure_dirs()
    env = dict(os.environ)
    if root:
        env["MPV_UOS_ROOT"] = root
    env.setdefault("PYTHONUNBUFFERED", "1")
    cmd = [
        sys.executable, "-m", "mpvd", "serve",
        "--runtime-dir", str(settings.runtime_dir), "--cache-dir", str(settings.cache_dir),
        "--idle-timeout", str(settings.idle_timeout), "--workers", str(settings.workers),
        "--log-level", settings.log_level,
    ]
    log_fh = open(settings.cache_dir / "mpvd.stdio.log", "ab")  # noqa: SIM115 - handed to the child
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": log_fh, "stderr": log_fh, "close_fds": True, "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    log_fh.close()
    return proc


@contextlib.contextmanager
def _spawn_lock(settings: Settings):  # type: ignore[no-untyped-def]
    """Serialise concurrent `ensure` calls so only one of them spawns the daemon."""
    settings.ensure_dirs()
    path = settings.runtime_dir / "mpvd.lock"
    fh = open(path, "a+")  # noqa: SIM115
    try:
        if sys.platform != "win32":
            import fcntl  # noqa: PLC0415

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform != "win32":
            import fcntl  # noqa: PLC0415

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def ensure_daemon(settings: Settings, root: str | None = None, timeout: float = 15.0) -> bool:
    """Make sure a daemon answers on settings.socket_path. Returns True if it was started now."""
    if _ping(settings):
        return False
    with _spawn_lock(settings):
        if _ping(settings):
            return False
        spawn_daemon(settings, root=root)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _ping(settings, timeout=1.0):
                return True
            time.sleep(0.1)
    raise TimeoutError(f"mpvd did not answer on {settings.socket_path} within {timeout}s (see {settings.log_path})")


def cmd_ensure(args: argparse.Namespace) -> int:
    settings = _settings(args)
    out: dict[str, Any] = {"ok": False, "socket": str(settings.socket_path)}
    try:
        out["started"] = ensure_daemon(settings, root=args.root, timeout=args.timeout)
        if args.attach:
            out["session"] = rpc_call(str(settings.socket_path), "sessions.register",
                                      {"ipc": args.attach, "pid": args.pid}, timeout=args.timeout)
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 - report everything to mu-core as JSON
        out["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


def cmd_call(args: argparse.Namespace) -> int:
    settings = _settings(args)
    params = json.loads(args.params) if args.params else None
    try:
        result = rpc_call(str(settings.socket_path), args.method, params, timeout=args.timeout)
    except RpcError as exc:
        print(json.dumps({"error": exc.to_obj()}, ensure_ascii=False))
        return 2
    except OSError as exc:
        print(json.dumps({"error": {"code": -32002, "message": f"mpvd not reachable: {exc}"}}), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 1))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the MCP stdio server; starts the daemon first if needed (logs go to stderr, never stdout)."""
    import asyncio  # noqa: PLC0415

    from mpvd.mcp import serve  # noqa: PLC0415

    settings = _settings(args)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    ensure_daemon(settings, root=getattr(args, "root", None))
    autoconfirm = bool(args.yes) or os.environ.get("MPVD_MCP_AUTOCONFIRM", "0") == "1"
    return asyncio.run(serve(str(settings.socket_path), args.session, autoconfirm))


def cmd_link(args: argparse.Namespace) -> int:
    """``mpv-uos://download?url=…`` links from the browser: start the daemon if needed, queue, notify, print JSON."""
    from mpvd import handoff  # noqa: PLC0415

    settings = _settings(args)
    out: dict[str, Any]
    if not any(handoff.parse(link) for link in args.links):
        out = {"ok": False, "count": 0, "rejected": len(args.links),
               "error": "enlace no válido (se esperaba mpv-uos://download?url=https://…)"}
    else:
        try:
            ensure_daemon(settings, root=args.root or os.environ.get("MPV_UOS_ROOT"), timeout=args.timeout)
            out = handoff.handle(str(settings.socket_path), args.links, timeout=args.timeout)
        except RpcError as exc:
            out = {"ok": False, "count": 0, "error": exc.message}
        except Exception as exc:  # noqa: BLE001 - reported to the user as a notification
            out = {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"}
    if not args.quiet:
        handoff.notify(out)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if not _ping(settings):
        print(json.dumps({"running": False, "socket": str(settings.socket_path)}))
        return 3
    sock = str(settings.socket_path)
    print(json.dumps({
        "running": True, "socket": sock,
        "version": rpc_call(sock, "version"),
        "sessions": rpc_call(sock, "sessions.list"),
        "jobs": rpc_call(sock, "jobs.list", {"include_finished": False}),
        "guardian": rpc_call(sock, "guardian.status"),
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if not _ping(settings):
        print("mpvd no está en marcha")
        return 0
    rpc_call(str(settings.socket_path), "shutdown")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _ping(settings, timeout=0.5):
        time.sleep(0.1)
    print("mpvd detenido" if not _ping(settings, timeout=0.5) else "mpvd sigue respondiendo")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mpvd", description="MPV-UOS companion daemon")
    p.add_argument("--version", action="version", version=f"mpvd {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runtime-dir", help="directory for sockets/pid (default: $XDG_RUNTIME_DIR/mpv-uos)")
    common.add_argument("--cache-dir", help="cache directory (default: <project>/.cache or XDG cache)")
    common.add_argument("--timeout", type=float, default=15.0)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", parents=[common], help="run the daemon in the foreground")
    s.add_argument("--idle-timeout", type=float, default=None, help="exit after N s without sessions (0 = never)")
    s.add_argument("--workers", type=int, default=None)
    s.add_argument("--log-level", default=None)
    s.add_argument("--foreground", action="store_true", help="also log to stderr")
    s.set_defaults(fn=cmd_serve)

    e = sub.add_parser("ensure", parents=[common], help="start the daemon if needed and attach an mpv instance")
    e.add_argument("--attach", metavar="IPC", help="mpv --input-ipc-server path to attach")
    e.add_argument("--pid", type=int, default=None, help="pid of that mpv")
    e.add_argument("--root", default=None, help="project root to pass to the daemon (MPV_UOS_ROOT)")
    e.add_argument("--idle-timeout", type=float, default=None)
    e.add_argument("--workers", type=int, default=None)
    e.add_argument("--log-level", default=None)
    e.set_defaults(fn=cmd_ensure)

    c = sub.add_parser("call", parents=[common], help="call a JSON-RPC method")
    c.add_argument("method")
    c.add_argument("params", nargs="?", help="JSON object or array")
    c.add_argument("--compact", action="store_true")
    c.set_defaults(fn=cmd_call)

    lk = sub.add_parser("link", parents=[common], help="queue mpv-uos://download?url=… links (from the browser)")
    lk.add_argument("links", nargs="+")
    lk.add_argument("--root", default=None, help="project root to pass to the daemon (MPV_UOS_ROOT)")
    lk.add_argument("--quiet", action="store_true", help="no desktop notification")
    lk.set_defaults(fn=cmd_link)
    sub.add_parser("status", parents=[common], help="show sessions, jobs and guardian").set_defaults(fn=cmd_status)
    m = sub.add_parser("mcp", parents=[common], help="MCP server over stdio (for Claude Code, Claude Desktop, etc.)")
    m.add_argument("--session", default=None, help="mpv session id to drive (default: the most recent one)")
    m.add_argument("--yes", action="store_true", help="skip the on-screen confirmations")
    m.set_defaults(fn=cmd_mcp)
    sub.add_parser("stop", parents=[common], help="ask the daemon to exit").set_defaults(fn=cmd_stop)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
