"""Índice local de lo descargado: SQLite en ``<salida>/.atalaya/index.db``.

Para qué sirve, en una frase: que la aplicación sepa lo que ya tiene antes de volver a bajarlo. Con eso se responde
«¿este vídeo ya está?» (aunque esté en otra carpeta), «¿qué falta de esta lista?» y «¿qué bajé la semana pasada?», y
una suscripción puede encolar solo lo nuevo en vez de preguntarle a yt-dlp vídeo por vídeo.

Claves:

- **Dedupe por ``video_id`` + ``perfil``.** El mismo vídeo en Opus y en 720p son dos filas: son dos ficheros.
- **Nada de rutas absolutas como identidad.** La identidad es el id del sitio; la ruta solo dice dónde está.
- Una conexión por operación (``check_same_thread=False``): el índice lo escriben el bucle de descargas y el sondeo de
  suscripciones, que no comparten hilo.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

FOLDER = ".atalaya"
DB_NAME = "index.db"
CHUNK = 1024 * 1024

# La plantilla del repo acaba en «[%(id)s].%(ext)s»: el id está entre corchetes y antes de la extensión.
ID_EN_RUTA = re.compile(r"\[([A-Za-z0-9_-]{6,})\](?:\.[A-Za-z0-9]{1,5})?$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT    NOT NULL,
    perfil      TEXT    NOT NULL DEFAULT '',
    canal       TEXT    NOT NULL DEFAULT '',
    titulo      TEXT    NOT NULL DEFAULT '',
    ruta        TEXT    NOT NULL DEFAULT '',
    contenedor  TEXT    NOT NULL DEFAULT '',
    bytes       INTEGER NOT NULL DEFAULT 0,
    fecha       REAL,                       -- cuándo se publicó (epoch), si el origen lo dice
    descargado  REAL    NOT NULL DEFAULT 0, -- cuándo se bajó
    hash        TEXT    NOT NULL DEFAULT '',
    UNIQUE (video_id, perfil)
);
CREATE INDEX IF NOT EXISTS items_canal ON items (canal);
CREATE INDEX IF NOT EXISTS items_descargado ON items (descargado DESC);
"""


def default_path(salida: str | os.PathLike[str]) -> Path:
    """``<salida>/.atalaya/index.db``: el índice vive junto a lo descargado, no en los datos de la aplicación."""
    return Path(salida).expanduser() / FOLDER / DB_NAME


def id_de_ruta(ruta: str | os.PathLike[str]) -> str:
    """El id del sitio a partir del nombre del fichero (``Título [dQw4w9WgXcQ].mkv`` → ``dQw4w9WgXcQ``)."""
    nombre = Path(str(ruta)).name
    m = ID_EN_RUTA.search(nombre)
    if m:
        return m.group(1)
    # nombre sin corchetes: «Título [id] - 01.mp4» o el id suelto como nombre
    m = re.search(r"\[([A-Za-z0-9_-]{6,})\]", nombre)
    if m:
        return m.group(1)
    base = Path(nombre).stem
    return base if re.fullmatch(r"[A-Za-z0-9_-]{6,}", base) else ""


def hash_fichero(ruta: str | os.PathLike[str]) -> str:
    """SHA-256 completo del fichero (``sha256:…``); cadena vacía si no se puede leer.

    Se calcula solo cuando se pide —un fichero de 2 GB tarda—: sirve para detectar un fichero cambiado por fuera, no
    para identificar el vídeo, que ya tiene id.
    """
    h = hashlib.sha256()
    try:
        with open(ruta, "rb") as fh:
            for bloque in iter(lambda: fh.read(CHUNK), b""):
                h.update(bloque)
    except OSError:
        return ""
    return "sha256:" + h.hexdigest()


@dataclass
class Item:
    video_id: str
    perfil: str = ""
    canal: str = ""
    titulo: str = ""
    ruta: str = ""
    contenedor: str = ""
    bytes: int = 0
    fecha: float | None = None
    descargado: float = field(default_factory=time.time)
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"video_id": self.video_id, "perfil": self.perfil, "canal": self.canal, "titulo": self.titulo,
                "ruta": self.ruta, "contenedor": self.contenedor, "bytes": self.bytes, "fecha": self.fecha,
                "descargado": self.descargado, "hash": self.hash}

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Item":
        return cls(video_id=row["video_id"], perfil=row["perfil"], canal=row["canal"], titulo=row["titulo"],
                   ruta=row["ruta"], contenedor=row["contenedor"], bytes=int(row["bytes"] or 0),
                   fecha=row["fecha"], descargado=float(row["descargado"] or 0.0), hash=row["hash"])


