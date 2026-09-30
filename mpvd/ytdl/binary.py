"""Locate, run and update the yt-dlp binary used by mpvd and by mpv's ytdl_hook.

Resolution order: ``$MPV_UOS_YTDLP`` → ``<project>/vendor/bin/yt-dlp`` (installed by tools/vendor.sh from vendor.lock, or by
the updater below) → ``yt-dlp`` on PATH. The vendored asset is the official ``yt-dlp`` zipimport file (runs with any
Python ≥ 3.9); mpvd runs it with its own interpreter so the shebang never matters.

Updater: at most once a day mpvd asks the GitHub API for the latest release, downloads the ``yt-dlp`` asset and verifies it
against the official ``SHA2-256SUMS`` before atomically replacing ``vendor/bin/yt-dlp``. See docs/YTDLP.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.net import FetchError, HttpCache

log = logging.getLogger("mpvd.ytdl.binary")

RELEASES_LATEST_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
# nightly channel (H19): same asset names and SHA2-256SUMS (verified 2026-09-30, tag 2026.09.27.232945)
NIGHTLY_RELEASES_URL = "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest"


def platform_asset_name() -> str:
    """The release asset the updater installs: the zipimport build (runs with any python3), or on Windows the native
    ``yt-dlp.exe`` that ytdl_hook needs and vendor_path() points at (tools/install.ps1; docs/PLATAFORMAS.md)."""
    return "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"


ASSET_NAME = platform_asset_name()
SUMS_NAME = "SHA2-256SUMS"
UPDATE_CHECK_TTL = 24 * 3600.0
VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}(\.\d+)?$")
NODE_MIN_MAJOR = 22

# Always passed: no self-update, no remote component downloads, no interactive noise.
SAFE_ARGS = ["--no-update", "--no-remote-components", "--no-warnings", "--color", "never"]


def _is_python_zip(path: Path) -> bool:
    """The official ``yt-dlp`` asset is a ``#!`` shebang followed by a zip; native builds are ELF/PE/Mach-O."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return head.startswith(b"#!") or head.startswith(b"PK")


def _node_version(exe: str) -> int | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(r"v?(\d+)", out.strip())
    return int(m.group(1)) if m else None


@dataclass
class JsRuntime:
    name: str  # deno | node
    path: str
    version: str = ""

    def args(self) -> list[str]:
        # deno on PATH is enabled by default, but an explicit path is required for the vendored copy; node is never
        # enabled by default (docs/YTDLP.md §4).
        return ["--js-runtimes", f"{self.name}:{self.path}"]


def find_js_runtime(root: Path | None) -> JsRuntime | None:
    """Prefer a vendored deno, then deno on PATH, then node ≥ 22 on PATH."""
    candidates: list[Path] = []
    if root is not None:
        candidates += [root / "vendor" / "bin" / "deno", root / "vendor" / "bin" / "deno.exe"]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return JsRuntime("deno", str(c))
    deno = shutil.which("deno")
    if deno:
        return JsRuntime("deno", deno)
    node = shutil.which("node")
    if node:
        major = _node_version(node)
        if major is not None and major >= NODE_MIN_MAJOR:
            return JsRuntime("node", node, str(major))
    return None


@dataclass
class YtdlpBinary:
    path: Path
    source: str  # env | vendor | system
    js_runtime: JsRuntime | None = None
    ffmpeg: str | None = None
    _version: str | None = field(default=None, repr=False)

    @property
    def argv(self) -> list[str]:
        if _is_python_zip(self.path):
            return [sys.executable, str(self.path)]
        return [str(self.path)]

    def base_args(self) -> list[str]:
        args = list(SAFE_ARGS)
        if self.js_runtime is not None:
            args += self.js_runtime.args()
        if self.ffmpeg:
            args += ["--ffmpeg-location", self.ffmpeg]
        return args

    def command(self, *extra: str) -> list[str]:
        return [*self.argv, *self.base_args(), *extra]

    def version_sync(self, refresh: bool = False, timeout: float = 30.0) -> str:
        if self._version is None or refresh:
            out = subprocess.run([*self.argv, "--version"], capture_output=True, text=True, timeout=timeout, check=False)
            self._version = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        return self._version

    async def version(self, refresh: bool = False) -> str:
        return await asyncio.to_thread(self.version_sync, refresh)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path), "source": self.source, "argv": self.argv,
            "version": self._version, "js_runtime": self.js_runtime.__dict__ if self.js_runtime else None,
            "ffmpeg": self.ffmpeg,
        }


def vendor_path(root: Path | None) -> Path | None:
    """``<root>/vendor/bin/yt-dlp``, or ``$MPV_UOS_VENDOR_BIN/yt-dlp`` when the checkout is read-only (the AppImage
    points it at a folder of the user's data so the daily update has somewhere to write)."""
    name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    env = os.environ.get("MPV_UOS_VENDOR_BIN")
    if env:
        return Path(env) / name
    if root is None:
        return None
    return root / "vendor" / "bin" / name


