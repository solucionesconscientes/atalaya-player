"""What the GPU can encode through VA-API (Linux), read from ``vainfo`` (H20).

``vainfo --display drm --device /dev/dri/renderDN`` lists ``VAProfile… : VAEntrypoint…`` pairs; encoding entry points
are ``VAEntrypointEncSlice`` (normal) and ``VAEntrypointEncSliceLP`` (low power: some Intel iGPUs only have this one,
e.g. the iHD driver on this laptop offers H.264 encoding only as EncSliceLP and no HEVC encoding). ffmpeg's
``h264_vaapi``/``hevc_vaapi`` must exist too. Without vainfo (Windows, macOS, no driver), a timeout or
``MPV_UOS_VAAPI=0`` everything is encoded on the CPU. Test hooks: ``MPV_UOS_VAINFO`` (another vainfo executable) and
``MPV_UOS_VAAPI_DEVICE`` (render node).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from typing import Any

from mpvd.convert.presets import HwPlan

_LINE = re.compile(r"^\s*(VAProfile\w+)\s*:\s*(VAEntrypoint\w+)", re.MULTILINE)
_DRIVER = re.compile(r"Driver version:\s*(.+)")
# VA profile prefix → codec name used by the presets (8-bit profiles only: the presets encode yuv420p)
PROFILE_CODECS = (("VAProfileH264", "h264"), ("VAProfileHEVCMain10", ""), ("VAProfileHEVCMain", "hevc"),
                  ("VAProfileVP9Profile0", "vp9"), ("VAProfileAV1Profile0", "av1"))
ENCODE_ENTRYPOINTS = ("VAEntrypointEncSlice", "VAEntrypointEncSliceLP")
FFMPEG_ENCODERS = {"h264": "h264_vaapi", "hevc": "hevc_vaapi"}   # the ones the presets use
VAINFO_TIMEOUT = 5.0


def parse_vainfo(text: str) -> dict[str, Any]:
    """{"driver", "profiles": {profile: [entrypoints]}, "encode": {codec: {"low_power": bool, "profiles": [...]}}}."""
    profiles: dict[str, list[str]] = {}
    for prof, entry in _LINE.findall(text or ""):
        profiles.setdefault(prof, []).append(entry)
    encode: dict[str, dict[str, Any]] = {}
    for prof, entries in profiles.items():
        codec = next((c for prefix, c in PROFILE_CODECS if prof.startswith(prefix)), "")
        enc = [e for e in entries if e in ENCODE_ENTRYPOINTS]
        if not codec or not enc:
            continue
        slot = encode.setdefault(codec, {"normal": False, "low_power_entry": False, "profiles": []})
        slot["normal"] = slot["normal"] or "VAEntrypointEncSlice" in enc
        slot["low_power_entry"] = slot["low_power_entry"] or "VAEntrypointEncSliceLP" in enc
        slot["profiles"].append(prof)
    for slot in encode.values():
        # only the low-power entry point: ffmpeg must be told (-low_power 1)
        slot["low_power"] = slot["low_power_entry"] and not slot["normal"]
    m = _DRIVER.search(text or "")
    return {"driver": m.group(1).strip() if m else "", "profiles": profiles, "encode": encode}


def render_devices() -> list[str]:
    env = os.environ.get("MPV_UOS_VAAPI_DEVICE")
    if env:
        return [env]
    return sorted(glob.glob("/dev/dri/renderD*"))


def ffmpeg_encoders(ffmpeg: str | None = None, timeout: float = 10.0) -> set[str]:
    ff = ffmpeg or shutil.which("ffmpeg")
    if not ff:
        return set()
    try:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=timeout,
                             check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {m.group(1) for m in re.finditer(r"^\s*[VAS][\w.]{5}\s+(\S+)", out, re.MULTILINE)}


def detect(timeout: float = VAINFO_TIMEOUT) -> dict[str, Any]:
    """VA-API encode capabilities of this machine (blocking: call it in a thread; mpvd caches the result)."""
    result: dict[str, Any] = {"available": False, "device": "", "driver": "", "codecs": [], "encode": {},
                              "reason": ""}
    if os.environ.get("MPV_UOS_VAAPI", "1") == "0":
        result["reason"] = "desactivado (MPV_UOS_VAAPI=0)"
        return result
    vainfo = os.environ.get("MPV_UOS_VAINFO") or shutil.which("vainfo")
    if not vainfo:
        result["reason"] = "vainfo no está instalado"
        return result
    devices = render_devices()
    if not devices:
        result["reason"] = "no hay /dev/dri/renderD*"
        return result
    encoders = ffmpeg_encoders()
    for dev in devices:
        try:
            out = subprocess.run([vainfo, "--display", "drm", "--device", dev], capture_output=True, text=True,
                                 timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            result["reason"] = f"vainfo no respondió en {timeout:.0f} s"
            continue
        except OSError as exc:
            result["reason"] = f"vainfo: {exc}"
            continue
        caps = parse_vainfo(out.stdout + "\n" + out.stderr)
        usable = {c: v for c, v in caps["encode"].items() if FFMPEG_ENCODERS.get(c) in encoders}
        if out.returncode != 0 or not usable:
            result["reason"] = "la tarjeta gráfica no codifica H.264/H.265" if out.returncode == 0 \
                else f"vainfo terminó con {out.returncode}"
            continue
        result.update({"available": True, "device": dev, "driver": caps["driver"], "codecs": sorted(usable),
                       "encode": usable, "reason": ""})
        return result
    return result


def plan_for(caps: dict[str, Any], codec: str | None) -> HwPlan | None:
    """The VA-API plan for ``codec`` when this machine can encode it, else None (CPU)."""
    if not codec or not caps.get("available"):
        return None
    enc = (caps.get("encode") or {}).get(codec)
    if not enc:
        return None
    return HwPlan(device=str(caps["device"]), codec=codec, low_power=bool(enc.get("low_power")))
