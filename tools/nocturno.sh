#!/usr/bin/env bash
# Runner nocturno: encadena iteraciones de `claude -p` hasta completar el backlog o llegar a la hora límite.
# Variables: EFFORT (de .runner.env), MAX_ITER (iteraciones productivas), DEADLINE (HH:MM), ITER_TIMEOUT (p. ej. 3h),
# LIMIT_WAIT (segundos de espera si el aviso de límite no trae hora de reset).
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
# shellcheck disable=SC1091
source .runner.env 2>/dev/null || true
EFFORT="${EFFORT:-high}"
MAX_ITER="${MAX_ITER:-20}"; DEADLINE="${DEADLINE:-07:30}"; ITER_TIMEOUT="${ITER_TIMEOUT:-3h}"; LIMIT_WAIT="${LIMIT_WAIT:-1800}"
d=$(date -d "today $DEADLINE" +%s); [ "$d" -le "$(date +%s)" ] && d=$(date -d "tomorrow $DEADLINE" +%s)
mkdir -p logs
log() { echo "$(date '+%F %T') $*" | tee -a logs/runner.log; }

# Sleeps until epoch $1 or the deadline, whichever comes first. Returns 1 if the deadline was reached.
wait_until() {
  local until=$1 now
  while :; do
    now=$(date +%s)
    [ "$now" -ge "$d" ] && return 1
    [ "$now" -ge "$until" ] && return 0
    sleep $(( (until - now) < 60 ? (until - now) : 60 ))
  done
}

# Given the `result` text of a limit notice ("... resets 12:20pm (Europe/Madrid)"), prints the epoch of the reset
# plus a safety margin, or nothing if no parseable time is found.
reset_epoch() {
  local when t
  when=$(printf '%s' "$1" | grep -oiE 'resets? (at )?[0-9]{1,2}(:[0-9]{2})? ?(am|pm)' | head -1 | sed -E 's/^resets? (at )?//I')
  [ -n "$when" ] || return 0
  t=$(date -d "today $when" +%s 2>/dev/null) || return 0
  [ "$t" -le "$(date +%s)" ] && t=$(date -d "tomorrow $when" +%s)
  echo $(( t + 120 ))
}

log "runner: effort=$EFFORT max_iter=$MAX_ITER deadline=$(date -d "@$d" '+%F %H:%M') iter_timeout=$ITER_TIMEOUT"
fails=0; done_iter=0; n_file=0
while [ "$done_iter" -lt "$MAX_ITER" ]; do
  grep -q "ESTADO_GLOBAL: COMPLETADO" PROGRESS.md && { log "Backlog completado"; break; }
  [ "$(date +%s)" -ge "$d" ] && { log "Hora límite alcanzada"; break; }
  n_file=$((n_file+1)); n=$(printf %02d "$n_file"); iter=$((done_iter+1))
  log "▶ iteración $iter (archivo $n, fable, effort=$EFFORT)"
  timeout -k 60 "$ITER_TIMEOUT" claude -p "$(sed "s/{N}/$iter/g" prompts/iteracion.md)" \
    --model fable --fallback-model opus --effort "$EFFORT" \
    --permission-mode auto --permission-prompts none \
    --name "mpv-uos-noche-$n" --output-format stream-json --verbose \
    > "logs/iter-$n.jsonl" 2> "logs/iter-$n.err"
  rc=$?
  last=$(tail -n 1 "logs/iter-$n.jsonl" 2>/dev/null || true)
  result=$(printf '%s' "$last" | jq -r '.result // empty' 2>/dev/null || true)
  turns=$(printf '%s' "$last" | jq -r '.num_turns // 0' 2>/dev/null || echo 0)
  log "■ iteración $iter terminó (código $rc, turnos $turns)"

  if printf '%s' "$result" | grep -qiE "hit your .*limit|usage limit|rate limit"; then
    # Cupo agotado: no es un fallo. Esperar al reset (o LIMIT_WAIT) sin pasar de la hora límite.
    [ "$turns" -gt 1 ] && done_iter=$((done_iter+1))
    until_t=$(reset_epoch "$result"); [ -n "$until_t" ] || until_t=$(( $(date +%s) + LIMIT_WAIT ))
    log "cupo agotado: «$result» → espero hasta $(date -d "@$until_t" '+%F %H:%M')"
    wait_until "$until_t" || { log "Hora límite alcanzada durante la espera"; break; }
    fails=0; continue
  fi
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    log "iteración cortada por tiempo ($ITER_TIMEOUT); se continúa"
    done_iter=$((done_iter+1)); fails=0; continue
  fi
  if [ "$rc" -ne 0 ]; then
    fails=$((fails+1)); [ "$fails" -ge 6 ] && { log "Demasiados fallos seguidos"; break; }
    wait_until $(( $(date +%s) + 900 )) || { log "Hora límite alcanzada"; break; }
  else
    done_iter=$((done_iter+1)); fails=0
  fi
done
log "runner terminado (iteraciones productivas: $done_iter)"
notify-send "MPV-UOS" "Sesión nocturna terminada: revisa PROGRESS.md y NEEDS_HUMAN.md" 2>/dev/null || true
