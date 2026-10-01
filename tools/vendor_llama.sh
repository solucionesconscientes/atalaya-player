#!/usr/bin/env bash
# Vendoriza llama.cpp (solo CPU) en vendor/llama/bin para el resumen en prosa (H38/G6).
#
# Por qué un binario oficial y no compilar: compilar llama.cpp en el portátil objetivo tarda más que todo lo demás junto
# y no aporta nada (el build de CPU de la release ya trae las variantes AVX y elige la del equipo en tiempo de ejecución).
# Por qué no se instala de serie: el índice del vídeo (nivel 1) no lo necesita, y son ~18 MB de binario más el modelo
# (806 MB el de serie), que solo se baja cuando alguien pide un resumen.
#
# Uso: tools/vendor_llama.sh   (o MU_VENDOR_LLAMA=1 tools/vendor.sh)
set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
. vendor.lock
DL="${MPV_UOS_VENDOR_DL:-$ROOT/vendor/dl}"
DEST="$ROOT/vendor/llama"
mkdir -p "$DL" "$DEST/bin"

arch="$(uname -m)"; os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$arch" in x86_64|amd64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; esac
asset_var="LLAMA_ASSET_${arch}_${os}"; sha_var="LLAMA_SHA256_${arch}_${os}"
asset="${!asset_var:-}"; sha="${!sha_var:-}"
if [ -z "$asset" ] || [ -z "$sha" ]; then
  echo "llama.cpp: sin asset fijado para $os/$arch en vendor.lock." >&2
  echo "  Descarga el binario de CPU de https://github.com/ggml-org/llama.cpp/releases, comprueba su SHA-256 y añádelo." >&2
  exit 1
fi
if [ -x "$DEST/bin/llama-cli" ] && [ "${MU_FORCE:-0}" != "1" ]; then
  echo "llama.cpp: ya está en vendor/llama/bin ($(cat "$DEST/VERSION" 2>/dev/null || echo '?'))"
  exit 0
fi
tgz="$DL/$asset"
if [ ! -f "$tgz" ] || ! echo "$sha  $tgz" | sha256sum -c --status; then
  echo "descargando $asset"
  curl -fsSL --retry 2 -m 600 -o "$tgz.part" "$LLAMA_BASE_URL/$asset" || { echo "no se pudo descargar" >&2; exit 1; }
  if ! echo "$sha  $tgz.part" | sha256sum -c --status; then
    echo "ERROR: SHA-256 incorrecto para $asset" >&2; rm -f "$tgz.part"; exit 1
  fi
  mv "$tgz.part" "$tgz"
fi
tmp="$(mktemp -d "$DL/llama-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
tar xzf "$tgz" -C "$tmp" || { echo "no se pudo descomprimir" >&2; exit 1; }
# El tarball trae un directorio con los ejecutables y las libs juntos: se copia solo lo que hace falta para generar.
src="$(find "$tmp" -name llama-cli -type f | head -1)"
[ -n "$src" ] || { echo "ERROR: el tarball no trae llama-cli" >&2; exit 1; }
srcdir="$(dirname "$src")"
install -m 0755 "$src" "$DEST/bin/llama-cli"
# Todas las bibliotecas que vienen al lado: el binario no solo necesita libggml/libllama, también su propia
# libllama-cli-impl.so (y el que falte se nota solo al ejecutarlo, no al copiar).
found=0
for lib in "$srcdir"/*.so*; do
  [ -e "$lib" ] || continue
  install -m 0644 "$lib" "$DEST/bin/"; found=$((found + 1))
done
[ "$found" -gt 0 ] || { echo "ERROR: no se encontró ninguna biblioteca junto a llama-cli" >&2; exit 1; }
echo "$LLAMA_VERSION" > "$DEST/VERSION"
echo "llama.cpp $LLAMA_VERSION instalado en vendor/llama/bin ($found bibliotecas)"
LD_LIBRARY_PATH="$DEST/bin" "$DEST/bin/llama-cli" --version 2>&1 | head -2 || true
