#!/usr/bin/env python3
"""H50 · construye la página pública a partir de UNA sola fuente (web/contenido.json + web/plantilla.html).

    tools/build_web.py [--dist DIR] [--out DIR]

Escribe `web/index.html` (castellano), `web/en/index.html`, `web/fr/index.html` y, de paso, el `web/latest.json`
que consulta el propio programa para avisar de que hay versión nueva (H68): los dos salen de los mismos datos, así
que la página y el aviso no pueden contradecirse.

Tres decisiones que están en el código y conviene no deshacer:

* **Una fuente, tres idiomas.** Escribir tres HTML a mano garantiza que en un mes digan cosas distintas. Las
  claves tienen que ser las mismas en los tres idiomas y lo comprueba un test.
* **Las descargas se leen de `dist/`**, con el tamaño y el SHA-256 de verdad de cada archivo. Una página que
  anuncia una suma que no es la del fichero es peor que no anunciar ninguna.
* **Si una captura no está, se deja un hueco con su descripción** en vez de inventarse una maqueta: la página se
  puede publicar hoy y mejora cuando haya capturas, y mientras tanto se ve qué falta.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
IDIOMAS = ("es", "en", "fr")
NOMBRE_IDIOMA = {"es": "Castellano", "en": "English", "fr": "Français"}
# Los paquetes que se publican, en el orden en que se ofrecen (lo primero, lo que más gente va a usar).
PAQUETES = (
    ("atalaya-player_{v}_amd64.deb", "Debian · Ubuntu · Mint (64 bits)", "deb"),
    ("Atalaya-x86_64.AppImage", "Cualquier Linux (64 bits), sin instalar", "appimage"),
    ("atalaya-player_{v}_arm64.deb", "Raspberry Pi OS · Debian ARM (64 bits)", "deb"),
    ("Atalaya-aarch64.AppImage", "Cualquier Linux ARM (64 bits), sin instalar", "appimage"),
    ("Atalaya-{v}-windows-x86_64.zip", "Windows 10 y 11 (64 bits), portable", "zip"),
)


def corta(ruta: Path) -> str:
    """El nombre que se imprime: relativo al proyecto si está dentro, y entero si se construye fuera (los tests
    construyen en un temporal para no pisar `web/`)."""
    try:
        return str(ruta.relative_to(ROOT))
    except ValueError:
        return str(ruta)


def version() -> str:
    return tomllib.load(open(ROOT / "pyproject.toml", "rb"))["project"]["version"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for trozo in iter(lambda: fh.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def tamano_legible(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} kB"


def paquetes(dist: Path, v: str) -> list[dict]:
    """Lo que hay construido, con su tamaño y su suma de verdad. Lo que no esté, no se anuncia."""
    salida = []
    for patron, etiqueta, tipo in PAQUETES:
        nombre = patron.format(v=v)
        f = dist / nombre
        if not f.is_file():
            continue
        salida.append({"name": nombre, "label": etiqueta, "kind": tipo, "size": f.stat().st_size,
                       "sha256": sha256(f)})
    return salida


def render(plantilla: str, valores: dict[str, str]) -> str:
    texto = plantilla
    for clave, valor in valores.items():
        texto = texto.replace("{{" + clave + "}}", valor)
    sobran = [t for t in texto.split("{{")[1:] if "}}" in t]
    if sobran:
        raise SystemExit("faltan valores en la plantilla: " + ", ".join(s.split("}}")[0] for s in sobran))
    return texto


def bloque_cosas(cosas: list[dict], raiz: str) -> str:
    trozos = []
    for cosa in cosas:
        captura = WEB / cosa["captura"]
        if captura.is_file():
            medio = (f'<img src="{raiz}{html.escape(cosa["captura"])}" alt="{html.escape(cosa["alt"])}" '
                     f'loading="lazy" decoding="async">')
        else:
            medio = f'<div class="sin-captura">{html.escape(cosa["alt"])}</div>'
        trozos.append(
            '  <article class="cosa">\n'
            f'    <div class="texto"><h3>{html.escape(cosa["titulo"])}</h3><p>{cosa["texto"]}</p></div>\n'
            f'    <div class="medio">{medio}</div>\n'
            "  </article>")
    return "\n".join(trozos)


def bloque_descargas(lista: list[dict], v: str, textos: dict) -> str:
    if not lista:
        return ('  <p class="nota">Los paquetes se construyen con <code>tools/build_deb.sh</code>, '
                "<code>tools/build_appimage.sh</code> y <code>tools/build_zip_windows.sh</code>.</p>")
    filas = []
    for p in lista:
        filas.append(
            f'    <li><a href="descargas/{html.escape(p["name"])}">{html.escape(p["label"])}</a>'
            f'<span class="detalle">{html.escape(p["name"])} · {tamano_legible(p["size"])}</span>'
            f'<span class="suma">SHA-256 {p["sha256"]}</span></li>')
    return f'  <p class="nota">Versión {html.escape(v)}</p>\n  <ul>\n' + "\n".join(filas) + "\n  </ul>"


def bloque_idiomas(actual: str, raiz: str) -> str:
    enlaces = []
    for lang in IDIOMAS:
        destino = f"{raiz}index.html" if lang == "es" else f"{raiz}{lang}/index.html"
        marca = ' aria-current="true"' if lang == actual else ""
        enlaces.append(f'<a href="{destino}"{marca}>{NOMBRE_IDIOMA[lang]}</a>')
    return "".join(enlaces)


def bloque_estado(filas: list[list[str]]) -> str:
    return "\n".join(
        f'      <tr><td>{html.escape(a)}</td><td class="{c}">{html.escape(b)}</td></tr>' for a, b, c in filas)


def main() -> int:
    ap = argparse.ArgumentParser(description="Construye la página pública de Atalaya Player.")
    ap.add_argument("--dist", default=str(ROOT / "dist"), help="de dónde salen los paquetes que se anuncian")
    ap.add_argument("--out", default=str(WEB), help="dónde se escribe (por defecto, web/)")
    a = ap.parse_args()
    dist, out = Path(a.dist), Path(a.out)

    contenido = json.loads((WEB / "contenido.json").read_text(encoding="utf-8"))
    plantilla = (WEB / "plantilla.html").read_text(encoding="utf-8")
    v = version()
    lista = paquetes(dist, v)

    # el logo y el estilo, al lado de las páginas
    (out / "marca").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "docs/marca/logo-sin-fondo.svg", out / "marca/anillo.svg")
    if (WEB / "estilo.css") != (out / "estilo.css"):
        shutil.copy(WEB / "estilo.css", out / "estilo.css")

    for lang in IDIOMAS:
        textos = contenido[lang]
        raiz = "" if lang == "es" else "../"
        destino = out / "index.html" if lang == "es" else out / lang / "index.html"
        destino.parent.mkdir(parents=True, exist_ok=True)
        valores = {k: v2 for k, v2 in textos.items() if isinstance(v2, str)}
        valores |= {
            "raiz": raiz,
            "etq_idioma": "Idioma" if lang == "es" else ("Language" if lang == "en" else "Langue"),
            "idiomas": bloque_idiomas(lang, raiz),
            "cosas": bloque_cosas(textos["cosas"], raiz),
            "confianza": "\n".join(f"    <li>{x}</li>" for x in textos["confianza"]),
            "estado_tabla": bloque_estado(textos["estado_tabla"]),
            "descargas": bloque_descargas(lista, v, textos),
            "enlace_codigo": f"{raiz}arquitectura.html",
        }
        destino.write_text(render(plantilla, valores), encoding="utf-8")
        print(corta(destino))

    # «cómo está hecho», generada del documento que ya existe: no se escribe dos veces (docs/SITIO-WEB.md)
    sys.path.insert(0, str(ROOT / "tools"))
    from markdown_min import a_html  # noqa: PLC0415 - es una herramienta de construcción, no del programa

    doc = (ROOT / "docs/ARQUITECTURA.md").read_text(encoding="utf-8")
    cuerpo = a_html(doc)
    (out / "arquitectura.html").write_text(
        '<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Cómo está hecho Atalaya Player</title>\n"
        '<meta name="description" content="El mapa de la arquitectura: las dos piezas, cómo se hablan, '
        'la caché, qué sale a la red y cómo se prueba.">\n'
        '<link rel="icon" href="marca/anillo.svg" type="image/svg+xml">\n'
        '<link rel="stylesheet" href="estilo.css">\n</head>\n'
        '<body class="documento">\n<main>\n<p class="volver"><a href="index.html">← Atalaya Player</a></p>\n'
        f"{cuerpo}\n</main>\n</body>\n</html>\n", encoding="utf-8")
    print(corta(out / "arquitectura.html"))

    # el mismo dato, para el aviso de versión nueva del propio programa (H68)
    latest = {"version": v, "url": contenido["es"].get("sitio") or "https://solucionesconscientes.es/atalaya",
              "notes": "", "files": [{"name": p["name"], "sha256": p["sha256"], "size": p["size"]} for p in lista]}
    (out / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{corta(out / 'latest.json')} ({len(lista)} paquetes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
