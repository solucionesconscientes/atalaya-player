"""Modelo de lenguaje local para el resumen en prosa (H38, nivel 2): llama.cpp por procesos, como whisper.cpp.

Decisiones que vienen del banco de pruebas (docs/BENCHMARKS_LLM.md) y del hardware objetivo (4 núcleos, sin GPU):

* **Un proceso por petición** (`llama-cli -st`), no un servidor: un resumen se pide de vez en cuando y tener 1 GB de
  RAM ocupados y un puerto abierto todo el día para eso no se sostiene. Es el mismo razonamiento que ADR-024 para
  whisper-cli.
* **El modelo se descarga cuando se pide**, verificado por SHA-256, igual que los de whisper (ADR-023). El binario de
  llama.cpp se vendoriza con `tools/vendor_llama.sh` desde la release oficial, fijado en vendor.lock.
* **Los minutos no los pone el modelo**: el prompt lleva el índice de nivel 1 con sus marcas y, al volver, cada `[mm:ss]`
  se comprueba contra el subtítulo (`recap.validate_marks`). Lo que el modelo invente, se quita.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mpvd import priority as prio

log = logging.getLogger("mpvd.llm")

GGUF_MAGIC = b"GGUF"
CTX = 4096
TIMEOUT = 1800.0
# Lo de «cada frase EMPIEZA con su marca» no es un capricho: sin eso, gemma-3-1b escribía el resumen corto sin ninguna
# marca, y un resumen sin minutos no sirve para lo que se quiere (pulsar y saltar). Con eso, 5 marcas de 5 válidas.
SYSTEM = {
    "es": ("Eres un ayudante que resume vídeos en español. Escribes solo con la información que te dan. Cada frase del "
           "resumen EMPIEZA con una de las marcas [mm:ss] del índice, tal cual, y nunca te inventas una marca ni un "
           "dato."),
    "en": ("You summarise videos in English. You only use the information you are given. Every sentence STARTS with one "
           "of the [mm:ss] marks from the outline, exactly as given, and you never invent a mark or a fact."),
    "fr": ("Tu résumes des vidéos en français. Tu n'utilises que les informations fournies. Chaque phrase COMMENCE par "
           "une des marques [mm:ss] de l'index, telle quelle, et tu n'invente jamais une marque ni un fait."),
}
ASK = {
    "es": ("Escribe un resumen del vídeo en español, en prosa, de {cuantas}. Cada frase empieza con su marca [mm:ss] "
           "del índice, por ejemplo: «[2:15] Aquí explica…». No uses rangos como [1:00-2:00]. No añadas títulos ni "
           "listas."),
    "en": ("Write a summary of the video in English, in prose, {cuantas}. Every sentence starts with its [mm:ss] mark "
           "from the outline, like «[2:15] Here he explains…». No ranges like [1:00-2:00]. No headings, no lists."),
    "fr": ("Écris un résumé de la vidéo en français, en prose, de {cuantas}. Chaque phrase commence par sa marque "
           "[mm:ss] de l'index, par exemple « [2:15] Ici il explique… ». Pas de plages comme [1:00-2:00]. Pas de "
           "titres ni de listes."),
}
LENGTHS = {
    "short": {"es": "entre 3 y 4 frases", "en": "3 to 4 sentences", "fr": "3 à 4 phrases", "tokens": 260},
    "long": {"es": "entre 8 y 12 frases", "en": "8 to 12 sentences", "fr": "8 à 12 phrases", "tokens": 700},
}


@dataclass(frozen=True)
class LlmModel:
    name: str
    url: str
    sha256: str
    size_bytes: int
    note: str

    @property
    def filename(self) -> str:
        return self.name + ".gguf"

    @property
    def size_mb(self) -> int:
        return round(self.size_bytes / 2**20)


# Medidos aquí el 2026-10-01 con 15 min de una charla real en español (docs/BENCHMARKS_LLM.md). Las sumas son de los
# ficheros que se probaron. `qwen2.5-1.5b-instruct` se descartó tras medirlo: repetía frases enteras y escribía rangos
# «[0:04-0:36]» en vez de instantes, y además era más lento que gemma.
CATALOG: dict[str, LlmModel] = {m.name: m for m in (
    LlmModel("gemma-3-1b-it-q4_k_m",
             "https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
             "8270790f3ab69fdfe860b7b64008d9a19986d8df7e407bb018184caa08798ebd", 806058272,
             "rápido: 40 s por resumen en un portátil de 4 núcleos"),
    LlmModel("qwen2.5-3b-instruct-q4_k_m",
             "https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf",
             "9c9f56a391a3abbd5b89d0245bf6106081bcc3173119d4229235dd9d23253f94", 1929903264,
             "escribe mejor, tarda el doble (unos 2 min)"),
)}
DEFAULT_MODEL = "gemma-3-1b-it-q4_k_m"     # lo decidió el banco de pruebas; ver docs/BENCHMARKS_LLM.md


class LlmError(RuntimeError):
    pass


def is_gguf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == GGUF_MAGIC
    except OSError:
        return False


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_binary(root: Path | None) -> Path | None:
    """``llama-cli`` de $MPV_UOS_LLAMA_BIN, vendor/llama/bin o el PATH (``.exe`` en Windows)."""
    name = "llama-cli.exe" if sys.platform == "win32" else "llama-cli"
    env = os.environ.get("MPV_UOS_LLAMA_BIN")
    candidates = [Path(env) / name] if env else []
    if root is not None:
        candidates.append(root / "vendor" / "llama" / "bin" / name)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which(name)
    return Path(found) if found else None


def model_dirs(root: Path | None, data_dir: Path) -> list[Path]:
    env = os.environ.get("MPV_UOS_LLM_MODELS")
    dirs: list[Path] = [Path(env)] if env else []
    if root is not None:
        dirs.append(root / "vendor" / "models" / "llm")
    dirs.append(data_dir / "models" / "llm")
    return dirs


class LlmStore:
    def __init__(self, dirs: list[Path]):
        self.dirs = [Path(d) for d in dirs]

    def find(self, name: str) -> Path | None:
        model = CATALOG.get(name)
        if model is None:
            return None
        for d in self.dirs:
            p = d / model.filename
            if p.is_file() and p.stat().st_size > 1024 and is_gguf(p):
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
        raise LlmError("no hay ninguna carpeta donde guardar el modelo")

    def present(self) -> list[str]:
        return [n for n in CATALOG if self.find(n) is not None]

    def catalogue(self) -> list[dict[str, Any]]:
        return [{"name": m.name, "size_mb": m.size_mb, "note": m.note, "present": self.find(m.name) is not None,
                 "default": m.name == DEFAULT_MODEL} for m in CATALOG.values()]

    async def download(self, name: str, progress: Callable[[float, str], None] | None = None) -> Path:
        model = CATALOG.get(name)
        if model is None:
            raise LlmError(f"modelo desconocido {name!r}")
        existing = self.find(name)
        if existing is not None:
            return existing
        dest = self.download_dir() / model.filename
        part = dest.with_suffix(".gguf.part")

        def fetch() -> None:
            req = urllib.request.Request(model.url, headers={"User-Agent": "mpv-uos/llm"})
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:  # noqa: S310 - URL fijada
                total = int(resp.headers.get("Content-Length") or model.size_bytes)
                done = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(min(0.98, done / total if total else 0.0),
                                 f"{done / 2**20:.0f} / {total / 2**20:.0f} MiB")

        try:
            await asyncio.to_thread(fetch)
            if await asyncio.to_thread(sha256_of, part) != model.sha256:
                raise LlmError(f"SHA-256 incorrecto para {model.url}")
            if not is_gguf(part):
                raise LlmError("lo descargado no es un modelo GGUF")
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


# La interfaz de chat de llama-cli escribe en la MISMA salida su cartel, el eco del prompt (precedido de «> ») y, al
# final, una línea de estadísticas. Lo único que es contrato estable es ese eco: el prompt se repite tal cual, así que
# la respuesta es lo que hay entre la última línea del prompt y la línea de estadísticas. Verificado contra b11319.
STATS_RE = re.compile(r"\[ Prompt:\s*([\d.,]+) t/s \| Generation:\s*([\d.,]+) t/s \]")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except (TypeError, ValueError):
        return None


INDEX_LINE = re.compile(r"^(?:\s*-\s*\[\d{1,2}:\d{2}\]|\s*\d+\.\s*\[\d{1,2}:\d{2}\]).*$", re.M)
ECHO_LINE = re.compile(r"^>.*$", re.M)


def clean_output(raw: str, prompt: str = "") -> tuple[str, float | None]:
    """(lo que escribió el modelo, tokens por segundo). Nunca devuelve el cartel ni el eco del prompt.

    Estructura real de la salida de `llama-cli -st` (comprobada con b11319): cartel y datos del modelo, la lista de
    órdenes de la interfaz, el eco del prompt empezando por «> » —**recortado**, acabando en «(truncated)» si es largo—,
    la respuesta, y una línea `[ Prompt: … t/s | Generation: … t/s ]`. La respuesta es lo que hay entre el final del eco
    y esa línea. Lo de «(truncated)» es la pista más fiable, porque el eco no se puede comparar con el prompt entero.
    """
    stats = STATS_RE.search(raw)
    tps = _number(stats.group(2)) if stats else None
    body = raw[:stats.start()] if stats else raw
    cut = 0
    t = body.rfind("(truncated)")
    if t >= 0:
        cut = t + len("(truncated)")
    for rx in (INDEX_LINE, ECHO_LINE):
        for m in rx.finditer(body):
            cut = max(cut, m.end())
    tail = " ".join((prompt or "").split()).split()[-6:]
    if tail:
        j = body.rfind(" ".join(tail))
        if j >= 0:
            cut = max(cut, j + len(" ".join(tail)))
    body = body[cut:] if cut else body
    body = re.sub(r"\n?Exiting\.\.\.\s*$", "", body)
    return body.strip(), tps


class LlmEngine:
    """Un proceso ``llama-cli`` por petición. No se queda nada cargado entre resúmenes."""

    def __init__(self, store: LlmStore, binary: Path | None, threads: int | None = None):
        self.store = store
        self.binary = binary
        cpu = os.cpu_count() or 2
        self.threads = threads or max(1, min(cpu - 1, 8))
        self.lock = asyncio.Lock()      # un solo modelo en memoria a la vez (≈1 GB)

    @property
    def available(self) -> bool:
        return self.binary is not None

    def status(self) -> dict[str, Any]:
        return {"binary": str(self.binary) if self.binary else None, "available": self.available,
                "threads": self.threads, "ctx": CTX, "default_model": DEFAULT_MODEL,
                "models": self.store.catalogue()}

    def argv(self, model: Path, system: str, prompt: str, tokens: int) -> list[str]:
        # `--no-display-prompt` y `--log-disable`: sin ellos, llama-cli escribe su cartel y repite el prompt (recortado)
        # en la misma salida, y eso acabó dentro del resumen la primera vez que se probó de verdad.
        return [str(self.binary), "-m", str(model), "-st", "--no-warmup", "--no-display-prompt", "--log-disable",
                "--color", "off", "-sys", system, "-p", prompt,
                "-n", str(tokens), "-t", str(self.threads), "-c", str(CTX), "--temp", "0.3", "--top-p", "0.9"]

    async def generate(self, model_name: str, system: str, prompt: str, tokens: int,
                       timeout: float = TIMEOUT) -> dict[str, Any]:
        if not self.available:
            raise LlmError("falta llama-cli: ejecuta tools/vendor_llama.sh")
        model = self.store.find(model_name)
        if model is None:
            raise LlmError(f"el modelo {model_name} no está descargado")
        env = dict(os.environ)
        if self.binary is not None:
            libs = str(self.binary.parent)
            key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
            env[key] = libs + (os.pathsep + env[key] if env.get(key) else "")
        async with self.lock:
            # H69 · nunca compite con la reproducción
            proc = await asyncio.create_subprocess_exec(*self.argv(model, system, prompt, tokens),
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, env=env,
                                                        **prio.background(nice=prio.NICE_HEAVY))
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise LlmError("el modelo tardó demasiado") from None
        if proc.returncode != 0:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            raise LlmError("llama-cli falló: " + " / ".join(tail))
        text, tps = clean_output((out or b"").decode("utf-8", "replace"), prompt)
        return {"text": text, "model": model_name, "tokens_per_second": tps}


def prompt_from_outline(sections: list[dict[str, Any]], length: str = "short", language: str = "es") -> tuple[str, str, int]:
    """(sistema, prompt, tokens) para un índice de nivel 1. Las marcas del prompt son las del subtítulo."""
    lang = language if language in SYSTEM else "es"
    spec = LENGTHS.get(length) or LENGTHS["short"]
    lines = []
    for i, sec in enumerate(sections, 1):
        lines.append(f"{i}. [{int(sec['start']) // 60}:{int(sec['start']) % 60:02d}] {sec.get('title', '')}")
        for p in sec.get("points", []):
            lines.append(f"   - [{int(p['start']) // 60}:{int(p['start']) % 60:02d}] {p.get('text', '')}")
    head = {"es": "ÍNDICE DEL VÍDEO (con los minutos reales):", "en": "VIDEO OUTLINE (with the real minutes):",
            "fr": "INDEX DE LA VIDÉO (avec les minutes réelles) :"}[lang]
    ask = ASK[lang].format(cuantas=spec[lang])
    return SYSTEM[lang], f"{head}\n" + "\n".join(lines) + f"\n\n{ask}", int(spec["tokens"])