def nightly_path(root: Path | None, data_dir: Path) -> Path:
    """Where the nightly build lives: next to the vendored stable one (``yt-dlp-nightly``), else in the data dir."""
    name = "yt-dlp-nightly.exe" if sys.platform == "win32" else "yt-dlp-nightly"
    vp = vendor_path(root)
    return (vp.parent / name) if vp is not None else (data_dir / "bin" / name)


def find_nightly(root: Path | None, data_dir: Path) -> YtdlpBinary | None:
    """The nightly yt-dlp if present (``$MPV_UOS_YTDLP_NIGHTLY`` wins: tests, custom builds)."""
    js = find_js_runtime(root)
    ffmpeg = shutil.which("ffmpeg")
    env = os.environ.get("MPV_UOS_YTDLP_NIGHTLY")
    if env and Path(env).is_file():
        return YtdlpBinary(Path(env), "env-nightly", js, ffmpeg)
    p = nightly_path(root, data_dir)
    return YtdlpBinary(p, "nightly", js, ffmpeg) if p.is_file() else None


def find_ytdlp(root: Path | None) -> YtdlpBinary | None:
    env = os.environ.get("MPV_UOS_YTDLP")
    js = find_js_runtime(root)
    ffmpeg = shutil.which("ffmpeg")
    if env:
        p = Path(env)
        if p.is_file():
            return YtdlpBinary(p, "env", js, ffmpeg)
        log.warning("MPV_UOS_YTDLP=%s does not exist; falling back", env)
    vp = vendor_path(root)
    if vp is not None and vp.is_file():
        return YtdlpBinary(vp, "vendor", js, ffmpeg)
    system = shutil.which("yt-dlp")
    if system:
        return YtdlpBinary(Path(system), "system", js, ffmpeg)
    return None


# -- updater ----------------------------------------------------------------------------------------


def parse_sums(text: str) -> dict[str, str]:
    """``SHA2-256SUMS`` lines are ``<hex>  <name>``."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[-1].lstrip("*")] = parts[0].lower()
    return sums


def version_newer(latest: str, installed: str) -> bool:
    """Release tags are dates (2026.08.19[.N]); compare numerically."""

    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)

    if not VERSION_RE.match(latest or ""):
        return False
    return key(latest) > key(installed or "0")


@dataclass
class UpdateState:
    installed: str = ""
    latest: str = ""
    checked_at: float = 0.0
    update_available: bool = False
    applied_at: float = 0.0
    error: str = ""
    assets: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("assets", None)
        return d


class YtdlpUpdater:
    def __init__(self, http: HttpCache, target: Path, releases_url: str | None = None,
                 asset_name: str | None = None):
        self.http = http
        self.target = target
        self.releases_url = releases_url or os.environ.get("MPV_UOS_YTDLP_RELEASES_URL") or RELEASES_LATEST_URL
        self.asset_name = asset_name or platform_asset_name()
        self.state = UpdateState()

    def check(self, installed: str, force: bool = False) -> UpdateState:
        st = self.state
        st.installed = installed
        try:
            res = self.http.fetch(self.releases_url, ttl=UPDATE_CHECK_TTL, force=force, timeout=20,
                                  headers={"Accept": "application/vnd.github+json"})
            rel = json.loads(res.read_text())
            st.latest = str(rel.get("tag_name") or "")
            st.assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", []) if "name" in a}
            st.checked_at = res.fetched_at
            st.update_available = version_newer(st.latest, installed)
            st.error = ""
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            st.error = str(exc)
            log.warning("yt-dlp update check failed: %s", exc)
        return st

    def apply(self) -> UpdateState:
        """Download the asset of the last checked release, verify it against SHA2-256SUMS and install it atomically."""
        st = self.state
        if not st.latest or not st.assets:
            st.error = "no release information (run check first)"
            return st
        try:
            asset_url = st.assets[self.asset_name]
            sums_url = st.assets[SUMS_NAME]
        except KeyError as exc:
            st.error = f"release {st.latest} has no asset {exc}"
            return st
        try:
            sums = parse_sums(self.http.fetch(sums_url, ttl=UPDATE_CHECK_TTL, timeout=30).read_text())
            expected = sums.get(self.asset_name)
            if not expected:
                raise FetchError(f"{SUMS_NAME} has no entry for {self.asset_name}")
            body = self.http.fetch(asset_url, ttl=UPDATE_CHECK_TTL, timeout=600).read_bytes()
            actual = hashlib.sha256(body).hexdigest()
            if actual != expected:
                self.http.invalidate(asset_url)
                raise FetchError(f"SHA-256 mismatch for {self.asset_name}: {actual} != {expected}")
            self.target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".yt-dlp.", dir=str(self.target.parent))
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.chmod(tmp, 0o755)
            os.replace(tmp, self.target)
            (self.target.parent / (self.target.name + ".version")).write_text(st.latest + "\n", encoding="utf-8")
            st.installed = st.latest
            st.update_available = False
            st.applied_at = time.time()
            st.error = ""
            log.info("yt-dlp updated to %s (%s)", st.latest, self.target)
        except (FetchError, OSError) as exc:
            st.error = str(exc)
            log.warning("yt-dlp update failed: %s", exc)
        return st
