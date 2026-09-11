#!/usr/bin/env bash
# Fetch + publish del día UNGA (hora de Nueva York).
#
# En el server, desde el clone (una vez):
#   chmod +x pipeline/scripts/daily.sh
#   docker compose -f docker-compose.pipeline.yml build
#
# Crontab (usuario que pueda docker, working dir da igual):
#   crontab -e
#   PATH=/usr/local/bin:/usr/bin:/bin
#   CRON_TZ=America/New_York
#   30 22 * * * /ruta/al/repo/pipeline/scripts/daily.sh
#
# 22:30 NY = fin del día de debate. --skip-existing no re-transcribe lo que ya está.
# Corrida a mano: SESSION=81 DAY=2026-09-22 ./pipeline/scripts/daily.sh

set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"
export CRON_TZ="${CRON_TZ:-America/New_York}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SESSION="${SESSION:-81}"
DAY="${DAY:-$(TZ=America/New_York date +%F)}"
COMPOSE=(docker compose -f docker-compose.pipeline.yml)
LOG_DIR="${LOG_DIR:-$ROOT/pipeline/log}"
LOCK="${LOCK:-$ROOT/pipeline/cache/daily.lock}"
SKIP_EXISTING="${SKIP_EXISTING:-1}"

mkdir -p "$LOG_DIR" "$(dirname "$LOCK")"
LOG="$LOG_DIR/${DAY}.log"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) ya hay un daily.sh corriendo; salgo" | tee -a "$LOG"
  exit 0
fi

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

run_pipeline() {
  "${COMPOSE[@]}" run --rm pipeline "$@"
}

FETCH_ARGS=(fetch --session "$SESSION" --day "$DAY")
if [[ "$SKIP_EXISTING" != "0" ]]; then
  FETCH_ARGS+=(--skip-existing)
fi

JOURNAL="$ROOT/pipeline/data/unga${SESSION}/${DAY}.txt"
if [[ ! -f "$JOURNAL" ]]; then
  log "aviso: no está $JOURNAL (orden del UN Journal). Fetch igual, pero sin journal ni date index puede recorrer toda la sesión."
fi

log "inicio session=$SESSION day=$DAY skip_existing=$SKIP_EXISTING"
set +e
run_pipeline "${FETCH_ARGS[@]}" 2>&1 | tee -a "$LOG"
fetch_rc=${PIPESTATUS[0]}
run_pipeline publish --session "$SESSION" --day "$DAY" --github --sheet 2>&1 | tee -a "$LOG"
publish_rc=${PIPESTATUS[0]}
set -e

log "fin fetch=$fetch_rc publish=$publish_rc"
if [[ "$fetch_rc" -ne 0 || "$publish_rc" -ne 0 ]]; then
  exit 1
fi
exit 0
