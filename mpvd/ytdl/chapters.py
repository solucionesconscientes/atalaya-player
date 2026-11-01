"""Capítulos de un vídeo: leerlos del ``-J``, elegirlos y entregarlos.

Tres piezas y una regla que no se salta: **nada se recodifica sin decirlo**.

* ``capitulos(info)``  — lo que el origen declara: número, título, inicio, fin y duración.
* ``seleccionar()``    — índices (``3``, ``3-5``, ``2,4,7``), rangos abiertos (``3-``, ``-4``) o regex de título
  (``/entrevista/`` o una palabra suelta). Devuelve la lista ordenada y falla con el motivo si no queda nada.
* ``args_capitulos()`` — la orden de yt-dlp para los dos modos de entrega:

  * **un fichero por capítulo**: ``--split-chapters`` y plantilla con prefijo ``chapter:`` (número y título). Si se
    elige un subconjunto, yt-dlp partiría *todos* los capítulos, así que se pide un ``--download-sections`` por
    capítulo elegido: sale un fichero por capítulo elegido, que es lo prometido (y no sobran trozos).
  * **un solo fichero**: un ``--download-sections`` por capítulo; la unión se hace después con ``unir()``. Se dice en
    la nota (``NOTA_UNION``) porque son dos pasos y quien pinta la pantalla tiene que contarlo.

* ``unir()`` — concatenación **sin recodificar** (concat demuxer, ``-c copy``) con ffprobe delante y detrás: si los
  trozos no comparten códec o parámetros, se devuelve el motivo y no se toca nada. Nunca se recodifica en silencio.

Verificado contra el ``--help`` del yt-dlp empaquetado (2026.10.26): ``--split-chapters`` existe, el prefijo
``chapter:`` sirve para nombrar los trozos, y ``--download-sections`` acepta un regex de título o ``*inicio-fin``
(los tiempos negativos se cuentan desde el final) y se puede repetir.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from mpvd.i18n import t
from mpvd.proc import spawn_kwargs

SEPARADO = "separado"          # un fichero por capítulo
UNIDO = "unido"                # un solo fichero: trozos y unión después
MODOS: tuple[str, ...] = (SEPARADO, UNIDO)

# El prefijo «chapter:» es lo que le dice a yt-dlp que esa plantilla es para los trozos de --split-chapters
# (--help: «The "chapter:" prefix can be used with --paths and --output to set the output filename for the split
# files»). section_number va a dos cifras para que el orden alfabético sea el del vídeo.
PLANTILLA = "chapter:%(section_number)02d - %(section_title)s.%(ext)s"
NOTA_UNION = "los capítulos se bajan como trozos y se unen después, sin recodificar"

TOLERANCIA = 1.0               # s: la duración de la unión tiene que ser la suma ±1 s
FFPROBE_ENTRIES = "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,sample_rate,channels"


# ------------------------------------------------------------------ leer el -J

def _num(v: Any, por_defecto: float = 0.0) -> float:
    try:
        return float(v) if v is not None else por_defecto
    except (TypeError, ValueError):
        return por_defecto


def capitulos(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Los capítulos del origen, numerados desde 1, con inicio, fin y duración en segundos.

    ``info`` es el ``-J`` de yt-dlp (``chapters: [{start_time, end_time, title}]``). Un vídeo sin capítulos devuelve
    una lista vacía —es un caso normal, no un error—; quien pinta la pantalla dice «Este vídeo no tiene capítulos».
    """
    if not isinstance(info, dict):
        return []
    crudos = info.get("chapters")
    if crudos is None:
        crudos = info.get("capitulos")
    if not isinstance(crudos, list):
        return []
    total = _num(info.get("duration") if info.get("duration") is not None else info.get("duracion"))
    out: list[dict[str, Any]] = []
    for i, c in enumerate(crudos, 1):
        if not isinstance(c, dict):
            continue
        inicio = _num(c.get("start_time", c.get("inicio")))
        fin = c.get("end_time", c.get("fin"))
        fin_n = _num(fin, -1.0) if fin is not None else -1.0
        if fin_n < inicio:                       # sin fin, o roto: hasta el final del vídeo
            fin_n = total if total > inicio else inicio
        titulo = str(c.get("title") or c.get("titulo") or "").strip() or t("Capítulo %d") % i
        out.append({"numero": i, "titulo": titulo, "inicio": round(inicio, 3), "fin": round(fin_n, 3),
                    "duracion": round(fin_n - inicio, 3)})
    return out


