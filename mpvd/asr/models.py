"""whisper.cpp model catalogue: what is vendored, what can be downloaded on demand, and which one fits the hardware.

Model files are the official ggml conversions published by ggml-org (same URLs as whisper.cpp's
``models/download-ggml-model.sh`` and ``models/download-vad-model.sh``). Files start with the ggml magic ``lmgg``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GGML_MAGIC = b"lmgg"
MODELS_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
VAD_BASE_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main"
VAD_MODEL = "silero-v5.1.2"

# name -> (approx size in MiB, multilingual, note in Spanish for menus)
CATALOG: dict[str, tuple[int, bool, str]] = {
    "tiny": (75, True, "muy rápido, calidad básica"),
    "tiny-q5_1": (31, True, "muy rápido, calidad básica (cuantizado)"),
    "tiny.en": (75, False, "solo inglés, muy rápido"),
    "base": (142, True, "rápido, calidad aceptable"),
    "base-q5_1": (57, True, "rápido, calidad aceptable (cuantizado)"),
    "base.en": (142, False, "solo inglés, rápido"),
    "small": (466, True, "buena calidad, 4+ núcleos"),
    "small-q5_1": (181, True, "buena calidad (cuantizado), 4+ núcleos"),
    "small-q8_0": (252, True, "buena calidad (q8), 4+ núcleos"),
    "small.en": (466, False, "solo inglés, buena calidad"),
    "medium": (1500, True, "muy buena calidad, lento en CPU"),
    "medium-q5_0": (514, True, "muy buena calidad (cuantizado), lento en CPU"),
    "large-v3-turbo": (1600, True, "calidad máxima con decodificador rápido, GPU recomendada"),
    "large-v3-turbo-q5_0": (547, True, "calidad máxima (cuantizado), GPU recomendada"),
    "large-v3": (3100, True, "calidad máxima, GPU necesaria"),
}

# Ordered from cheapest to best; ``pick_model`` walks it with the hardware tier.
LIVE_ORDER = ["tiny-q5_1", "tiny", "base-q5_1", "base", "small-q5_1", "small-q8_0", "small", "medium-q5_0", "medium",
              "large-v3-turbo-q5_0", "large-v3-turbo", "large-v3"]

# Live captions need RTF <= 0.5 while mpv keeps decoding; precompute tolerates RTF < 1 (docs/BENCHMARKS.md, 4-core i5).
# With 28.5 s chunks one whisper call covers a full 30 s window, so small-q8_0 (RTF ~1.0 with 12 s of audio) gets
# close to 0.4-0.5: good enough to pre-subtitle on a 4-core laptop, while base transcribes badly and with almost no
# punctuation (which is what ruins the translations). Live stays on base there: mpv needs the spare CPU.
TIER_LIVE = {"small": "base", "medium": "base", "large": "small-q8_0"}
TIER_PRECOMPUTE = {"small": "small-q8_0", "medium": "small-q8_0", "large": "small"}


class ModelError(RuntimeError):
    pass


def model_filename(name: str) -> str:
    return f"ggml-{name}.bin"


def model_url(name: str) -> str:
    if name == VAD_MODEL:
        return f"{os.environ.get('MPV_UOS_WHISPER_VAD_URL', VAD_BASE_URL)}/{model_filename(name)}"
    return f"{os.environ.get('MPV_UOS_WHISPER_MODELS_URL', MODELS_BASE_URL)}/{model_filename(name)}"


def is_ggml(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == GGML_MAGIC
    except OSError:
        return False


@dataclass
class ModelInfo:
    name: str
    path: Path | None
    size_mb: int
    multilingual: bool
    note: str
    present: bool
    is_vad: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": str(self.path) if self.path else None, "size_mb": self.size_mb,
                "multilingual": self.multilingual, "note": self.note, "present": self.present, "vad": self.is_vad}


class ModelStore:
    """Models live in one or more directories; downloads go to the first writable one."""

    def __init__(self, dirs: list[Path]):
        self.dirs = [Path(d) for d in dirs]

    def find(self, name: str) -> Path | None:
        for d in self.dirs:
            p = d / model_filename(name)
            if p.is_file() and p.stat().st_size > 1024 and is_ggml(p):
                return p
        return None

    def download_dir(self) -> Path:
        for d in self.dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                if os.access(d, os.W_OK):
                    return d
            except OSError:
                continue
        raise ModelError("no writable model directory")

    def list(self) -> list[ModelInfo]:
        out = []
        for name, (size, multi, note) in CATALOG.items():
            p = self.find(name)
            out.append(ModelInfo(name, p, size, multi, note, p is not None))
        vad = self.find(VAD_MODEL)
        out.append(ModelInfo(VAD_MODEL, vad, 1, True, "detector de voz (VAD) Silero", vad is not None, is_vad=True))
        return out

    def present(self) -> list[str]:
        return [m.name for m in self.list() if m.present and not m.is_vad]

    def pick(self, tier: str, purpose: str = "live", prefer_present: bool = True) -> str:
        """Model name for the hardware tier (``live`` or ``precompute``); prefers what is already on disk."""
        table = TIER_LIVE if purpose == "live" else TIER_PRECOMPUTE
        wanted = table.get(tier, "base")
        if not prefer_present:
            return wanted
        present = set(self.present())
        if wanted in present:
            return wanted
        # quantized/base variants of the same family, then anything cheaper that exists
        family = wanted.split("-")[0].split(".")[0]
        for cand in LIVE_ORDER:
            if cand.startswith(family) and cand in present:
                return cand
        idx = LIVE_ORDER.index(wanted) if wanted in LIVE_ORDER else len(LIVE_ORDER)
        for cand in reversed(LIVE_ORDER[:idx]):
            if cand in present:
                return cand
        return wanted

    async def download(self, name: str, progress: Callable[[float, str], None] | None = None,
                       timeout: float = 3600.0) -> Path:
        """Fetch a model into the first writable directory (atomic: ``.part`` then rename); verifies the ggml magic."""
        if name != VAD_MODEL and name not in CATALOG:
            raise ModelError(f"unknown model {name!r}")
        existing = self.find(name)
        if existing is not None:
            return existing
        dest_dir = self.download_dir()
        dest = dest_dir / model_filename(name)
        part = dest.with_suffix(".bin.part")
        url = model_url(name)

        def _fetch() -> None:
            req = urllib.request.Request(url, headers={"User-Agent": "mpv-uos/asr"})
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:  # noqa: S310 - https URL from catalogue
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        frac = done / total if total else 0.0
                        progress(min(frac, 0.99), f"{done / 2**20:.0f} / {total / 2**20:.0f} MiB" if total
                                 else f"{done / 2**20:.0f} MiB")

        try:
            await asyncio.wait_for(asyncio.to_thread(_fetch), timeout)
            if not is_ggml(part):
                raise ModelError(f"downloaded file is not a ggml model: {url}")
            part.replace(dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        if progress is not None:
            progress(1.0, "listo")
        return dest

    def remove(self, name: str) -> bool:
        p = self.find(name)
        if p is None:
            return False
        p.unlink()
        return True


def default_model_dirs(root: Path | None, data_dir: Path) -> list[Path]:
    env = os.environ.get("MPV_UOS_WHISPER_MODELS")
    dirs: list[Path] = [Path(env)] if env else []
    if root is not None:
        dirs.append(root / "vendor" / "whisper" / "models")
    dirs.append(data_dir / "models" / "whisper")
    return dirs


def find_binary(name: str, root: Path | None) -> Path | None:
    """``whisper-cli`` / ``whisper-server`` from $MPV_UOS_WHISPER_BIN, vendor/whisper/bin or PATH."""
    env = os.environ.get("MPV_UOS_WHISPER_BIN")
    candidates = [Path(env) / name] if env else []
    if root is not None:
        candidates.append(root / "vendor" / "whisper" / "bin" / name)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    p = shutil.which(name)
    return Path(p) if p else None
