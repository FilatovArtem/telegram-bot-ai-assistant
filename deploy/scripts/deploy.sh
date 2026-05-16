#!/usr/bin/env bash
# Server-side deploy script. Idempotent — safe to re-run.
# Pull latest main, rebuild image, recreate container, verify health.
# Triggered by .github/workflows/deploy.yml after CI succeeds, or run manually.

set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/telegram-bot-ai-assistant}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
LOG_PREFIX="[deploy]"

log() { echo "${LOG_PREFIX} $*"; }
die() { echo "${LOG_PREFIX} ERROR: $*" >&2; exit 1; }

cd "$REPO_DIR" || die "repo dir not found: $REPO_DIR — run bootstrap.sh first"

[[ -f .env ]] || die ".env missing — copy from .env.example and fill secrets"

log "fetching origin/main"
git fetch --quiet origin main
PREV_SHA=$(git rev-parse HEAD)
git reset --hard origin/main
NEW_SHA=$(git rev-parse HEAD)

if [[ "$PREV_SHA" == "$NEW_SHA" ]]; then
  log "no new commits since $PREV_SHA — rebuilding anyway (config or .env may have changed)"
else
  log "advancing from $PREV_SHA to $NEW_SHA"
fi

log "building image + (re)creating container"
docker compose up -d --build --remove-orphans

log "waiting for /health (max ${HEALTH_RETRIES} × ${HEALTH_INTERVAL}s)"
for i in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
    log "health check passed on attempt $i"
    docker compose ps
    docker compose logs --tail 20 bot || true
    exit 0
  fi
  sleep "$HEALTH_INTERVAL"
done

log "health check FAILED after $((HEALTH_RETRIES * HEALTH_INTERVAL))s — last logs:"
docker compose logs --tail 60 bot || true
exit 1
