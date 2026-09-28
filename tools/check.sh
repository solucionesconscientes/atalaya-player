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

step "vendor (uosc, thumbfast)"
if [ -f mpv-config/scripts/uosc/main.lua ] && [ -f mpv-config/scripts/thumbfast.lua ]; then
  echo "presentes ($(grep -o "uosc_version = '[^']*'" mpv-config/scripts/uosc/main.lua))"
  [ -x mpv-config/scripts/uosc/bin/ziggy-linux ] || tools/vendor.sh || true
else
  tools/vendor.sh || fail=1
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
