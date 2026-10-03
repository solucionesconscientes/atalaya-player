"""``torrent.*``: abrir un enlace y servirlo por HTTP local para que mpv lo vea mientras se descarga (H59).

Apagado de fábrica, como los servicios de nube: se enciende en Preferencias y se dice en una línea qué implica.
mpv no sabe nada de torrents; recibe una dirección `http://127.0.0.1:<puerto>/t/<id>/<n>?k=…` y pide rangos como
a cualquier servidor, y es el lector de ``core.py`` el que espera las piezas que hacen falta. Así los saltos
funcionan dentro de lo descargado y «ver mientras baja» no es un modo aparte: es el mismo reproductor.

Al terminar **se deja de sembrar**, que es lo que Ser decidió; seguir es un interruptor explícito. La subida no se
estrangula mientras se baja, porque un cliente que no devuelve nada es un cliente que no baja.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.remote.http import HttpError, HttpServer, Request, Response
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, NOT_FOUND, UNAVAILABLE, RpcError
from mpvd.torrent import core

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.torrent.service")

METADATA_WAIT = 60.0          # un magnet sin metadatos no se puede abrir: hay que esperarlos
AVISO = ("Esto baja de otros usuarios de internet y lo que se baje es responsabilidad de quien lo baja. "
         "La herramienta no decide qué es legal donde estés.")


def service_delete_files(lt: Any) -> int:
    """`session.delete_files`, que es 1, pero por su nombre: un número mágico en una llamada que BORRA no."""
    return int(getattr(lt.session, "delete_files", 1))


def config_dir() -> Path:
    """`~/.config/mpv-uos/`, que es donde Ser quiso las credenciales: fuera del repo y con permisos cerrados."""
    base = Path(os.environ.get("MPV_UOS_CONFIG_DIR") or (Path.home() / ".config" / "mpv-uos"))
    return base


@dataclass
class Settings:
    enabled: bool = False
    seed_after: bool = False
    anonymous: bool = False
    proxy: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Settings:
        path = config_dir() / "torrent.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(enabled=bool(raw.get("enabled")), seed_after=bool(raw.get("seed_after")),
                   anonymous=bool(raw.get("anonymous")), proxy=dict(raw.get("proxy") or {}))

    def save(self) -> None:
        path = config_dir() / "torrent.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"enabled": self.enabled, "seed_after": self.seed_after,
                                   "anonymous": self.anonymous, "proxy": self.proxy},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)          # lleva credenciales del proxy: nadie más del equipo tiene por qué leerlas
        tmp.replace(path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)

    def public(self) -> dict[str, Any]:
        p = dict(self.proxy)
        if p.get("password"):
            p["password"] = "·" * 6   # no se devuelve nunca
        return {"enabled": self.enabled, "seed_after": self.seed_after, "anonymous": self.anonymous, "proxy": p}


@dataclass
class Item:
    id: str
    link: str
    handle: Any
    token: str
    name: str = ""
    index: int = -1
    file_path: Path | None = None
    size: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)
    added_at: float = field(default_factory=time.time)
    done: bool = False

    def public(self) -> dict[str, Any]:
        st = self.handle.status()
        return {
            "id": self.id, "name": self.name or st.name or "", "index": self.index, "size": self.size,
            "progress": round(float(st.progress), 4), "download_rate": int(st.download_rate),
            "upload_rate": int(st.upload_rate), "peers": int(st.num_peers), "seeds": int(st.num_seeds),
            "state": str(st.state), "done": self.done, "wanted": int(getattr(st, "total_wanted", 0) or 0),
            "wanted_done": int(getattr(st, "total_wanted_done", 0) or 0),
            "files": self.files, "added_at": self.added_at,
        }


class TorrentService:
    def __init__(self, server: MpvdServer):
        self.server = server
        self.settings = Settings.load()
        self.lt = core.load()
        self.client: core.Client | None = None
        self.items: dict[str, Item] = {}
        self.http: HttpServer | None = None
        self.port = 0
        self._trackers: list[str] = []
        server.services["torrent"] = bool(self.lt) and self.settings.enabled

    # -- ciclo de vida ----------------------------------------------------------------------

    def _need(self) -> None:
        if self.lt is None:
            raise RpcError(UNAVAILABLE, t("libtorrent no está instalado: uv sync --extra torrent"))
        if not self.settings.enabled:
            raise RpcError(UNAVAILABLE, t("los torrents están apagados: se encienden en Preferencias"))

    async def _ensure(self) -> core.Client:
        self._need()
        if self.client is None:
            save = self.server.settings.data_dir / "torrents"
            self.client = await asyncio.to_thread(
                core.Client, self.lt, save, anonymous=self.settings.anonymous,
                proxy=self.settings.proxy or None)
            self._trackers = await asyncio.to_thread(core.trackers, self.server.settings.cache_dir)
            log.info("torrent: sesión abierta en %s (%d trackers)", save, len(self._trackers))
        if self.http is None:
            self.http = HttpServer(self._handle, name="mpvd-torrent")
            _host, self.port = await self.http.start("127.0.0.1", 0)
            log.info("torrent: sirviendo en 127.0.0.1:%d", self.port)
        return self.client

    async def close(self) -> None:
        for item in list(self.items.values()):
            with contextlib.suppress(Exception):
                if not self.settings.seed_after:
                    self.client.session.remove_torrent(item.handle)  # type: ignore[union-attr]
        self.items.clear()
        if self.http is not None:
            with contextlib.suppress(Exception):
                await self.http.stop()
            self.http = None
        if self.client is not None:
            await asyncio.to_thread(self.client.close)
            self.client = None

    # -- abrir ------------------------------------------------------------------------------

    async def open(self, link: str, index: int | None = None) -> dict[str, Any]:
        client = await self._ensure()
        link = str(link or "").strip()
        if not core.looks_like_torrent(link):
            raise RpcError(INVALID_PARAMS, t("no es un magnet ni un .torrent de este equipo"))
        tid = hashlib.sha1(link.encode("utf-8", "replace")).hexdigest()[:12]
        item = self.items.get(tid)
        if item is None:
            handle = await asyncio.to_thread(client.add, link, self._trackers)
            item = Item(id=tid, link=link, handle=handle, token=secrets.token_urlsafe(12))
            self.items[tid] = item
        ti = await self._metadata(item)
        item.files = core.file_rows(ti)
        item.name = ti.name()
        want = index if index is not None else core.pick_file(ti)
        if want < 0 or want >= len(item.files):
            raise RpcError(INVALID_PARAMS, t("ese torrent no tiene el archivo %s") % (want,))
        item.index = want
        item.size = item.files[want]["size"]
        # solo el fichero que se va a ver: lo demás no se baja hasta que alguien lo pida
        for i in range(len(item.files)):
            with contextlib.suppress(Exception):
                item.handle.file_priority(i, 4 if i == want else 0)
        item.file_path = Path(item.handle.status().save_path) / item.files[want]["path"]
        return {**item.public(), "url": self._url(item), "aviso": AVISO}

    async def _metadata(self, item: Item) -> Any:
        """Un magnet llega sin metadatos: no hay nombre, ni ficheros, ni piezas hasta que alguien los manda."""
        deadline = time.monotonic() + METADATA_WAIT
        while True:
            ti = item.handle.torrent_file()
            if ti is not None and item.handle.status().has_metadata:
                return ti
            if time.monotonic() >= deadline:
                raise RpcError(UNAVAILABLE, t("nadie ha contestado con los datos del torrent en %ss: "
                                              "puede que no tenga semillas") % (round(METADATA_WAIT),))
            await asyncio.sleep(0.2)

    def _url(self, item: Item) -> str:
        return f"http://127.0.0.1:{self.port}/t/{item.id}/{item.index}?k={item.token}"

    # -- servir -----------------------------------------------------------------------------

    async def _handle(self, req: Request) -> Response:
        parts = [p for p in req.path.split("/") if p]
        if len(parts) != 3 or parts[0] != "t":
            raise HttpError(404)
        item = self.items.get(parts[1])
        if item is None or item.file_path is None:
            raise HttpError(404, "ese torrent ya no está abierto")
        if req.query.get("k") != item.token:
            raise HttpError(403)
        try:
            index = int(parts[2])
        except ValueError:
            raise HttpError(404) from None
        if index != item.index:
            raise HttpError(404, "ese archivo no es el que se está viendo")
        size = item.size
        start, end = 0, size - 1
        rng = req.headers.get("range") or ""
        partial = False
        if rng.startswith("bytes="):
            spec = rng[6:].split(",")[0].strip()
            a, _, b = spec.partition("-")
            try:
                if a:
                    start = int(a)
                    end = int(b) if b else size - 1
                else:
                    start = max(0, size - int(b))
            except ValueError:
                raise HttpError(400) from None
            if start >= size or start > end:
                return Response(416, {"Content-Range": f"bytes */{size}"}, b"")
            end = min(end, size - 1)
            partial = True
        reader = core.Reader(item.handle, item.index, item.handle.torrent_file())
        headers = {"Content-Type": "application/octet-stream", "Accept-Ranges": "bytes",
                   "Cache-Control": "no-store", "Content-Length": str(end - start + 1)}
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if req.method == "HEAD":
            return Response(206 if partial else 200, headers, b"")
        return Response(206 if partial else 200, headers, b"",
                        stream=reader.stream(start, end, item.file_path))

    # -- estado -----------------------------------------------------------------------------

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for item in self.items.values():
            st = item.handle.status()
            item.done = bool(getattr(st, "is_seeding", False)) or float(st.progress) >= 1.0
            if item.done and not self.settings.seed_after:
                with contextlib.suppress(Exception):
                    item.handle.pause()
            out.append(item.public())
        return out

    async def remove(self, tid: str, data: bool = False) -> dict[str, Any]:
        item = self.items.pop(tid, None)
        if item is None:
            raise RpcError(NOT_FOUND, t("no hay ningún torrent %s") % (tid,))
        if self.client is not None:
            flags = service_delete_files(self.lt) if data else 0
            await asyncio.to_thread(self.client.session.remove_torrent, item.handle, flags)
        return {"removed": True, "data": bool(data)}


def register(server: MpvdServer, service: TorrentService) -> None:
    d = server.dispatcher

    @d.method("torrent.capabilities")
    async def caps(ctx: RpcContext) -> dict[str, Any]:
        """Whether libtorrent is installed and whether the user turned torrents on."""
        return {"installed": service.lt is not None, "version": getattr(service.lt, "__version__", ""),
                **service.settings.public(), "aviso": AVISO}

    @d.method("torrent.open")
    async def open_(ctx: RpcContext, link: str, index: int | None = None) -> dict[str, Any]:
        """Add a magnet (or a local .torrent), wait for its metadata and give back a local URL mpv can play while
        it downloads. ``index`` picks another file; by default the biggest playable one."""
        return await service.open(link, index)

    @d.method("torrent.list")
    async def list_(ctx: RpcContext) -> dict[str, Any]:
        """What is open right now, with progress, speed and peers."""
        return {"torrents": await asyncio.to_thread(service.rows)}

    @d.method("torrent.connect")
    async def connect(ctx: RpcContext, id: str, host: str, port: int) -> dict[str, Any]:  # noqa: A002
        """Add a peer by hand. Normally nobody needs it —the trackers and the DHT do that— but it is how the
        offline tests work: a seeder on 127.0.0.1 that no tracker knows about."""
        item = service.items.get(str(id))
        if item is None:
            raise RpcError(NOT_FOUND, t("no hay ningún torrent %s") % (id,))
        await asyncio.to_thread(item.handle.connect_peer, (str(host), int(port)))
        return {"added": True}

    @d.method("torrent.remove")
    async def remove(ctx: RpcContext, id: str, data: bool = False) -> dict[str, Any]:  # noqa: A002
        """Forget a torrent; ``data`` also deletes what was downloaded."""
        return await service.remove(str(id), bool(data))

    @d.method("torrent.settings.get")
    async def settings_get(ctx: RpcContext) -> dict[str, Any]:
        """The switches: on/off, keep seeding, anonymous mode and the SOCKS5 proxy (never the password)."""
        return service.settings.public()

    @d.method("torrent.settings.set")
    async def settings_set(ctx: RpcContext, enabled: bool | None = None, seed_after: bool | None = None,
                           anonymous: bool | None = None, proxy: dict[str, Any] | None = None) -> dict[str, Any]:
        """Change the switches. Turning it off closes the session; the proxy and the anonymous mode only take
        effect on the next session, so it is closed too."""
        s = service.settings
        reopen = False
        if enabled is not None:
            s.enabled = bool(enabled)
            reopen = True
        if seed_after is not None:
            s.seed_after = bool(seed_after)
        if anonymous is not None and bool(anonymous) != s.anonymous:
            s.anonymous = bool(anonymous)
            reopen = True
        if proxy is not None:
            s.proxy = {k: v for k, v in proxy.items() if v not in (None, "")}
            reopen = True
        await asyncio.to_thread(s.save)
        if reopen:
            await service.close()
        server.services["torrent"] = service.lt is not None and s.enabled
        return s.public()
