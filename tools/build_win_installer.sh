#!/usr/bin/env bash
# Instalador .exe para Windows (H75/F5, ADR-135): envuelve el zip portable en un instalador NSIS por usuario, sin
# UAC. Motivo: un EMPEZAR-AQUI.cmd descargado de internet lleva la marca de Internet y el Control Inteligente de
# Aplicaciones (SAC) lo bloquea; instalado con un .exe, los ficheros no llevan esa marca y arranca sin recuadros.
#
# Uso: tools/build_win_installer.sh [--zip dist/Atalaya-X-windows-x86_64.zip] [--out DIR]
# Necesita: unzip, makensis (con sus stubs: NSISDIR) y, para el icono, google-chrome/chromium + ImageMagick.
# En server1, sin root:
#   apt-get download nsis nsis-common && dpkg-deb -x nsis_*.deb ~/.local/nsis   (y ~/.local/bin/makensis con NSISDIR)
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
OUT="$ROOT/dist"
ZIP=""

while [ $# -gt 0 ]; do
  case "$1" in
    --zip) ZIP="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$ZIP" ]; then
  ZIP="$(ls -t "$ROOT"/dist/Atalaya-*-windows-x86_64.zip 2>/dev/null | head -1 || true)"
fi
[ -n "$ZIP" ] && [ -f "$ZIP" ] || { echo "no hay zip portable: ejecuta antes tools/build_zip_windows.sh" >&2; exit 1; }

VERSION="$(basename "$ZIP" | sed -E 's/^Atalaya-([0-9][^-]*)-windows.*/\1/')"
[ -n "$VERSION" ] || { echo "no sé sacar la versión de $ZIP" >&2; exit 1; }

NSIS="$(command -v makensis || true)"
[ -n "$NSIS" ] || { echo "falta makensis (paquete nsis + nsis-common; ver la cabecera)" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
[ -d "$TMP/Atalaya" ] || { echo "el zip no trae la carpeta Atalaya/" >&2; exit 1; }

# Icono del instalador y de la entrada de «Aplicaciones instaladas»: el logo aprobado, rasterizado con Chrome (el
# renderizador de ImageMagick no dibuja bien los trazos del SVG) y convertido a .ico multi-tamaño.
ICON_ARGS=()
SVG="$ROOT/docs/marca/logo-anillo.svg"
CHROME="$(command -v google-chrome-stable || command -v google-chrome || command -v chromium || true)"
if [ -f "$SVG" ] && [ -n "$CHROME" ] && command -v convert >/dev/null; then
  {
    echo '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:transparent}svg{display:block;width:256px;height:256px}</style>'
    sed 's/width="128" height="128"/width="256" height="256"/' "$SVG"
  } > "$TMP/icon.html"
  if "$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --default-background-color=00000000 \
       --window-size=256,256 --screenshot="$TMP/icon.png" "file://$TMP/icon.html" >/dev/null 2>&1 \
     && convert "$TMP/icon.png" -define icon:auto-resize=256,128,64,48,32,16 "$TMP/atalaya.ico" 2>/dev/null; then
    ICON_ARGS=(-DICON="$TMP/atalaya.ico")
  else
    echo "aviso: no se ha podido generar el icono; el instalador sale sin él" >&2
  fi
else
  echo "aviso: sin Chrome o sin ImageMagick no hay icono; el instalador sale sin él" >&2
fi

mkdir -p "$OUT"

# mpv de reserva: se baja una vez (queda en la caché) y se verifica con el SHA-256 de vendor.lock. Va dentro del
# instalador para que una instalación nueva funcione aunque el equipo no tenga mpv ni internet en ese momento.
MPV_ARGS=()
# shellcheck source=/dev/null
source "$ROOT/vendor.lock"
if [ -n "${MPV_WIN_URL:-}" ]; then
  if command -v 7z >/dev/null; then
    CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/atalaya-mpv/$MPV_WIN_VERSION"
    mkdir -p "$CACHE"
    if [ ! -f "$CACHE/mpv.7z" ] || [ "$(sha256sum "$CACHE/mpv.7z" | cut -d' ' -f1)" != "$MPV_WIN_SHA256" ]; then
      curl -fL --retry 3 -o "$CACHE/mpv.7z" "$MPV_WIN_URL"
    fi
    echo "$MPV_WIN_SHA256  $CACHE/mpv.7z" | sha256sum -c -
    [ -f "$CACHE/files/mpv.exe" ] || 7z x -y -o"$CACHE/files" "$CACHE/mpv.7z" mpv.exe mpv.com d3dcompiler_43.dll mpv/fonts.conf >/dev/null
    MPV_ARGS=(-DMPVDIR="$CACHE/files")
  else
    echo "aviso: sin 7z no se puede añadir mpv al instalador; saldrá sin copia de reserva" >&2
  fi
fi

"$NSIS" -V2 -DSRCDIR="$TMP/Atalaya" -DOUTFILE="$OUT/Atalaya-$VERSION-setup.exe" -DVERSION="$VERSION" \
  "${ICON_ARGS[@]}" "${MPV_ARGS[@]}" "$ROOT/tools/atalaya.nsi"

ls -l "$OUT/Atalaya-$VERSION-setup.exe"
sha256sum "$OUT/Atalaya-$VERSION-setup.exe"
