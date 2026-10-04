"""Ver un torrent mientras se descarga (H59): la sesión de libtorrent y el lector que espera las piezas.

Decidido con Ser: **extra opcional y apagado por defecto**, como los servicios de nube. Lo que hace que esto se
pueda ver y no solo descargar es el lector: mpv pide un trozo de fichero, y en vez de secuencial a lo bruto
—que pelea con cualquier salto hacia delante— se le pone `set_piece_deadline` a las piezas de ESE trozo, que es
decirle a libtorrent «estas primero y ya». Lo demás se queda como lo trae libtorrent (DHT, LSD, cifrado, 200
conexiones), que son valores probados por mucha más gente que nosotros, y la subida no se estrangula: un cliente
que no devuelve nada es un cliente que no baja.

libtorrent no es una dependencia obligatoria: se importa cuando hace falta y, si no está, el servicio no se
anuncia en `capabilities` y el reproductor no ofrece nada, igual que con whisper o fpcalc.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

log = logging.getLogger("mpvd.torrent")

MAX_TORRENT_BYTES = 8 * 1024 * 1024   # un .torrent de más de 8 MB no es un .torrent
PIECE_DEADLINE_MS = 1500      # «esta pieza, para ya»: lo que se le pide a las del trozo que mpv está leyendo
READAHEAD_PIECES = 8          # y a las siguientes, con menos prisa, para que no haya un tirón en cada chunk
CHUNK = 256 * 1024            # lo que se le entrega a mpv de una vez (lo mismo que sirve una sala, H44/C1)
WAIT_SLICE = 0.05
PLAYABLE = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".flv", ".ogv",
            ".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac"}


def load() -> Any | None:
    """El módulo de libtorrent, o None si no está instalado."""
    try:
        import libtorrent  # noqa: PLC0415
    except Exception:       # noqa: BLE001 - una rueda que no carga es lo mismo que no tenerla
        return None
    return libtorrent


def available() -> bool:
    return load() is not None


def is_magnet(text: str) -> bool:
    return text.strip().lower().startswith("magnet:?")


def is_torrent_file(text: str) -> bool:
    t = text.strip()
    return t.lower().endswith(".torrent") and Path(t).is_file()


def is_torrent_url(text: str) -> bool:
    """Un .torrent que está en la web: es lo que se arrastra desde el navegador, no un fichero del disco."""
    t = text.strip().lower()
    return t.startswith(("http://", "https://")) and t.split("?")[0].split("#")[0].endswith(".torrent")


def looks_like_torrent(text: str) -> bool:
    return is_magnet(text) or is_torrent_file(text) or is_torrent_url(text)


def fetch_torrent(url: str, cache_dir: Path, timeout: float = 30.0) -> Path:
    """Baja un .torrent de la web a la caché y devuelve su ruta. Es un fichero de unos pocos KB."""
    import urllib.request   # noqa: PLC0415 - solo hace falta aquí

    destino = cache_dir / (hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:16] + ".torrent")
    if destino.is_file() and destino.stat().st_size > 0:
        return destino
    req = urllib.request.Request(url, headers={"User-Agent": "mpvd"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - la URL la pega quien usa el programa
        datos = r.read(MAX_TORRENT_BYTES + 1)
    if len(datos) > MAX_TORRENT_BYTES:
        raise ValueError("ese .torrent es demasiado grande")
    if not datos.startswith(b"d"):          # bencode siempre empieza por un diccionario
        raise ValueError("eso no es un .torrent")
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_bytes(datos)
    tmp.replace(destino)
    return destino


def pick_file(ti: Any) -> int:
    """El fichero que se va a ver: el más grande de los reproducibles, que en la práctica es la película."""
    best, best_size = -1, -1
    files = ti.files()
    for i in range(files.num_files()):
        name = files.file_path(i)
        size = files.file_size(i)
        if Path(name).suffix.lower() in PLAYABLE and size > best_size:
            best, best_size = i, size
    if best < 0:                      # nada reproducible: el más grande, que lo diga mpv
        for i in range(files.num_files()):
            if files.file_size(i) > best_size:
                best, best_size = i, files.file_size(i)
    return best


def file_rows(ti: Any) -> list[dict[str, Any]]:
    files = ti.files()
    return [{"index": i, "name": Path(files.file_path(i)).name, "path": files.file_path(i),
             "size": files.file_size(i),
             "playable": Path(files.file_path(i)).suffix.lower() in PLAYABLE}
            for i in range(files.num_files())]


class Reader:
    """Lee un trozo del fichero aunque todavía no esté descargado: pide sus piezas con prisa y espera a tenerlas.

    Es la pieza que convierte «descargar» en «ver». mpv pide rangos por HTTP y aquí se traducen a piezas con
    ``map_file``; a las del trozo pedido se les pone fecha límite y a las siguientes también, con menos prisa, para
    que no haya un tirón en cada chunk. Un salto hacia delante no rompe nada: el rango siguiente es otro y sus
    piezas se piden igual.
    """

    def __init__(self, handle: Any, index: int, ti: Any, timeout: float = 120.0):
        self.handle, self.index, self.ti, self.timeout = handle, index, ti, timeout
        self.size = ti.files().file_size(index)
        self.pieces = ti.num_pieces()

    def _pieces_for(self, offset: int, length: int) -> tuple[int, int]:
        req = self.ti.map_file(self.index, max(0, offset), max(1, length))
        first = int(req.piece)
        last = int(self.ti.map_file(self.index, max(0, offset + length - 1), 1).piece)
        return first, min(last, self.pieces - 1)

    def _ask(self, first: int, last: int) -> None:
        for p in range(first, min(last + 1, self.pieces)):
            if not self.handle.have_piece(p):
                self.handle.set_piece_deadline(p, PIECE_DEADLINE_MS)
        for n, p in enumerate(range(last + 1, min(last + 1 + READAHEAD_PIECES, self.pieces))):
            if not self.handle.have_piece(p):
                self.handle.set_piece_deadline(p, PIECE_DEADLINE_MS * (n + 2))

    async def _await_pieces(self, first: int, last: int) -> None:
        deadline = time.monotonic() + self.timeout
        self._ask(first, last)
        while True:
            if all(self.handle.have_piece(p) for p in range(first, last + 1)):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"las piezas {first}-{last} no llegaron en {self.timeout:.0f}s")
            await asyncio.sleep(WAIT_SLICE)
            self._ask(first, last)

    async def stream(self, start: int, end: int, path: Path) -> AsyncIterator[bytes]:
        """Los bytes de ``start`` a ``end`` (incluido), esperando lo que falte por descargar."""
        pos = start
        while pos <= end:
            length = min(CHUNK, end - pos + 1)
            first, last = self._pieces_for(pos, length)
            await self._await_pieces(first, last)
            data = await asyncio.to_thread(_read_at, path, pos, length)
            if not data:
                raise TimeoutError(f"no se pudo leer en {pos}")
            yield data
            pos += len(data)


def _read_at(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(length)


# -- la sesión ---------------------------------------------------------------------------------

TRACKERS_URL = "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt"
TRACKERS_MAX_AGE = 7 * 24 * 3600      # se refresca una vez por semana, no en cada torrent


def bundled_trackers() -> list[str]:
    """La copia que viaja con el repo: sin red, o con GitHub caído, los torrents siguen encontrando gente."""
    try:
        text = (Path(__file__).parent / "trackers_best.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def trackers(cache_dir: Path, allow_network: bool = True) -> list[str]:
    """Los 20 «mejores» de ngosang/trackerslist, en caché semanal, con la copia del repo como respaldo."""
    cache = cache_dir / "trackers_best.txt"
    try:
        fresh = cache.is_file() and (time.time() - cache.stat().st_mtime) < TRACKERS_MAX_AGE
    except OSError:
        fresh = False
    if not fresh and allow_network:
        try:
            import urllib.request  # noqa: PLC0415
            with urllib.request.urlopen(TRACKERS_URL, timeout=15) as r:   # noqa: S310 - URL fija y https
                text = r.read().decode("utf-8", "replace")
            if "://" in text:
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(cache)
        except Exception as exc:      # noqa: BLE001 - sin lista nueva se usa la de antes o la del repo
            log.info("torrent: no se pudo refrescar la lista de trackers (%s)", exc)
    if cache.is_file():
        try:
            rows = [ln.strip() for ln in cache.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
            if rows:
                return rows
        except OSError:
            pass
    return bundled_trackers()


class Client:
    """La sesión de libtorrent, con los valores de fábrica salvo lo que Ser decidió cambiar."""

    def __init__(self, lt: Any, save_path: Path, *, anonymous: bool = False, proxy: dict[str, Any] | None = None,
                 app: str = "Atalaya"):
        self.lt = lt
        self.save_path = save_path
        save_path.mkdir(parents=True, exist_ok=True)
        settings: dict[str, Any] = {
            "user_agent": f"{app}/1.0 libtorrent/{lt.__version__}",
            "alert_mask": lt.alert.category_t.error_notification | lt.alert.category_t.status_notification,
        }
        if anonymous:
            # no se anuncia el cliente ni se usan trackers por HTTP sin proxy; lo pidió Ser como INTERRUPTOR,
            # no como norma, porque cuesta velocidad y no es lo que la mayoría necesita
            settings["anonymous_mode"] = True
        if proxy:
            settings.update({
                "proxy_type": lt.proxy_type_t.socks5_pw if proxy.get("username") else lt.proxy_type_t.socks5,
                "proxy_hostname": str(proxy.get("host") or ""),
                "proxy_port": int(proxy.get("port") or 1080),
                "proxy_username": str(proxy.get("username") or ""),
                "proxy_password": str(proxy.get("password") or ""),
                "proxy_peer_connections": True,
                "proxy_hostnames": True,
            })
        self.session = lt.session(settings)

    def add(self, link: str, trackers_list: list[str] | None = None) -> Any:
        lt = self.lt
        if is_magnet(link):
            params = lt.parse_magnet_uri(link)
            params.save_path = str(self.save_path)
        else:
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(link)
            params.save_path = str(self.save_path)
        handle = self.session.add_torrent(params)
        if trackers_list:
            self.add_trackers(handle, trackers_list)
        return handle

    def add_trackers(self, handle: Any, urls: list[str]) -> int:
        """Nunca en un torrent privado: anunciarse fuera de su tracker es motivo de expulsión."""
        ti = handle.torrent_file()
        if ti is not None and ti.priv():
            return 0
        n = 0
        for url in urls:
            try:
                handle.add_tracker({"url": url})
                n += 1
            except Exception:   # noqa: BLE001, PERF203 - un tracker mal formado no tumba el torrent
                continue
        return n

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.session.pause()
