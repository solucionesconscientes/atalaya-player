#!/usr/bin/env bash
# Pasa el runner a effort xhigh (.runner.env) en cuanto sea seguro: en una pausa por cupo, en la puerta de H34 o cuando el runner
# termina. Si el backlog aún tiene tareas [ ] (p. ej. se añadieron tras marcarlo completado), reabre ESTADO_GLOBAL y relanza.
set -uo pipefail
cd "$(dirname "$0")/.."
while :; do
  run=$(grep '▶ iteración' logs/runner.log | tail -n 1)
  case "$run" in *effort=xhigh*) exit 0 ;; esac
  last=$(tail -n 1 logs/runner.log 2>/dev/null)
  active=$(systemctl --user is-active mpv-uos-runner.service mpv-uos-runner-diario.service 2>/dev/null | grep -c '^active$')
  if [[ "$last" == *"cupo agotado"* ]] || [ "$active" = 0 ] || grep -q '^H34 espera iteración xhigh$' PROGRESS.md; then
    systemctl --user stop mpv-uos-runner.service mpv-uos-runner-diario.service 2>/dev/null || true
    sleep 5
    if grep -qE '^- \[ \]' BACKLOG.md; then
      sed -i 's/^ESTADO_GLOBAL: COMPLETADO$/ESTADO_GLOBAL: EN_CURSO/' PROGRESS.md
      systemctl --user enable mpv-uos-runner-diario.timer 2>/dev/null || true
    fi
    systemctl --user start --no-block mpv-uos-runner-diario.service
    echo "$(date '+%F %T') vigilante: runner relanzado en xhigh" >> logs/runner.log
    exit 0
  fi
  sleep 60
done
