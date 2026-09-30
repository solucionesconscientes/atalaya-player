#!/usr/bin/env bash
# Pasa el runner a effort xhigh (.runner.env) en cuanto sea seguro: en una pausa por cupo o cuando llega a la puerta de H34.
# Para el servicio del runner actual y arranca el diario (que relee .runner.env). Termina cuando ya corre en xhigh.
set -uo pipefail
cd "$(dirname "$0")/.."
while :; do
  last=$(tail -n 1 logs/runner.log 2>/dev/null)
  run=$(grep '▶ iteración' logs/runner.log | tail -n 1)
  case "$run" in *effort=xhigh*) exit 0 ;; esac
  if [[ "$last" == *"cupo agotado"* ]] || grep -q '^H34 espera iteración xhigh$' PROGRESS.md; then
    systemctl --user stop mpv-uos-runner.service mpv-uos-runner-diario.service 2>/dev/null || true
    sleep 5
    systemctl --user start --no-block mpv-uos-runner-diario.service
    echo "$(date '+%F %T') vigilante: runner relanzado en xhigh" >> logs/runner.log
    exit 0
  fi
  grep -q '^ESTADO_GLOBAL: COMPLETADO$' PROGRESS.md && exit 0
  sleep 60
done
