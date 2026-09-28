"""Best-effort hardware detection to size models and worker pools (no external dependencies)."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from functools import lru_cache
from typing import Any


def _ram_gb() -> float | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return None


@lru_cache(maxsize=1)
def hardware_info() -> dict[str, Any]:
    cpu = os.cpu_count() or 1
    ram = _ram_gb()
    info: dict[str, Any] = {
        "platform": sys.platform,
        "machine": platform.machine(),
        "cpu_count": cpu,
        "ram_gb": ram,
        "tools": {name: shutil.which(name) is not None
                  for name in ("ffmpeg", "ffprobe", "vulkaninfo", "vainfo", "nvidia-smi", "yt-dlp", "fpcalc", "espeak-ng")},
    }
    # Tier used to pick models: small (<=4 cores or <8 GB), medium, large.
    if cpu <= 4 or (ram is not None and ram < 8):
        info["tier"] = "small"
    elif cpu <= 8 or (ram is not None and ram < 16):
        info["tier"] = "medium"
    else:
        info["tier"] = "large"
    return info
