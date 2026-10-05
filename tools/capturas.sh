#!/usr/bin/env bash
# H50 · las capturas de la página pública, hechas con el programa de verdad (no son maquetas).
#
#   tools/capturas.sh [archivo de vídeo]
#
# Abre el reproductor en una ventana, va pidiendo cada pantalla por su atajo y guarda `screenshot window`, que
# incluye la interfaz de uosc y los menús. Sin argumento usa el vídeo de pruebas del proyecto, que es una carta
# de ajuste: sirve para ver la interfaz, pero para la página quedan mucho mejor con una película de verdad, y por
# eso el archivo es un argumento. Las que no se puedan hacer (la TV necesita las listas descargadas, la sala
# necesita el demonio) se saltan y se dicen: la página deja un hueco con su descripción en vez de inventarse una.
#
# Necesita una sesión gráfica: aquí se abre una ventana de verdad, no se puede hacer sin pantalla.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PELI="${1:-$ROOT/tests/fixtures/media/video30.mkv}"
DESTINO="$ROOT/web/capturas"
SOCK="$ROOT/tmp/capturas.sock"
# CON UNA CARPETA DE DATOS VACÍA, SIEMPRE. La primera tanda de capturas salió con el menú enseñando el historial
# de quien tiene este equipo —los últimos vídeos vistos, con sus títulos— y eso iba camino de una página pública.
# Una captura para la web se hace con el programa recién estrenado: sin historial, sin favoritos y sin notas.
DATOS="$ROOT/tmp/capturas-datos"
rm -rf "$DATOS"
mkdir -p "$DESTINO" "$ROOT/tmp" "$DATOS"
export MPV_UOS_DATA_DIR="$DATOS"
export MPV_UOS_RUNTIME_DIR="$ROOT/tmp/capturas-rt"
rm -f "$SOCK"

[ -f "$PELI" ] || { echo "no existe $PELI" >&2; exit 1; }
[ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] || { echo "hace falta una sesión gráfica" >&2; exit 1; }

"$ROOT/bin/mpv-uos" --input-ipc-server="$SOCK" --pause=yes --start=5 --geometry=1280x720 \
  --screenshot-directory="$DESTINO" --screenshot-template="bruta-%n" --screenshot-format=png \
  --no-terminal "$PELI" &
MPV=$!
trap 'kill "$MPV" 2>/dev/null || true; rm -f "$SOCK"' EXIT

for _ in $(seq 60); do [ -S "$SOCK" ] && break; sleep 0.25; done
[ -S "$SOCK" ] || { echo "el reproductor no abrió" >&2; exit 1; }

orden() { printf '%s\n' "$1" | socat - "UNIX-CONNECT:$SOCK" >/dev/null 2>&1 || true; }
captura() { # captura <nombre> <atajo> <segundos de espera>
  local nombre="$1" atajo="$2" espera="${3:-3}"
  orden "{\"command\":[\"script-binding\",\"$atajo\"]}"
  sleep "$espera"
  rm -f "$DESTINO"/bruta-*.png
  orden '{"command":["screenshot","window"]}'
  sleep 1.5
  local hecha
  hecha="$(ls -1 "$DESTINO"/bruta-*.png 2>/dev/null | head -1 || true)"
  if [ -n "$hecha" ]; then
    mv "$hecha" "$DESTINO/$nombre.png"
    echo "  ✓ $nombre.png"
  else
    echo "  — $nombre: no salió (se queda el hueco en la página)"
  fi
  orden '{"command":["keypress","ESC"]}'
  sleep 0.5
}

echo "capturando con «$(basename "$PELI")» en $DESTINO"
captura puerta     mu_ytdl/ytdl-gate 2
captura subtitulos mu_subs/subs-toggle 3
captura indice     mu_recap/outline 4
captura sala       mu_share/share-menu 3
captura tv         mu_iptv/tv-menu 4
captura menu       mu_menu/root 2
rm -f "$DESTINO"/bruta-*.png
echo "listo: $(ls -1 "$DESTINO"/*.png 2>/dev/null | wc -l) capturas"
