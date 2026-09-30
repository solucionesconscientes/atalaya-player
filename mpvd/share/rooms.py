"""Room and guest logic of «Compartir» (no I/O): random room ids, long invitation tokens, expiry, failed-attempt
limits (per address and per room), guest names, per-room signed cookies and permissions (view / control with the
host's approval, revocable, kick).

A room link is ``/s/<room id>#k=<token>``: the token travels in the URL fragment (never in a request line or a
log), the page posts it once with the chosen name and gets a cookie signed with the room's own secret, scoped to
``/s/<room id>``. Closing the room (or its expiry) invalidates every cookie: the secret dies with it."""

from __future__ import annotations

import hmac
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

PERM_VIEW = "view"
PERM_CONTROL = "control"
PERMS = (PERM_VIEW, PERM_CONTROL)

ROOM_TTL = 4 * 3600.0          # default life of a room
MAX_TTL = 24 * 3600.0
MIN_TTL = 60.0
MAX_GUESTS = 12
MAX_FAILS_ROOM = 20            # wrong tokens for one room before its link stops working (the host makes a new one)
MAX_FAILS_IP = 5               # wrong tokens from one address inside FAIL_WINDOW before it is blocked for BLOCK_SECONDS
FAIL_WINDOW = 600.0
BLOCK_SECONDS = 600.0
NAME_MAX = 24
TOKEN_BYTES = 24               # 192 bits → 32 URL-safe characters
ROOM_ID_BYTES = 6              # 48 bits → 8 characters (not a secret: the token is)

_CTRL = re.compile(r"[\x00-\x1f\x7f<>\"'`\\]")


class JoinError(Exception):
    """A refused join: ``status`` is the HTTP status the page gets (403 bad token, 410 closed, 429 blocked...)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clean_name(raw: Any, taken: set[str] | None = None) -> str:
    """The name a guest typed, made safe for the OSD and the page: no control/markup characters, spaces collapsed,
    at most NAME_MAX characters, unique inside the room ("Ana", "Ana (2)"). ValueError when nothing is left."""
    text = unicodedata.normalize("NFC", str(raw or ""))
    text = _CTRL.sub("", text)
    text = re.sub(r"[{}]", "", text)  # ASS override blocks in the host's OSD
    text = " ".join(text.split())[:NAME_MAX].strip()
    if not text:
        raise ValueError("escribe tu nombre")
    taken_l = {t.casefold() for t in (taken or set())}
    if text.casefold() not in taken_l:
        return text
    n = 2
    while True:
        cand = f"{text[:NAME_MAX - 4]} ({n})"
        if cand.casefold() not in taken_l:
            return cand
        n += 1


class AttemptLimiter:
    """Failed join attempts per client address: MAX_FAILS_IP inside FAIL_WINDOW blocks it for BLOCK_SECONDS."""

    def __init__(self, max_fails: int = MAX_FAILS_IP, window: float = FAIL_WINDOW, block: float = BLOCK_SECONDS):
        self.max_fails = max_fails
        self.window = window
        self.block = block
        self._fails: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}

    def blocked(self, ip: str, now: float | None = None) -> float:
        """Seconds left of the block of ``ip`` (0 = not blocked)."""
        now = time.time() if now is None else now
        until = self._blocked.get(ip, 0.0)
        if until <= now:
            self._blocked.pop(ip, None)
            return 0.0
        return until - now

    def fail(self, ip: str, now: float | None = None) -> bool:
        """Count one failure; True when ``ip`` is blocked from now on."""
        now = time.time() if now is None else now
        rows = [t for t in self._fails.get(ip, []) if now - t < self.window]
        rows.append(now)
        self._fails[ip] = rows
        if len(rows) >= self.max_fails:
            self._blocked[ip] = now + self.block
            self._fails.pop(ip, None)
            return True
        return False

    def success(self, ip: str) -> None:
        self._fails.pop(ip, None)

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._fails = {ip: [t for t in rows if now - t < self.window] for ip, rows in self._fails.items()}
        self._fails = {ip: rows for ip, rows in self._fails.items() if rows}
        self._blocked = {ip: t for ip, t in self._blocked.items() if t > now}


@dataclass
class Guest:
    id: str
    name: str
    ip: str = ""
    perm: str = PERM_VIEW
    pending: bool = False          # asked for control, the host has not answered yet
    joined_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    streams: int = 0               # open event streams (0 = not connected right now)
    kicked: bool = False

    @property
    def connected(self) -> bool:
        return self.streams > 0

    @property
    def can_control(self) -> bool:
        return self.perm == PERM_CONTROL and not self.kicked

    def public(self) -> dict[str, Any]:
        """What the host's menu and the other guests see."""
        return {"id": self.id, "name": self.name, "perm": self.perm, "pending": self.pending,
                "connected": self.connected, "joined_at": round(self.joined_at, 1)}


