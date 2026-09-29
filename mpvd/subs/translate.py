"""Offline subtitle translation with Argos Translate packages run directly on CTranslate2 + sentencepiece
(no ``argostranslate``: it drags stanza/torch, see docs/TRADUCCION.md and ADR-025).

A package is a zip (``.argosmodel``) holding ``model/`` (CTranslate2), ``sentencepiece.model`` and ``metadata.json``.
Pinned packages are verified by SHA-256; other pairs come from the official index and pivot through English when
there is no direct model. Cues are grouped into sentences before translating and the result is redistributed over
the original cues by length, so timing is untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mpvd.asr.srt import Segment

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
USER_AGENT = "mpv-uos/translate"

# (from, to) -> (url, sha256, version). 1.0 packages: sentencepiece + CT2, work in int8; es→en 1.9 is BPE and broken in int8.
PINNED: dict[tuple[str, str], tuple[str, str, str]] = {
    ("es", "en"): ("https://argos-net.com/v1/translate-es_en-1_0.argosmodel",
                   "1b963aa0e0cb6e5ce874f0aa1a1949a19bc4d762e833532239a8834340f6b378", "1.0"),
    ("en", "es"): ("https://argos-net.com/v1/translate-en_es-1_0.argosmodel",
                   "d698d0ef87ad70d5d184b7fa6965905bf4368f09a2bb9ffb165a79bac96af0c4", "1.0"),
}

_ABBR = re.compile(r"(?:^|\s)(?:Sr|Sra|Srta|Dr|Dra|Ud|Uds|Vd|EE\.UU|p\.ej|etc|Mr|Mrs|Ms|St|vs|No|núm|art|pág|tel|aprox)\.$", re.I)
_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[¿¡\"'(\[A-ZÁÉÍÓÚÑ0-9])")
_DASH = re.compile(r"(?:^|\s)[-–—]\s*(?=\S)")
_FINAL = re.compile(r"[.!?…]\s*[\"')\]]*$")


class TranslateError(RuntimeError):
    pass


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _SPLIT.split(" ".join(text.split())):
        if out and _ABBR.search(out[-1]):
            out[-1] += " " + chunk
        else:
            out.append(chunk)
    return [s for s in out if s]


def split_dialogue(text: str) -> list[str]:
    """``- ¿Vienes? - Sí.`` → two pieces (keeping the dashes) so each speaker is translated on its own."""
    if len(_DASH.findall(text)) < 2:
        return [text]
    parts = [p for p in _DASH.split(text) if p.strip()]
    return ["- " + p.strip() for p in parts]


# -- cue grouping ------------------------------------------------------------------------------------------------


@dataclass
class Unit:
    cue_indices: list[int]
    pieces: list[str]          # sentences to translate (in order)


def group_cues(cues: list[Segment], max_cues: int = 3, max_chars: int = 220, max_gap: float = 1.5) -> list[Unit]:
    """Join consecutive cues until a sentence ends (or limits are hit), then split each unit into sentences."""
    units: list[Unit] = []
    i = 0
    while i < len(cues):
        idx = [i]
        text = cues[i].text.strip()
        while (not _FINAL.search(text) and len(idx) < max_cues and i + 1 < len(cues)
               and cues[i + 1].start - cues[i].end <= max_gap and len(text) + len(cues[i + 1].text) <= max_chars):
            i += 1
            idx.append(i)
            text = f"{text} {cues[i].text.strip()}"
        pieces: list[str] = []
        for d in split_dialogue(text):
            pieces.extend(split_sentences(d))
        units.append(Unit(idx, pieces or [text]))
        i += 1
    return units


def distribute(text: str, weights: list[int]) -> list[str]:
    """Split ``text`` at word boundaries into len(weights) parts roughly proportional to the weights (first parts win
    when there are fewer words than parts)."""
    words = text.split()
    n = len(weights)
    if n == 1:
        return [text]
    if len(words) <= n:
        return [words[k] if k < len(words) else "" for k in range(n)]
    total = sum(max(1, w) for w in weights)
    cum, acc = [], 0
    for w in weights[:-1]:
        acc += max(1, w)
        cum.append(int(round(len(words) * acc / total)))
    bounds = [0, *cum, len(words)]
    for k in range(1, len(bounds)):
        bounds[k] = max(bounds[k], bounds[k - 1])
    return [" ".join(words[bounds[k]:bounds[k + 1]]) for k in range(n)]


def translate_cues(cues: list[Segment], translate: Callable[[list[str]], list[str]], batch: int = 32,
                   progress: Callable[[float], None] | None = None) -> list[Segment]:
    units = group_cues(cues)
    pieces = [p for u in units for p in u.pieces]
    translated: list[str] = []
    for k in range(0, len(pieces), batch):
        translated.extend(translate(pieces[k:k + batch]))
        if progress is not None:
            progress(min(1.0, (k + batch) / max(1, len(pieces))))
    out = [Segment(c.start, c.end, c.text) for c in cues]
    pos = 0
    for u in units:
        n = len(u.pieces)
        joined = " ".join(t.strip() for t in translated[pos:pos + n] if t.strip())
        pos += n
        parts = distribute(joined, [len(cues[i].text) for i in u.cue_indices])
        for i, part in zip(u.cue_indices, parts, strict=True):
            out[i] = Segment(cues[i].start, cues[i].end, part or cues[i].text)
    return out


# -- package store ---------------------------------------------------------------------------------------------------


@dataclass
class Package:
    source: str
    target: str
    path: Path | None
    version: str
    url: str | None
    size_mb: int | None
    present: bool
    pinned: bool

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "path": str(self.path) if self.path else None,
                "version": self.version, "url": self.url, "size_mb": self.size_mb, "present": self.present,
                "pinned": self.pinned}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ArgosStore:
    """Packages live unpacked in ``<dir>/<from>_<to>/`` (first writable dir receives downloads); zips are cached in dl_dir."""

    def __init__(self, dirs: list[Path], dl_dir: Path):
        self.dirs = [Path(d) for d in dirs]
        self.dl_dir = Path(dl_dir)
        self._index: list[dict[str, Any]] | None = None

    @staticmethod
    def valid(pkg: Path) -> bool:
        return (pkg / "model" / "model.bin").is_file() and (pkg / "sentencepiece.model").is_file()

    def find(self, source: str, target: str) -> Path | None:
        for d in self.dirs:
            p = d / f"{source}_{target}"
            if self.valid(p):
                return p
        return None

    def present(self) -> list[tuple[str, str]]:
        seen: dict[tuple[str, str], Path] = {}
        for d in self.dirs:
            if not d.is_dir():
                continue
            for p in d.iterdir():
                m = re.fullmatch(r"([a-z]{2,3})_([a-z]{2,3})", p.name)
                if m and self.valid(p):
                    seen.setdefault((m.group(1), m.group(2)), p)
        return sorted(seen)

    def download_dir(self) -> Path:
        for d in self.dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                if os.access(d, os.W_OK):
                    return d
            except OSError:
                continue
        raise TranslateError("no writable model directory")

    def index(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Official package index (cached on disk for offline use)."""
        cache = self.dl_dir / "argospm-index.json"
        if self._index is not None and not refresh:
            return self._index
        if cache.is_file() and not refresh:
            try:
                self._index = json.loads(cache.read_text(encoding="utf-8"))
                return self._index
            except ValueError:
                pass
        url = os.environ.get("MPV_UOS_ARGOS_INDEX", INDEX_URL)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - https URL from configuration
            data = json.loads(resp.read().decode("utf-8"))
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        self._index = data
        return data

    def catalogue(self, with_index: bool = False) -> list[Package]:
        out: dict[tuple[str, str], Package] = {}
        for (s, t), (url, _sha, ver) in PINNED.items():
            p = self.find(s, t)
            out[(s, t)] = Package(s, t, p, ver, url, 88, p is not None, True)
        for s, t in self.present():
            if (s, t) not in out:
                out[(s, t)] = Package(s, t, self.find(s, t), "?", None, None, True, False)
        if with_index:
            try:
                for row in self.index():
                    key = (str(row.get("from_code")), str(row.get("to_code")))
                    if key in out:
                        continue
                    links = [u for u in row.get("links") or [] if str(u).startswith("https://")]
                    out[key] = Package(key[0], key[1], None, str(row.get("package_version", "?")),
                                       links[0] if links else None, None, False, False)
            except (OSError, ValueError):
                pass
        return sorted(out.values(), key=lambda p: (not p.present, p.source, p.target))

    def resolve_url(self, source: str, target: str) -> tuple[str, str | None, str]:
        if (source, target) in PINNED:
            url, sha, ver = PINNED[(source, target)]
            return url, sha, ver
        for row in self.index():
            if row.get("from_code") == source and row.get("to_code") == target:
                links = [u for u in row.get("links") or [] if str(u).startswith("https://")]
                if links:
                    return links[0], None, str(row.get("package_version", "?"))
        raise TranslateError(f"no hay paquete de traducción {source}→{target}")

    async def download(self, source: str, target: str, progress: Callable[[float, str], None] | None = None) -> Path:
        existing = self.find(source, target)
        if existing is not None:
            return existing
        url, sha, _ver = await asyncio.to_thread(self.resolve_url, source, target)
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        zip_path = self.dl_dir / url.rsplit("/", 1)[-1]

        def _fetch() -> None:
            if zip_path.is_file() and (sha is None or sha256_of(zip_path) == sha):
                return
            part = zip_path.with_suffix(".part")
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:  # noqa: S310
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(min(0.9, 0.9 * done / total) if total else 0.0, f"{done / 2**20:.0f} MiB")
            if sha is not None and sha256_of(part) != sha:
                part.unlink(missing_ok=True)
                raise TranslateError(f"SHA-256 incorrecto para {url}")
            part.replace(zip_path)

        await asyncio.to_thread(_fetch)
        dest = self.download_dir() / f"{source}_{target}"
        await asyncio.to_thread(self._unpack, zip_path, dest)
        if progress is not None:
            progress(1.0, "listo")
        return dest

    def _unpack(self, zip_path: Path, dest: Path) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="argos-", dir=str(dest.parent)))
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    name = info.filename
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise TranslateError(f"ruta sospechosa en el zip: {name}")
                    if "/stanza/" in name or name.endswith("/stanza"):
                        continue  # sentence splitter for argostranslate: not needed
                    zf.extract(info, tmp)
            roots = [p for p in tmp.iterdir() if p.is_dir()]
            root = roots[0] if len(roots) == 1 and not self.valid(tmp) else tmp
            if not self.valid(root):
                raise TranslateError(f"el paquete {zip_path.name} no contiene model/model.bin + sentencepiece.model")
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(root), str(dest))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def remove(self, source: str, target: str) -> bool:
        p = self.find(source, target)
        if p is None:
            return False
        shutil.rmtree(p)
        return True


