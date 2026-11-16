#!/bin/sh
# Atalaya dentro del Flatpak.
#
# Aquí SÍ llevamos mpv (lo instala la receta en /app/bin): es el sentido del paquete, una sola versión de mpv y sus
# drivers en el mismo runtime para todas las distribuciones. Por eso no se busca el mpv del sistema.
#
# El reproductor vive en /app/lib/atalaya con la misma forma que en el AppImage, así que bin/mpv-uos encuentra sus
# scripts, mpvd y sus datos igual que siempre.
set -eu

ROOT=/app/lib/atalaya
export MPV_UOS_MPV="${MPV_UOS_MPV:-/app/bin/mpv}"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="/app/bin:$PATH"

# Datos del usuario: dentro del sandbox, en $HOME/.var/app/<id>/.local/share/mpv-uos. La primera vez se copian los
# que dejó el paquete .deb o el AppImage (posiciones, preferencias, favoritos): cambiar de formato no puede costar
# el historial de nadie. Se copia, no se mueve: si vuelve al paquete viejo, lo encuentra donde estaba.
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/mpv-uos"
if [ ! -d "$DATA" ] && [ -d /home/user/.local/share/mpv-uos ]; then
    mkdir -p "$DATA"
    cp -a /home/user/.local/share/mpv-uos/. "$DATA/" 2>/dev/null || true
fi

exec "$ROOT/bin/mpv-uos" "$@"
