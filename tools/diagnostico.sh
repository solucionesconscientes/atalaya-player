#!/usr/bin/env bash
# Recoge datos mientras ves algo con Atalaya, para saber por qué va a tirones. Ctrl+C para terminar.
set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/tools/diagnostico.py" "$@"
