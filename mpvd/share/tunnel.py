"""Cloudflare quick tunnel for the share rooms (H25, ADR-068): entering the room from outside the house.

``cloudflared tunnel --url http://127.0.0.1:<port>`` needs no account and no configuration: it prints an address like
``https://<random-words>.trycloudflare.com`` that lives exactly as long as the process. So the tunnel is opened when the
room opens and the process is killed when the room closes, and nothing of the player is reachable from the internet one
second longer than the room is. It is off unless the viewer asks for it in the «Compartir» menu.

Where the binary comes from (F1, ADR-139): the copy bundled in the package, ``$MPV_UOS_CLOUDFLARED``, the writable
``vendor/bin`` (``$MPV_UOS_VENDOR_BIN``), ``<root>/vendor/bin`` and the PATH; last, a download pinned by platform in
``vendor.lock``. The download goes to a ``.part`` file (resumed with a Range request if it was cut), is checked against
its SHA-256 and only then renamed into place, so a half-downloaded or tampered file is never executed.

A PID file is written next to the daemon's socket and the next daemon start sweeps it: if mpvd died with SIGKILL,
the orphaned cloudflared does not survive the next start (and the room close already stops it when the player closes
normally). No ``preexec_fn`` is used to get this: forking with Python code in a threaded daemon is not safe.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mpvd.config import project_root
from mpvd.proc import spawn_kwargs

log = logging.getLogger("mpvd.share.tunnel")

URL_RE = re.compile(rb"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
START_TIMEOUT = 40.0      # cloudflared needs a few seconds to register the quick tunnel
STOP_TIMEOUT = 5.0
TAIL_LINES = 8
PID_NAME = "cloudflared.pid"
CHUNK = 1 << 20
DOWNLOAD_TIMEOUT = 60.0


def _binary_name() -> str:
    return "cloudflared.exe" if sys.platform == "win32" else "cloudflared"


def _disabled() -> bool:
    """``MPV_UOS_CLOUDFLARED=0`` says «do not use one here», downloads included (tests rely on it)."""
    return (os.environ.get("MPV_UOS_CLOUDFLARED") or "").strip().lower() in ("0", "none", "off")


def _packaged() -> bool:
    """True inside a .deb/AppImage/zip: the copy shipped in the package is the first candidate (ADR-139)."""
    return bool(os.environ.get("MPV_UOS_PACKAGED") or os.environ.get("MPV_UOS_APPIMAGE"))


def _vendor_bin(root: Path | None) -> Path | None:
    """Writable vendor/bin: ``$MPV_UOS_VENDOR_BIN`` in packages, ``<root>/vendor/bin`` in a checkout."""
    env = os.environ.get("MPV_UOS_VENDOR_BIN")
    if env:
        return Path(env)
    return root / "vendor" / "bin" if root is not None else None


def find_cloudflared(root: Path | None) -> Path | None:
    """The first usable cloudflared: package copy, ``$MPV_UOS_CLOUDFLARED``, writable vendor/bin,
    ``<root>/vendor/bin``, PATH. ``MPV_UOS_CLOUDFLARED=0`` says there is none.

    The download (the last step of the order) is ``ensure_cloudflared`` below, called only when a room actually asks
    for the internet: ``status()`` must never start a download just because a menu was opened."""
    name = _binary_name()
    if _disabled():
        return None
    candidates: list[Path] = []
    if root is not None and _packaged():
        candidates.append(root / "vendor" / "bin" / name)
    env = os.environ.get("MPV_UOS_CLOUDFLARED")
    if env:
        p = Path(env)
        candidates.append(p if p.is_file() else p / name)
    vb = _vendor_bin(root)
    if vb is not None:
        candidates.append(vb / name)
    if root is not None:
        candidates.append(root / "vendor" / "bin" / name)
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which(name)
    return Path(found) if found else None


@dataclass(frozen=True)
class CloudflaredAsset:
    name: str
    url: str
    sha256: str
    version: str


def _read_lock(root: Path | None) -> dict[str, str]:
    """``vendor.lock`` is KEY=VALUE with comments; the same file tools/vendor.sh sources."""
    base = root or project_root()
    if base is None:
        return {}
    try:
        text = (base / "vendor.lock").read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def platform_asset(root: Path | None) -> CloudflaredAsset | None:
    """The pinned asset for this machine, or None when vendor.lock has no hash for it (macOS today)."""
    lock = _read_lock(root)
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        return None
    if sys.platform.startswith("linux"):
        os_name = "linux"
        asset = "cloudflared-linux-amd64" if arch == "x86_64" else "cloudflared-linux-arm64"
    elif sys.platform == "win32":
        if arch != "x86_64":
            return None
        os_name = "windows"
        asset = "cloudflared-windows-amd64.exe"
    else:
        return None
    sha = lock.get(f"CLOUDFLARED_SHA256_{arch}_{os_name}", "")
    base_url = lock.get("CLOUDFLARED_BASE_URL", "").rstrip("/")
    if not sha or not base_url:
        return None
    return CloudflaredAsset(asset, f"{base_url}/{asset}", sha, lock.get("CLOUDFLARED_VERSION", ""))


def download_path(root: Path | None, data_dir: Path) -> Path:
    """Where a downloaded cloudflared lives: the writable vendor/bin, else ``<data>/bin``."""
    name = _binary_name()
    vb = _vendor_bin(root)
    if vb is not None:
        try:
            vb.mkdir(parents=True, exist_ok=True)
            if os.access(vb, os.W_OK):
                return vb / name
        except OSError:
            pass
    target = data_dir / "bin"
    target.mkdir(parents=True, exist_ok=True)
    return target / name


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _number(n: int) -> str:
    value = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return (value or "0").replace(".", ",")


def progress_text(done: int, total: int) -> str:
    """«12,4 de 40 MB», the one line the menu shows while downloading."""
    if total > 0:
        return f"{_number(done)} de {_number(total)} MB"
    return f"{_number(done)} MB"


def _fetch(asset: CloudflaredAsset, part: Path, progress: Callable[[int, int], None] | None,
           cancelled: Callable[[], bool] | None, retry: bool = True) -> None:
    offset = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": "mpv-uos"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    req = urllib.request.Request(asset.url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310 - pinned https URL
            status = getattr(resp, "status", 200)
            if offset and status != 206:
                offset = 0                      # the server ignored/rejected the Range: start over
            mode = "ab" if offset else "wb"
            length = int(resp.headers.get("Content-Length") or 0)
            total = offset + length if length else 0
            done = offset
            if progress is not None:
                progress(done, total)
            with part.open(mode) as out:
                while True:
                    if cancelled is not None and cancelled():
                        raise CloudflaredDownloadError("descarga cancelada")
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and retry:
            part.unlink(missing_ok=True)
            _fetch(asset, part, progress, cancelled, retry=False)
            return
        raise CloudflaredDownloadError(f"no se pudo descargar cloudflared: {exc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudflaredDownloadError(f"no se pudo descargar cloudflared: {exc}") from exc


def _ensure_sync(asset: CloudflaredAsset, dest: Path,
                 progress: Callable[[int, int], None] | None,
                 cancelled: Callable[[], bool] | None) -> Path:
    if dest.is_file():
        if sha256_of(dest) == asset.sha256:
            return dest
        dest.unlink(missing_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    if part.is_file() and sha256_of(part) == asset.sha256:
        os.replace(part, dest)
    else:
        _fetch(asset, part, progress, cancelled)
        if cancelled is not None and cancelled():
            raise CloudflaredDownloadError("descarga cancelada")
        got = sha256_of(part)
        if got != asset.sha256:
            part.unlink(missing_ok=True)
            raise CloudflaredDownloadError(
                f"el SHA-256 de {asset.name} no coincide: se esperaba {asset.sha256} y llegó {got}; "
                "se ha borrado lo descargado")
        os.replace(part, dest)
    try:
        os.chmod(dest, 0o755)
    except OSError:
        pass
    if progress is not None:
        size = dest.stat().st_size
        progress(size, size)
    return dest


async def download_cloudflared(root: Path | None, data_dir: Path,
                               progress: Callable[[int, int], None] | None = None,
                               cancelled: Callable[[], bool] | None = None) -> Path:
    """Download and verify the pinned cloudflared for this machine, atomically. Reused on retry."""
    if _disabled():
        raise CloudflaredDownloadError("cloudflared está desactivado en este equipo")
    asset = platform_asset(root)
    if asset is None:
        raise CloudflaredDownloadError(
            "no hay un cloudflared fijado para este sistema; instálalo en el PATH o define MPV_UOS_CLOUDFLARED")
    dest = download_path(root, data_dir)
    return await asyncio.to_thread(_ensure_sync, asset, dest, progress, cancelled)


async def ensure_cloudflared(root: Path | None, data_dir: Path,
                             progress: Callable[[int, int], None] | None = None,
                             cancelled: Callable[[], bool] | None = None) -> Path:
    """The whole F1 search order: what is already there, else the pinned download."""
    found = find_cloudflared(root)
    if found is not None:
        return found
    return await download_cloudflared(root, data_dir, progress, cancelled)


def _pid_is_cloudflared(pid: int) -> bool:
    """Do not kill an unrelated process that reused the PID after a crash."""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, check=False, **spawn_kwargs())
        return "cloudflared" in out.stdout.lower()
    if sys.platform.startswith("linux"):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return False
        return "cloudflared" in cmd.lower()
    with contextlib.suppress(OSError):
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                             check=False, **spawn_kwargs())
        return "cloudflared" in out.stdout.lower()
    return False


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _pid_is_cloudflared(pid)
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            state = stat.rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            return False
        return state != "Z"          # a zombie is already dead; its parent may take a while to reap it
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_stale(pid_file: Path) -> int | None:
    """Kill the cloudflared a previous mpvd left behind (SIGKILL, crash), if the PID is still ours."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        with contextlib.suppress(OSError):
            pid_file.unlink()
        return None
    if not _pid_is_cloudflared(pid):
        with contextlib.suppress(OSError):
            pid_file.unlink()
        return None
    log.warning("killing stale cloudflared %d left by a previous run", pid)
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False,
                           **spawn_kwargs())
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.1)
        if _pid_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        pid_file.unlink()
    return pid


