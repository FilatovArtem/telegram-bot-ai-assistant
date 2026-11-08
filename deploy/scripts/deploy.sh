#!/usr/bin/env bash
# Server-side deploy script. Idempotent — safe to re-run.
# Pull latest main, rebuild image, recreate container, verify health.
# Invoked by the manual GitHub Actions Deploy workflow or directly on the server.

set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/telegram-bot-ai-assistant}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
LOG_PREFIX="[deploy]"

# Pick API_PORT from .env (fallback 8000) so the health check matches the
# port the container actually binds. Multi-bot hosts override API_PORT in .env
# to avoid collisions between bots running on the same machine.
read_api_port() {
  local port
  if [[ -f "$REPO_DIR/.env" ]]; then
    port=$(grep -E '^API_PORT=' "$REPO_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '\r\n ' || true)
  fi
  echo "${port:-8000}"
}

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

API_PORT=$(read_api_port)
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${API_PORT}/health}"
log "waiting for ${HEALTH_URL} (max ${HEALTH_RETRIES} × ${HEALTH_INTERVAL}s)"
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
