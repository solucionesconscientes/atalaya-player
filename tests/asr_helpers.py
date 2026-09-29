"""Shared helpers for the ASR tests: is whisper.cpp vendored, which model to use, keyword scoring."""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path

from mpvd.asr.models import ModelStore, default_model_dirs, find_binary

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "tests" / "fixtures" / "media"


def whisper_available() -> bool:
    return find_binary("whisper-cli", ROOT) is not None and asr_model() is not None


def asr_model() -> str | None:
    """Model used by the tests: base if vendored (reliable keywords), else tiny (basic), overridable by env."""
    store = ModelStore(default_model_dirs(ROOT, ROOT / ".cache" / "data"))
    env = os.environ.get("MPV_UOS_TEST_ASR_MODEL")
    for name in ([env] if env else []) + ["base", "base-q5_1", "tiny", "tiny-q5_1"]:
        if store.find(name) is not None:
            return name
    return None


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def keywords_hit(text: str, lang: str) -> list[str]:
    kws = json.loads((MEDIA / f"voz_{lang}.json").read_text(encoding="utf-8"))["keywords"]
    folded = fold(text)
    return [k for k in kws if fold(k) in folded]


def min_keywords() -> int:
    """tiny gets ~3 of 7 keywords on the synthetic voices, base ≥ 5."""
    return 2 if (asr_model() or "").startswith("tiny") else 3