@dataclass
class Room:
    id: str
    token: str
    secret: bytes
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    guests: dict[str, Guest] = field(default_factory=dict)
    fails: int = 0
    locked: bool = False            # too many wrong tokens: the link no longer works until rotate()
    closed: bool = False
    max_guests: int = MAX_GUESTS

    @classmethod
    def new(cls, session_id: str | None = None, ttl: float = ROOM_TTL, now: float | None = None) -> Room:
        now = time.time() if now is None else now
        ttl = max(MIN_TTL, min(MAX_TTL, float(ttl)))
        return cls(id=secrets.token_urlsafe(ROOM_ID_BYTES), token=secrets.token_urlsafe(TOKEN_BYTES),
                   secret=secrets.token_bytes(32), session_id=session_id, created_at=now, expires_at=now + ttl)

    # -- life -------------------------------------------------------------------------------------------

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at

    def alive(self, now: float | None = None) -> bool:
        return not self.closed and not self.expired(now)

    def link_path(self) -> str:
        return f"/s/{self.id}#k={self.token}"

    def rotate(self) -> str:
        """New invitation token (the old link stops working; guests inside stay). Unlocks the room."""
        self.token = secrets.token_urlsafe(TOKEN_BYTES)
        self.fails = 0
        self.locked = False
        return self.token

    # -- joining ----------------------------------------------------------------------------------------

    def check_token(self, token: str) -> bool:
        return bool(token) and hmac.compare_digest(token.encode("utf-8", "replace"), self.token.encode())

    def join(self, token: Any, name: Any, ip: str, limiter: AttemptLimiter | None = None,
             now: float | None = None) -> Guest:
        now = time.time() if now is None else now
        if self.closed:
            raise JoinError(410, "la sala está cerrada")
        if self.expired(now):
            raise JoinError(410, "la sala ha caducado")
        if limiter is not None:
            left = limiter.blocked(ip, now)
            if left > 0:
                raise JoinError(429, f"demasiados intentos: espera {int(left // 60) + 1} min")
        if self.locked:
            raise JoinError(403, "este enlace ya no vale: pide uno nuevo al anfitrión")
        if not isinstance(token, str) or not self.check_token(token):
            self.fails += 1
            if self.fails >= MAX_FAILS_ROOM:
                self.locked = True
            if limiter is not None and limiter.fail(ip, now):
                raise JoinError(429, "demasiados intentos: espera unos minutos")
            raise JoinError(403, "enlace no válido o caducado")
        active = [g for g in self.guests.values() if not g.kicked]
        if len(active) >= self.max_guests:
            raise JoinError(403, "la sala está llena")
        try:
            clean = clean_name(name, {g.name for g in active})
        except ValueError as exc:
            raise JoinError(400, str(exc)) from None
        if limiter is not None:
            limiter.success(ip)
        guest = Guest(id=secrets.token_hex(8), name=clean, ip=ip, joined_at=now, last_seen=now)
        self.guests[guest.id] = guest
        return guest

    # -- cookies ----------------------------------------------------------------------------------------

    def cookie_value(self, guest_id: str) -> str:
        return guest_id + "." + hmac.new(self.secret, f"{self.id}:{guest_id}".encode(), sha256).hexdigest()[:32]

    def guest_from_cookie(self, raw: str | None) -> Guest | None:
        gid, _, sig = (raw or "").partition(".")
        if not gid or not sig or not self.alive():
            return None
        guest = self.guests.get(gid)
        if guest is None or guest.kicked:
            return None
        if not hmac.compare_digest(self.cookie_value(gid), raw or ""):
            return None
        guest.last_seen = time.time()
        return guest

    # -- permissions ------------------------------------------------------------------------------------

    def _guest(self, guest_id: str) -> Guest:
        g = self.guests.get(guest_id)
        if g is None or g.kicked:
            raise KeyError(guest_id)
        return g

    def request_control(self, guest_id: str) -> bool:
        """The guest asks for control; True when this is a new request the host must answer."""
        g = self._guest(guest_id)
        if g.perm == PERM_CONTROL or g.pending:
            return False
        g.pending = True
        return True

    def set_perm(self, guest_id: str, perm: str) -> Guest:
        if perm not in PERMS:
            raise ValueError(f"permiso desconocido: {perm}")
        g = self._guest(guest_id)
        g.perm = perm
        g.pending = False
        return g

    def deny(self, guest_id: str) -> Guest:
        g = self._guest(guest_id)
        g.pending = False
        return g

    def kick(self, guest_id: str) -> Guest:
        g = self._guest(guest_id)
        g.kicked = True
        g.pending = False
        g.perm = PERM_VIEW
        return g

    def active_guests(self) -> list[Guest]:
        return sorted((g for g in self.guests.values() if not g.kicked), key=lambda g: g.joined_at)

    def pending(self) -> list[Guest]:
        return [g for g in self.active_guests() if g.pending]

    def public(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        return {"id": self.id, "created_at": round(self.created_at, 1), "expires_at": round(self.expires_at, 1),
                "expires_in": max(0, int(self.expires_at - now)), "locked": self.locked,
                "guests": [g.public() for g in self.active_guests()]}