# ------------------------------------------------------------------ elegir

_SOLO = re.compile(r"^\s*(\d+)\s*$")
_RANGO = re.compile(r"^\s*(\d*)\s*-\s*(\d*)\s*$")
# una selección es de números si TODOS sus trozos lo son: «2,4,7» sí, «2, intro» no (entonces es un título)
_NUMERICA = re.compile(r"^\s*(?:\d+|\d*-\d*)\s*$")


def _regex(texto: str) -> re.Pattern[str]:
    """``/texto/`` o el texto tal cual → regex sobre el título (sin distinguir mayúsculas)."""
    patron = texto[1:-1] if len(texto) >= 2 and texto.startswith("/") and texto.endswith("/") else texto
    try:
        return re.compile(patron, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(t("la búsqueda por título no es válida: %s") % exc) from None


def seleccionar(caps: Sequence[dict[str, Any]], expr: str | Sequence[int] | None) -> list[dict[str, Any]]:
    """Los capítulos elegidos, ordenados y sin repetir. Los números son los que se ven: el primero es el 1.

    Acepta ``3``, ``3-5``, ``2,4,7``, ``3-`` (del 3 al final), ``-4`` (del principio al 4) y una regex de título
    (``/entrevista/`` o ``entrevista``). Falla con el motivo legible si la selección queda vacía o pide un capítulo
    que el vídeo no tiene: una lista vacía que viaja a la descarga es un fichero de cero bytes.
    """
    todos = sorted(caps, key=lambda c: int(c.get("numero") or 0))
    if not todos:
        raise ValueError(t("este vídeo no tiene capítulos"))
    if isinstance(expr, (list, tuple)):
        return _por_indices(todos, [int(x) for x in expr])
    texto = str(expr or "").strip()
    if not texto:
        raise ValueError(t("no has elegido ningún capítulo"))

    trozos = [x.strip() for x in texto.split(",") if x.strip()]
    if not trozos:
        raise ValueError(t("no has elegido ningún capítulo"))
    if not all(_NUMERICA.match(x) for x in trozos):
        # un título, una palabra o un /regex/: se busca en los títulos
        rx = _regex(texto)
        elegidos = [c for c in todos if rx.search(str(c.get("titulo") or ""))]
        if not elegidos:
            raise ValueError(t("ningún capítulo se llama así: %s") % texto)
        return elegidos

    pedidos: list[int] = []
    for trozo in trozos:
        m = _RANGO.match(trozo)
        if m and (m.group(1) or m.group(2)):
            n = len(todos)
            desde = int(m.group(1)) if m.group(1) else 1
            hasta = int(m.group(2)) if m.group(2) else n
            if desde > hasta:
                raise ValueError(t("el rango %s va al revés") % trozo)
            pedidos += list(range(desde, hasta + 1))
            continue
        m = _SOLO.match(trozo)
        if not m:                                  # «-» suelto: ni número ni rango
            raise ValueError(t("no entiendo «%s»: usa números (3), rangos (3-5), una coma (2,4,7) o el título") % trozo)
        pedidos.append(int(m.group(1)))
    return _por_indices(todos, pedidos)


def _por_indices(todos: list[dict[str, Any]], pedidos: Iterable[int]) -> list[dict[str, Any]]:
    n = len(todos)
    por_numero = {int(c["numero"]): c for c in todos}
    fuera = sorted({i for i in pedidos if i < 1 or i > n})
    if fuera:
        raise ValueError(t("el vídeo tiene %d capítulos: no existe el %s") % (n, ", ".join(str(i) for i in fuera)))
    elegidos = {int(i) for i in pedidos}
    if not elegidos:
        raise ValueError(t("no has elegido ningún capítulo"))
    return [por_numero[i] for i in sorted(elegidos) if i in por_numero]


# ------------------------------------------------------------------ la orden de yt-dlp

def formato_tiempo(seg: float) -> str:
    """Segundos → lo que entiende yt-dlp en un rango: ``12.500``, ``-30.000`` o ``inf``."""
    if seg == float("inf"):
        return "inf"
    return f"{seg:.3f}"


def plantilla_capitulos(plantilla: str | None = None) -> str:
    """La plantilla de los trozos, con el prefijo ``chapter:`` y el número y el título dentro.

    Sin plantilla se usa ``PLANTILLA``. Una plantilla propia que no nombre el capítulo se rechaza: dos capítulos
    acabarían en el mismo fichero y ``--no-overwrites`` dejaría uno solo, sin avisar.
    """
    p = (plantilla or "").strip() or PLANTILLA
    if not p.startswith("chapter:"):
        p = "chapter:" + p
    if "%(section_title)" not in p and "%(chapter)s" not in p:
        raise ValueError(t("la plantilla de los capítulos tiene que llevar %(section_title)s"))
    if "%(ext)" not in p:
        p += ".%(ext)s"
    return p


def _unico(titulo: str, todos: Sequence[dict[str, Any]] | None) -> bool:
    if not titulo:
        return False
    if todos is None:
        return True
    return sum(1 for c in todos if str(c.get("titulo") or "") == titulo) == 1


def _seccion(cap: dict[str, Any], todos: Sequence[dict[str, Any]] | None = None) -> str:
    """Un capítulo como selector de ``--download-sections``: su título anclado, o su rango de tiempo.

    El título se ancla (``^…$``) para que «Parte 1» no se lleve también «Parte 10»; si el título está repetido en el
    vídeo no sirve para distinguir y se usa el tiempo, que siempre es exacto.
    """
    titulo = str(cap.get("titulo") or "")
    if _unico(titulo, todos):
        return "^" + re.escape(titulo) + "$"
    return "*%s-%s" % (formato_tiempo(_num(cap.get("inicio"))), formato_tiempo(_num(cap.get("fin"))))


def _rangos(sel: Sequence[dict[str, Any]], todos: Sequence[dict[str, Any]] | None) -> list[str]:
    args: list[str] = []
    for cap in sorted(sel, key=lambda c: int(c.get("numero") or 0)):
        args += ["--download-sections", _seccion(cap, todos)]
    return args


def args_capitulos(sel: Sequence[dict[str, Any]], modo: str = SEPARADO, plantilla: str | None = None,
                   todos: Sequence[dict[str, Any]] | None = None) -> list[str]:
    """Los argumentos de yt-dlp para entregar los capítulos elegidos.

    ``modo`` = ``SEPARADO`` (un fichero por capítulo) o ``UNIDO`` (un solo fichero: trozos y unión después, ver
    ``NOTA_UNION``). ``todos`` son los capítulos del vídeo: sirven para saber si la selección es completa (entonces
    cabe ``--split-chapters``) y para no fiarse de un título repetido.
    """
    if modo not in MODOS:
        raise ValueError(t("modo de capítulos desconocido: %s") % modo)
    elegidos = sorted(sel, key=lambda c: int(c.get("numero") or 0))
    if not elegidos:
        raise ValueError(t("no has elegido ningún capítulo"))
    if modo == SEPARADO and (todos is None or len(elegidos) >= len(todos)):
        # todos los capítulos: --split-chapters los parte con su número y su título en la plantilla
        return ["--split-chapters", "-o", plantilla_capitulos(plantilla)]
    # un subconjunto (o un solo fichero): un --download-sections por capítulo. Con --split-chapters saldrían
    # también los que no se han elegido, que es justo lo que no se ha pedido.
    return _rangos(elegidos, todos)


# ------------------------------------------------------------------ unir sin recodificar

@dataclass(frozen=True)
class Union:
    """Lo que pasó al unir: el fichero, la duración medida, la esperada y, si no se pudo, el motivo."""

    ok: bool
    salida: str
    duracion: float | None = None
    esperado: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ejecutable(nombre: str) -> str | None:
    return shutil.which(nombre)


def _sonda(ruta: str) -> dict[str, Any]:
    """``ffprobe`` → duración y firma de los flujos (lo que tiene que coincidir para poder copiar sin recodificar)."""
    ffprobe = _ejecutable("ffprobe")
    if ffprobe is None:
        raise ValueError(t("hace falta ffprobe para comprobar los trozos y no está instalado"))
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", FFPROBE_ENTRIES, "-of", "json", ruta],
                          capture_output=True, text=True, timeout=120, check=False, **spawn_kwargs())
    if proc.returncode != 0:
        raise ValueError(t("no se puede leer %s: %s") % (ruta, proc.stderr.strip().splitlines()[-1:] or ""))
    datos = json.loads(proc.stdout or "{}")
    flujos = []
    for s in datos.get("streams") or []:
        tipo = str(s.get("codec_type") or "")
        if tipo not in ("video", "audio"):
            continue
        firma = [tipo, s.get("codec_name")]
        if tipo == "video":
            firma += [s.get("width"), s.get("height"), s.get("pix_fmt")]
        else:
            firma += [s.get("sample_rate"), s.get("channels")]
        flujos.append(tuple(firma))
    return {"duracion": _num((datos.get("format") or {}).get("duration")), "flujos": sorted(flujos, key=str)}


