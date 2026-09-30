#!/usr/bin/env bash
# Lanzado cada mañana por el timer de usuario `mpv-uos-runner-diario` (~/.config/systemd/user): espera a que termine el
# runner anterior (puede estar acabando su última iteración), archiva sus logs y arranca otro hasta la hora límite
# (DEADLINE, por defecto 07:30 del día siguiente). Con el backlog completo, desactiva el timer y avisa.
set -uo pipefail
cd "$(dirname "$0")/.."
if grep -q '^ESTADO_GLOBAL: COMPLETADO$' PROGRESS.md; then
  systemctl --user disable --now mpv-uos-runner-diario.timer 2>/dev/null || true
  notify-send "MPV-UOS" "Backlog completado: el runner diario se ha desactivado" 2>/dev/null || true
  exit 0
fi
for _ in $(seq 1 240); do   # hasta 4 h esperando a que el runner anterior termine su iteración
  pgrep -f 'bash tools/nocturno.sh' >/dev/null || break
  sleep 60
done
pgrep -f 'bash tools/nocturno.sh' >/dev/null && exit 0
d="logs/tanda-$(date +%F-%H%M)"; mkdir -p "$d"; mv logs/iter-*.jsonl logs/iter-*.err "$d"/ 2>/dev/null || true
exec /usr/bin/systemd-inhibit --what=sleep:idle --who=MPV-UOS --why="Runner diario Claude Code" /usr/bin/bash tools/nocturno.sh
