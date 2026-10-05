"""H68 · avisar de que hay una versión nueva. Avisar: nunca instalar nada solo.

Qué se consulta: un `latest.json` en el sitio del proyecto (el `site` de brand.json), una vez al día, con la misma
caché HTTP que usa la comprobación diaria de yt-dlp —petición condicional, y si la red falla se sirve lo último
que hubiera—. La forma del fichero es el contrato con la web:

    {"version": "0.2.0",
     "date": "2026-11-01",
     "notes": "Qué cambia, en dos líneas.",
     "url": "https://solucionesconscientes.es/atalaya",
     "files": [{"name": "atalaya-player_0.2.0_amd64.deb", "sha256": "…", "size": 28311552}, …]}

Solo `version` es obligatorio: si falta el resto, se avisa con lo que haya.

**Por qué esto NO se autoinstala.** Un reproductor que se actualiza solo tiene que descargar, verificar,
reemplazarse mientras está en marcha y saber volver atrás si la nueva no arranca. Las dos últimas son la parte
difícil, y hacerlas mal deja a alguien sin reproductor; además en Linux eso ya lo saben hacer apt o el gestor de
AppImage de cada uno. Así que aquí solo se dice «hay una nueva», con el enlace y —si el fichero los trae— el
SHA-256 de cada paquete, para quien quiera comprobar lo que descarga. Lo vigila un test: este módulo no descarga
paquetes ni escribe ejecutables.

**Cuándo está encendido.** Solo cuando el programa viene de un paquete (`MPV_UOS_PACKAGED`, que pone el lanzador
del `.deb`, o `MPV_UOS_APPIMAGE`): en una copia del repositorio la forma de actualizar es `git pull`, y avisar ahí
sería ruido. `MPV_UOS_UPDATES=1|0` lo fuerza en los dos sentidos, y `MPV_UOS_UPDATE_URL` cambia de dónde se lee
(los tests lo usan con un servidor local).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd import __version__
from mpvd.brand import app_site
from mpvd.i18n import t
from mpvd.net import FetchError, HttpCache
from mpvd.rpc import INVALID_PARAMS, RpcError

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

log = logging.getLogger("mpvd.updates")

TTL = 24 * 3600.0           # una vez al día, igual que la de yt-dlp
# Solo la parte numérica del principio: «0.2.0-rc1» es (0, 2, 0) y no (0, 2, 0, 1), que compararía como más
# nueva que 0.2.0. Quien no anda buscando candidatas no tiene por qué enterarse de que existen.
NUM = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")
MAX_NOTES = 500


def version_tuple(text: str) -> tuple[int, ...]:
    """«0.2.0» → (0, 2, 0). Lo que no sea número se ignora, así que «0.2.0-rc1» y «0.2.0» comparan igual: una
    candidata no es una versión nueva para quien no las anda buscando."""
    m = NUM.match(text or "")
    return tuple(int(n) for n in m.group(1).split(".")[:4]) if m else ()


def enabled() -> bool:
    forzado = (os.environ.get("MPV_UOS_UPDATES") or "").strip().lower()
    if forzado in ("0", "no", "false"):
        return False
    if forzado in ("1", "yes", "true"):
        return True
    return bool(os.environ.get("MPV_UOS_PACKAGED") or os.environ.get("MPV_UOS_APPIMAGE"))


def latest_url() -> str:
    env = os.environ.get("MPV_UOS_UPDATE_URL")
    if env:
        return env
    sitio = (app_site() or "").rstrip("/")
    return f"{sitio}/latest.json" if sitio else ""


class UpdateService:
    """Mira si hay versión nueva y se acuerda de cuál ya se ha dicho, para no repetirlo cada vez que se abre."""

    def __init__(self, server: MpvdServer):
        self.server = server
        self.http = HttpCache(server.settings.cache_dir / "http")
        self.path = Path(server.settings.data_dir) / "updates.json"

    # -- lo que ya se ha dicho ----------------------------------------------------------------

    def _seen(self) -> str:
        try:
            return str(json.loads(self.path.read_text(encoding="utf-8")).get("announced") or "")
        except (OSError, ValueError, AttributeError):
            return ""

    def mark_seen(self, version: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"announced": version, "at": time.time()}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- la comprobación ----------------------------------------------------------------------

    def check(self, force: bool = False) -> dict[str, Any]:
        base = {"enabled": enabled(), "current": __version__, "url": latest_url(), "latest": "", "newer": False,
                "notes": "", "date": "", "files": [], "stale": False, "announced": self._seen()}
        if not base["enabled"] and not force:
            return base
        url = latest_url()
        if not url:
            return base | {"error": t("no hay dónde consultar las actualizaciones")}
        try:
            res = self.http.fetch(url, ttl=TTL, force=force, timeout=20)
            data = json.loads(res.read_text())
        except (FetchError, ValueError, OSError) as exc:
            # no hay versión nueva que anunciar y tampoco es un fallo que haya que contar: puede no haber red, o
            # el fichero puede no existir todavía (la web se publica después que esto)
            log.debug("updates: no se pudo consultar %s (%s)", url, exc)
            return base | {"error": str(exc)[:200]}
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            return base | {"error": t("la respuesta no tiene el formato esperado")}
        ultima = data["version"].strip()
        salida = base | {
            "latest": ultima,
            "newer": version_tuple(ultima) > version_tuple(__version__),
            "notes": str(data.get("notes") or "")[:MAX_NOTES],
            "date": str(data.get("date") or ""),
            "url": str(data.get("url") or "").strip() or app_site(),
            "files": [f for f in (data.get("files") or []) if isinstance(f, dict) and f.get("name")][:12],
            "stale": bool(getattr(res, "stale", False)),
        }
        return salida

    def announce(self) -> dict[str, Any]:
        """Lo que hay que decir UNA vez: si hay versión nueva y no se ha dicho ya, se devuelve y se apunta."""
        estado = self.check()
        if estado.get("newer") and estado["latest"] != estado.get("announced"):
            self.mark_seen(estado["latest"])
            return estado | {"announce": True}
        return estado | {"announce": False}


def register(server: MpvdServer, service: UpdateService) -> None:
    d = server.dispatcher

    @d.method("updates.check")
    async def check(ctx: RpcContext, force: bool = False) -> dict[str, Any]:
        """¿Hay una versión nueva? Consulta `latest.json` una vez al día (``force`` salta la caché).

        Devuelve `current`, `latest`, `newer`, `notes`, `date`, `url` y `files` (nombre, tamaño y SHA-256 de cada
        paquete, para quien quiera comprobar lo que descarga). NUNCA descarga ni instala nada."""
        return service.check(force=bool(force))

    @d.method("updates.announce")
    async def announce(ctx: RpcContext) -> dict[str, Any]:
        """Lo mismo, pero `announce` solo sale a `true` la primera vez que se ve esa versión nueva: es lo que
        usa el reproductor para decirlo una vez y no cada vez que se abre."""
        return service.announce()

    @d.method("updates.seen")
    async def seen(ctx: RpcContext, version: str) -> dict[str, Any]:
        """Apunta que esa versión ya se ha dicho (la usa el reproductor si enseña el aviso por su cuenta)."""
        if not isinstance(version, str) or not version.strip():
            raise RpcError(INVALID_PARAMS, t("hace falta la versión"))
        service.mark_seen(version.strip())
        return {"announced": version.strip()}
