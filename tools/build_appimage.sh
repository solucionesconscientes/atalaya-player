#!/usr/bin/env bash
# Build dist/<marca>-x86_64.AppImage (H28, ADR-067): the project files tracked by git, a relocatable CPython 3.12
# (the python-build-standalone build uv manages) with the light extras, and the vendored yt-dlp. mpv itself is NOT
# inside: the AppImage uses the system mpv (>= 0.41), like bin/mpv-uos does; AppRun explains how to install it if
# it is missing. Whisper, the translation and embedding models stay out (hundreds of MB; downloaded on demand).
#
# Usage: tools/build_appimage.sh [--extras desktop,impersonate] [--out DIR]
# Needs: git, uv, curl, sha256sum (and network the first time: appimagetool and its runtime).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/vendor.lock"
EXTRAS="desktop"
OUT="$ROOT/dist"
while [ $# -gt 0 ]; do
  case "$1" in
    --extras) EXTRAS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
ARCH="$(uname -m)"
[ "$ARCH" = "x86_64" ] || { echo "de momento solo x86_64 (ver docs/PLATAFORMAS.md)" >&2; exit 2; }

WORK="$ROOT/tmp/appimage"
APPDIR="$WORK/app.AppDir"
APP="$APPDIR/usr/share/mpv-uos"
TOOL="$ROOT/vendor/bin/appimagetool-$APPIMAGETOOL_VERSION-x86_64.AppImage"
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/brand.json")"
# El nombre del FICHERO sale de «folder», que no lleva espacios; «name» puede llevarlos («Atalaya Player») y un
# AppImage con un espacio en el nombre es un estorbo en cualquier terminal.
FILE_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("folder","mpv-uos"))' "$ROOT/brand.json")"
APP_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id","mpv-uos"))' "$ROOT/brand.json")"

# 1. appimagetool, pinned and checked
if [ ! -x "$TOOL" ]; then
  mkdir -p "$ROOT/vendor/bin"
  curl -fsSL -o "$TOOL.part" "$APPIMAGETOOL_URL"
  echo "$APPIMAGETOOL_SHA256  $TOOL.part" | sha256sum -c --quiet
  chmod +x "$TOOL.part" && mv "$TOOL.part" "$TOOL"
fi

# 2. the app: exactly what git tracks (no .venv, caches, vendor downloads or tests)
rm -rf "$APPDIR"
mkdir -p "$APP"
git -C "$ROOT" ls-files -z -- bin mpv-config mpvd brand.json pyproject.toml vendor.lock README.md docs/USO.md \
    docs/ATAJOS.md docs/marca | (cd "$ROOT" && xargs -0 cp --parents -t "$APP")
# uosc's helper binaries are fetched by tools/vendor.sh (ignored by git) and uosc needs them
if [ -d "$ROOT/mpv-config/scripts/uosc/bin" ]; then
  cp -a "$ROOT/mpv-config/scripts/uosc/bin" "$APP/mpv-config/scripts/uosc/"
fi
mkdir -p "$APP/vendor/bin"
if [ -f "$ROOT/vendor/bin/yt-dlp" ]; then
  cp "$ROOT/vendor/bin/yt-dlp" "$APP/vendor/bin/"
  [ -f "$ROOT/vendor/bin/yt-dlp.version" ] && cp "$ROOT/vendor/bin/yt-dlp.version" "$APP/vendor/bin/"
fi

# 3. a relocatable Python with the light extras; `.venv/bin/python` is where mu-core and bin/mpv-uos look for it
PYSRC="$(uv python find --managed-python 3.12 2>/dev/null || uv python find 3.12)"
PYHOME="$(dirname "$(dirname "$(readlink -f "$PYSRC")")")"
[ -f "$PYHOME/lib/libpython3.12.so.1.0" ] || [ -d "$PYHOME/lib/python3.12" ] || { echo "Python 3.12 de uv no encontrado" >&2; exit 1; }
cp -a "$PYHOME" "$APPDIR/usr/python"
rm -rf "$APPDIR/usr/python/lib/python3.12/test" "$APPDIR/usr/python/lib/python3.12/idlelib" \
       "$APPDIR/usr/python/lib/python3.12/tkinter" "$APPDIR/usr/python/lib/python3.12/turtledemo"
