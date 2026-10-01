"""OPUS-MT "tc-big" translation models (Helsinki-NLP, CC-BY 4.0) run on CTranslate2 int8 (see docs/TRADUCCION.md).

The official Marian release zips (Tatoeba-Challenge, object storage of CSC) are downloaded on demand, verified by
SHA-256, and converted once with ``ctranslate2.converters.OpusMTConverter`` (numpy + pyyaml only, no torch) into
``<models>/<id>/`` (``model.bin`` + ``shared_vocabulary.json`` + ``source.spm``/``target.spm``, ~234 MB). The zip
(~860 MB, the fp32 ``.npz`` inside is ~930 MB) and the unpacked weights are deleted afterwards.

Multi-target models need a ``>>lang<<`` token in front of the source pieces (e.g. ``>>spa<<`` for English→Spanish).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_AGENT = "mpv-uos/translate"
BASE_URL = "https://object.pouta.csc.fi/Tatoeba-MT-models"
ENGINE_ID = "opus-big"
ENGINE_NAME = "Calidad (OPUS-MT)"
LICENSE = "CC-BY 4.0 (Helsinki-NLP / OPUS-MT, Tiedemann & Thottingal 2020)"


class OpusError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpusModel:
    id: str
    release: str                     # path under BASE_URL
    sha256: str
    zip_bytes: int
    size_mb: int                     # converted (int8) on disk
    pairs: dict[tuple[str, str], str | None] = field(default_factory=dict)   # (src, tgt) -> target token or None

    @property
    def url(self) -> str:
        return f"{os.environ.get('MPV_UOS_OPUS_BASE_URL', BASE_URL)}/{self.release}"

    @property
    def zip_name(self) -> str:
        return f"opus-mt-{self.id}.zip"


# Verified on 2026-09-30 and, for the French pair, on 2026-10-01 (HEAD + full download + sha256sum here):
# sizes are the Content-Length of the object storage and the sums are of the files downloaded and used here.
MODELS: dict[str, OpusModel] = {m.id: m for m in (
    OpusModel("tc-big-cat_oci_spa-eng-2022-03-13",
              "cat+oci+spa-eng/opusTCv20210807+bt_transformer-big_2022-03-13.zip",
              "a2d13b90c2d59fdd6b5db723fea7aaf32f1a5ef25d7119806c94df753e70f17f", 863152463, 234,
              {("es", "en"): None, ("ca", "en"): None}),
    OpusModel("tc-big-eng-cat_oci_spa-2022-03-13",
              "eng-cat+oci+spa/opusTCv20210807+bt_transformer-big_2022-03-13.zip",
              "a5f01f26b1f22cc840b9f94e98a4fe2b517fca85296f7f802771272fbfcd659e", 862895279, 234,
              {("en", "es"): ">>spa<<", ("en", "ca"): ">>cat<<"}),
    # H36/C6: con estos dos, el triángulo es/en/fr es OPUS-MT de punta a punta (es→fr pivota por inglés: no existe
    # ningún tc-big spa-fra ni fra-spa en el repositorio, comprobado listando el bucket el 2026-10-01).
    # Un solo idioma de origen y uno de destino → sin token >>lang<< (README y preprocess.sh del propio zip).
    OpusModel("tc-big-fra-eng-2022-03-09",
              "fra-eng/opusTCv20210807+bt_transformer-big_2022-03-09.zip",
              "61f0684da189ef6cacbf4202284a390648312f13c87a37cde079f2c18829bac2", 856639098, 234,
              {("fr", "en"): None}),
    OpusModel("tc-big-eng-fra-2022-03-09",
              "eng-fra/opusTCv20210807+bt_transformer-big_2022-03-09.zip",
              "65218bc83ae8983c0c78eb10a810f7d83756d62decb7144c2f5efaae6d4fae7f", 856664877, 234,
              {("en", "fr"): None}),
)}

# Members of the release zip needed for the conversion (the rest are logs and scripts).
_NEEDED = re.compile(r"^(decoder\.yml|source\.spm|target\.spm|LICENSE|README\.md|[^/]+\.vocab\.yml|[^/]+\.npz)$")
_KEEP = ("source.spm", "target.spm", "LICENSE", "README.md")

# Light version of the release's preprocess.sh normalisation (typographic quotes, ellipsis, control characters).
_NORM = str.maketrans({"’": "'", "“": '"', "”": '"', "…": "...", "​": None, "﻿": None, "⁠": None})
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def normalize(text: str) -> str:
    return " ".join(_CTRL.sub(" ", text.translate(_NORM)).split())


def model_for(source: str, target: str) -> tuple[OpusModel, str | None] | None:
    for m in MODELS.values():
        if (source, target) in m.pairs:
            return m, m.pairs[(source, target)]
    return None


def supported_pairs() -> list[tuple[str, str]]:
    return sorted(p for m in MODELS.values() for p in m.pairs)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def converter_available() -> bool:
    try:
        import ctranslate2.converters  # noqa: F401, PLC0415
        import numpy  # noqa: F401, PLC0415
        import yaml  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


class OpusStore:
    """Converted models live in ``<dir>/<model id>/``; the first directory that has one wins, downloads go to
    ``download_to`` (the user's data dir, never vendor/). ``dl_dirs`` may hold a pre-downloaded release zip (kept)."""

    META = "mpv-uos.json"

    def __init__(self, dirs: list[Path], download_to: Path, dl_dirs: list[Path] | None = None):
        self.dirs = [Path(d) for d in dirs]
        self.download_to = Path(download_to)
        if self.download_to not in self.dirs:
            self.dirs.append(self.download_to)
        self.dl_dirs = [Path(d) for d in (dl_dirs or [])]

    @classmethod
    def valid(cls, path: Path) -> bool:
        return all((path / f).is_file() for f in ("model.bin", "source.spm", "target.spm", "config.json")) and (
            (path / "shared_vocabulary.json").is_file() or (path / "shared_vocabulary.txt").is_file())

    def find_model(self, model_id: str) -> Path | None:
        for d in self.dirs:
            p = d / model_id
            if self.valid(p):
                return p
        return None

    def find(self, source: str, target: str) -> tuple[Path, str | None] | None:
        hit = model_for(source, target)
        if hit is None:
            return None
        path = self.find_model(hit[0].id)
        return (path, hit[1]) if path is not None else None

    def present_pairs(self) -> list[tuple[str, str]]:
        return [p for p in supported_pairs() if self.find(*p) is not None]

    def catalogue(self) -> list[dict[str, Any]]:
        out = []
        for (s, t) in supported_pairs():
            m, _tok = model_for(s, t) or (None, None)
            assert m is not None
            path = self.find_model(m.id)
            out.append({"engine": ENGINE_ID, "source": s, "target": t, "model": m.id, "present": path is not None,
                        "path": str(path) if path else None, "size_mb": m.size_mb,
                        "download_mb": round(m.zip_bytes / 1e6), "url": m.url, "license": LICENSE})
        return out

    def remove(self, source: str, target: str) -> bool:
        hit = model_for(source, target)
        path = self.find_model(hit[0].id) if hit else None
        if path is None:
            return False
        shutil.rmtree(path)
        return True

    # -- download + conversion ---------------------------------------------------------------------------------

    def _preseeded(self, m: OpusModel) -> Path | None:
        for d in self.dl_dirs:
            for name in (m.zip_name, m.release.replace("/", "_")):
                p = d / name
                if p.is_file() and p.stat().st_size == m.zip_bytes:
                    return p
        return None

    async def download(self, source: str, target: str,
                       progress: Callable[[float, str], None] | None = None) -> Path:
        hit = model_for(source, target)
        if hit is None:
            raise OpusError(f"OPUS-MT no cubre {source}→{target}")
        m = hit[0]
        existing = self.find_model(m.id)
        if existing is not None:
            return existing
        if not converter_available():
            raise OpusError("falta el runtime de traducción: uv sync --extra translate")
        self.download_to.mkdir(parents=True, exist_ok=True)

        def report(frac: float, message: str) -> None:
            if progress is not None:
                progress(frac, message)

        return await asyncio.to_thread(self._install, m, report)

    def _fetch(self, m: OpusModel, dest: Path, report: Callable[[float, str], None]) -> None:
        part = dest.with_suffix(".part")
        req = urllib.request.Request(m.url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:  # noqa: S310 - pinned URL
                total = int(resp.headers.get("Content-Length") or m.zip_bytes)
                done = 0
                last = 0.0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last > 0.5:
                        last = now
                        report(0.75 * done / total, f"descargando {done / 1e6:.0f} / {total / 1e6:.0f} MB")
            report(0.76, "comprobando SHA-256")
            if sha256_of(part) != m.sha256:
                raise OpusError(f"SHA-256 incorrecto para {m.url}")
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)

    def _install(self, m: OpusModel, report: Callable[[float, str], None]) -> Path:
        work = Path(tempfile.mkdtemp(prefix=f".{m.id}-", dir=str(self.download_to)))
        try:
            zip_path = self._preseeded(m)
            if zip_path is not None:
                report(0.7, "comprobando el zip ya descargado")
                if sha256_of(zip_path) != m.sha256:
                    zip_path = None
            owned = zip_path is None
            if zip_path is None:
                zip_path = work / m.zip_name
                self._fetch(m, zip_path, report)
            report(0.8, "descomprimiendo")
            src = work / "src"
            src.mkdir()
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if not _NEEDED.match(info.filename):
                        continue       # flat release zips: nested names or traversal never match
                    with zf.open(info) as fin, (src / info.filename).open("wb") as fout:
                        shutil.copyfileobj(fin, fout, 1 << 20)
            if owned:
                zip_path.unlink(missing_ok=True)   # 860 MB: free it before the conversion
            if not (src / "decoder.yml").is_file():
                raise OpusError(f"{m.zip_name}: falta decoder.yml")
            report(0.88, "convirtiendo a CTranslate2 int8")
            out = work / "ct2"
            import ctranslate2  # noqa: PLC0415
            from ctranslate2.converters import OpusMTConverter  # noqa: PLC0415

            OpusMTConverter(str(src)).convert(str(out), quantization="int8", force=True)
            for name in _KEEP:
                if (src / name).is_file():
                    shutil.copy2(src / name, out / name)
            (out / self.META).write_text(json.dumps({
                "id": m.id, "url": m.url, "sha256": m.sha256, "pairs": [list(p) for p in m.pairs],
                "ctranslate2": ctranslate2.__version__, "quantization": "int8", "license": LICENSE,
                "converted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }, indent=1), encoding="utf-8")
            if not self.valid(out):
                raise OpusError("la conversión no produjo un modelo CTranslate2 completo")
            dest = self.download_to / m.id
            if dest.exists():
                shutil.rmtree(dest)
            out.replace(dest)
            report(1.0, "listo")
            return dest
        finally:
            shutil.rmtree(work, ignore_errors=True)


def default_dirs(root: Path | None, data_dir: Path) -> tuple[list[Path], Path, list[Path]]:
    """(search dirs, download dir, dirs with pre-downloaded zips). ``MPV_UOS_OPUS_MODELS`` overrides the download dir;
    in a checkout, ``vendor/models/opus-mt`` is searched (read-only) and ``vendor/dl`` may hold the release zips."""
    env = os.environ.get("MPV_UOS_OPUS_MODELS")
    download_to = Path(env) if env else data_dir / "models" / "opus-mt"
    dirs = [download_to]
    dl: list[Path] = []
    if os.environ.get("MPV_UOS_OPUS_DL"):
        dl.append(Path(os.environ["MPV_UOS_OPUS_DL"]))
    if env:
        return dirs, download_to, dl  # an explicit models dir is the only one (tests, custom setups)
    if root is not None:
        dirs.append(root / "vendor" / "models" / "opus-mt")
        dl.append(root / "vendor" / "dl")
    return dirs, download_to, dl


class OpusEngine:
    """CTranslate2 translators for the OPUS-MT big models (≈430 MB of RAM each: unload after every job)."""

    def __init__(self, store: OpusStore, threads: int | None = None, beam_size: int = 4):
        self.store = store
        cpu = os.cpu_count() or 2
        # half the cores: see the note in mpvd/subs/translate.py (transcribing and translating happen together)
        self.threads = threads or max(1, min(cpu // 2, 8))
        self.beam_size = beam_size
        self._loaded: dict[str, tuple[Any, Any, Any]] = {}
        self.stats = {"sentences": 0, "seconds": 0.0, "load_seconds": 0.0}

    def status(self) -> dict[str, Any]:
        return {"id": ENGINE_ID, "threads": self.threads, "beam_size": self.beam_size, "loaded": list(self._loaded),
                "present": [f"{s}_{t}" for s, t in self.store.present_pairs()], "converter": converter_available()}

    def _load(self, source: str, target: str) -> tuple[Any, Any, Any, str | None]:
        hit = self.store.find(source, target)
        if hit is None:
            raise OpusError(f"modelo OPUS-MT {source}→{target} no descargado")
        path, token = hit
        key = str(path)
        if key not in self._loaded:
            import ctranslate2  # noqa: PLC0415
            import sentencepiece as spm  # noqa: PLC0415

            t0 = time.monotonic()
            types = ctranslate2.get_supported_compute_types("cpu")
            tr = ctranslate2.Translator(str(path), device="cpu", compute_type="int8" if "int8" in types else "auto",
                                        inter_threads=1, intra_threads=self.threads)
            sp_src = spm.SentencePieceProcessor(model_file=str(path / "source.spm"))
            sp_tgt = spm.SentencePieceProcessor(model_file=str(path / "target.spm"))
            if len(self._loaded) >= 1:
                self._loaded.clear()        # one big model at a time
            self._loaded[key] = (tr, sp_src, sp_tgt)
            self.stats["load_seconds"] += time.monotonic() - t0
        tr, sp_src, sp_tgt = self._loaded[key]
        return tr, sp_src, sp_tgt, token

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        tr, sp_src, sp_tgt, token = self._load(source, target)
        out = list(texts)
        idx = [i for i, t in enumerate(texts) if t.strip()]
        if not idx:
            return out
        t0 = time.monotonic()
        toks = []
        for i in idx:
            pieces = sp_src.encode(normalize(texts[i]), out_type=str)
            toks.append(([token] if token else []) + pieces[:500])
        res = tr.translate_batch(toks, beam_size=self.beam_size, max_decoding_length=256)
        for i, r in zip(idx, res, strict=True):
            if r.hypotheses:
                out[i] = sp_tgt.decode(r.hypotheses[0])
        self.stats["sentences"] += len(idx)
        self.stats["seconds"] += time.monotonic() - t0
        return out

    def unload(self) -> None:
        self._loaded.clear()
