"""Subscriptions, their rules and the global settings, persisted in ``<datos>/feeds.json`` (atomic writes)."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpvd.subscriptions.chain import ChainConfig
from mpvd.subscriptions.rules import Quota, normalize_window
from mpvd.ytdl.presets import CONTAINERS, PRESETS, SPONSORBLOCK_MODES

log = logging.getLogger("mpvd.subscriptions")

PRESET_IDS = tuple(p["id"] for p in PRESETS if p["group"] in ("video", "audio"))
MAX_SEEN = 3000
MIN_INTERVAL_H = 0.25
DEFAULT_INITIAL = {"channel": 3, "rss": 3, "playlist": -1}   # -1 = everything the list has (at most 500)


@dataclass
class Subscription:
    url: str
    kind: str                         # channel | playlist | rss
    title: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    preset: str = "video_1080"        # a download preset of ytdl (quality / audio only)
    container: str = ""               # "" = the download settings' container
    sponsorblock: str = ""            # "" = the download settings' choice; none | mark | remove
    keep: int = 0                     # keep only the N newest files (0 = all)
    keep_watched_only: bool = True    # … and when trimming, delete only the ones already watched
    delete_watched: bool = False      # delete what was watched (after a grace period)
    initial: int = 3                  # on subscribing: download the N newest (0 = only what comes next, -1 = all)
    chain: dict[str, Any] | None = None   # None = the global default chain
    folder: str = ""
    paused: bool = False
    created_at: float = field(default_factory=time.time)
    last_check: float = 0.0
    last_error: str = ""
    last_found: int = 0
    checked: bool = False             # the first check ran (it decides what «initial» takes)
    seen: list[str] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    failed: int = 0

    def is_audio(self) -> bool:
        return self.preset.startswith("audio")

    def mark_seen(self, entry_id: str) -> None:
        if entry_id and entry_id not in self.seen:
            self.seen.append(entry_id)
            if len(self.seen) > MAX_SEEN:
                del self.seen[: len(self.seen) - MAX_SEEN]

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Subscription:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        s.validate()
        return s

    def update(self, changes: dict[str, Any]) -> None:
        for k in ("title", "preset", "container", "sponsorblock", "folder"):
            if k in changes and changes[k] is not None:
                setattr(self, k, str(changes[k]).strip())
        for k in ("keep", "initial"):
            if k in changes and changes[k] is not None:
                setattr(self, k, int(changes[k]))
        for k in ("keep_watched_only", "delete_watched", "paused"):
            if k in changes and changes[k] is not None:
                setattr(self, k, bool(changes[k]))
        if "chain" in changes:
            c = changes["chain"]
            if c is None:
                self.chain = None
            else:
                cfg = ChainConfig.from_dict(self.chain) if self.chain is not None else ChainConfig()
                cfg.update(dict(c))
                self.chain = cfg.to_dict()
        self.validate()

    def validate(self) -> None:
        if self.kind not in ("channel", "playlist", "rss"):
            raise ValueError(f"tipo desconocido: {self.kind}")
        if self.preset not in PRESET_IDS:
            raise ValueError(f"calidad desconocida: {self.preset}")
        if self.container and self.container not in CONTAINERS:
            raise ValueError(f"contenedor desconocido: {self.container}")
        if self.sponsorblock and self.sponsorblock not in SPONSORBLOCK_MODES:
            raise ValueError(f"SponsorBlock: {self.sponsorblock}")
        if self.keep < 0 or self.keep > 1000:
            raise ValueError("conservar: de 0 a 1000")
        if self.initial < -1 or self.initial > 500:
            raise ValueError("al suscribirse: de -1 (todo) a 500")


@dataclass
class FeedSettings:
    interval_h: float = 2.0           # how often each subscription is checked
    window: str = ""                  # «01:00-07:00»; "" = any time
    max_items: int = 0                # downloads per window (0 = no limit)
    max_mb: int = 0                   # MB per window (0 = no limit)
    pause_metered: bool = True        # no subscription downloads on a metered connection
    keep_running: bool = False        # mpvd keeps checking after the player closes
    parallel: int = 1                 # subscription downloads at the same time
    scan_depth: int = 15              # newest entries of a channel looked at per check
    watched_grace_h: float = 24.0     # «borrar lo visto» waits this long after it was watched
    chain_downloads: bool = False     # the default chain also runs after ordinary downloads
    chain: dict[str, Any] = field(default_factory=lambda: ChainConfig().to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def update(self, d: dict[str, Any]) -> None:
        if "window" in d:
            self.window = normalize_window(str(d["window"] or ""))
        if "interval_h" in d:
            v = float(d["interval_h"])
            if not MIN_INTERVAL_H <= v <= 24 * 7:
                raise ValueError("intervalo: de 15 minutos a 7 días")
            self.interval_h = v
        for k, lo, hi in (("max_items", 0, 1000), ("max_mb", 0, 10_000_000), ("parallel", 1, 4),
                          ("scan_depth", 1, 200)):
            if k in d:
                v = int(d[k])
                if not lo <= v <= hi:
                    raise ValueError(f"{k}: de {lo} a {hi}")
                setattr(self, k, v)
        if "watched_grace_h" in d:
            self.watched_grace_h = max(0.0, float(d["watched_grace_h"]))
        for k in ("pause_metered", "keep_running", "chain_downloads"):
            if k in d:
                setattr(self, k, bool(d[k]))
        if "chain" in d and d["chain"] is not None:
            cfg = ChainConfig.from_dict(self.chain)
            cfg.update(dict(d["chain"]))
            self.chain = cfg.to_dict()

    @classmethod
    def from_dict(cls, d: Any) -> FeedSettings:
        s = cls()
        if isinstance(d, dict):
            try:
                s.update(d)
            except (TypeError, ValueError) as exc:
                log.warning("feeds settings: %s (defaults kept)", exc)
        return s


class FeedStore:
    def __init__(self, path: Path):
        self.path = path
        self.settings = FeedSettings()
        self.quota = Quota()
        self.subs: dict[str, Subscription] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.settings = FeedSettings.from_dict(data.get("settings"))
        self.quota = Quota.from_dict(data.get("quota"))
        for row in data.get("subscriptions") or []:
            try:
                s = Subscription.from_dict(row)
            except (TypeError, ValueError) as exc:
                log.warning("feeds: subscription dropped (%s)", exc)
                continue
            self.subs[s.id] = s

    def save(self) -> None:
        data = {"version": 1, "settings": self.settings.to_dict(), "quota": self.quota.to_dict(),
                "subscriptions": [s.to_dict() for s in self.subs.values()]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("cannot save subscriptions: %s", exc)


__all__ = ["DEFAULT_INITIAL", "FeedSettings", "FeedStore", "PRESET_IDS", "Subscription"]
