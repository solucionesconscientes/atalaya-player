"""Offline subtitle translation on CTranslate2 + sentencepiece: Argos Translate packages (every pair, pivot through
English; no ``argostranslate``: it drags stanza/torch, see docs/TRADUCCION.md and ADR-025) and, for es/ca/fr ↔ en, the
OPUS-MT "tc-big" models (``mpvd/subs/opus.py``), chosen per leg by :class:`TranslationRouter`.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.asr.srt import DASH_START, STRONG_END, Segment, cut_words

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
_TURN = re.compile(r"(\s+)[-–—]+\s*(?=\S)")
_LEAD_DASH = re.compile(r"^[-–—]+\s*")
_FINAL = re.compile(r"[.!?…]\s*[\"')\]»”]*$")
PUNCTUATED_RATIO = 0.25   # below this share of cues ending a sentence, a subtitle is treated as unpunctuated (Whisper base)


class TranslateError(RuntimeError):
    pass


# ISO 639-2 codes used by containers (mpv track-list ``lang``) → ISO 639-1 codes of the models and menus.
ISO3 = {"spa": "es", "eng": "en", "cat": "ca", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de", "ita": "it",
        "por": "pt", "glg": "gl", "eus": "eu", "baq": "eu", "nld": "nl", "dut": "nl", "pol": "pl", "rus": "ru",
        "ukr": "uk", "tur": "tr", "ara": "ar", "hin": "hi", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko"}


def normalize_lang(code: str | None) -> str:
    """``spa`` → ``es``, ``en-US``/``pt_BR`` → ``en``/``pt``; ``auto`` stays; anything unusable → ``""``."""
    c = (code or "").strip().lower()
    if c == "auto":
        return c
    c = re.split(r"[-_]", c, maxsplit=1)[0]
    c = ISO3.get(c, c)
    return c if re.fullmatch(r"[a-z]{2,3}", c) else ""


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _SPLIT.split(" ".join(text.split())):
        if out and _ABBR.search(out[-1]):
            out[-1] += " " + chunk
        else:
            out.append(chunk)
    return [s for s in out if s]


def dialogue_turns(text: str) -> list[tuple[str, bool, str]]:
    """Speaker turns of a dialogue cue as ``(separator before the turn, had a dash, body)``.

    A cue is a dialogue when it starts with a dash and another dash follows a line break or a sentence end
    (``- ¿Vienes? - Sí.`` / ``- ¿Vienes?\n- Sí.``), or when a later line starts with a dash (first dash omitted).
    Anything else (``Hola - dijo él``, ``MPV-UOS``) is a single turn."""
    s = text.strip()
    starts_dash = bool(DASH_START.match(s))
    cuts: list[tuple[int, int, str]] = []
    for m in _TURN.finditer(s):
        ws = m.group(1)
        before = s[:m.start()].rstrip()
        if "\n" in ws or (starts_dash and before and STRONG_END.search(before)):
            cuts.append((m.start(), m.end(), "\n" if "\n" in ws else " "))
    if not cuts or (not starts_dash and not any(sep == "\n" for _, _, sep in cuts)):
        return [("", starts_dash, _LEAD_DASH.sub("", s) if starts_dash else s)]
    turns: list[tuple[str, bool, str]] = []
    first = s[:cuts[0][0]]
    turns.append(("", starts_dash, _LEAD_DASH.sub("", first).strip()))
    for k, (_a, b, sep) in enumerate(cuts):
        end = cuts[k + 1][0] if k + 1 < len(cuts) else len(s)
        turns.append((sep, True, s[b:end].strip()))
    return [t for t in turns if t[2]]


def split_dialogue(text: str) -> list[str]:
    """``- ¿Vienes? - Sí.`` → ``["- ¿Vienes?", "- Sí."]`` (each speaker is translated on its own)."""
    turns = dialogue_turns(text)
    if len(turns) < 2:
        return [text.strip()]
    return [("- " if dash else "") + body for _sep, dash, body in turns]


def is_dialogue(text: str) -> bool:
    return len(dialogue_turns(text)) >= 2


def is_punctuated(cues: list[Segment]) -> bool:
    """Whether the subtitle marks sentence ends (human subtitles, Whisper small) or not (Whisper base/tiny)."""
    if not cues:
        return True
    finals = sum(1 for c in cues if _FINAL.search(c.text.strip()))
    return finals >= PUNCTUATED_RATIO * len(cues)


# -- cue grouping ------------------------------------------------------------------------------------------------


@dataclass
class Unit:
    cue_indices: list[int]
    pieces: list[str]          # sentences to translate (in order; dialogue dashes removed)
    seps: list[str] = field(default_factory=list)   # text put before each translated piece ("" before the first)

    def __post_init__(self) -> None:
        if not self.seps:
            self.seps = [""] + [" "] * (len(self.pieces) - 1)

    def join(self, translated: list[str]) -> str:
        out = "".join(sep + t.strip() for sep, t in zip(self.seps, translated, strict=True) if t.strip())
        return "\n".join(" ".join(line.split()) for line in out.split("\n")).strip()


def make_unit(indices: list[int], text: str) -> Unit:
    turns = dialogue_turns(text)
    if len(turns) < 2:
        body = turns[0][2] if turns else text
        pieces = split_sentences(body) or [body]
        seps = [""] + [" "] * (len(pieces) - 1)
        if turns and turns[0][1]:
            seps[0] = "- "
        return Unit(indices, pieces, seps)
    pieces, seps = [], []
    for t_i, (sep, dash, body) in enumerate(turns):
        for s_i, sentence in enumerate(split_sentences(body) or [body]):
            pieces.append(sentence)
            if s_i:
                seps.append(" ")
            else:
                seps.append(("" if t_i == 0 else sep) + ("- " if dash else ""))
    return Unit(indices, pieces, seps)


def group_cues(cues: list[Segment], max_cues: int = 4, max_chars: int = 220, max_gap: float = 1.5,
               pause: float = 0.6, punctuated: bool | None = None) -> list[Unit]:
    """Join consecutive cues into sentences before translating.

    Punctuated subtitles: join until a sentence ends (or a gap > ``max_gap``). Unpunctuated ones (Whisper base): join
    while the pause between cues is < ``pause`` seconds. Always ≤ ``max_cues`` cues / ``max_chars`` characters, and a
    dialogue cue (or one starting a new speaker with a dash) is never glued to its neighbours."""
    if punctuated is None:
        punctuated = is_punctuated(cues)
    units: list[Unit] = []
    i = 0
    while i < len(cues):
        idx = [i]
        text = cues[i].text.strip()
        if not is_dialogue(text):
            while len(idx) < max_cues and i + 1 < len(cues):
                nxt = cues[i + 1].text.strip()
                gap = cues[i + 1].start - cues[i].end
                if DASH_START.match(nxt) or "\n" in nxt or len(text) + len(nxt) + 1 > max_chars:
                    break
                if (punctuated and (_FINAL.search(text) or gap > max_gap)) or (not punctuated and gap >= pause):
                    break
                i += 1
                idx.append(i)
                text = f"{text} {nxt}"
        units.append(make_unit(idx, text))
        i += 1
    return units


def distribute(text: str, weights: list[int]) -> list[str]:
    """Split ``text`` into len(weights) parts roughly proportional to the weights (characters of the original cues),
    moving each cut up to ±3 words to a sentence end, comma or conjunction and never after an article/preposition.
    With fewer words than parts, the first parts get one word each and the rest are empty."""
    words = text.split()
    n = len(weights)
    if n == 1:
        return [text]
    if len(words) <= n:
        return [words[k] if k < len(words) else "" for k in range(n)]
    bounds = [0, *cut_words(words, [max(1, w) for w in weights]), len(words)]
    return [" ".join(words[bounds[k]:bounds[k + 1]]) for k in range(n)]


def translate_cues(cues: list[Segment], translate: Callable[[list[str]], list[str]], batch: int = 32,
                   progress: Callable[[float], None] | None = None) -> list[Segment]:
    """Translate cue texts keeping every timing. When a sentence spread over several cues comes back too short to
    fill all of them, the empty cue's time goes to its neighbour instead of showing a blank (or untranslated) line."""
    units = group_cues(cues)
    pieces = [p for u in units for p in u.pieces]
    translated: list[str] = []
    for k in range(0, len(pieces), batch):
        translated.extend(translate(pieces[k:k + batch]))
        if progress is not None:
            progress(min(1.0, (k + batch) / max(1, len(pieces))))
    out: list[Segment] = []
    pos = 0
    for u in units:
        n = len(u.pieces)
        joined = u.join(translated[pos:pos + n])
        pos += n
        if len(u.cue_indices) == 1:
            c = cues[u.cue_indices[0]]
            out.append(Segment(c.start, c.end, joined or c.text))
            continue
        parts = distribute(joined, [len(cues[i].text) for i in u.cue_indices])
        pending_start: float | None = None
        first = len(out)
        for i, part in zip(u.cue_indices, parts, strict=True):
            c = cues[i]
            if part:
                out.append(Segment(c.start if pending_start is None else pending_start, c.end, part))
                pending_start = None
            elif len(out) > first:
                out[-1].end = c.end
            elif pending_start is None:
                pending_start = c.start
        if len(out) == first:   # nothing came back at all: keep the originals
            out.extend(Segment(cues[i].start, cues[i].end, cues[i].text) for i in u.cue_indices)
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
        # Half the cores, not all but one: translating and transcribing are the two things that happen together (the
        # subtitle menu invites exactly that, and the post-download chain does both), and with cpu-1 each they asked for
        # six threads on a four-core laptop while mpv was still decoding. MPV_UOS_TRANSLATE_THREADS overrides it.
        self.threads = threads or max(1, min(cpu // 2, 8))
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


# -- engines: Argos (fast, every pair) and OPUS-MT big (quality, es/ca/fr ↔ en) -------------------------------------

ENGINES = ("auto", "argos", "opus-big")
ENGINE_NAMES = {"argos": "Rápido (Argos)", "opus-big": "Calidad (OPUS-MT)"}


@dataclass(frozen=True)
class Leg:
    engine: str       # "argos" | "opus-big"
    source: str
    target: str

    @property
    def key(self) -> str:
        return f"{self.engine}:{self.source}_{self.target}"


class TranslationRouter:
    """Chooses an engine per translation leg: ``opus-big`` where OPUS-MT covers the pair (``auto``: only if it is
    already downloaded), Argos for the rest and for pivots through English (``fr→es`` = Argos fr→en + OPUS en→es)."""

    def __init__(self, argos: ArgosEngine, opus: Any):
        self.argos = argos
        self.opus = opus            # mpvd.subs.opus.OpusEngine

    @property
    def available(self) -> bool:
        return self.argos.available

    def _leg(self, source: str, target: str, engine: str) -> tuple[Leg, bool]:
        from mpvd.subs.opus import model_for  # noqa: PLC0415

        if engine in ("auto", "opus-big") and model_for(source, target) is not None:
            present = self.opus.store.find(source, target) is not None
            if present or engine == "opus-big":
                return Leg("opus-big", source, target), present
        return Leg("argos", source, target), self.argos.store.find(source, target) is not None

    def plan(self, source: str, target: str, engine: str = "auto") -> list[Leg]:
        if engine not in ENGINES:
            raise TranslateError(f"motor desconocido {engine!r} (auto, argos, opus-big)")
        if source == target:
            return []
        leg, ok = self._leg(source, target, engine)
        if ok:
            return [leg]
        if source != "en" and target != "en":
            (a, ok_a), (b, ok_b) = self._leg(source, "en", engine), self._leg("en", target, engine)
            if ok_a and ok_b:
                return [a, b]
        raise TranslateError(f"falta el modelo {source}→{target} (descárgalo desde el menú)")

    def missing_for(self, source: str, target: str, engine: str = "auto") -> list[Leg]:
        """Models to download so that ``plan`` works (the direct pair if some engine has it, else via English)."""
        if source == target:
            return []
        try:
            self.plan(source, target, engine)
            return []
        except TranslateError:
            pass
        leg, _ok = self._leg(source, target, engine)
        if leg.engine == "opus-big" or source == "en" or target == "en":
            return [leg]
        try:
            self.argos.store.resolve_url(source, target)
            return [leg]
        except (TranslateError, OSError, ValueError):
            pass
        return [lg for lg, ok in (self._leg(source, "en", engine), self._leg("en", target, engine)) if not ok]

    def translate(self, texts: list[str], legs: list[Leg]) -> list[str]:
        out = list(texts)
        for leg in legs:
            eng = self.opus if leg.engine == "opus-big" else self.argos
            out = eng.translate(out, leg.source, leg.target)
        return out

    def beams(self, legs: list[Leg]) -> dict[str, int]:
        return {leg.engine: (self.opus.beam_size if leg.engine == "opus-big" else self.argos.beam_size) for leg in legs}

    def unload(self) -> None:
        self.opus.unload()
