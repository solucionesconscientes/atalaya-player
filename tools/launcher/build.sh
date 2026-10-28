#!/usr/bin/env bash
# Compila Atalaya.exe, el lanzador GUI de Windows (sin consola y con un .exe real para «Abrir con»).
# Zig trae el mingw-w64 necesario; no hace falta root ni un Windows.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
ZIG="${ZIG:-$HOME/.local/zig/zig}"
[ -x "$ZIG" ] || { echo "falta zig en $ZIG (ZIG=/ruta/zig)" >&2; exit 1; }
"$ZIG" cc -target x86_64-windows-gnu -O2 -Wl,--subsystem,windows \
  -o "$ROOT/tools/launcher/Atalaya.exe" "$ROOT/tools/launcher/atalaya.c" -luser32
echo "$ROOT/tools/launcher/Atalaya.exe"
