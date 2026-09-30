"""Shared fixtures: project paths, generated media, headless mpv via bin/mpv-uos, and mpvd daemons."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from mpvd.client import rpc_call
from mpvd.mpvipc import MpvIpcClient

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "tests" / "fixtures" / "media"
# MU_TEST_TMP: shorter base for sockets when the checkout path is long (git worktrees): AF_UNIX paths max ~107 bytes
TMP = Path(os.environ["MU_TEST_TMP"]) if os.environ.get("MU_TEST_TMP") else ROOT / "tmp"


@pytest.fixture(autouse=True, scope="session")
def _isolated_user_data(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Tests never read or write the user's data (favourites, notes, prefs, watch_later): every mpvd or
    bin/mpv-uos started without an explicit data dir lands in a throwaway directory."""
    data = tmp_path_factory.mktemp("user-data")
    old = os.environ.get("MPV_UOS_DATA_DIR")
    os.environ["MPV_UOS_DATA_DIR"] = str(data)
    # and never pop desktop notifications at the user while the suite runs (e.g. the "moved checkout" launcher test)
    os.environ["MPV_UOS_NO_NOTIFY"] = "1"
    # nor show test players in the desktop's media controls (MPRIS on the user's session bus); test_mpris uses its own bus
    os.environ["MPV_UOS_MPRIS"] = "0"
    yield data
    if old is None:
        os.environ.pop("MPV_UOS_DATA_DIR", None)
    else:
        os.environ["MPV_UOS_DATA_DIR"] = old
PYTHON = ROOT / ".venv" / "bin" / "python"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def media_dir() -> Path:
    """Generated test media (tools/make_test_media.sh runs on demand)."""
    if not (MEDIA / "manifest.json").exists():
        subprocess.run([str(ROOT / "tools" / "make_test_media.sh")], check=True, capture_output=True)
    return MEDIA


