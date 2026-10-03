#!/usr/bin/env python3
"""H49/G6 · recoge para los catálogos las cadenas de las páginas que sirve mpvd.

Las páginas (la sala de «ver juntos», el mando del móvil, el panel de descargas) las abre OTRA persona en SU
navegador, así que su idioma es el que pide el navegador, no el de este equipo. El servidor le inyecta en la página
solo las cadenas que esa página usa y el `t()` del JavaScript las busca ahí; el texto que ya viene escrito en el
HTML se traduce en el propio navegador con el mismo catálogo.

Aquí no se reescribe nada: los `t('…')` del .js los pone una persona (una frase entera, nunca `t('a' + 'b')`) y el
HTML no hace falta tocarlo. Esto solo apunta las cadenas en los catálogos para que se puedan traducir.

    tools/i18n_extract_web.py            # cuántas hay y cuántas faltan
    tools/i18n_extract_web.py --write    # las añade a locales/en.json y fr.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mpvd.i18n import page_keys  # noqa: E402  (hace falta el sys.path de arriba)

LOCALES = ROOT / "locales"
PAGINAS = ("mpvd/remote/www", "mpvd/share/www")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    todas: set[str] = set()
    for rel in PAGINAS:
        claves = page_keys(str(ROOT / rel))
        print(f"{rel}: {len(claves)} cadenas")
        todas |= set(claves)
    for lang in ("en", "fr"):
        f = LOCALES / f"{lang}.json"
        cat = json.loads(f.read_text(encoding="utf-8"))
        faltan = sorted(k for k in todas if not cat.get(k, "").strip())
        if a.write:
            for k in todas:
                cat.setdefault(k, "")
            f.write_text(json.dumps(dict(sorted(cat.items())), ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        print(f"{f.name}: {len(faltan)} de las páginas por traducir, {len(cat)} en total")
        for k in faltan:
            print(f"    · {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