def unir(rutas: Sequence[str | Path], salida: str | Path) -> Union:
    """Concatena los trozos en ``salida`` **copiando los flujos** y comprueba la duración con ffprobe.

    Concat demuxer + ``-c copy``: no se recodifica nada. Antes de unir se comparan los códecs y parámetros de todos
    los trozos, porque el demuxer no los convierte y mezclarlos daría un fichero que se rompe a mitad; si no
    coinciden se devuelve ``ok=False`` con el motivo, sin tocar la salida. Después, la duración tiene que ser la suma
    de los trozos ±1 s (``TOLERANCIA``): si no, tampoco se da por buena.
    """
    trozos = [Path(p) for p in rutas]
    if len(trozos) < 2:
        raise ValueError(t("hacen falta al menos dos trozos para unir"))
    faltan = [str(p) for p in trozos if not p.is_file()]
    if faltan:
        raise ValueError(t("falta el trozo %s") % ", ".join(faltan))
    destino = Path(salida)
    if not destino.parent.is_dir():
        raise ValueError(t("la carpeta de destino no existe: %s") % destino.parent)
    ffmpeg = _ejecutable("ffmpeg")
    if ffmpeg is None:
        raise ValueError(t("hace falta ffmpeg para unir y no está instalado"))

    sondas = [_sonda(str(p)) for p in trozos]
    firma = sondas[0]["flujos"]
    for i, s in enumerate(sondas[1:], 2):
        if s["flujos"] != firma:
            return Union(False, str(destino), error=t(
                "los trozos no se pueden unir sin recodificar: el %d no tiene el mismo códec ni los mismos "
                "parámetros (%s frente a %s)") % (i, s["flujos"], firma))
    esperado = sum(s["duracion"] for s in sondas)

    with tempfile.TemporaryDirectory(prefix="atalaya-union-") as tmp:
        lista = Path(tmp) / "trozos.txt"
        lista.write_text("".join("file '%s'\n" % str(p.resolve()).replace("'", r"'\''") for p in trozos),
                         encoding="utf-8")
        proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                               "-i", str(lista), "-c", "copy", str(destino)],
                              capture_output=True, text=True, timeout=1800, check=False, **spawn_kwargs())
    if proc.returncode != 0:
        ultima = (proc.stderr.strip().splitlines() or [""])[-1]
        return Union(False, str(destino), esperado=esperado, error=t("ffmpeg no pudo unir los trozos: %s") % ultima)
    if not destino.is_file() or destino.stat().st_size == 0:
        return Union(False, str(destino), esperado=esperado, error=t("la unión no dejó ningún fichero"))

    duracion = _sonda(str(destino))["duracion"]
    if abs(duracion - esperado) > TOLERANCIA:
        return Union(False, str(destino), duracion=duracion, esperado=esperado,
                     error=t("la unión dura %.1f s y los trozos suman %.1f s: revisa los cortes")
                     % (duracion, esperado))
    return Union(True, str(destino), duracion=duracion, esperado=esperado)


def nombre_unido(rutas: Sequence[str | Path]) -> str:
    """El nombre del fichero unido: el del primer trozo sin el ``[inicio-fin s]`` que pone el corte.

    Además del nombre propuesto, sirve para comprobar en la interfaz que la unión no va a pisar un trozo.
    """
    primero = Path(str(rutas[0]))
    base = re.sub(r"\s*\[\d+-\d+ s\]$", "", primero.stem)
    return base + primero.suffix