@pytest.fixture(scope="session")
def media_manifest(media_dir: Path) -> dict[str, dict[str, Any]]:
    return json.loads((media_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def mpv_option_names() -> set[str]:
    """All option names known to the installed mpv (from --list-options)."""
    out = subprocess.run(["mpv", "--no-config", "--list-options"], capture_output=True, text=True, check=True).stdout
    names: set[str] = set()
    for line in out.splitlines():
        m = re.match(r"^\s*--([A-Za-z0-9_-]+)", line)
        if m:
            names.add(m.group(1))
    return names


SCRIPT_ERROR_RE = re.compile(r"\]\[(e|f)\]\[(uosc|thumbfast|mu_[a-z0-9_]+)\]|Lua error|stack traceback")


class MpvHeadless:
    """A running mpv (no video/audio output, idle) with an IPC socket and a verbose log file."""

    def __init__(self, proc: subprocess.Popen[bytes], socket: Path, log: Path, run_dir: Path):
        self.proc = proc
        self.socket = socket
        self.log = log
        self.run_dir = run_dir

    def run(self, fn: Callable[[MpvIpcClient], Awaitable[Any]], timeout: float = 60.0) -> Any:
        """Run an async function with a fresh connected client (tests stay synchronous)."""

        async def _go() -> Any:
            async with MpvIpcClient(str(self.socket)) as client:
                return await asyncio.wait_for(fn(client), timeout)

        return asyncio.run(_go())

    def get(self, prop: str) -> Any:
        return self.run(lambda c: c.get_property(prop))

    def command(self, *args: Any) -> Any:
        return self.run(lambda c: c.command(*args))

    def wait_property(self, prop: str, predicate: Callable[[Any], bool], timeout: float = 10.0) -> Any:
        return self.run(lambda c: c.wait_property(prop, predicate, timeout=timeout), timeout=timeout + 5)

    def log_text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""

    def script_errors(self) -> list[str]:
        return [line for line in self.log_text().splitlines() if SCRIPT_ERROR_RE.search(line)]

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                self.run(lambda c: c.command("quit"), timeout=5)
            except Exception:  # noqa: BLE001 - mpv may close the socket before answering
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def start_mpv(run_dir: Path, extra_args: list[str] | None = None, env: dict[str, str] | None = None,
              start_screen: bool = False, wait_socket: Path | None = None,
              launcher: Path | None = None) -> MpvHeadless:
    """Launch bin/mpv-uos headless: --vo=null --ao=null --idle=yes, socket and log under run_dir.

    The mu-menu start screen is off unless ``start_screen`` (it would open a menu in every test). User data (prefs.json
    of mu-prefs, favourites...) goes to a fresh ``run_dir/data-<tag>`` unless ``env`` sets MPV_UOS_DATA_DIR: tests never
    read or write the user's real data, and one test's remembered volume/filters never leak into the next one.
    ``launcher``: another front end with bin/mpv-uos's arguments (the AppImage)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    socket = run_dir / f"mpv-{tag}.sock"
    log = run_dir / f"mpv-{tag}.log"
    args = [
        str(launcher or ROOT / "bin" / "mpv-uos"),
        "--vo=null", "--ao=null", "--hwdec=no", "--idle=yes", "--no-terminal",
        # never resume/persist positions or track choices between test runs (mpv.conf enables them for users)
        "--save-position-on-quit=no", "--resume-playback=no", f"--watch-later-dir={run_dir / 'watch_later'}",
        f"--input-ipc-server={socket}", f"--log-file={log}",
        *(extra_args or []),
        # appended last: a later --script-opts=... would replace the whole list otherwise
        f"--script-opts-append=mu-menu-start_screen={'yes' if start_screen else 'no'}",
    ]
    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "MPV_UOS_RUNTIME_DIR": str(run_dir), "MPV_UOS_DATA_DIR": str(run_dir / f"data-{tag}"),
             **(env or {})},
    )
    h = MpvHeadless(proc, wait_socket or socket, log, run_dir)
    h.run(lambda c: c.get_property("mpv-version"))  # waits for the socket
    return h


@pytest.fixture
def mpv_headless(media_dir: Path) -> Any:
    """Headless mpv WITHOUT the daemon (mu-core autostart off) for config/UI smoke tests."""
    run_dir = TMP / "test-mpv"
    h = start_mpv(run_dir, ["--script-opts=mu-core-autostart=no"])
    try:
        yield h
    finally:
        h.stop()
        if not os.environ.get("MU_KEEP_LOGS"):
            for p in (h.socket, h.log):
                p.unlink(missing_ok=True)
            shutil.rmtree(run_dir / f"data-{h.socket.stem.removeprefix('mpv-')}", ignore_errors=True)


# -- mpvd -------------------------------------------------------------------------------------


class DaemonEnv:
    """Isolated runtime + cache directories for one mpvd daemon."""

    def __init__(self, base: Path):
        self.base = base
        self.runtime_dir = base / "rt"
        if len(str(self.runtime_dir / "mpv-00000000.sock")) > 100:
            # AF_UNIX socket paths are limited to 107 bytes: long checkouts (git worktrees) use a short private dir
            self.runtime_dir = Path(tempfile.mkdtemp(prefix="mu-rt-"))
        self.cache_dir = base / "cache"
        self.data_dir = base / "data"  # favourites, recents, user lists: never the developer's .cache/data
        self.extra_env: dict[str, str] = {}  # per-test daemon switches (e.g. MPVD_SEMANTIC_FAKE=1)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def socket(self) -> Path:
        return self.runtime_dir / "mpvd.sock"

    @property
    def env(self) -> dict[str, str]:
        return {"MPV_UOS_RUNTIME_DIR": str(self.runtime_dir), "MPV_UOS_CACHE_DIR": str(self.cache_dir),
                "MPV_UOS_DATA_DIR": str(self.data_dir), "MPVD_IDLE_TIMEOUT": "120", **self.extra_env}

    def cli(self, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PYTHON), "-m", "mpvd", *args], capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **self.env},
        )

    def call(self, method: str, params: Any = None, timeout: float = 30.0) -> Any:
        return rpc_call(str(self.socket), method, params, timeout=timeout)

    def alive(self) -> bool:
        try:
            return bool(self.call("ping", timeout=2).get("pong"))
        except Exception:  # noqa: BLE001
            return False

    def wait(self, predicate: Callable[[], bool], timeout: float = 30.0, interval: float = 0.1) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(interval)
        raise TimeoutError("condition not met")

    def stop(self) -> None:
        if self.alive():
            try:
                self.call("shutdown", timeout=5)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.wait(lambda: not self.alive(), timeout=10)
            except TimeoutError:
                pass
        pid_file = self.runtime_dir / "mpvd.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        log = self.cache_dir / "mpvd.log"
        if log.exists() and os.environ.get("MU_KEEP_LOGS"):
            print(log.read_text(encoding="utf-8", errors="replace"))


@pytest.fixture
def daemon_env() -> Any:
    base = TMP / "test-mpvd" / uuid.uuid4().hex[:8]
    d = DaemonEnv(base)
    try:
        yield d
    finally:
        d.stop()
        if not os.environ.get("MU_KEEP_LOGS"):
            shutil.rmtree(base, ignore_errors=True)
        if base not in d.runtime_dir.parents:
            shutil.rmtree(d.runtime_dir, ignore_errors=True)
