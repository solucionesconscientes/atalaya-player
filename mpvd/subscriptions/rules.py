"""When subscriptions may download: a daily time window («de 1:00 a 7:00»), a limit per window (downloads or MB) and
the pause on metered connections (NetworkManager). Pure functions with the clock passed in, so tests control time.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

# a window is (start, end) in minutes after midnight; end < start crosses midnight; None = always
Window = tuple[int, int]

_TIME = r"(\d{1,2})(?:[:.h](\d{2}))?\s*(?:h|horas?)?"
WINDOW_RE = re.compile(rf"^\s*(?:de|desde|from)?\s*{_TIME}\s*(?:-|–|a|al|hasta|to)\s*(?:las\s+)?{_TIME}\s*$",
                       re.IGNORECASE)
ALWAYS = ("", "siempre", "always", "todo el día", "todo el dia", "24h", "0")


class WindowError(ValueError):
    pass


def parse_window(text: str | None) -> Window | None:
    """``"1:00-7:00"``, ``"de 1:00 a 7:00"``, ``"23 a 6"``, ``"1h-7h"`` → (60, 420); ``""``/``"siempre"`` → None."""
    t = (text or "").strip().lower()
    t = re.sub(r"^de\s+las\s+", "de ", t)
    if t in ALWAYS:
        return None
    m = WINDOW_RE.match(t)
    if not m:
        raise WindowError(f"franja no válida: {text!r} (ejemplo: de 1:00 a 7:00)")
    h1, m1, h2, m2 = (int(x) if x else 0 for x in m.groups())
    if h2 == 24 and m2 == 0:
        h2 = 0
    if not (0 <= h1 < 24 and 0 <= h2 < 24 and 0 <= m1 < 60 and 0 <= m2 < 60):
        raise WindowError(f"hora fuera de rango en {text!r}")
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start == end:
        return None   # «de 0:00 a 0:00» = the whole day
    return start, end


def format_window(w: Window | None) -> str:
    if w is None:
        return "siempre"
    return f"de {w[0] // 60}:{w[0] % 60:02d} a {w[1] // 60}:{w[1] % 60:02d}"


def normalize_window(text: str | None) -> str:
    """Canonical text stored in the settings (``""`` = always)."""
    w = parse_window(text)
    return "" if w is None else f"{w[0] // 60:02d}:{w[0] % 60:02d}-{w[1] // 60:02d}:{w[1] % 60:02d}"


def _minutes(now: dt.datetime) -> int:
    return now.hour * 60 + now.minute


def in_window(now: dt.datetime, w: Window | None) -> bool:
    if w is None:
        return True
    m = _minutes(now)
    start, end = w
    if start < end:
        return start <= m < end
    return m >= start or m < end   # crosses midnight


def period_start(now: dt.datetime, w: Window | None) -> dt.datetime:
    """Start of the period the per-window limit counts in: the current (or last) occurrence of the window; without a
    window, the current day. ``now`` is local time (naive or aware, returned alike)."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if w is None:
        return midnight
    start = midnight + dt.timedelta(minutes=w[0])
    if start > now:
        start -= dt.timedelta(days=1)
    return start


def next_open(now: dt.datetime, w: Window | None) -> dt.datetime:
    """When downloads may start next (``now`` when the window is open)."""
    if in_window(now, w):
        return now
    assert w is not None
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight + dt.timedelta(minutes=w[0])
    return start if start > now else start + dt.timedelta(days=1)


@dataclass
class Quota:
    """Downloads and bytes spent in the current period (persisted with the subscriptions)."""

    period: float = 0.0     # epoch of the period start
    items: int = 0
    bytes: int = 0

    def roll(self, start: float) -> None:
        if abs(self.period - start) > 1:
            self.period, self.items, self.bytes = start, 0, 0

    def to_dict(self) -> dict[str, Any]:
        return {"period": self.period, "items": self.items, "bytes": self.bytes}

    @classmethod
    def from_dict(cls, d: Any) -> Quota:
        if not isinstance(d, dict):
            return cls()
        try:
            return cls(float(d.get("period") or 0), int(d.get("items") or 0), int(d.get("bytes") or 0))
        except (TypeError, ValueError):
            return cls()


def gate(now: dt.datetime, window: Window | None, quota: Quota, max_items: int, max_mb: int,
         metered: bool | None, pause_metered: bool) -> tuple[bool, str]:
    """May a subscription download start now? (allowed, reason in Spanish when not). Rolls ``quota`` over to the
    current period first."""
    if pause_metered and metered:
        return False, "conexión medida: en pausa"
    if not in_window(now, window):
        return False, "fuera de la franja (" + format_window(window) + ")"
    quota.roll(period_start(now, window).timestamp())
    if max_items > 0 and quota.items >= max_items:
        return False, f"límite de la franja: {max_items} descargas"
    if max_mb > 0 and quota.bytes >= max_mb * 1_000_000:
        return False, f"límite de la franja: {max_mb} MB"
    return True, ""


# -- metered connection --------------------------------------------------------------------------------------------


def parse_nmcli(text: str) -> bool | None:
    """``nmcli -t -f GENERAL.STATE,GENERAL.METERED,IP4.GATEWAY,IP6.GATEWAY dev show`` → is the connection that goes
    to the internet metered? Only connected devices with a gateway count; ``yes``/``yes (guessed)`` = metered,
    ``no…`` = not, ``unknown`` or nothing connected = None."""
    devices: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                devices.append(cur)
                cur = {}
            continue
        key, _, value = line.partition(":")
        if key == "GENERAL.STATE" and cur:
            devices.append(cur)
            cur = {}
        cur[key] = value.strip()
    if cur:
        devices.append(cur)
    result: bool | None = None
    for d in devices:
        state = d.get("GENERAL.STATE", "")
        if not state.startswith("100") or "externally" in state:
            continue
        if not (d.get("IP4.GATEWAY") or d.get("IP6.GATEWAY")):
            continue
        metered = d.get("GENERAL.METERED", "").lower()
        if metered.startswith("yes"):
            return True
        if metered.startswith("no"):
            result = False
    return result


def detect_metered(timeout: float = 5.0) -> bool | None:
    """Linux with NetworkManager: asks nmcli (C locale). ``MPV_UOS_METERED=1/0`` forces it (tests, other systems).
    None = cannot tell (no nmcli, Windows, macOS): callers treat it as not metered."""
    env = os.environ.get("MPV_UOS_METERED")
    if env is not None and env != "":
        return env.lower() in ("1", "yes", "true", "sí", "si")
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return None
    try:
        out = subprocess.run([nmcli, "-t", "-f", "GENERAL.STATE,GENERAL.METERED,IP4.GATEWAY,IP6.GATEWAY", "dev", "show"],
                             capture_output=True, text=True, timeout=timeout, check=False,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return parse_nmcli(out.stdout)


__all__ = ["Quota", "Window", "WindowError", "detect_metered", "format_window", "gate", "in_window", "next_open",
           "normalize_window", "parse_nmcli", "parse_window", "period_start"]
