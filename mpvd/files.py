"""Explorar las carpetas del equipo (H64), que es la única forma de elegir una película sin teclado.

Pedido por Ser pensando en una Raspberry conectada al televisor: allí no hay teclado, así que «teclea la ruta» —que
es lo único que había para añadir una carpeta a la biblioteca— no sirve de nada. Con esto se navega con arriba,
abajo, aceptar y atrás, que es justo lo que manda el mando de la tele (H61) y lo que manda un gamepad.

Dos métodos y nada más: ``files.places`` da las **puertas** (Vídeos, Música, Descargas, Imágenes, la carpeta
personal, las carpetas de la biblioteca y las unidades conectadas — un pincho USB en la Pi aparece ahí solo) y
``files.browse`` lista una carpeta. Reproducir una carpeta entera **no** se construye aquí: mpv 0.41 abre
directorios él mismo (``--directory-mode``, ``--directory-filter-types``, comprobado contra el mpv instalado), así
que el reproductor le pasa la ruta y mpv monta la lista.

Lo que NO hace, a propósito: no indexa, no recorre recursivamente y no recuerda nada. Para eso está la biblioteca,
que es lo que se escanea en segundo plano; esto es un navegador, y un navegador tiene que contestar al instante
aunque la carpeta tenga diez mil archivos.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mpvd.convert.presets import AUDIO_EXTS, VIDEO_EXTS
from mpvd.i18n import t
from mpvd.rpc import INVALID_PARAMS, RpcError
from mpvd.ytdl.downloads import _xdg_user_dir

if TYPE_CHECKING:
    from mpvd.server import MpvdServer, RpcContext

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".tif", ".tiff"}
PLAYLIST_EXTS = {".m3u", ".m3u8", ".pls", ".xspf", ".cue"}
PLAYABLE = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | PLAYLIST_EXTS
MAX_ENTRIES = 2000        # una carpeta con más que esto se corta y se dice: el menú no puede pintar diez mil filas


def kind_of(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PLAYLIST_EXTS:
        return "playlist"
    return "other"


def natural_key(name: str) -> list[Any]:
    """«Capítulo 2» antes que «Capítulo 10», y sin que las mayúsculas manden."""
    out: list[Any] = []
    digits = ""
    for ch in name:
        if ch.isdigit():
            digits += ch
            continue
        if digits:
            out.append((0, int(digits)))
            digits = ""
        out.append((1, ch.casefold()))
    if digits:
        out.append((0, int(digits)))
    return out


def drives() -> list[Path]:
    """Las unidades conectadas: un pincho o un disco USB, que en una Raspberry es justo lo que se va a ver."""
    out: list[Path] = []
    if sys.platform == "win32":
        out = [Path(f"{c}:\\") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").is_dir()]
    elif sys.platform == "darwin":
        out = _children(Path("/Volumes"))
    else:
        user = os.environ.get("USER") or ""
        for base in (Path("/media") / user, Path("/run/media") / user, Path("/media"), Path("/mnt")):
            out += _children(base)
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _children(base: Path) -> list[Path]:
    try:
        return sorted((c for c in base.iterdir() if c.is_dir() and not c.name.startswith(".")),
                      key=lambda c: natural_key(c.name))
    except OSError:
        return []


def places(library: list[str] | None = None) -> list[dict[str, Any]]:
    """Las puertas por las que se empieza a explorar, sin repetir ninguna."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: Path, title: str, kind: str) -> None:
        try:
            if not path.is_dir():
                return
        except OSError:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        rows.append({"path": key, "title": title, "kind": kind})

    for xdg, fallback, title in (("VIDEOS", "Videos", "Vídeos"), ("MUSIC", "Music", "Música"),
                                 ("DOWNLOAD", "Downloads", "Descargas"), ("PICTURES", "Pictures", "Imágenes")):
        base = _xdg_user_dir(xdg)
        add(base if base is not None else Path.home() / fallback, title, "user")
    for folder in library or []:
        add(Path(folder), Path(folder).name or folder, "library")
    for d in drives():
        add(d, d.name or str(d), "drive")
    add(Path.home(), "Carpeta personal", "home")
    return rows


