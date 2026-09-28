#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
source .runner.env 2>/dev/null || EFFORT=xhigh
MAX_ITER="${MAX_ITER:-20}"; DEADLINE="${DEADLINE:-09:00}"
d=$(date -d "today $DEADLINE" +%s); [ "$d" -le "$(date +%s)" ] && d=$(date -d "tomorrow $DEADLINE" +%s)
mkdir -p logs; fails=0
for i in $(seq 1 "$MAX_ITER"); do
  grep -q "ESTADO_GLOBAL: COMPLETADO" PROGRESS.md && { echo "Backlog completado"; break; }
  [ "$(date +%s)" -ge "$d" ] && { echo "Hora límite alcanzada"; break; }
  n=$(printf %02d "$i")
  echo "$(date '+%F %T') ▶ iteración $n (fable, effort=$EFFORT)" | tee -a logs/runner.log
  claude -p "$(sed "s/{N}/$i/g" prompts/iteracion.md)" \
    --model fable --fallback-model opus --effort "$EFFORT" \
    --permission-mode auto --permission-prompts none \
    --name "mpv-uos-noche-$n" --output-format stream-json --verbose \
    > "logs/iter-$n.jsonl" 2> "logs/iter-$n.err"
  rc=$?
  echo "$(date '+%F %T') ■ iteración $n terminó (código $rc)" | tee -a logs/runner.log
  if [ "$rc" -ne 0 ]; then
    fails=$((fails+1)); [ "$fails" -ge 6 ] && { echo "Demasiados fallos seguidos" | tee -a logs/runner.log; break; }
    sleep 900
  else fails=0; fi
done
notify-send "MPV-UOS" "Sesión nocturna terminada: revisa PROGRESS.md y NEEDS_HUMAN.md" 2>/dev/null || true
