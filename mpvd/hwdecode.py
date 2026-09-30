"""Which video codecs this machine decodes in hardware (H31, ADR-053), to label formats «fluido en tu equipo» /
«exigente (por procesador)» and to prefer them for playback and downloads.

Linux: ``vainfo`` (VA-API) decode entrypoints (``VAEntrypointVLD``). Windows (D3D11VA/DXVA2) and macOS (VideoToolbox)
are documented in docs/PLATAFORMAS.md but not probed yet: the result is then ``unknown`` and nothing is labelled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from typing import Any

# VA-API profile (without the "VAProfile" prefix) → codec key
VA_PROFILES: list[tuple[str, str]] = [
    ("H264", "h264"),
    ("HEVCMain10", "hevc10"),
    ("HEVCMain", "hevc"),
    ("VP9Profile2", "vp9_10"),
    ("VP9Profile0", "vp9"),
    ("AV1Profile0", "av1"),
    ("VP8", "vp8"),
    ("MPEG2", "mpeg2"),
    ("VC1", "vc1"),
]

# yt-dlp's codec preference order (README, "Sorting Formats"): av01 > vp9.2 > vp9 > h265 > h264 > vp8 > h263 > theora.
# For `-S vcodec:X` («prefer codecs no better than X»), X = the best codec decoded in hardware.
YTDLP_ORDER: list[tuple[str, str]] = [("av1", "av01"), ("vp9", "vp9"), ("hevc", "h265"), ("h264", "h264")]

NAMES = {"h264": "H.264", "hevc": "HEVC", "hevc10": "HEVC 10 bits", "vp9": "VP9", "vp9_10": "VP9 10 bits",
         "av1": "AV1", "vp8": "VP8", "mpeg2": "MPEG-2", "vc1": "VC-1"}


def parse_vainfo(text: str) -> set[str]:
    """Codec keys with a decode (VLD) entrypoint in ``vainfo`` output."""
    out: set[str] = set()
    for m in re.finditer(r"VAProfile(\w+)\s*:\s*VAEntrypointVLD\b", text):
        profile = m.group(1)
        for prefix, key in VA_PROFILES:
            if profile.startswith(prefix):
                out.add(key)
                break
    return out


def codec_key(vcodec: str | None) -> str | None:
    """yt-dlp/ffprobe codec string → codec key (``avc1.64001F`` → h264, ``vp09.02.51.10`` → vp9_10…)."""
    v = (vcodec or "").strip().lower()
    if not v or v == "none":
        return None
    if v.startswith(("avc", "h264")):
        return "h264"
    if v.startswith(("hvc1", "hev1", "hevc", "h265")):
        # hvc1.2.* / hev1.2.* = Main 10 profile
        return "hevc10" if re.match(r"^(hvc1|hev1)\.2\.", v) else "hevc"
    if v.startswith(("vp09", "vp9")):
        return "vp9_10" if v.startswith("vp09.02") or v.startswith("vp09.03") else "vp9"
    if v.startswith(("av01", "av1")):
        return "av1"
    if v.startswith("vp8"):
        return "vp8"
    if v.startswith(("mp2v", "mpeg2")):
        return "mpeg2"
    return v.split(".")[0]


def supports(hw: set[str], key: str | None) -> bool:
    if key is None:
        return False
    if key in hw:
        return True
    # a 10-bit decoder also decodes 8 bits
    return (key == "hevc" and "hevc10" in hw) or (key == "vp9" and "vp9_10" in hw)


def classify(hw: set[str] | None, vcodec: str | None) -> str | None:
    """``hw`` (decoded by the graphics chip), ``cpu`` (by the processor) or None (audio-only, or unknown machine)."""
    key = codec_key(vcodec)
    if key is None or hw is None:
        return None
    return "hw" if supports(hw, key) else "cpu"


LABELS = {"hw": "fluido en tu equipo", "cpu": "exigente (por procesador)"}


def sort_codec(hw: set[str] | None) -> str:
    """The ``-S vcodec:X`` limit: the most efficient codec the machine decodes in hardware; H.264 when none is known
    (the cheapest to decode on a processor)."""
    for key, name in YTDLP_ORDER:
        if hw and supports(hw, key):
            return name
    return "h264"


def playback_format(hw: set[str] | None, max_height: int = 1080) -> str:
    """``ytdl-format`` for playback: best video ≤ max_height in the preferred codec family, best audio."""
    h = f"[height<=?{int(max_height)}]"
    prefer = {"av01": "[vcodec^=av01]", "vp9": "[vcodec~='^(vp0?9)']", "h265": "[vcodec~='^(hev1|hvc1|hevc|h265)']",
              "h264": "[vcodec~='^(avc1|h264)']"}
    order = [name for key, name in YTDLP_ORDER if hw and supports(hw, key)]
    if "h264" not in order:
        order.append("h264")
    parts = [f"bestvideo{h}{prefer[n]}+bestaudio" for n in order]
    return "/".join(parts + [f"bestvideo{h}+bestaudio", "best"])


@lru_cache(maxsize=1)
def detect() -> dict[str, Any]:
    """``{source, hw: [keys], names: [...], sort: 'h265', note}``; ``hw`` is None when unknown.
    ``$MPV_UOS_HWDECODE`` (``h264,hevc`` · ``none`` · ``unknown``) replaces the probe (tests, odd drivers)."""
    env = os.environ.get("MPV_UOS_HWDECODE")
    if env is not None:
        if env.strip() == "unknown":
            return {"source": "env", "hw": None, "names": [], "sort": "h264", "note": ""}
        keys = {k.strip() for k in env.split(",") if k.strip() and k.strip() != "none"}
        return {"source": "env", "hw": sorted(keys), "names": [NAMES.get(k, k) for k in sorted(keys)],
                "sort": sort_codec(keys), "note": ""}
    if sys.platform.startswith("linux") and shutil.which("vainfo"):
        try:
            res = subprocess.run(["vainfo"], capture_output=True, text=True, timeout=8)
            hw = parse_vainfo(res.stdout + res.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"source": "vainfo", "hw": None, "names": [], "sort": "h264", "note": f"vainfo falló: {exc}"}
        return {"source": "vainfo", "hw": sorted(hw), "names": [NAMES.get(k, k) for k in sorted(hw)],
                "sort": sort_codec(hw), "note": "" if hw else "VA-API sin decodificación de vídeo"}
    note = {"win32": "D3D11VA/DXVA2 sin comprobar (docs/PLATAFORMAS.md)",
            "darwin": "VideoToolbox sin comprobar (docs/PLATAFORMAS.md)"}.get(sys.platform, "sin vainfo")
    return {"source": "none", "hw": None, "names": [], "sort": "h264", "note": note}


def hw_set() -> set[str] | None:
    hw = detect()["hw"]
    return set(hw) if hw is not None else None
