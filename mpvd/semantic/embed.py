"""Multilingual sentence embeddings without torch: ONNX Runtime + sentencepiece (verified in docs/SEMANTICA.md).

Model: Xenova/paraphrase-multilingual-MiniLM-L12-v2 ``onnx/model_quantized.onnx`` (XLM-R tokenizer, 384 dims, mean pooling,
L2-normalised, 128 tokens max, no prefixes). Files are pinned by SHA-256 in vendor.lock and downloaded on demand into
``vendor/models/embed`` (or ``MPV_UOS_EMBED_DIR``). The ``[semantic]`` extra (onnxruntime, numpy, sentencepiece) is optional: without
it ``available()`` is False and the service answers "unavailable" instead of failing at import time.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import os
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("mpvd.semantic")

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2-q8"
MODEL_VERSION = "1"
DIM = 384
MAX_TOKENS = 128
# XLM-R special ids (fairseq layout): <s>=0 <pad>=1 </s>=2 <unk>=3; sentencepiece piece p -> p+1, spm <unk>(0) -> 3
CLS, PAD, SEP, UNK = 0, 1, 2, 3


@dataclass(frozen=True)
class ModelFile:
    filename: str
    url: str
    sha256: str
    size: int


FILES: tuple[ModelFile, ...] = (
    ModelFile("model_quantized.onnx",
              "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/onnx/model_quantized.onnx",
              "66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc", 118_308_126),
    ModelFile("sentencepiece.bpe.model",
              "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/sentencepiece.bpe.model",
              "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865", 5_069_051),
)


class EmbedError(RuntimeError):
    pass


def runtime_available() -> bool:
    """onnxruntime + numpy + sentencepiece importable (the ``semantic`` extra)."""
    return all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "numpy", "sentencepiece"))


def default_model_dir() -> Path:
    env = os.environ.get("MPV_UOS_EMBED_DIR")
    if env:
        return Path(env)
    root = Path(os.environ.get("MPV_UOS_ROOT") or Path(__file__).resolve().parents[2])
    return root / "vendor" / "models" / "embed"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_present(model_dir: Path | None = None) -> bool:
    d = model_dir or default_model_dir()
    return all((d / f.filename).is_file() and (d / f.filename).stat().st_size == f.size for f in FILES)


def threads_for_hardware() -> int:
    """2 threads is the sweet spot measured on a 4-core laptop (4 does not help, 1 doubles the time); leave CPU to mpv."""
    n = os.cpu_count() or 2
    return max(1, min(2, n - 1))


async def download_model(model_dir: Path | None = None, progress: Callable[[float, str], None] | None = None) -> Path:
    """Fetch the pinned files that are missing (atomic .part + SHA-256 check)."""
    d = model_dir or default_model_dir()
    d.mkdir(parents=True, exist_ok=True)
    total = sum(f.size for f in FILES)
    done_before = 0
    for f in FILES:
        dest = d / f.filename
        if dest.is_file() and dest.stat().st_size == f.size:
            done_before += f.size
            continue
        part = dest.with_suffix(dest.suffix + ".part")

        def _fetch(f: ModelFile = f, part: Path = part, base: int = done_before) -> None:
            req = urllib.request.Request(f.url, headers={"User-Agent": "mpv-uos/semantic"})
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:  # noqa: S310 - pinned https URL
                got = 0
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if progress is not None:
                        progress(min(0.95, (base + got) / total), f"{f.filename}: {got // (1 << 20)} / {f.size // (1 << 20)} MiB")
            if sha256_of(part) != f.sha256:
                raise EmbedError(f"SHA-256 incorrecto para {f.url}")

        try:
            await asyncio.to_thread(_fetch)
            part.replace(dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        done_before += f.size
    if progress is not None:
        progress(1.0, "listo")
    return d


class Embedder:
    """Loads the ONNX model once; ``encode(texts)`` -> float32 array (n, 384), L2-normalised. Runs in a thread."""

    def __init__(self, model_dir: Path | None = None, threads: int | None = None):
        if not runtime_available():
            raise EmbedError("falta el extra 'semantic' (uv sync --extra semantic)")
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
        import sentencepiece as spm  # noqa: PLC0415

        d = model_dir or default_model_dir()
        if not model_present(d):
            raise EmbedError("el modelo de embeddings no está descargado (semantic.models.download)")
        self.np = np
        self.model_dir = d
        self.threads = threads or threads_for_hardware()
        so = ort.SessionOptions()
        so.intra_op_num_threads = self.threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(d / "model_quantized.onnx"), so, providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.session.get_inputs()]
        self.sp = spm.SentencePieceProcessor(model_file=str(d / "sentencepiece.bpe.model"))
        self.name = MODEL_NAME
        self.dim = DIM

    def tokenize(self, texts: Sequence[str], max_len: int = MAX_TOKENS) -> list[list[int]]:
        rows = []
        for ids in self.sp.encode(list(texts), out_type=int):
            ids = [UNK if i == 0 else i + 1 for i in ids][: max_len - 2]
            rows.append([CLS, *ids, SEP])
        return rows

    def encode(self, texts: Sequence[str], batch_size: int = 32) -> Any:
        np = self.np
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        out = []
        for i in range(0, len(texts), batch_size):
            rows = self.tokenize(texts[i:i + batch_size])
            width = max(len(r) for r in rows)
            ids = np.full((len(rows), width), PAD, dtype=np.int64)
            mask = np.zeros((len(rows), width), dtype=np.int64)
            for j, r in enumerate(rows):
                ids[j, : len(r)] = r
                mask[j, : len(r)] = 1
            feed: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]                       # (batch, seq, 384)
            m = mask[..., None].astype(np.float32)
            emb = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)     # mean pooling
            out.append(emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None))
        return np.concatenate(out).astype(np.float32)