def default_dirs(root: Path | None, data_dir: Path) -> list[Path]:
    env = os.environ.get("MPV_UOS_ARGOS_MODELS")
    dirs: list[Path] = [Path(env)] if env else []
    if root is not None:
        dirs.append(root / "vendor" / "models" / "argos")
    dirs.append(data_dir / "models" / "argos")
    return dirs


# -- engine ------------------------------------------------------------------------------------------------------------


def runtime_available() -> bool:
    try:
        import ctranslate2  # noqa: F401, PLC0415
        import sentencepiece  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


class ArgosEngine:
    """Loads at most two translators (a pivot pair) and translates batches of sentences."""

    def __init__(self, store: ArgosStore, threads: int | None = None, beam_size: int = 2):
        self.store = store
        cpu = os.cpu_count() or 2
        self.threads = threads or max(1, min(cpu - 1, 8)) if cpu > 2 else cpu
        self.beam_size = beam_size
        self._loaded: dict[tuple[str, str], tuple[Any, Any]] = {}
        self.stats = {"sentences": 0, "seconds": 0.0}

    @property
    def available(self) -> bool:
        return runtime_available()

    def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {"available": self.available, "threads": self.threads, "beam_size": self.beam_size,
                                "loaded": [f"{s}_{t}" for s, t in self._loaded], "present": [f"{s}_{t}" for s, t in self.store.present()]}
        if self.available:
            import ctranslate2  # noqa: PLC0415

            info["ctranslate2"] = ctranslate2.__version__
            info["compute_types"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
        return info

    def route(self, source: str, target: str) -> list[tuple[str, str]]:
        """Direct package or a pivot through English; raises when neither is on disk."""
        if source == target:
            return []
        if self.store.find(source, target) is not None:
            return [(source, target)]
        if source != "en" and target != "en" and self.store.find(source, "en") and self.store.find("en", target):
            return [(source, "en"), ("en", target)]
        raise TranslateError(f"falta el paquete {source}→{target} (descárgalo desde el menú)")

    def missing_for(self, source: str, target: str) -> list[tuple[str, str]]:
        """Packages to download so that ``route`` works (direct if it exists in the catalogue, else via English)."""
        if source == target or self.store.find(source, target):
            return []
        try:
            self.store.resolve_url(source, target)
            return [(source, target)]
        except (TranslateError, OSError, ValueError):
            pass
        out = []
        if not self.store.find(source, "en"):
            out.append((source, "en"))
        if not self.store.find("en", target):
            out.append(("en", target))
        return out

    def _load(self, pair: tuple[str, str]) -> tuple[Any, Any]:
        if pair in self._loaded:
            return self._loaded[pair]
        if not self.available:
            raise TranslateError("falta el runtime de traducción: uv sync --extra translate")
        import ctranslate2  # noqa: PLC0415
        import sentencepiece as spm  # noqa: PLC0415

        pkg = self.store.find(*pair)
        if pkg is None:
            raise TranslateError(f"paquete {pair[0]}→{pair[1]} no descargado")
        sp = spm.SentencePieceProcessor(model_file=str(pkg / "sentencepiece.model"))
        types = ctranslate2.get_supported_compute_types("cpu")
        compute = "int8" if "int8" in types else "auto"
        tr = ctranslate2.Translator(str(pkg / "model"), device="cpu", compute_type=compute, inter_threads=1,
                                    intra_threads=self.threads)
        if len(self._loaded) >= 2:
            self._loaded.pop(next(iter(self._loaded)))
        self._loaded[pair] = (sp, tr)
        return sp, tr

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        import time  # noqa: PLC0415

        route = self.route(source, target)
        out = list(texts)
        t0 = time.monotonic()
        for pair in route:
            sp, tr = self._load(pair)
            idx = [i for i, t in enumerate(out) if t.strip()]
            if not idx:
                break
            toks = [sp.encode(out[i], out_type=str) for i in idx]
            res = tr.translate_batch(toks, beam_size=self.beam_size, max_decoding_length=256)
            for i, r in zip(idx, res, strict=True):
                out[i] = sp.decode(r.hypotheses[0]) if r.hypotheses else out[i]
        self.stats["sentences"] += len(texts)
        self.stats["seconds"] += time.monotonic() - t0
        return out

    def unload(self) -> None:
        self._loaded.clear()
