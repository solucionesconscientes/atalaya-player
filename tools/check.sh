#!/usr/bin/env bash
# Full check: environment, vendored components, test media, Lua lint, shell lint (if available) and pytest.
# Must be green before every commit. MU_SKIP_NETWORK=1 skips the @network tests even when online.
set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"
fail=0
step() { echo; echo "▶ $*"; }

step "entorno"
command -v mpv >/dev/null || { echo "falta mpv"; exit 1; }
command -v uv >/dev/null || { echo "falta uv"; exit 1; }
mpv --version | head -1
[ -x .venv/bin/python ] || uv sync --quiet
.venv/bin/python --version

step "vendor (uosc, thumbfast, yt-dlp)"
if [ -f mpv-config/scripts/uosc/main.lua ] && [ -f mpv-config/scripts/thumbfast.lua ]; then
  echo "presentes ($(grep -o "uosc_version = '[^']*'" mpv-config/scripts/uosc/main.lua))"
  if [ -x mpv-config/scripts/uosc/bin/ziggy-linux ] && [ -x vendor/bin/yt-dlp ]; then
    echo "yt-dlp $(cat vendor/bin/yt-dlp.version 2>/dev/null || echo '?') en vendor/bin"
  else
    tools/vendor.sh || true
  fi
else
  tools/vendor.sh || fail=1
fi

step "vendor (whisper.cpp)"
if [ -x vendor/whisper/bin/whisper-cli ]; then
  echo "whisper-cli presente ($(head -1 vendor/whisper/VERSION 2>/dev/null || echo '?')); modelos: $(ls vendor/whisper/models 2>/dev/null | grep -c '^ggml-')"
else
  tools/vendor_whisper.sh || echo "AVISO: sin whisper.cpp los tests de ASR se omiten (ver docs/WHISPER.md)"
fi

step "traducción (ctranslate2 + paquetes Argos)"
if .venv/bin/python -c "import ctranslate2, sentencepiece" 2>/dev/null; then
  echo "runtime presente; paquetes: $(ls vendor/models/argos 2>/dev/null | tr '\n' ' ')"
else
  uv sync --quiet --extra translate --extra semantic --extra impersonate && echo "runtime instalado (extra translate)" || echo "AVISO: sin runtime de traducción los tests se omiten"
fi

step "búsqueda semántica (onnxruntime + modelo de embeddings)"
if .venv/bin/python -c "import onnxruntime, numpy, sentencepiece" 2>/dev/null; then
  if [ -f vendor/models/embed/model_quantized.onnx ]; then echo "runtime y modelo presentes (vendor/models/embed)"; else echo "runtime presente; modelo pendiente: semantic.models.download"; fi
else
  uv sync --quiet --extra translate --extra semantic --extra impersonate && echo "runtime instalado (extra semantic)" || echo "AVISO: sin onnxruntime los tests del modelo real se omiten"
fi

step "medios de prueba"
[ -f tests/fixtures/media/manifest.json ] && echo "presentes" || tools/make_test_media.sh || fail=1

step "lint Lua"
if command -v luacheck >/dev/null; then
  luacheck . || fail=1
else
  for f in mpv-config/scripts/mu-*.lua; do luajit -bl "$f" >/dev/null && echo "sintaxis OK $f" || fail=1; done
fi

step "lint shell"
if command -v shellcheck >/dev/null; then
  shellcheck -S warning bin/mpv-uos tools/*.sh || fail=1
else
  for f in bin/mpv-uos tools/*.sh; do bash -n "$f" && echo "sintaxis OK $f" || fail=1; done
fi

step "pytest (sin red)"
uv run --quiet pytest || fail=1

if [ "${MU_SKIP_NETWORK:-0}" != "1" ] && curl -fsI -m 5 https://github.com >/dev/null 2>&1; then
  step "pytest (red)"
  uv run --quiet pytest -m network -o addopts="-q -ra"; rc=$?
  # 5 = no network tests collected (yet); that is fine.
  [ $rc -eq 0 ] || [ $rc -eq 5 ] || fail=1
else
  step "pytest (red): omitido (sin conectividad o MU_SKIP_NETWORK=1)"
fi

echo
if [ $fail -eq 0 ]; then echo "✅ check OK"; else echo "❌ check FALLÓ"; fi
exit $fail
