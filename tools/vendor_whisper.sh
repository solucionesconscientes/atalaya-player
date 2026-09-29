#!/usr/bin/env bash
# Vendor whisper.cpp (binaries, shared libs and ggml models) into vendor/whisper/ WITHOUT compiling (ADR-023).
# Source: $WHISPER_BUILD (a whisper.cpp cmake build dir with bin/) and $WHISPER_MODELS (ggml-*.bin), defaulting to the
# read-only checkout in ~/proyectos/live-captions-linux/whisper.cpp. Safe to re-run: existing files of the same size are kept.
# Usage: tools/vendor_whisper.sh [--models-only|--bin-only]
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"
SRC_ROOT="${WHISPER_SRC:-$HOME/proyectos/live-captions-linux/whisper.cpp}"
BUILD="${WHISPER_BUILD:-$SRC_ROOT/build}"
MODELS="${WHISPER_MODELS:-$SRC_ROOT/models}"
DEST="$ROOT/vendor/whisper"
mode="${1:-all}"

copy_if_needed() { # copy_if_needed <src> <dest-dir>
  local src="$1" dir="$2" name; name="$(basename "$src")"
  if [ -L "$src" ]; then
    cp -a "$src" "$dir/$name"; return 0
  fi
  if [ -f "$dir/$name" ] && [ "$(stat -c %s "$src")" = "$(stat -c %s "$dir/$name")" ]; then
    echo "  ok $name"; return 0
  fi
  echo "  copiando $name"
  cp -a "$src" "$dir/$name.part" && mv "$dir/$name.part" "$dir/$name"
}

status=0
if [ "$mode" != "--models-only" ]; then
  echo "binarios: $BUILD/bin"
  if [ -x "$BUILD/bin/whisper-cli" ]; then
    mkdir -p "$DEST/bin"
    for f in whisper-cli whisper-server whisper-bench whisper-quantize whisper-vad-speech-segments; do
      [ -x "$BUILD/bin/$f" ] && copy_if_needed "$BUILD/bin/$f" "$DEST/bin"
    done
    # shared libs (cmake puts them in bin/ or src/, ggml/src/); keep symlink chains intact
    while IFS= read -r lib; do copy_if_needed "$lib" "$DEST/bin"; done < <(
      find "$BUILD" \( -name 'libwhisper.so*' -o -name 'libggml*.so*' -o -name 'libwhisper*.dylib' -o -name 'libggml*.dylib' \) \
        -not -path '*/CMakeFiles/*' 2>/dev/null | sort -u | awk -F/ '!seen[$NF]++')
    if LD_LIBRARY_PATH="$DEST/bin" "$DEST/bin/whisper-cli" --version >"$DEST/VERSION" 2>&1; then
      echo "  $(head -1 "$DEST/VERSION")"
    else
      echo "  ERROR: whisper-cli no arranca (faltan libs?)" >&2; cat "$DEST/VERSION" >&2; status=1
    fi
  else
    echo "  no hay build de whisper.cpp en $BUILD (WHISPER_BUILD=<dir>)." >&2
    echo "  Alternativa manual (fuera de este script, tarda >45 min en un portátil de 4 núcleos):" >&2
    echo "    git clone --depth 1 -b v1.9.3 https://github.com/ggml-org/whisper.cpp vendor/whisper-src" >&2
    echo "    cmake -S vendor/whisper-src -B vendor/whisper-src/build -DCMAKE_BUILD_TYPE=Release && cmake --build vendor/whisper-src/build -j\$(nproc)" >&2
    echo "    WHISPER_BUILD=vendor/whisper-src/build tools/vendor_whisper.sh --bin-only" >&2
    status=1
  fi
fi

if [ "$mode" != "--bin-only" ]; then
  echo "modelos: $MODELS"
  mkdir -p "$DEST/models"
  n=0
  for m in "$MODELS"/ggml-*.bin; do
    [ -f "$m" ] || continue
    # only real ggml files (magic "lmgg"), never the for-tests-* toys
    [ "$(head -c 4 "$m")" = "lmgg" ] || continue
    copy_if_needed "$m" "$DEST/models"; n=$((n + 1))
  done
  echo "  $n modelos en vendor/whisper/models (los demás se descargan bajo demanda desde Hugging Face)"
fi
exit $status
