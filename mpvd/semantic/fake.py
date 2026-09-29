"""Deterministic stand-in for the ONNX embedder (tests and MPVD_SEMANTIC_FAKE=1): hashed bag of words, 64 dims, L2-normalised.
Sentences sharing vocabulary get similar vectors, which is all the index/chapter logic needs to be exercised end to end."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class FakeEmbedder:
    name = "fake-bow"
    dim = 64
    threads = 1

    def __init__(self, model_dir: Path | None = None, threads: int | None = None):
        self.model_dir = model_dir

    def encode(self, texts: Any, batch_size: int = 32) -> Any:
        import numpy as np  # noqa: PLC0415

        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in str(t).lower().split():
                w = w.strip(".,;:!?¿¡\"'()…")
                if len(w) < 3:
                    continue
                h = int.from_bytes(hashlib.md5(w.encode()).digest()[:4], "little")  # noqa: S324 - not security
                out[i, h % self.dim] += 1.0
            n = float(np.linalg.norm(out[i]))
            if n > 0:
                out[i] /= n
        return out
