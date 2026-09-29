"""whisper.cpp engine: one ``whisper-cli`` process per audio chunk, JSON output parsed into segments.

Verified against whisper.cpp 1.9.x (``examples/cli/cli.cpp``): ``-oj -of <base>`` writes ``<base>.json`` with
``result.language`` and ``transcription[] = {timestamps:{from,to}, offsets:{from,to} (ms), text}``.
A global lock serialises whisper processes: on a 4-core laptop two concurrent decoders only thrash.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.asr.models import VAD_MODEL, ModelError, ModelStore, find_binary
from mpvd.asr.srt import Segment, clean_text

log = logging.getLogger("mpvd.asr")


class EngineError(RuntimeError):
    pass


@dataclass
class TranscribeResult:
    segments: list[Segment]
    language: str
    elapsed: float
    audio_seconds: float
    model: str
    argv: list[str] = field(default_factory=list, repr=False)

    @property
    def rtf(self) -> float:
        return self.elapsed / self.audio_seconds if self.audio_seconds > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"segments": [s.to_dict() for s in self.segments], "language": self.language, "elapsed": self.elapsed,
                "audio_seconds": self.audio_seconds, "rtf": round(self.rtf, 3), "model": self.model}


def parse_cli_json(data: dict[str, Any]) -> tuple[list[Segment], str]:
    segs: list[Segment] = []
    for row in data.get("transcription") or []:
        off = row.get("offsets") or {}
        try:
            start, end = float(off["from"]) / 1000.0, float(off["to"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        text = clean_text(str(row.get("text", "")))
        if text and end > start:
            segs.append(Segment(start, end, text))
    lang = str((data.get("result") or {}).get("language") or (data.get("params") or {}).get("language") or "")
    return segs, lang


def library_env(cli: Path) -> dict[str, str]:
    env = dict(os.environ)
    var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    if sys.platform != "win32":
        prev = env.get(var, "")
        env[var] = str(cli.parent) + (os.pathsep + prev if prev else "")
    return env


class WhisperEngine:
    def __init__(self, models: ModelStore, root: Path | None = None, threads: int | None = None,
                 cli: Path | None = None, use_vad: bool = True, beam_size: int | None = None,
                 best_of: int | None = None, no_fallback: bool = False):
        self.models = models
        self.root = root
        self.cli = cli or find_binary("whisper-cli", root)
        cpu = os.cpu_count() or 2
        self.threads = threads or (max(1, min(cpu - 1, 8)) if cpu > 2 else max(1, cpu))
        self.use_vad = use_vad
        self.beam_size = beam_size
        self.best_of = best_of
        self.no_fallback = no_fallback
        self._lock = asyncio.Lock()
        self._version: str | None = None
        self.stats = {"runs": 0, "elapsed": 0.0, "audio": 0.0}

    @property
    def available(self) -> bool:
        return self.cli is not None

    def version(self) -> str:
        if self._version is None:
            self._version = "unknown"
            if self.cli is not None:
                try:
                    import subprocess  # noqa: PLC0415

                    out = subprocess.run([str(self.cli), "--version"], capture_output=True, text=True, timeout=10,
                                         env=library_env(self.cli), check=False)
                    txt = (out.stdout or out.stderr).strip().splitlines()
                    self._version = txt[0].strip() if txt else "unknown"
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return self._version

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available, "cli": str(self.cli) if self.cli else None, "version": self.version(),
            "threads": self.threads, "vad": self.use_vad and self.models.find(VAD_MODEL) is not None,
            "runs": self.stats["runs"],
            "rtf": round(self.stats["elapsed"] / self.stats["audio"], 3) if self.stats["audio"] else None,
        }

    def argv(self, wav: Path, model_path: Path, out_base: Path, language: str | None, translate: bool = False,
             prompt: str | None = None, duration_ms: int | None = None) -> list[str]:
        if self.cli is None:
            raise EngineError("whisper-cli not found (vendor/whisper/bin or PATH)")
        args = [str(self.cli), "-m", str(model_path), "-f", str(wav), "-t", str(self.threads), "-l", language or "auto",
                "-oj", "-of", str(out_base), "-np"]
        if translate:
            args.append("-tr")
        if prompt:
            args += ["--prompt", prompt]
        if duration_ms:
            args += ["-d", str(int(duration_ms))]
        if self.beam_size is not None:
            args += ["-bs", str(self.beam_size)]
        if self.best_of is not None:
            args += ["-bo", str(self.best_of)]
        if self.no_fallback:
            args.append("-nf")
        vad = self.models.find(VAD_MODEL) if self.use_vad else None
        if vad is not None:
            args += ["--vad", "--vad-model", str(vad)]
        return args

    async def transcribe(self, wav: Path, model: str, language: str | None = None, translate: bool = False,
                         prompt: str | None = None, audio_seconds: float | None = None,
                         timeout: float = 600.0) -> TranscribeResult:
        model_path = self.models.find(model)
        if model_path is None:
            raise ModelError(f"model {model!r} is not downloaded")
        out_base = wav.with_suffix("")
        out_json = out_base.with_suffix(".json")
        args = self.argv(wav, model_path, out_base, language, translate, prompt)
        if audio_seconds is None:
            from mpvd.asr.audio import wav_duration  # noqa: PLC0415

            audio_seconds = wav_duration(wav)
        async with self._lock:
            t0 = time.monotonic()
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.PIPE, env=library_env(self.cli))
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
                raise
            elapsed = time.monotonic() - t0
        if proc.returncode != 0 or not out_json.exists():
            tail = err.decode("utf-8", "replace").strip()[-400:]
            raise EngineError(f"whisper-cli failed ({proc.returncode}): {tail}")
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise EngineError(f"bad JSON from whisper-cli: {exc}") from exc
        finally:
            out_json.unlink(missing_ok=True)
        segments, lang = parse_cli_json(data)
        self.stats["runs"] += 1
        self.stats["elapsed"] += elapsed
        self.stats["audio"] += audio_seconds
        log.debug("whisper %s: %.1fs audio in %.2fs (rtf %.2f), %d segments, lang=%s", model, audio_seconds, elapsed,
                  elapsed / audio_seconds if audio_seconds else 0, len(segments), lang)
        return TranscribeResult(segments, lang, elapsed, audio_seconds, model, args)
