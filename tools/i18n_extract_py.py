#!/usr/bin/env python3
"""H49/G5 · wrap mpvd's **user-facing** messages in `t(...)` and collect them for the catalogues.

Only the messages written **in Spanish** are touched: those were written for a person, and they end up on screen
because the player shows `err.message`. The ones written in English (`"path required"`, `"unknown download: …"`)
are the API's own contract between mpvd and the scripts —the project's convention is code in English, user text in
Spanish— so they stay as they are.

Only one shape is rewritten, the one that reaches the screen:

    RpcError(CODE, "texto")        → RpcError(CODE, t("texto"))
    RpcError(CODE, f"texto {x}")   → RpcError(CODE, t("texto %s") % (x,))

An f-string with a format spec (`{x:.1f}`) is NOT touched and is listed at the end: converting it would change
what the number looks like, and that is a decision for a human.

    tools/i18n_extract_py.py --check mpvd/iptv/service.py
    tools/i18n_extract_py.py --write mpvd/**/*.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"

# una pista barata y fiable de que el mensaje se escribió para una persona y en castellano
SPANISH = re.compile(r"[áéíóúñ¿¡Á-Ú]|\b(no|se|hay|falta|hace|solo|sólo|para|con|los|las|del|que|una|este|esta|"
                     r"nada|ningún|ninguna|todavía|aún|ya|desde|hasta|sin|sobre|como|cuando|porque)\b")
CALL = re.compile(r"""RpcError\(\s*([A-Z_]+)\s*,\s*(f?)("(?:[^"\\]|\\.)*")""")
FIELD = re.compile(r"\{([^{}:!]+)(![rsa])?\}")
HAS_SPEC = re.compile(r"\{[^{}]*:[^{}]*\}")
# una cadena pegada a la siguiente (concatenación implícita de Python) no se puede envolver a trozos:
# `t("a") "b"` no es código válido. Se salta y se avisa, igual que las frases pegadas del Lua.
SIGUE = re.compile(r"\s*f?\"")
# y para poder COMPROBAR (tests, catálogos) lo que ya está envuelto: `t("…")` tal cual
WRAPPED = re.compile(r"""\bt\(\s*("(?:[^"\\]|\\.)*")\s*\)""")


def convert(code: str, is_f: str, literal: str) -> tuple[str, str] | None:
    """(new call, catalogue key) or None when it must be left alone."""
    text = json.loads(literal)
    if not SPANISH.search(text):
        return None
    if not is_f:
        return f'RpcError({code}, t({literal})', text
    if HAS_SPEC.search(text):
        return None
    args: list[str] = []

    def one(m: re.Match[str]) -> str:
        expr, conv = m.group(1).strip(), m.group(2)
        args.append(f"repr({expr})" if conv == "!r" else expr)
        return "%s"

    key = FIELD.sub(one, text)
    if not args:
        return f'RpcError({code}, t({json.dumps(key, ensure_ascii=False)})', key
    coma = "," if len(args) == 1 else ""
    return (f'RpcError({code}, t({json.dumps(key, ensure_ascii=False)}) % ({", ".join(args)}{coma})', key)


def process(path: Path) -> tuple[str, list[str], list[str]]:
    src = path.read_text(encoding="utf-8")
    found: list[str] = []
    skipped: list[str] = []
    out, last = [], 0
    for m in CALL.finditer(src):
        code, is_f, literal = m.group(1), m.group(2), m.group(3)
        if SIGUE.match(src, m.end()):
            text = json.loads(literal)
            if SPANISH.search(text):
                skipped.append(text + "  ← partida en varias líneas, a mano")
            continue
        res = convert(code, is_f, literal)
        if res is None:
            text = json.loads(literal)
            if SPANISH.search(text):
                skipped.append(text)
            continue
        call, key = res
        out.append(src[last:m.start()])
        out.append(call)
        last = m.end()
        found.append(key)
    out.append(src[last:])
    # lo que ya estaba envuelto cuenta igual: si no, un segundo pase creería que no hay nada que traducir
    for m in WRAPPED.finditer(src):
        found.append(json.loads(m.group(1)))
    return "".join(out), found, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    todas: set[str] = set()
    for name in a.files:
        p = Path(name)
        new, found, skipped = process(p)
        if not found and not skipped:
            continue
        print(f"{p}: {len(found)} mensajes envueltos" + (f", {len(skipped)} con formato (a mano)" if skipped else ""))
        for s in sorted(set(skipped)):
            print(f"    · {s}")
        todas |= set(found)
        if a.write and new != p.read_text(encoding="utf-8"):
            if "from mpvd.i18n import t" not in new:
                new = re.sub(r"^(from mpvd\.rpc import .*)$", r"from mpvd.i18n import t\n\1", new, count=1,
                             flags=re.MULTILINE)
            p.write_text(new, encoding="utf-8")
    if a.write and todas:
        for lang in ("en", "fr"):
            f = LOCALES / f"{lang}.json"
            cat = json.loads(f.read_text(encoding="utf-8"))
            for k in todas:
                cat.setdefault(k, "")
            f.write_text(json.dumps(dict(sorted(cat.items())), ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
            print(f"{f}: {sum(1 for v in cat.values() if not v.strip())} por traducir, {len(cat)} en total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
