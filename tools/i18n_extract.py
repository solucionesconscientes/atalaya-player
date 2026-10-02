#!/usr/bin/env python3
"""H49 · wrap the user-visible strings of a mu-* script in `tr(...)`, and collect them for the catalogues.

Only known **positions** are touched — the slots that end up on screen — never a string that could be an icon, a
view name, a property or a key:

    title = '…'   hint = '…'   label = '…'   footnote = '…'
    osd('…')   show('…', …)   uosc.message_items('…', …)   uosc.loading_items('…')

A string glued to something else with `..` is NOT touched: a sentence split in pieces cannot be translated, because
the word order changes between languages. Those are listed at the end so a human turns them into one string with
placeholders (`tr('%d archivos'):format(n)`). See docs/IDIOMAS.md.

    tools/i18n_extract.py --check  mpv-config/scripts/mu-menu/main.lua   # say what it would do
    tools/i18n_extract.py --write  mpv-config/scripts/mu-menu/main.lua   # do it
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("title", "hint", "label", "footnote")
# a quoted string that is NOT followed by a concatenation, and is not already wrapped
STR = r"'((?:[^'\\]|\\.){2,}?)'"
FIELD_RE = re.compile(rf"\b({'|'.join(FIELDS)})(\s*=\s*){STR}(?!\s*\.\.)")
CALL_RE = re.compile(rf"\b(osd|show|uosc\.message_items|uosc\.loading_items)(\()" + STR + r"(?!\s*\.\.)")
# strings with no letters (icons, '%s', separators) are not text
HAS_LETTER = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2}")
GLUED_RE = re.compile(rf"\b(?:{'|'.join(FIELDS)})\s*=\s*{STR}\s*\.\.|\b(?:osd|show)\(\s*{STR}\s*\.\.")
# Item builders that translate inside themselves (mu-menu): their first two arguments are the title and the hint,
# so they must NOT be wrapped here — only collected, or the catalogue would miss most of the menu.
HELPER_RE = re.compile(rf"\b(?:cmd|bind|sub|child|toggle)\(\s*{STR}\s*,\s*(?:{STR}\s*,)?")
# Already wrapped by an earlier run: collected again so that running this twice is harmless and the catalogue is
# always complete (it is rebuilt from the sources, not accumulated).
WRAPPED_RE = re.compile(rf"\btr\(\s*{STR}")


# A hint is often a key name ('ctrl+b', 'alt+R · alt+I', '?'): not text, and it must not reach the catalogue.
# A token counts as a key only if it carries a modifier, is a single character, or is one of mpv's named keys.
# Being short is NOT enough: that first attempt swallowed «Grabar» and «Salir», which are very much text.
SPECIAL = ("esc|tab|enter|space|bs|del|ins|up|down|left|right|home|end|pgup|pgdwn|menu|f[0-9]{1,2}"
           "|mbtn_[a-z]+|wheel_[a-z]+")
KEY_TOKEN = rf"(?:(?:(?:ctrl|alt|shift|meta)\+)+[\w?<>↑↓←→]+|{SPECIAL}|[^\w\s]|\w)"
KEY_RE = re.compile(rf"^{KEY_TOKEN}(?:\s*[·,]\s*{KEY_TOKEN})*$", re.IGNORECASE)
# Proper nouns and program names are not translated either.
NOMBRES = {"mpvd", "mpv", "uosc", "OpenSubtitles", "SponsorBlock", "yt-dlp", "DLNA", "Cloudflare", "YouTube",
           "whisper.cpp", "OPUS-MT", "Whisper", "Instagram", "TikTok"}


def interesting(text: str) -> bool:
    if text in NOMBRES or text.startswith("mu-") or "://" in text:
        return False
    if KEY_RE.match(text):
        return False
    return bool(HAS_LETTER.search(text))


def process(path: Path) -> tuple[str, list[str], list[str]]:
    src = path.read_text(encoding="utf-8")
    found: list[str] = []

    def field(m: re.Match[str]) -> str:
        if not interesting(m.group(3)):
            return m.group(0)
        found.append(m.group(3))
        return f"{m.group(1)}{m.group(2)}tr('{m.group(3)}')"

    def call(m: re.Match[str]) -> str:
        if not interesting(m.group(3)):
            return m.group(0)
        found.append(m.group(3))
        return f"{m.group(1)}{m.group(2)}tr('{m.group(3)}')"

    out = CALL_RE.sub(call, FIELD_RE.sub(field, src))
    for texto in WRAPPED_RE.findall(src):
        if interesting(texto):
            found.append(texto)
    for grupo in HELPER_RE.findall(src):
        for texto in (grupo if isinstance(grupo, tuple) else (grupo,)):
            if texto and interesting(texto):
                found.append(texto)
    glued = sorted({g for pair in GLUED_RE.findall(src) for g in pair if g and interesting(g)})
    return out, found, glued


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    todas: list[str] = []
    for f in args.files:
        out, found, glued = process(f)
        todas += found
        print(f"\n{f}: {len(found)} cadenas envueltas, {len(glued)} pegadas con `..` (a mano)")
        for g in glued:
            print(f"    · {g}")
        if args.write and out != f.read_text(encoding="utf-8"):
            f.write_text(out, encoding="utf-8")
            print("    escrito")

    if args.write:
        # las cadenas nuevas se añaden a los catálogos sin traducir, para que se vea qué falta
        for lang in ("en", "fr"):
            path = ROOT / "locales" / f"{lang}.json"
            cat = json.loads(path.read_text(encoding="utf-8"))
            nuevas = {k: "" for k in sorted(set(todas)) if k not in cat}
            if nuevas:
                cat.update(nuevas)
                path.write_text(json.dumps(cat, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
            print(f"locales/{lang}.json: {len(nuevas)} por traducir, {len(cat)} en total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
