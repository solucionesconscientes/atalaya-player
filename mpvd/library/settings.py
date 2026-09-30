"""Library settings in the user's data dir: ``library-settings.json`` (switches, languages) and, apart,
``library-secrets.json`` (TMDB key, OpenSubtitles Api-Key, user and password) written with permissions 0600.

Online services are OFF by default (local-first, ADR-003) and never contacted without the switch AND the key.
Secrets are never returned by ``public()`` nor logged: callers only learn whether each one is set.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("mpvd.library")

DEFAULTS: dict[str, Any] = {
    "tmdb_enabled": False,        # online metadata and posters (TMDB, user's own key)
    "tmdb_language": "es-ES",
    "osub_enabled": False,        # subtitles from OpenSubtitles.com (user's own Api-Key / account)
    "osub_languages": "es,en",
    "osub_resync": "auto",        # auto: only subtitles not matched by hash · always · never
}
SECRETS = ("tmdb_key", "osub_api_key", "osub_username", "osub_password")
RESYNC_MODES = ("auto", "always", "never")
LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,4})?$")


def normalize_languages(value: str) -> str:
    """``"EN, es ,pt-BR"`` → ``"en,es,pt-br"`` (OpenSubtitles wants lower-case codes; order kept = preference)."""
    out: list[str] = []
    for part in re.split(r"[\s,;]+", str(value or "").lower()):
        if part and LANG_RE.match(part) and part not in out:
            out.append(part)
    if not out:
        raise ValueError("idiomas no válidos (ejemplo: es,en)")
    return ",".join(out[:8])


def write_private(path: Path, data: dict[str, Any]) -> None:
    """Atomic write readable only by the user (0600 from creation, not chmod afterwards)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


class LibrarySettings:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "library-settings.json"
        self.secrets_path = Path(data_dir) / "library-secrets.json"
        self._lock = threading.Lock()
        self.values: dict[str, Any] = dict(DEFAULTS)
        self._secrets: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                        self.values[k] = v
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s: %s", self.path, exc)
        try:
            data = json.loads(self.secrets_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._secrets = {k: v for k, v in data.items() if k in SECRETS and isinstance(v, str) and v}
            with contextlib.suppress(OSError):
                if self.secrets_path.stat().st_mode & 0o077:
                    os.chmod(self.secrets_path, 0o600)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("cannot read the library secrets file: %s", type(exc).__name__)

    def get(self, key: str) -> Any:
        return self.values.get(key, DEFAULTS.get(key))

    def secret(self, key: str) -> str:
        return self._secrets.get(key, "")

    @property
    def tmdb_active(self) -> bool:
        return bool(self.values["tmdb_enabled"] and self.secret("tmdb_key"))

    @property
    def osub_active(self) -> bool:
        return bool(self.values["osub_enabled"] and self.secret("osub_api_key"))

    def public(self) -> dict[str, Any]:
        return {**self.values, "has_tmdb_key": bool(self.secret("tmdb_key")),
                "has_osub_key": bool(self.secret("osub_api_key")),
                "osub_username": self.secret("osub_username"),
                "has_osub_password": bool(self.secret("osub_password")),
                "tmdb_active": self.tmdb_active, "osub_active": self.osub_active}

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Validate and store; a secret set to "" is forgotten. Unknown keys are an error (typos must not pass)."""
        values = dict(self.values)
        secrets = dict(self._secrets)
        for k, v in changes.items():
            if k in SECRETS:
                if v is None or str(v).strip() == "":
                    secrets.pop(k, None)
                else:
                    secrets[k] = str(v).strip()
            elif k in DEFAULTS:
                want = type(DEFAULTS[k])
                if want is bool:
                    if not isinstance(v, bool):
                        raise ValueError(f"{k}: se esperaba sí/no")
                elif not isinstance(v, str):
                    raise ValueError(f"{k}: se esperaba texto")
                if k == "osub_languages":
                    v = normalize_languages(v)
                elif k == "osub_resync" and v not in RESYNC_MODES:
                    raise ValueError(f"osub_resync: {', '.join(RESYNC_MODES)}")
                elif k == "tmdb_language" and not re.match(r"^[a-z]{2}(-[A-Z]{2})?$", v):
                    raise ValueError("tmdb_language: por ejemplo es-ES")
                values[k] = v
            else:
                raise ValueError(f"ajuste desconocido: {k}")
        with self._lock:
            if values != self.values:
                write_private(self.path, values)
                self.values = values
            if secrets != self._secrets:
                write_private(self.secrets_path, secrets)
                self._secrets = secrets
        return self.public()
