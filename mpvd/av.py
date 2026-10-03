"""``av.*``: small models for the "Sonido e imagen" menu (RNNoise ``.rnnn`` for ``arnndn``, a free SOFA HRTF for
``sofalizer``), pinned by SHA-256 in vendor.lock and downloaded on demand into ``vendor/models/<kind>/`` (dev) or
``<data_dir>/models/<kind>/``. The filters themselves run inside mpv (mu-av.lua); mpvd only provides the files."""

from __future__ import annotations

import asyncio
import hashlib
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.jobs import Job, Priority
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext


@dataclass(frozen=True)
class AvModel:
    name: str
    kind: str          # rnnoise | sofa
    filename: str
    url: str
    sha256: str
    size_kb: int
    note: str          # Spanish, for menus


MODELS: dict[str, AvModel] = {
    "rnnoise-sh": AvModel("rnnoise-sh", "rnnoise", "sh.rnnn",
                          "https://github.com/GregorR/rnnoise-models/raw/master/somnolent-hogwash-2018-09-01/sh.rnnn",
                          "70bb6685eb0c2a1d18e2918dca3fbfbd39317010b1802eb1b6ea73a92f3fdec0", 291,
                          "RNNoise general (voz + ruido variado)"),
    "rnnoise-bd": AvModel("rnnoise-bd", "rnnoise", "bd.rnnn",
                          "https://github.com/GregorR/rnnoise-models/raw/master/beguiling-drafter-2018-08-30/bd.rnnn",
                          "ae3f7411e1e6a884f839a4a145c394408398f09854dbc1216ee02faafc98a17b", 293,
                          "RNNoise para voz grabada con ruido de fondo"),
    "sofa-kemar": AvModel("sofa-kemar", "sofa", "mit_kemar_normal_pinna.sofa",
                          "https://sofacoustics.org/data/database/mit/mit_kemar_normal_pinna.sofa",
                          "e7035994f5fd754058424c061380ee92b1d5ed58fccef2887a4266916616acdf", 1145,
                          "HRTF MIT KEMAR (binaural para auriculares)"),
}
DEFAULTS = {"rnnoise": "rnnoise-sh", "sofa": "sofa-kemar"}


class AvModelError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class AvModelStore:
    def __init__(self, dirs: list[Path]):
        self.dirs = [Path(d) for d in dirs]

    def find(self, name: str) -> Path | None:
        m = MODELS.get(name)
        if m is None:
            return None
        for d in self.dirs:
            p = d / m.kind / m.filename
            if p.is_file() and p.stat().st_size > 1024:
                return p
        return None

    def download_dir(self, kind: str) -> Path:
        for d in self.dirs:
            try:
                (d / kind).mkdir(parents=True, exist_ok=True)
                if os.access(d / kind, os.W_OK):
                    return d / kind
            except OSError:
                continue
        raise AvModelError("no writable model directory")

    def list(self) -> list[dict[str, Any]]:
        out = []
        for m in MODELS.values():
            p = self.find(m.name)
            out.append({"name": m.name, "kind": m.kind, "path": str(p) if p else None, "present": p is not None,
                        "size_kb": m.size_kb, "note": m.note, "url": m.url,
                        "default": DEFAULTS.get(m.kind) == m.name})
        return out

    def default_path(self, kind: str) -> Path | None:
        for m in MODELS.values():
            if m.kind == kind:
                p = self.find(m.name)
                if p is not None and (DEFAULTS.get(kind) == m.name or True):
                    return p
        return None

    async def download(self, name: str, progress: Callable[[float, str], None] | None = None) -> Path:
        m = MODELS.get(name)
        if m is None:
            raise AvModelError(f"unknown model {name!r}")
        existing = self.find(name)
        if existing is not None:
            return existing
        dest = self.download_dir(m.kind) / m.filename
        part = dest.with_suffix(dest.suffix + ".part")

        def _fetch() -> None:
            req = urllib.request.Request(m.url, headers={"User-Agent": "mpv-uos/av"})
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:  # noqa: S310 - pinned https URL
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None and total:
                        progress(min(0.95, done / total), f"{done // 1024} / {total // 1024} KiB")
            if sha256_of(part) != m.sha256:
                raise AvModelError(f"SHA-256 incorrecto para {m.url}")

        try:
            await asyncio.to_thread(_fetch)
            part.replace(dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        if progress is not None:
            progress(1.0, "listo")
        return dest


def default_dirs(root: Path | None, data_dir: Path) -> list[Path]:
    env = os.environ.get("MPV_UOS_AV_MODELS")
    dirs: list[Path] = [Path(env)] if env else []
    if root is not None:
        dirs.append(root / "vendor" / "models")
    dirs.append(data_dir / "models")
    return dirs


class AvService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.store = AvModelStore(default_dirs(server.root, server.settings.data_dir))

    def models(self) -> dict[str, Any]:
        return {"models": self.store.list(),
                "defaults": {k: (str(self.store.find(v)) if self.store.find(v) else None) for k, v in DEFAULTS.items()}}

    def download(self, name: str, notify: str, session_id: str | None) -> Job:
        if name not in MODELS:
            raise RpcError(INVALID_PARAMS, f"unknown model {name!r}")

        def push(job: Job, status: str | None = None, final: bool = False) -> None:
            if not session_id:
                return
            session = self.server.sessions.get(session_id)
            if session is None or not session.connected:
                return
            data = job.to_dict()
            data["status"] = status or data["status"]
            session.push_event(notify, "av-model:" + name, f"{data['status']}:{job.progress:.2f}",
                               {"event": "av-model", "model": name, "job": data}, min_interval=0.5, final=final)

        async def body(job: Job) -> dict[str, Any]:
            loop = asyncio.get_running_loop()

            def report(frac: float, message: str) -> None:
                job.report(frac, message)
                push(job)

            push(job)
            try:
                path = await self.store.download(name, progress=lambda f, m: loop.call_soon_threadsafe(report, f, m))
            except asyncio.CancelledError:
                push(job, "cancelled", final=True)
                raise
            except Exception as exc:
                job.report(None, str(exc))
                push(job, "failed", final=True)
                raise
            job.report(1.0, "listo")
            push(job, "done", final=True)
            return {"name": name, "path": str(path)}

        return self.server.jobs.submit(f"av.model.{name}", body, priority=Priority.INTERACTIVE, heavy=False,
                                       session_id=None, meta={"notify": notify, "model": name, "session": session_id})


def register(server: MpvdServer, service: AvService) -> None:
    d = server.dispatcher
    server.services["av"] = True

    def _sid(ctx: RpcContext) -> str | None:
        return ctx.session.id if ctx.session is not None else None

    @d.method("av.models")
    async def models(ctx: RpcContext) -> dict[str, Any]:
        """RNNoise / SOFA models for the audio filters: present or downloadable, with paths for mpv's lavfi graphs."""
        return service.models()

    @d.method("av.models.download")
    async def models_download(ctx: RpcContext, name: str, notify: str = "mu_av") -> dict[str, Any]:
        """Download a model in the background (progress pushed as ``av-model`` events)."""
        return service.download(name, notify, _sid(ctx)).to_dict()

    @d.method("av.models.path")
    async def models_path(ctx: RpcContext, name: str) -> dict[str, Any]:
        """Path of a downloaded model (error when missing)."""
        p = service.store.find(name)
        if p is None:
            raise RpcError(NOT_FOUND, t("modelo %s no descargado") % (name,))
        return {"name": name, "path": str(p)}
