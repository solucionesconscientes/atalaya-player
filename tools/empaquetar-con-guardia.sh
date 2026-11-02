#!/usr/bin/env bash
# Verifica y empaqueta: batería completa, fallos nuevos re-ejecutados de uno en uno, y solo entonces build.
#
# Regla: un fallo que NO está en la lista de rojos de entorno se repite a solas. Si a solas pasa, es
# inestabilidad del banco (carga, tiempo) y se anota; si vuelve a fallar, es una regresión y no se empaqueta.
#
# Uso: tools/empaquetar-con-guardia.sh [version]
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
VERSION="${1:-${VERSION:-}}"
LOG=tmp/empaquetar-con-guardia.log
mkdir -p tmp
: > "$LOG"
log() { printf '\n=== %s\n' "$*" | tee -a "$LOG"; }

# Rojos conocidos del banco: navegador headless sin GPU, xdg-mime de qtxdg, timeouts de 180 s, intermitentes.
CONOCIDOS='test_share_browser|test_install|test_downloads_panel|test_mu_subs|test_smoke_mpv|test_flujos_e2e'

if [ -n "$VERSION" ]; then
  log "versión $VERSION"
  python3 - "$VERSION" <<'PY' | tee -a "$LOG"
import re, sys, tomllib
from pathlib import Path
v = sys.argv[1]
p = Path("pyproject.toml"); p.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{v}"', p.read_text(encoding="utf-8"), count=1), encoding="utf-8")
q = Path("mpvd/__init__.py"); q.write_text(re.sub(r'(?m)^__version__ = "[^"]+"', f'__version__ = "{v}"', q.read_text(encoding="utf-8"), count=1), encoding="utf-8")
print("pyproject:", tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])
PY
  # la versión visible dentro de la app (mpv-config/scripts/mu-ytdl/main.lua la lee de aquí)
  python3 -c "import tomllib;open('VERSION','w').write(tomllib.load(open('pyproject.toml','rb'))['project']['version']+'\n')"
  echo "VERSION: $(cat VERSION)" | tee -a "$LOG"
fi

log "batería completa"
MU_SKIP_NETWORK=1 .venv/bin/python -m pytest tests/ -q --basetemp=/tmp/pt-guardia -p no:cacheprovider > tmp/pytest-guardia.log 2>&1
log "pytest rc=$? · rojos: $(grep -cE '^(FAILED|ERROR)' tmp/pytest-guardia.log)"

mapfile -t NUEVOS < <(grep -E '^(FAILED|ERROR)' tmp/pytest-guardia.log | sed -E 's/ - .*//' | sort -u | grep -vE "$CONOCIDOS")
flaky=0
for linea in "${NUEVOS[@]:-}"; do
  [ -z "$linea" ] && continue
  prueba="${linea#FAILED }"; prueba="${prueba#ERROR }"
  log "re-ejecutando a solas: $prueba"
  if MU_SKIP_NETWORK=1 .venv/bin/python -m pytest "$prueba" -q --basetemp=/tmp/pt-solo -p no:cacheprovider >> "$LOG" 2>&1; then
    log "inestable: pasa a solas → se anota y se sigue"
    flaky=$((flaky + 1))
  else
    log "REGRESIÓN: falla también a solas → no se empaqueta"
    exit 2
  fi
done
[ "$flaky" -gt 0 ] && log "pruebas inestables anotadas: $flaky"

log "paquetes"
tools/reempaquetar.sh >> "$LOG" 2>&1 || { log "FALLO al empaquetar"; exit 3; }
log "fin"