class Index:
    """El índice. ``ruta`` explícita (tests) o ``salida`` (la carpeta de descargas, de donde sale ``.atalaya``)."""

    def __init__(self, ruta: str | os.PathLike[str] | None = None, salida: str | os.PathLike[str] | None = None):
        if ruta is None:
            if salida is None:
                raise ValueError("index needs a path or a download folder")
            ruta = default_path(salida)
        self.path = Path(ruta)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._crear()

    # -- conexión ------------------------------------------------------------------------------

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _crear(self) -> None:
        with self._con() as con:
            con.executescript(SCHEMA)

    # -- escritura -----------------------------------------------------------------------------

    def add(self, video_id: str, perfil: str = "", canal: str = "", titulo: str = "", ruta: str = "",
            contenedor: str = "", bytes_: int = 0, fecha: float | None = None,
            descargado: float | None = None, hash_: str = "") -> bool:
        """Anota un fichero. ``False`` si esa pareja (id, perfil) ya estaba —no se duplica ni se pisa la ruta."""
        if not video_id:
            raise ValueError("video_id required")
        with self._con() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO items (video_id, perfil, canal, titulo, ruta, contenedor, bytes, fecha,"
                " descargado, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (video_id, perfil or "", canal or "", titulo or "", str(ruta or ""), contenedor or "",
                 int(bytes_ or 0), fecha, float(descargado if descargado is not None else time.time()), hash_ or ""))
            return cur.rowcount > 0

    def actualizar(self, video_id: str, perfil: str = "", **campos: Any) -> bool:
        """Cambia campos de una fila existente (ruta nueva, hash calculado, bytes reales)."""
        validos = {k for k in ("canal", "titulo", "ruta", "contenedor", "bytes", "fecha", "descargado", "hash")}
        datos = {k: v for k, v in campos.items() if k in validos}
        if not datos:
            return False
        sets = ", ".join(f"{k} = ?" for k in datos)
        with self._con() as con:
            cur = con.execute(f"UPDATE items SET {sets} WHERE video_id = ? AND perfil = ?",
                              (*datos.values(), video_id, perfil or ""))
            return cur.rowcount > 0

    def anotar_salida(self, video_id: str, ruta: str, perfil: str = "", canal: str = "", titulo: str = "",
                      bytes_: int = 0, hash_: bool = False) -> bool:
        """Atajo para el final de una descarga: si ``bytes_`` es 0 se pregunta al sistema de ficheros."""
        tam = int(bytes_ or 0)
        if not tam:
            try:
                tam = Path(ruta).stat().st_size
            except OSError:
                tam = 0
        return self.add(video_id, perfil=perfil, canal=canal, titulo=titulo, ruta=str(ruta),
                        contenedor=Path(str(ruta)).suffix.lstrip(".").lower(), bytes_=tam,
                        hash_=hash_fichero(ruta) if hash_ else "")

    def borrar(self, video_id: str, perfil: str | None = None) -> int:
        with self._con() as con:
            if perfil is None:
                cur = con.execute("DELETE FROM items WHERE video_id = ?", (video_id,))
            else:
                cur = con.execute("DELETE FROM items WHERE video_id = ? AND perfil = ?", (video_id, perfil))
            return cur.rowcount

    # -- consultas -----------------------------------------------------------------------------

    def ya_descargado(self, video_id: str, perfil: str | None = None) -> Item | None:
        """La fila de ese vídeo (la más reciente). Con ``perfil``, solo si se bajó con ese perfil."""
        if not video_id:
            return None
        sql = "SELECT * FROM items WHERE video_id = ?"
        args: list[Any] = [video_id]
        if perfil is not None:
            sql += " AND perfil = ?"
            args.append(perfil)
        sql += " ORDER BY descargado DESC LIMIT 1"
        with self._con() as con:
            row = con.execute(sql, args).fetchone()
        return Item.from_row(row) if row else None

    def faltan(self, ids: list[str], perfil: str | None = None) -> list[str]:
        """Los ids de la lista que NO están en el índice, en el mismo orden y sin repetir."""
        unicos = list(dict.fromkeys(i for i in ids if i))
        if not unicos:
            return []
        presentes: set[str] = set()
        with self._con() as con:
            for inicio in range(0, len(unicos), 400):   # SQLite: máximo de parámetros por consulta
                trozo = unicos[inicio:inicio + 400]
                marcas = ",".join("?" * len(trozo))
                sql = f"SELECT video_id FROM items WHERE video_id IN ({marcas})"
                args: list[Any] = list(trozo)
                if perfil is not None:
                    sql += " AND perfil = ?"
                    args.append(perfil)
                presentes.update(r[0] for r in con.execute(sql, args))
        return [i for i in unicos if i not in presentes]

    def historial(self, n: int = 20, canal: str | None = None) -> list[Item]:
        """Lo último descargado, de más nuevo a más viejo (``n`` filas; 0 = todas)."""
        sql = "SELECT * FROM items"
        args: list[Any] = []
        if canal:
            sql += " WHERE canal = ?"
            args.append(canal)
        sql += " ORDER BY descargado DESC"
        if n and n > 0:
            sql += " LIMIT ?"
            args.append(int(n))
        with self._con() as con:
            return [Item.from_row(r) for r in con.execute(sql, args)]

    def por_canal(self, canal: str, n: int = 50) -> list[Item]:
        return self.historial(n, canal=canal)

    def canales(self) -> list[dict[str, Any]]:
        """Un renglón por canal: cuántos ficheros y cuánto ocupan (para la vista de suscripciones)."""
        with self._con() as con:
            rows = con.execute("SELECT canal, COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b FROM items"
                               " GROUP BY canal ORDER BY n DESC").fetchall()
        return [{"canal": r["canal"], "items": int(r["n"]), "bytes": int(r["b"])} for r in rows]

    def total(self) -> dict[str, Any]:
        with self._con() as con:
            row = con.execute("SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b FROM items").fetchone()
        return {"items": int(row["n"]), "bytes": int(row["b"])}

    def __len__(self) -> int:
        return int(self.total()["items"])


__all__ = ["FOLDER", "DB_NAME", "Index", "Item", "default_path", "hash_fichero", "id_de_ruta"]
