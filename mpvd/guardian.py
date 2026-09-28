"""Performance guardian: watches mpv frame drops and throttles heavy background work.

Sessions report ``frame-drop-count`` samples; when the drop rate of any playing session exceeds the
threshold, heavy jobs are held for ``cooldown`` seconds (renewed while drops continue). Light jobs
(interactive, urgent) are never held.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _Sample:
    count: int
    t: float


@dataclass
class PerformanceGuardian:
    drop_rate_threshold: float = 2.0  # dropped frames per second
    cooldown: float = 10.0  # seconds heavy work stays paused after the last burst
    clock: Callable[[], float] = time.monotonic
    _last: dict[str, _Sample] = field(default_factory=dict)
    _throttle_until: float = 0.0
    _listeners: list[Callable[[bool], None]] = field(default_factory=list)
    _last_rate: float = 0.0
    _triggers: int = 0

    def add_listener(self, fn: Callable[[bool], None]) -> None:
        self._listeners.append(fn)

    @property
    def throttled(self) -> bool:
        return self.clock() < self._throttle_until

    @property
    def throttle_remaining(self) -> float:
        return max(0.0, self._throttle_until - self.clock())

    def observe(self, session_id: str, frame_drop_count: int | None, paused: bool = False) -> bool:
        """Feed a sample; returns the throttled state after it."""
        now = self.clock()
        was = self.throttled
        if frame_drop_count is None or paused:
            self._last.pop(session_id, None)
            return was
        prev = self._last.get(session_id)
        self._last[session_id] = _Sample(frame_drop_count, now)
        if prev is not None and frame_drop_count >= prev.count:
            dt = now - prev.t
            if dt > 0:
                rate = (frame_drop_count - prev.count) / dt
                self._last_rate = rate
                if rate > self.drop_rate_threshold:
                    self._throttle_until = now + self.cooldown
                    self._triggers += 1
        is_now = self.throttled
        if is_now != was:
            for fn in self._listeners:
                fn(is_now)
        return is_now

    def forget(self, session_id: str) -> None:
        self._last.pop(session_id, None)

    def release(self) -> None:
        was = self.throttled
        self._throttle_until = 0.0
        if was:
            for fn in self._listeners:
                fn(False)

    def status(self) -> dict[str, object]:
        return {
            "throttled": self.throttled,
            "remaining": round(self.throttle_remaining, 2),
            "threshold": self.drop_rate_threshold,
            "cooldown": self.cooldown,
            "last_rate": round(self._last_rate, 2),
            "triggers": self._triggers,
            "sessions": list(self._last),
        }
