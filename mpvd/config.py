"""Runtime settings for mpvd: where sockets, cache and logs live.

Development checkout (pyproject.toml + mpv-config/ next to the package): cache in ``<root>/.cache``.
Installed/packaged: XDG-style user directories (``$XDG_CACHE_HOME`` or ``~/.cache``).
Sockets always go to a per-user runtime dir shared with bin/mpv-uos (``$MPV_UOS_RUNTIME_DIR``,
else ``$XDG_RUNTIME_DIR/mpv-uos``, else ``$TMPDIR/mpv-uos``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL_VERSION = 1
SOCKET_NAME = "mpvd.sock"
PID_NAME = "mpvd.pid"


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root() -> Path | None:
    """Project checkout root (dev mode) or None when running installed."""
    env = os.environ.get("MPV_UOS_ROOT")
    candidates = [Path(env)] if env else []
    candidates.append(_package_root())
    for c in candidates:
        if (c / "pyproject.toml").exists() and (c / "mpv-config").is_dir():
            return c.resolve()
    return None


def default_runtime_dir() -> Path:
    env = os.environ.get("MPV_UOS_RUNTIME_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "mpv-uos"


def default_cache_dir() -> Path:
    env = os.environ.get("MPV_UOS_CACHE_DIR")
    if env:
        return Path(env)
    root = project_root()
    if root is not None:
        return root / ".cache"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "mpv-uos" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "mpv-uos"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mpv-uos"


def default_data_dir() -> Path:
    """User data that is not a cache (favourites, recents, settings)."""
    env = os.environ.get("MPV_UOS_DATA_DIR")
    if env:
        return Path(env)
    # Always outside the checkout, also in development: a `rm -rf .cache` must never take the user's favourites,
    # notes or paired phones with it (bin/mpv-uos migrates the old <checkout>/.cache/data once).
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "mpv-uos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mpv-uos"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "mpv-uos"


@dataclass
class Settings:
    runtime_dir: Path = field(default_factory=default_runtime_dir)
    cache_dir: Path = field(default_factory=default_cache_dir)
    data_dir: Path = field(default_factory=default_data_dir)
    idle_timeout: float = 600.0  # seconds without sessions/clients before the daemon exits (0 = never)
    workers: int = max(1, (os.cpu_count() or 2) - 1)
    log_level: str = "INFO"

    @property
    def socket_path(self) -> Path:
        return self.runtime_dir / SOCKET_NAME

    @property
    def pid_path(self) -> Path:
        return self.runtime_dir / PID_NAME

    @property
    def log_path(self) -> Path:
        return self.cache_dir / "mpvd.log"

    def ensure_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.runtime_dir, 0o700)
        except OSError:
            pass

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        s = cls()
        if os.environ.get("MPVD_IDLE_TIMEOUT"):
            s.idle_timeout = float(os.environ["MPVD_IDLE_TIMEOUT"])
        if os.environ.get("MPVD_LOG_LEVEL"):
            s.log_level = os.environ["MPVD_LOG_LEVEL"]
        for k, v in overrides.items():
            if v is not None:
                setattr(s, k, v)
        return s