def browse(path: str, hidden: bool = False, only_playable: bool = True) -> dict[str, Any]:
    """Una carpeta: sus subcarpetas y los archivos que este reproductor puede abrir."""
    raw = os.path.expanduser(str(path or "").strip())
    if not raw:
        raise RpcError(INVALID_PARAMS, t("hace falta una carpeta"))
    here = Path(raw)
    if not here.is_dir():
        raise RpcError(INVALID_PARAMS, t("no es una carpeta: %s") % (raw,))
    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(here) as it:
            for entry in it:
                if not hidden and entry.name.startswith("."):
                    continue
                if len(dirs) + len(files) >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                p = Path(entry.path)
                if is_dir:
                    dirs.append({"name": entry.name, "path": entry.path, "dir": True, "kind": "dir",
                                 "items": _count(p, hidden)})
                    continue
                k = kind_of(p)
                if only_playable and k == "other":
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                files.append({"name": entry.name, "path": entry.path, "dir": False, "kind": k, "size": size})
    except PermissionError:
        raise RpcError(INVALID_PARAMS, t("no se puede leer: %s") % (raw,)) from None
    dirs.sort(key=lambda r: natural_key(r["name"]))
    files.sort(key=lambda r: natural_key(r["name"]))
    parent = str(here.parent) if here.parent != here else ""
    return {"path": str(here), "name": here.name or str(here), "parent": parent,
            "entries": dirs + files, "dirs": len(dirs), "files": len(files), "truncated": truncated}


def _count(p: Path, hidden: bool) -> int:
    """Cuántas cosas útiles hay dentro, para saber si merece la pena entrar. Un solo nivel: entrar es barato."""
    n = 0
    try:
        with os.scandir(p) as it:
            for entry in it:
                if not hidden and entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir() or kind_of(Path(entry.name)) != "other":
                        n += 1
                except OSError:
                    continue
                if n >= MAX_ENTRIES:
                    break
    except OSError:
        return -1          # no se puede leer: la fila lo dirá en vez de mentir con un 0
    return n


def playlist(paths: list[str], data_dir: Path, name: str = "") -> dict[str, Any]:
    """Varios archivos convertidos en UNA cosa que se puede poner: un .m3u8 en la carpeta de datos.

    Hace falta para programar varias canciones o varios vídeos: una franja pone una cosa, no veinte, y un .m3u8 es
    el formato que ya sabe cargar el programador (`loadlist` + repetir mientras dure la franja, J5). Se escribe con
    rutas absolutas, así que también lo abre cualquier otro reproductor."""
    rows = [os.path.expanduser(str(x or "").strip()) for x in (paths or [])]
    rows = [r for r in rows if r]
    if not rows:
        raise RpcError(INVALID_PARAMS, t("hace falta al menos un archivo"))
    if len(rows) > MAX_ENTRIES:
        raise RpcError(INVALID_PARAMS, t("como mucho %s archivos") % (MAX_ENTRIES,))
    faltan = [r for r in rows if not Path(r).exists()]
    if faltan:
        raise RpcError(INVALID_PARAMS, t("no existe: %s") % (faltan[0],))
    carpeta = data_dir / "programadas"
    carpeta.mkdir(parents=True, exist_ok=True)
    limpio = re.sub(r"[^\w .·-]+", "", str(name or "").strip(), flags=re.UNICODE)[:60].strip()
    base = limpio or time.strftime("%Y-%m-%d %H.%M")
    destino = carpeta / f"{base}.m3u8"
    n = 2
    while destino.exists():
        destino = carpeta / f"{base} ({n}).m3u8"
        n += 1
    cuerpo = ["#EXTM3U", f"#PLAYLIST:{base}"]
    for r in rows:
        cuerpo.append(f"#EXTINF:-1,{Path(r).stem}")
        cuerpo.append(r)
    destino.write_text("\n".join(cuerpo) + "\n", encoding="utf-8")
    return {"file": str(destino), "name": base, "count": len(rows)}


def register(server: MpvdServer) -> None:
    d = server.dispatcher
    server.services["files"] = True

    @d.method("files.places")
    async def places_(ctx: RpcContext) -> dict[str, Any]:
        """The doors to start browsing from: the user's media folders, the library's folders, mounted drives and
        the home folder. Only the ones that exist."""
        folders: list[str] = []
        lib = getattr(server, "library", None)
        if lib is not None:
            with contextlib.suppress(Exception):
                folders = [r["path"] for r in await asyncio.to_thread(lib.store.folders)]
        return {"places": await asyncio.to_thread(places, folders)}

    @d.method("files.playlist")
    async def playlist_(ctx: RpcContext, paths: list[str], name: str = "") -> dict[str, Any]:
        """Varios archivos en un .m3u8, para poder programarlos como una sola cosa."""
        return await asyncio.to_thread(playlist, paths, server.settings.data_dir, name)

    @d.method("files.browse")
    async def browse_(ctx: RpcContext, path: str, hidden: bool = False,
                      only_playable: bool = True) -> dict[str, Any]:
        """List one folder: its subfolders (with how many useful things each has inside) and the files this player
        can open. Nothing is indexed and nothing is remembered: this answers at once."""
        return await asyncio.to_thread(browse, path, bool(hidden), bool(only_playable))
