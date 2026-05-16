#!/usr/bin/env bash
# One-time server bootstrap. Run manually on nixos-home-server:
#
#   ssh nixos-home-server
#   bash <(curl -fsSL https://raw.githubusercontent.com/<OWNER>/telegram-bot-ai-assistant/main/deploy/scripts/bootstrap.sh)
#
# What it does:
#   1. Clone the repo into $HOME/telegram-bot-ai-assistant if not present.
#   2. Copy .env.example → .env if .env is missing.
#   3. Print next steps (fill secrets, then run deploy.sh).
#
# Idempotent — safe to re-run.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/FilatovArtem/telegram-bot-ai-assistant.git}"
REPO_DIR="${REPO_DIR:-$HOME/telegram-bot-ai-assistant}"

log() { echo "[bootstrap] $*"; }

# 1. Docker check (admin must be in docker group)
command -v docker >/dev/null 2>&1 || { echo "docker not installed" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker daemon not reachable for current user" >&2; exit 1; }

# 2. Clone repo
if [[ ! -d "$REPO_DIR/.git" ]]; then
  log "cloning $REPO_URL → $REPO_DIR"
  git clone --depth 50 "$REPO_URL" "$REPO_DIR"
else
  log "repo exists at $REPO_DIR — fetching latest"
  git -C "$REPO_DIR" fetch --quiet origin main
  git -C "$REPO_DIR" reset --hard origin/main
fi

# 3. .env
cd "$REPO_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  log ".env created from .env.example — chmod 0600"
  log "fill secrets:  nano $REPO_DIR/.env"
else
  log ".env already present — leaving as-is"
fi

# 4. data dirs (volumes will be created by docker compose, but if running bare metal — useful)
mkdir -p data/knowledge data/faiss_index

# 5. Next steps
cat <<'EOF'

[bootstrap] DONE.
Next steps on this server:

  1. Edit .env and fill in secrets:
       nano <REPO_DIR>/.env
     Required: BOT_TOKEN, ADMIN_IDS, OPENAI_API_KEY, API_ADMIN_TOKEN
     Optional (for proxy):
       TELEGRAM_PROXY=http://host.docker.internal:10808
       HTTPS_PROXY=http://host.docker.internal:10808
       NO_PROXY=localhost,127.0.0.1

  2. First deploy:
       bash <REPO_DIR>/deploy/scripts/deploy.sh

  3. Configure GitHub repo secrets (for auto-deploy on push):
       DEPLOY_HOST=95.165.71.0   (or hostname)
       DEPLOY_USER=admin
       DEPLOY_SSH_KEY=<contents of ~/.ssh/<deploy-key>>
       DEPLOY_KNOWN_HOSTS=<output of: ssh-keyscan -t ed25519,rsa <host>>

  4. Push to main -> GitHub Actions runs CI then SSH-deploys.

EOF