find "$APPDIR/usr/python" -name '__pycache__' -type d -prune -exec rm -rf {} +
PY="$APPDIR/usr/python/bin/python3.12"
pkgs=()
IFS=',' read -r -a wanted <<<"$EXTRAS"
for extra in "${wanted[@]}"; do
  [ -n "$extra" ] || continue
  while IFS= read -r dep; do [ -n "$dep" ] && pkgs+=("$dep"); done < <(
    python3 - "$ROOT/pyproject.toml" "$extra" <<'PYEOF'
import sys, tomllib
data = tomllib.load(open(sys.argv[1], "rb"))
for dep in data["project"]["optional-dependencies"].get(sys.argv[2], []):
    print(dep)
PYEOF
  )
done
if [ "${#pkgs[@]}" -gt 0 ]; then
  uv pip install --quiet --python "$PY" --break-system-packages "${pkgs[@]}"
fi
mkdir -p "$APP/.venv/bin"
ln -s ../../../../python/bin/python3.12 "$APP/.venv/bin/python"

# 4. AppRun, desktop entry and icon
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# MPV-UOS AppImage: the app is read-only here, so caches, data and the daily yt-dlp update go to the user's folders.
HERE="$(dirname "$(readlink -f "$0")")"
APP="$HERE/usr/share/mpv-uos"
DATA="${MPV_UOS_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mpv-uos}"
export MPV_UOS_CACHE_DIR="${MPV_UOS_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/mpv-uos}"
export MPV_UOS_VENDOR_BIN="${MPV_UOS_VENDOR_BIN:-$DATA/bin}"
export MPV_UOS_APPIMAGE="${APPIMAGE:-1}"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$MPV_UOS_VENDOR_BIN"
# the bundled yt-dlp seeds the writable copy; later ones come from mpvd's verified daily update
if [ ! -f "$MPV_UOS_VENDOR_BIN/yt-dlp" ] && [ -f "$APP/vendor/bin/yt-dlp" ]; then
  cp "$APP/vendor/bin/yt-dlp" "$MPV_UOS_VENDOR_BIN/yt-dlp" && chmod +x "$MPV_UOS_VENDOR_BIN/yt-dlp"
fi
if [ -z "${MPV_UOS_MPV:-}" ] && ! command -v mpv >/dev/null 2>&1; then
  text="MPV-UOS necesita el reproductor mpv (0.41 o posterior). Instálalo con tu gestor de paquetes, por ejemplo: sudo apt install mpv"
  if command -v notify-send >/dev/null 2>&1; then notify-send -a MPV-UOS "Falta mpv" "$text"; fi
  if command -v kdialog >/dev/null 2>&1; then kdialog --error "$text"
  elif command -v zenity >/dev/null 2>&1; then zenity --error --text="$text"; fi
  echo "$text" >&2
  exit 1
fi
exec "$APP/bin/mpv-uos" "$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$NAME
GenericName=Reproductor multimedia
Comment=Vídeo, TV y radio, YouTube, subtítulos con IA
Exec=AppRun %U
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Video;Player;TV;
MimeType=video/mp4;video/x-matroska;video/webm;audio/mpeg;audio/flac;audio/ogg;x-scheme-handler/mpv-uos;
EOF
cp "$ROOT/mpvd/remote/www/icon-512.png" "$APPDIR/$APP_ID.png"
ln -s "$APP_ID.png" "$APPDIR/.DirIcon"

# 5. pack (appimagetool is itself an AppImage: extract-and-run avoids needing FUSE on the build machine)
mkdir -p "$OUT"
OUTFILE="$OUT/$FILE_NAME-x86_64.AppImage"
rm -f "$OUTFILE"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" --no-appstream "$APPDIR" "$OUTFILE" >"$WORK/appimagetool.log" 2>&1 || {
  tail -20 "$WORK/appimagetool.log" >&2; exit 1; }
echo "$OUTFILE ($(du -h "$OUTFILE" | cut -f1))"
