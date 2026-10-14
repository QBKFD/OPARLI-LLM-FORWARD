#!/bin/bash
# ---------------------------------------------------------------------------
# Deploy local changes to the OPARLI production server (oparli.com).
# Bypasses GitHub (server has no git auth) — copies files directly over SSH.
#
# Usage:  ./scripts/deploy.sh            # frontend + backend + restart
#         ./scripts/deploy.sh frontend   # only rebuild+push the website
#         ./scripts/deploy.sh backend    # only push backend code + restart API
# ---------------------------------------------------------------------------
set -e

# Server and key come from the environment (never hard-code them here):
#   DEPLOY_HOST  ssh destination, user@host
#   DEPLOY_KEY   path to the private key for that host
KEY="${DEPLOY_KEY:?set DEPLOY_KEY to the SSH private key path}"
SRV="${DEPLOY_HOST:?set DEPLOY_HOST to user@host}"
APP=/opt/oparli

cd "$(dirname "$0")/.."          # repo root
WHAT="${1:-all}"

if [ "$WHAT" = "all" ] || [ "$WHAT" = "frontend" ]; then
  echo "==> Building frontend..."
  ( cd frontend && npm run build )
  echo "==> Uploading website to nginx root..."
  rsync -az --delete -e "ssh -i $KEY" frontend/dist/ "$SRV:$APP/frontend/dist/"
fi

if [ "$WHAT" = "all" ] || [ "$WHAT" = "backend" ]; then
  echo "==> Uploading backend code (skips venv / .env / caches)..."
  rsync -az \
    --exclude 'venv/' --exclude '.env' --exclude '__pycache__/' --exclude '*.pyc' \
    -e "ssh -i $KEY" backend/ "$SRV:$APP/backend/"
  echo "==> Restarting API..."
  ssh -i "$KEY" "$SRV" "systemctl restart oparli-api && sleep 2 && systemctl is-active oparli-api"
fi

echo "==> Done. Hard-refresh the browser (Cmd+Shift+R) to clear cached JS."
