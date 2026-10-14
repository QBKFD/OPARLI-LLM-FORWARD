#!/usr/bin/env bash
# Daily backup of the scanner journal, and of the datafeed store when the stream
# profile runs, to S3. Runs on the host from cron, next to docker-compose.yml.
#
# For each file it:
#   1. snapshots it inside the scanner container (backup_sqlite.py: SQLite online
#      backup API, consistent while the scanner or recorder writes);
#   2. copies the snapshot out and gzips it;
#   3. uploads it with server-side encryption to
#      s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/<journal|store>/<host>-<UTC ts>.sqlite.gz
#   4. removes the local and in-container copies.
# It never deletes anything from S3; retention is an S3 lifecycle rule
# (docs/DEPLOY_RUNBOOK.md). Credentials come from the instance's IAM role; no
# AWS keys in .env.
#
# On any failure: one Telegram message and a non-zero exit.
#
# Install (as ubuntu):  crontab -e  and add
#   30 22 * * * $HOME/oparli/deploy/scanner/backup.sh >> $HOME/scanner-backup.log 2>&1
set -uo pipefail
cd "$(dirname "$0")" || exit 1

log() { echo "$(date -u '+%F %T') $*"; }
val() { grep -E "^$1=" .env 2>/dev/null | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z0-9_]*=//' | tr -d "\"'"; }
tg() {
  curl -s -m 15 "https://api.telegram.org/bot$(val TELEGRAM_BOT_TOKEN)/sendMessage" \
    --data-urlencode "chat_id=$(val TELEGRAM_CHAT_ID)" --data-urlencode "text=$1" >/dev/null \
    || log "telegram send failed"
}
fail() { log "backup FAILED: $1"; tg "⚠️ scanner backup failed: $1 — see ~/scanner-backup.log"; exit 1; }

BUCKET="${BACKUP_S3_BUCKET:-$(val BACKUP_S3_BUCKET)}"
PREFIX="${BACKUP_S3_PREFIX:-$(val BACKUP_S3_PREFIX)}"
[ -n "$BUCKET" ] || fail "BACKUP_S3_BUCKET is not set in .env"
ts=$(date -u +%Y%m%dT%H%M%SZ)
host=$(hostname -s)
work=$(mktemp -d) || fail "mktemp"
trap 'rm -rf "$work"' EXIT

# backup_one KIND PATH-IN-CONTAINER TABLE
backup_one() {
  local kind=$1 path=$2 table=$3
  local name="$kind-$host-$ts.sqlite"
  local key="${PREFIX:+$PREFIX/}$kind/$name.gz"
  docker compose exec -T scanner python /app/backup_sqlite.py snapshot "$path" "/data/backups/$name" \
    --expect-table "$table" || return 1
  docker compose cp "scanner:/data/backups/$name" "$work/$name" || return 1
  docker compose exec -T scanner rm -f "/data/backups/$name" || return 1
  gzip -9 "$work/$name" || return 1
  aws s3 cp --only-show-errors --sse AES256 "$work/$name.gz" "s3://$BUCKET/$key" || return 1
  log "uploaded $kind to s3://$BUCKET/$key ($(wc -c < "$work/$name.gz" | tr -d ' ') bytes)"
  rm -f "$work/$name.gz"
}

backup_one journal /data/scanner_decisions.sqlite alerts || fail "journal"
if docker compose exec -T scanner test -f /datafeed/datafeed.sqlite; then
  backup_one store /datafeed/datafeed.sqlite bars || fail "datafeed store"
else
  log "no datafeed store (stream profile not running); journal only"
fi
log "backup done"
