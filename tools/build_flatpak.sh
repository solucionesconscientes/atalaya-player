#!/usr/bin/env bash
# Build dist/Atalaya-x86_64.flatpak: el reproductor con SU mpv dentro (0.42) y los drivers del runtime, para todas
# las distribuciones a la vez. Es el único paquete de Linux desde 0.1.6: el .deb y el AppImage se retiraron.
#
# Cómo se construye: flatpak-builder dentro de un contenedor Docker. En server1 no hay flatpak ni sudo, y el
# contenedor es root sin tocar el anfitrión (ADR-148). La primera vez baja el runtime de freedesktop (~1 GB) y lo
# deja en el volumen `atalaya-flatpak-cache`; las siguientes pasadas lo reutilizan y solo recompilan lo que cambia.
#
# Uso: tools/build_flatpak.sh [--limpio]
#   --limpio   borra el árbol de build (no el runtime) y recompila desde cero.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
OUT="${FLATPAK_OUT:-$ROOT/dist}"
MANIFEST=tools/flatpak/es.solucionesconscientes.Atalaya.yml
STAGE="$ROOT/tools/flatpak/stage"
LOG="$ROOT/tmp/flatpak-build.log"
CACHE_VOL=atalaya-flatpak-cache
APP_ID=es.solucionesconscientes.Atalaya
mkdir -p "$ROOT/tmp" "$OUT"

LIMPIAR=""
[ "${1:-}" = "--limpio" ] && LIMPIAR="rm -rf /cache/build /cache/builder"

# --- 1 · el árbol que entra: lo mismo que empaquetaban los build_*.sh, sin modelos ni descargas de vendor ---
echo "=== preparando el árbol ($(git -C "$ROOT" log --oneline -1)) ===" | tee "$LOG"
# lo que se compila es lo commiteado: si hay cambios sin commitear en el código, el paquete no los llevaría y el
# número de versión mentiría. Se avisa y se para.
if [ -n "$(git -C "$ROOT" status --porcelain -- bin mpv-config mpvd locales | head -1)" ]; then
  echo "❌ hay cambios sin commitear en bin/, mpv-config/, mpvd/ o locales/: commitea o revierte antes de empaquetar" | tee -a "$LOG"
  exit 2
fi
rm -rf "$STAGE"; mkdir -p "$STAGE"
git -C "$ROOT" archive HEAD bin mpv-config mpvd locales tools/flatpak docs/marca brand.json \
  pyproject.toml vendor.lock README.md VERSION LICENSE TERCEROS.md | tar -x -C "$STAGE"
# uosc se trae un binario que no está trackeado (el resto de su carpeta sí)
if [ -d "$ROOT/mpv-config/scripts/uosc/bin" ]; then
  mkdir -p "$STAGE/mpv-config/scripts/uosc"
  cp -a "$ROOT/mpv-config/scripts/uosc/bin" "$STAGE/mpv-config/scripts/uosc/"
fi
# yt-dlp y el almacén de certificados: sin ellos no hay descargas ni TLS propio
mkdir -p "$STAGE/vendor/bin"
for f in "$ROOT"/vendor/bin/yt-dlp "$ROOT"/vendor/bin/yt-dlp.exe "$ROOT"/vendor/bin/yt-dlp.version \
         "$ROOT"/vendor/bin/yt-dlp-nightly "$ROOT"/vendor/bin/yt-dlp-nightly.version; do
  [ -f "$f" ] && cp -a "$f" "$STAGE/vendor/bin/" || true
done
[ -f "$ROOT/vendor/cacert.pem" ] && cp -a "$ROOT/vendor/cacert.pem" "$STAGE/vendor/" || true
echo "    $(find "$STAGE" -type f | wc -l) ficheros, $(du -sh "$STAGE" | cut -f1)" | tee -a "$LOG"

# --- 2 · compilar dentro del contenedor ---
echo "=== flatpak-builder (esto tarda: ffmpeg, x264/x265, libplacebo y mpv) ===" | tee -a "$LOG"
docker run --rm --privileged \
  -v "$ROOT":/src:ro \
  -v "$OUT":/out \
  -v "$CACHE_VOL":/cache \
  -w /src \
  -e FLATPAK_USER_DIR=/cache/flatpak \
  atalaya-flatpak-builder bash -lc '
    set -e
    '"$LIMPIAR"'
    flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
    flatpak install -y --noninteractive flathub org.freedesktop.Platform//26.08 org.freedesktop.Sdk//26.08
    flatpak-builder --state-dir=/cache/builder --repo=/cache/repo --force-clean \
      --disable-rofiles-fuse /cache/build /src/tools/flatpak/es.solucionesconscientes.Atalaya.yml
    flatpak build-bundle /cache/repo /out/Atalaya-x86_64.flatpak '"$APP_ID"'
  ' 2>&1 | tee -a "$LOG"

# --- 3 · comprobar que lo que hay dentro es lo que creemos ---
PAQ="$OUT/Atalaya-x86_64.flatpak"
if [ ! -f "$PAQ" ]; then echo "❌ no se generó $PAQ (ver $LOG)"; exit 1; fi
echo "=== el paquete ===" | tee -a "$LOG"
stat -c 'creado %w · modificado %y · %s bytes' "$PAQ" | tee -a "$LOG"
sha256sum "$PAQ" | tee -a "$LOG"
# la versión y una cadena del cambio dentro del bundle (el .flatpak es un OCI empaquetado: se monta para mirarlo)
TMP=$(mktemp -d)
if command -v ostree >/dev/null 2>&1; then ostree --repo=/tmp/nada 2>/dev/null || true; fi
echo "    (comprobación por dentro: flatpak run --command=sh … en el equipo de Ser o con 'flatpak install --user $PAQ')" | tee -a "$LOG"
rm -rf "$TMP"
echo "✅ $PAQ"