class TunnelError(RuntimeError):
    pass


class CloudflaredDownloadError(RuntimeError):
    pass


class CloudflaredTunnel:
    """A quick tunnel in front of the local share server. One at a time: ``start`` twice reuses the running one."""

    name = "cloudflared"

    def __init__(self, binary: Path, start_timeout: float = START_TIMEOUT, pid_file: Path | None = None):
        self.binary = binary
        self.start_timeout = start_timeout
        self.pid_file = pid_file
        self.url: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._drain: asyncio.Task[None] | None = None
        self._pid: int | None = None

    def args(self, local_port: int) -> list[str]:
        return [str(self.binary), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{int(local_port)}"]

    def _write_pid(self, pid: int) -> None:
        self._pid = pid
        if self.pid_file is None:
            return
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(str(pid), encoding="utf-8")
        except OSError:
            log.warning("could not write the cloudflared pid file %s", self.pid_file)

    def _clear_pid(self) -> None:
        pid, self._pid = self._pid, None
        if self.pid_file is None:
            return
        try:
            if pid is None or self.pid_file.read_text(encoding="utf-8").strip() == str(pid):
                self.pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    async def start(self, local_port: int) -> str:
        if self._proc is not None and self._proc.returncode is None and self.url:
            return self.url
        await self.stop()
        proc = await asyncio.create_subprocess_exec(
            *self.args(local_port), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, **spawn_kwargs())
        self._proc = proc
        self._write_pid(proc.pid)
        assert proc.stderr is not None
        tail: list[bytes] = []
        try:
            url = await asyncio.wait_for(self._read_url(proc.stderr, tail), self.start_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            await self.stop()
            raise TunnelError("cloudflared no dio una dirección a tiempo: " + _tail(tail)) from None
        except asyncio.CancelledError:
            await self.stop()
            raise
        if url is None:
            code = proc.returncode
            await self.stop()
            raise TunnelError(f"cloudflared terminó ({code}) sin dar una dirección: " + _tail(tail))
        self.url = url
        # keep reading, or cloudflared blocks on a full stderr pipe once it has been chatting for a while
        self._drain = asyncio.create_task(self._drain_stderr(proc), name="share-tunnel-drain")
        log.info("tunnel %s open: %s -> 127.0.0.1:%d", self.name, url, local_port)
        return url

    async def _read_url(self, stderr: asyncio.StreamReader, tail: list[bytes]) -> str | None:
        while True:
            line = await stderr.readline()
            if not line:
                return None            # cloudflared closed its output: it died
            tail.append(line.strip())
            del tail[:-TAIL_LINES]
            m = URL_RE.search(line)
            if m:
                return m.group(0).decode("ascii")

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        with contextlib.suppress(Exception):
            while await proc.stderr.readline():
                pass

    async def stop(self) -> None:
        proc, self._proc, self.url = self._proc, None, None
        if self._drain is not None:
            self._drain.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._drain
            self._drain = None
        if proc is None or proc.returncode is not None:
            self._clear_pid()
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
        self._clear_pid()
        log.info("tunnel %s closed", self.name)


def _tail(lines: list[bytes]) -> str:
    text = " · ".join(ln.decode("utf-8", "replace") for ln in lines if ln)
    return text[-400:] if text else "sin salida"


__all__ = ["CloudflaredAsset", "CloudflaredDownloadError", "CloudflaredTunnel", "PID_NAME", "TunnelError",
           "download_cloudflared", "ensure_cloudflared", "find_cloudflared", "kill_stale", "platform_asset",
           "progress_text"]
