"""Suscripciones a canales sin API: el feed Atom público de YouTube, reglas por canal y sondeo incremental.

**Por qué el feed y no la API.** YouTube publica un Atom por canal —``feeds/videos.xml?channel_id=UC…``— con las 15
últimas entradas: id, título, fecha y autor. No hay clave, no hay cuota y no hay cuenta. Para «novedades» es
exactamente lo que hace falta: se sondea cada X, se comparan los ids con lo ya visto y con el índice local, y solo se
encola lo que no estaba.

**El fondo histórico no se hace con el feed** (15 entradas es nada): se pide la URL del canal con ``--dateafter`` +
``--download-archive`` + ``--break-on-existing``, que sí recorre todo y corta al llegar a lo conocido. Eso está en
``historico_args()`` y lo usa el RPC ``ytdl.subs.historico``.

**Reglas por canal** (``Regla``): perfil de descarga, carpeta, duración mínima, regex de título para dejar fuera
directos/shorts, ventana de horas (no bajar de madrugada) y tope de vídeos nuevos por sondeo. Se guardan en
``<datos>/ytdl-suscripciones.json`` con escritura atómica.

**La duración solo se filtra cuando se sabe.** El feed de YouTube no la trae; un RSS de podcast sí (``itunes:duration``)
o el propio ``media:content``. Cuando no viene, el vídeo pasa el filtro y la duración se comprueba en la descarga: aquí
no se inventa un dato para descartar contenido.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from mpvd.i18n import t
from mpvd.subscriptions.rss import FeedError, parse_feed
from mpvd.ytdl.presets import DownloadSpec

log = logging.getLogger("mpvd.ytdl.subs")

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
FEED_CHANNEL = "https://www.youtube.com/channel/{}"
WATCH = "https://www.youtube.com/watch?v={}"
INTERVALOS = (30, 360, 1440)                     # minutos: cada 30 min · cada 6 h · diario
PERFILES_SUSCRIPCION = ("opus_origen", "video_720", "compatible")   # «pistas» y «convertir» piden más datos
MAX_VISTOS = 500
MAX_SUSCRIPCIONES = 200
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")   # YouTube sirve el feed igual, pero sin UA de script es más fácil el 429
FETCH_TIMEOUT = 20.0
CANAL_RE = re.compile(r"UC[A-Za-z0-9_-]{20,24}")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{2,30}$")


class SubError(ValueError):
    """Una regla o una suscripción mal puesta (siempre con el motivo legible)."""


# -- direcciones -----------------------------------------------------------------------------------


def feed_url(valor: str) -> str:
    """La URL del feed a partir del id (``UC…``), la URL del canal o la del propio feed."""
    v = (valor or "").strip()
    if not v:
        raise SubError(t("falta el canal"))
    if "feeds/videos.xml" in v:
        return v
    m = CANAL_RE.search(v)
    if m:
        return FEED.format(m.group(0))
    if HANDLE_RE.match(v) or "/@" in v:
        raise SubError(t("con un @nombre hace falta resolver el canal una vez (abre el canal y dale a «Seguir»)"))
    raise SubError(t("no reconozco ese canal: pega la dirección del canal o su id (UC…)"))


def canal_id(valor: str) -> str:
    """El id ``UC…`` que hay en la URL o en el propio id; ``""`` si no se puede saber sin red."""
    m = CANAL_RE.search(valor or "")
    return m.group(0) if m else ""


def watch_url(video_id: str) -> str:
    return WATCH.format(video_id)


def fecha_arg(ts: float | None) -> str:
    """Epoch → ``AAAAMMDD`` para ``--dateafter`` (hora local, que es como lo lee el usuario)."""
    if not ts:
        return ""
    return time.strftime("%Y%m%d", time.localtime(float(ts)))


def historico_args(desde: float | None = None, archivo: str | Path = "", break_on_existing: bool = True,
                   hasta: float | None = None) -> list[str]:
    """Argumentos del fondo histórico de un canal: recorre todo, salta lo del archivo y corta al llegar a lo viejo.

    ``--break-on-existing`` es lo que hace barata la comprobación: al volver a pedir el canal, yt-dlp para en el primer
    vídeo que ya está en ``--download-archive`` en vez de recorrer las 200 entradas. Solo tiene sentido con archivo.
    """
    args: list[str] = []
    if desde:
        args += ["--dateafter", fecha_arg(desde)]
    if hasta:
        args += ["--datebefore", fecha_arg(hasta)]
    if archivo:
        args += ["--download-archive", str(archivo)]
        if break_on_existing:
            args += ["--break-on-existing"]
    return args


# -- reglas ----------------------------------------------------------------------------------------


@dataclass
class Regla:
    """Lo que se aplica a todo lo que llegue de un canal."""

    perfil: str = "video_720"        # opus_origen | video_720 | compatible
    carpeta: str = ""                # "" = la carpeta de descargas de ese tipo
    duracion_min: int = 0            # s: por debajo se descarta (0 = sin mínimo)
    duracion_max: int = 0            # s: por encima se descarta (0 = sin máximo)
    titulo_excluir: str = ""         # regex: lo que casa NO se baja (directos, shorts, «#shorts»)
    hora_desde: int = 0              # ventana local, 0..23; desde > hasta cruza la medianoche
    hora_hasta: int = 24             # 24 = hasta el final del día
    tope: int = 10                   # máx. vídeos nuevos por sondeo (los más recientes primero)
    intervalo: int = 360             # minutos: 30 | 360 | 1440
    subtitulos: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Regla":
        r = cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})
        r.validar()
        return r

    def validar(self) -> None:
        if self.perfil not in PERFILES_SUSCRIPCION:
            raise SubError(t("perfil no válido para una suscripción: %s") % self.perfil)
        if self.intervalo not in INTERVALOS:
            raise SubError(t("el intervalo tiene que ser 30, 360 o 1440 minutos"))
        if not (0 <= int(self.hora_desde) <= 23) or not (0 <= int(self.hora_hasta) <= 24):
            raise SubError(t("las horas van de 0 a 24"))
        if int(self.tope) < 1:
            raise SubError(t("el tope por sondeo tiene que ser al menos 1"))
        if int(self.duracion_min) < 0 or int(self.duracion_max) < 0:
            raise SubError(t("las duraciones no pueden ser negativas"))
        if self.titulo_excluir:
            try:
                re.compile(self.titulo_excluir)
            except re.error as exc:
                raise SubError(t("esa expresión de título no vale: %s") % exc) from None

    # -- filtros --

    def excluye_titulo(self, titulo: str) -> bool:
        if not self.titulo_excluir:
            return False
        try:
            return re.search(self.titulo_excluir, titulo or "", re.IGNORECASE) is not None
        except re.error:
            return False

    def duracion_vale(self, duracion: float | None) -> bool:
        """Sin dato de duración no se descarta: se comprueba al bajar (el feed de YouTube no la trae)."""
        if duracion is None:
            return True
        if self.duracion_min and duracion < self.duracion_min:
            return False
        return not (self.duracion_max and duracion > self.duracion_max)

    def en_ventana(self, ahora: float | None = None) -> bool:
        """¿Estamos dentro de las horas en que se permite bajar? Ventana vacía (0→24) siempre."""
        hora = time.localtime(ahora if ahora is not None else time.time()).tm_hour
        desde, hasta = int(self.hora_desde), int(self.hora_hasta)
        if desde == hasta or (desde == 0 and hasta >= 24):
            return True
        if desde < hasta:
            return desde <= hora < hasta
        return hora >= desde or hora < hasta     # cruza la medianoche

    def pasa(self, entrada: dict[str, Any]) -> bool:
        return not self.excluye_titulo(str(entrada.get("title") or "")) \
            and self.duracion_vale(entrada.get("duration"))


# -- suscripciones ---------------------------------------------------------------------------------


@dataclass
class Suscripcion:
    canal: str                                # id UC… (o la URL del feed, para RSS de otros sitios)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    titulo: str = ""
    url: str = ""                             # la dirección que se le dio (la del canal), para enseñarla
    regla: Regla = field(default_factory=Regla)
    activa: bool = True
    ultima: float = 0.0                       # último sondeo (epoch)
    ultimo_error: str = ""
    nuevos: int = 0                           # cuántos encoló el último sondeo
    total: int = 0                            # cuántos ha encolado desde que existe
    creada: float = field(default_factory=time.time)
    vistos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "regla"}
        d["regla"] = self.regla.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Suscripcion":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "regla"}
        s = cls(**known)
        s.regla = Regla.from_dict(d.get("regla"))
        if not s.canal:
            raise SubError(t("suscripción sin canal"))
        return s

    def marcar_vistos(self, ids: Iterable[str]) -> None:
        for i in ids:
            if i and i not in self.vistos:
                self.vistos.append(i)
        if len(self.vistos) > MAX_VISTOS:
            del self.vistos[: len(self.vistos) - MAX_VISTOS]

    def debida(self, ahora: float | None = None) -> bool:
        """Toca sondearla: activa, ha pasado su intervalo y estamos en su ventana de horas."""
        ahora = ahora if ahora is not None else time.time()
        if not self.activa:
            return False
        return (ahora - float(self.ultima or 0.0)) >= int(self.regla.intervalo) * 60 - 5 \
            and self.regla.en_ventana(ahora)

    def estado(self) -> str:
        """Texto de la fila en el menú: «cada 6 h · 3 nuevos · error: …»."""
        cada = {30: t("cada 30 min"), 360: t("cada 6 h"), 1440: t("cada día")}.get(self.regla.intervalo, "")
        partes = [cada]
        if self.nuevos:
            partes.append(t("%d nuevos") % self.nuevos)
        if not self.activa:
            partes.insert(0, t("en pausa"))
        if self.ultimo_error:
            partes.append(t("error: %s") % self.ultimo_error[:60])
        elif self.ultima:
            partes.append(t("visto %s") % time.strftime("%d-%b %H:%M", time.localtime(self.ultima)))
        return " · ".join(p for p in partes if p)


class Suscripciones:
    """Las suscripciones y sus reglas, en ``<datos>/ytdl-suscripciones.json``."""

    def __init__(self, path: str | os.PathLike[str] | None):
        self.path = Path(path) if path else None
        self.items: list[Suscripcion] = []
        self.cargar()

    # -- persistencia --

    def cargar(self) -> None:
        self.items = []
        if self.path is None:
            return
        try:
            datos = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in datos.get("suscripciones", []) if isinstance(datos, dict) else []:
            try:
                self.items.append(Suscripcion.from_dict(row))
            except (SubError, TypeError, ValueError) as exc:
                log.warning("suscripción descartada: %s", exc)

    def guardar(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "suscripciones": [s.to_dict() for s in self.items]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- CRUD --

    def lista(self) -> list[dict[str, Any]]:
        return [{**s.to_dict(), "estado": s.estado(), "debida": s.debida()} for s in self.items]

    def get(self, sid: str) -> Suscripcion | None:
        return next((s for s in self.items if s.id == sid), None)

    def por_canal(self, canal: str) -> Suscripcion | None:
        for s in self.items:
            if s.canal == canal or s.canal.endswith(canal):
                return s
        return None

    def add(self, valor: str, titulo: str = "", **cambios: Any) -> Suscripcion:
        """Añade un canal. ``valor`` puede ser el id, la URL del canal o la del feed."""
        canal = canal_id(valor) or (valor or "").strip()
        if "feeds/" in canal:
            # un feed cualquiera (podcast, blog): lo que identifica es la URL, no un UC…
            url = canal
        else:
            if not canal:
                raise SubError(t("falta el canal"))
            url = FEED_CHANNEL.format(canal)
        ya = next((s for s in self.items if s.canal == canal), None)
        if ya is not None:
            raise SubError(t("ya sigues ese canal"))
        if len(self.items) >= MAX_SUSCRIPCIONES:
            raise SubError(t("demasiados canales seguidos (máximo %d)") % MAX_SUSCRIPCIONES)
        regla = Regla(**{k: v for k, v in cambios.items() if k in Regla.__dataclass_fields__})
        regla.validar()
        s = Suscripcion(canal=canal, titulo=titulo or "", url=url, regla=regla)
        self.items.append(s)
        self.guardar()
        return s

    def update(self, sid: str, **cambios: Any) -> Suscripcion:
        s = self.get(sid)
        if s is None:
            raise SubError(t("esa suscripción no existe"))
        datos_regla = {k: v for k, v in cambios.items() if k in Regla.__dataclass_fields__}
        if datos_regla:
            r = Regla(**{**s.regla.to_dict(), **datos_regla})
            r.validar()
            s.regla = r
        if "titulo" in cambios and cambios["titulo"] is not None:
            s.titulo = str(cambios["titulo"])
        if "activa" in cambios and cambios["activa"] is not None:
            s.activa = bool(cambios["activa"])
        self.guardar()
        return s

    def borrar(self, sid: str) -> bool:
        antes = len(self.items)
        self.items = [s for s in self.items if s.id != sid]
        if len(self.items) != antes:
            self.guardar()
            return True
        return False

    def anotar(self, sid: str, nuevos: int = 0, error: str = "", vistos: Iterable[str] = (), cuando: float | None = None) -> None:
        """Apunta el resultado de un sondeo (y guarda)."""
        s = self.get(sid)
        if s is None:
            return
        s.ultima = cuando if cuando is not None else time.time()
        s.nuevos, s.ultimo_error = int(nuevos), error or ""
        if nuevos:
            s.total += int(nuevos)
        s.marcar_vistos(vistos)
        self.guardar()

    def debidas(self, ahora: float | None = None) -> list[Suscripcion]:
        return [s for s in self.items if s.debida(ahora)]


# -- el feed ---------------------------------------------------------------------------------------


def leer_feed(valor: str, fetcher: Callable[[str], bytes] | None = None,
              timeout: float = FETCH_TIMEOUT) -> bytes:
    """Descarga el feed (bloqueante). ``fetcher`` lo pone un test: nunca se toca la red en la batería."""
    url = feed_url(valor)
    if fetcher is not None:
        return fetcher(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/atom+xml, application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 - https fijo o el que dio el usuario
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise SubError(t("no se pudo leer el feed: %s") % exc) from None


def entradas(datos: bytes | str, limite: int = 50) -> list[dict[str, Any]]:
    """Filas del feed: ``[{video_id, title, url, published, duration}]``, las más nuevas primero."""
    try:
        feed = parse_feed(datos, max_entries=limite)
    except FeedError as exc:
        raise SubError(str(exc)) from None
    filas: list[dict[str, Any]] = []
    for e in feed.newest_first():
        vid = video_id_de(e.id, e.url)
        filas.append({"video_id": vid, "title": e.title, "url": e.url or (watch_url(vid) if vid else ""),
                      "published": e.published, "duration": e.duration})
    return filas


def video_id_de(entry_id: str, url: str) -> str:
    base = (entry_id or "").strip()
    if base.startswith("yt:video:"):
        base = base[len("yt:video:"):]
    if base and re.fullmatch(r"[A-Za-z0-9_-]{6,}", base):
        return base
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url or "")
    if m:
        return m.group(1)
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", url or "")
    return m.group(1) if m else ""


def nuevos(suscripcion: Suscripcion, filas: list[dict[str, Any]], indice: Any = None) -> list[dict[str, Any]]:
    """Lo que hay que encolar: no visto, no descargado (si hay índice), pasa los filtros y cabe en el tope."""
    salida: list[dict[str, Any]] = []
    vistos = set(suscripcion.vistos)
    for fila in filas:
        vid = str(fila.get("video_id") or "")
        if not vid or vid in vistos:
            continue
        if indice is not None and indice.ya_descargado(vid) is not None:
            continue
        if not suscripcion.regla.pasa(fila):
            continue
        salida.append(fila)
        if len(salida) >= int(suscripcion.regla.tope):
            break
    return salida


def spec_de(fila: dict[str, Any], suscripcion: Suscripcion) -> DownloadSpec:
    """El trabajo de descarga de un vídeo del feed, con el perfil de la regla y prioridad baja (es de fondo)."""
    url = str(fila.get("url") or watch_url(str(fila.get("video_id") or "")))
    return DownloadSpec(url=url, perfil=suscripcion.regla.perfil, playlist=False, archive=True,
                        subtitles=bool(suscripcion.regla.subtitulos), title=str(fila.get("title") or ""),
                        extra={"priority": "low", "subscription": suscripcion.id,
                               "video_id": fila.get("video_id")})


def spec_historico(valor: str, suscripcion: Suscripcion | None = None, desde: float | None = None,
                   hasta: float | None = None, archivo: str | Path = "", perfil: str | None = None) -> DownloadSpec:
    """El fondo histórico: la URL del canal entera, con fecha de corte y archivo (corta al llegar a lo conocido)."""
    url = valor if "://" in (valor or "") else FEED_CHANNEL.format(canal_id(valor) or valor)
    spec = DownloadSpec(url=url, perfil=perfil or (suscripcion.regla.perfil if suscripcion else "video_720"),
                        playlist=True, archive=True, list_folder=True)
    if desde:
        spec.extra["dateafter"] = fecha_arg(desde)
    if hasta:
        spec.extra["datebefore"] = fecha_arg(hasta)
    if archivo:
        spec.extra["archive_file"] = str(archivo)
    spec.extra["break_on_existing"] = True
    spec.extra["priority"] = "low"
    return spec


def ficha(suscripcion: Suscripcion) -> str:
    """Una línea para el menú: «Cada 6 h · 720p · sin directos · tope 10»."""
    r = suscripcion.regla
    partes = [{30: t("cada 30 min"), 360: t("cada 6 h"), 1440: t("cada día")}.get(r.intervalo, t("cada 6 h")),
              {"opus_origen": t("solo audio"), "video_720": t("720p"), "compatible": t("compatible")}.get(r.perfil, r.perfil)]
    if r.titulo_excluir:
        partes.append(t("filtro: %s") % r.titulo_excluir)
    if r.duracion_min:
        partes.append(t("más de %d min") % max(1, r.duracion_min // 60))
    if r.hora_desde != 0 or r.hora_hasta < 24:
        partes.append(t("de %02d:00 a %02d:00") % (r.hora_desde, min(24, r.hora_hasta)))
    partes.append(t("tope %d") % r.tope)
    if r.carpeta:
        partes.append(r.carpeta)
    return " · ".join(p for p in partes if p)


__all__ = ["INTERVALOS", "PERFILES_SUSCRIPCION", "Regla", "SubError", "Suscripcion", "Suscripciones", "canal_id",
           "video_id_de", "entradas", "fecha_arg", "feed_url", "ficha", "historico_args", "leer_feed", "nuevos",
           "spec_de", "spec_historico", "watch_url"]
