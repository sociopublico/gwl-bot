#!/usr/bin/env bash
# Extract + publish del día UNGA (hora de Nueva York).
#
# El scrape de gadebate (comando `roster`) corre en la laptop: el WAF de
# CloudFront bloquea el VPS. El cron acá asume que el JSON del día ya está
# en git (pipeline/data/roster/{sesión}/{día}.json).
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
  # -T: cron no tiene TTY; sin esto compose a veces se comporta mal.
  "${COMPOSE[@]}" run --rm -T pipeline "$@"
}

explain_rc() {
  local name="$1" rc="$2"
  if [[ "$rc" -eq 137 ]]; then
    log "$name salió 137 (SIGKILL). No es un restart del contenedor: el kernel lo mató, casi siempre por falta de RAM (OOM) al cargar Whisper. El contenedor que aparece después es publish, otro proceso. Probá PIPELINE_WHISPER_MODEL=tiny y ~4 GB libres."
  fi
}

if [[ -d "$ROOT/.git" ]]; then
  log "git pull --ff-only"
  git -C "$ROOT" pull --ff-only
fi

ROSTER="$ROOT/pipeline/data/roster/${SESSION}/${DAY}.json"
if [[ ! -f "$ROSTER" ]]; then
  log "error: no está $ROSTER — en la laptop: python -m pipeline roster --session $SESSION --day $DAY && git add $ROSTER && git commit && git push"
  exit 1
fi

EXTRACT_ARGS=(extract --session "$SESSION" --day "$DAY")
if [[ "$SKIP_EXISTING" != "0" ]]; then
  EXTRACT_ARGS+=(--skip-existing)
fi

JOURNAL="$ROOT/pipeline/data/unga${SESSION}/${DAY}.txt"
if [[ ! -f "$JOURNAL" ]]; then
  log "aviso: no está $JOURNAL (orden del UN Journal). Extract usa el roster."
fi

log "inicio session=$SESSION day=$DAY skip_existing=$SKIP_EXISTING roster=$ROSTER"
set +e
run_pipeline "${EXTRACT_ARGS[@]}" 2>&1 | tee -a "$LOG"
extract_rc=${PIPESTATUS[0]}
explain_rc extract "$extract_rc"
run_pipeline publish --session "$SESSION" --day "$DAY" --github --sheet 2>&1 | tee -a "$LOG"
publish_rc=${PIPESTATUS[0]}
explain_rc publish "$publish_rc"
set -e

log "fin extract=$extract_rc publish=$publish_rc"
if [[ "$extract_rc" -ne 0 || "$publish_rc" -ne 0 ]]; then
  exit 1
fi
exit 0
