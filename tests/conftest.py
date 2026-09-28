"""Shared fixtures: project paths, generated media, and a headless mpv launched through bin/mpv-uos."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from mpvd.mpvipc import MpvIpcClient

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "tests" / "fixtures" / "media"
TMP = ROOT / "tmp"


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

    def __init__(self, proc: subprocess.Popen[bytes], socket: Path, log: Path):
        self.proc = proc
        self.socket = socket
        self.log = log

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


@pytest.fixture
def mpv_headless(media_dir: Path) -> Any:
    """mpv launched via bin/mpv-uos with --vo=null --ao=null --idle=yes (socket and log under tmp/)."""
    run_dir = TMP / "test-mpv"
    run_dir.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    socket = run_dir / f"{tag}.sock"
    log = run_dir / f"{tag}.log"
    proc = subprocess.Popen(
        [
            str(ROOT / "bin" / "mpv-uos"),
            "--vo=null", "--ao=null", "--hwdec=no", "--idle=yes", "--no-terminal",
            f"--input-ipc-server={socket}", f"--log-file={log}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "MPV_UOS_RUNTIME_DIR": str(run_dir)},
    )
    h = MpvHeadless(proc, socket, log)
    try:
        h.run(lambda c: c.get_property("mpv-version"))  # waits for the socket
        yield h
    finally:
        h.stop()
        if not os.environ.get("MU_KEEP_LOGS"):
            for p in (socket, log):
                p.unlink(missing_ok=True)
