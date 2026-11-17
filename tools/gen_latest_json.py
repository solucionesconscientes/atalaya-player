#!/usr/bin/env python3
"""Genera `latest.json`, el fichero que leen las apps instaladas para saber si hay versión nueva.

El contrato está en `mpvd/updates.py`: solo `version` es obligatorio; el resto (fecha, notas, enlace y la lista
de paquetes con su SHA-256) se muestra si está.

Se genera desde lo que hay en `dist/`, así que nunca miente: si un paquete no está, no aparece. Ejecutarlo
después de compilar y antes de publicar.

Uso:
    tools/gen_latest_json.py                 # escribe dist/latest.json
    tools/gen_latest_json.py --notas "..."   # con las notas de la release
    tools/gen_latest_json.py --stdout        # para verlo sin escribir
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIST = RAIZ / "dist"


def version() -> str:
    with (RAIZ / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def sitio() -> str:
    datos = json.loads((RAIZ / "brand.json").read_text(encoding="utf-8"))
    return (datos.get("site") or "").rstrip("/")


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def paquetes(v: str) -> list[dict]:
    """Solo los ficheros de esta versión: si queda un .deb viejo en dist/, no se publica por error."""
    salida = []
    patron = re.compile(re.escape(v))
    for f in sorted(DIST.iterdir()):
        if not f.is_file() or f.suffix.lower() not in {".deb", ".appimage", ".exe", ".zip", ".flatpak"}:
            continue
        # AppImage y Flatpak no llevan versión en el nombre (Atalaya-x86_64.flatpak): se aceptan tal cual
        if not patron.search(f.name) and f.suffix.lower() not in (".appimage", ".flatpak"):
            continue
        salida.append({"name": f.name, "sha256": sha256(f), "size": f.stat().st_size})
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera latest.json desde dist/")
    ap.add_argument("--notas", default="")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()

    v = version()
    datos = {
        "version": v,
        "date": dt.date.today().isoformat(),
        "notes": a.notas,
        "url": f"{sitio()}" if sitio() else "",
        "files": paquetes(v),
    }
    texto = json.dumps(datos, ensure_ascii=False, indent=2) + "\n"
    if a.stdout:
        print(texto, end="")
        return 0
    destino = DIST / "latest.json"
    destino.write_text(texto, encoding="utf-8")
    print(f"{destino} · {len(datos['files'])} paquetes · versión {v}")
    faltan = [n for n in ("setup.exe", ".flatpak") if not any(n in p["name"] for p in datos["files"])]
    if faltan:
        print(f"aviso: no hay paquete de {', '.join(faltan)} en dist/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
