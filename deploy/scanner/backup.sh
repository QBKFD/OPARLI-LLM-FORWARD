#!/usr/bin/env bash
# Daily backup to S3, under BACKUP_S3_URI (e.g. s3://bucket/oparli).
# Runs on the host from cron, next to docker-compose.yml.
#
#   journal/  the scanner journal                  journal-<host>-<UTC ts>.sqlite.gz
#   store/    the datafeed store (stream profile)  store-<host>-<UTC ts>.sqlite.gz
#   archive/  the store's bars as daily parquet     archive/bars/date=YYYY-MM-DD/XAUUSD.parquet
#
# SQLite files are snapshotted inside the scanner container (backup_sqlite.py:
# the online backup API, consistent while the scanner or recorder writes),
# copied out, gzipped and uploaded with server-side encryption. The archive
# exports the last 7 completed UTC days (export_parquet.py) and uploads only
# the days not in S3 yet, so a missed run is caught up by the next one.
# Nothing is ever deleted from S3: retention is an S3 lifecycle rule
# (docs/DEPLOY_RUNBOOK.md). Credentials come from the instance's IAM role; no
# AWS keys in .env.
#
# Success: writes last_ok=<epoch> to $BACKUP_STATE (default
# ~/.scanner-backup), which watchdog.sh checks. Any failure: one Telegram
# message and a non-zero exit.
#
# Install (as ubuntu):  crontab -e  and add
#   30 22 * * * $HOME/oparli/deploy/scanner/backup.sh >> $HOME/scanner-backup.log 2>&1
set -uo pipefail
cd "$(dirname "$0")" || exit 1

STATE="${BACKUP_STATE:-$HOME/.scanner-backup}"
log() { echo "$(date -u '+%F %T') $*"; }
val() { grep -E "^$1=" .env 2>/dev/null | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z0-9_]*=//' | tr -d "\"'"; }
tg() {
  curl -s -m 15 "https://api.telegram.org/bot$(val TELEGRAM_BOT_TOKEN)/sendMessage" \
    --data-urlencode "chat_id=$(val TELEGRAM_CHAT_ID)" --data-urlencode "text=$1" >/dev/null \
    || log "telegram send failed"
}
fail() { log "backup FAILED: $1"; tg "⚠️ scanner backup failed: $1 — see ~/scanner-backup.log"; exit 1; }

URI="${BACKUP_S3_URI:-$(val BACKUP_S3_URI)}"
URI="${URI%/}"
case "$URI" in s3://?*) ;; *) fail "BACKUP_S3_URI is not set to an s3:// URI in .env" ;; esac
ts=$(date -u +%Y%m%dT%H%M%SZ)
host=$(hostname -s)
work=$(mktemp -d) || fail "mktemp"
trap 'rm -rf "$work"' EXIT

# backup_one KIND PATH-IN-CONTAINER TABLE
backup_one() {
  local kind=$1 path=$2 table=$3
  local name="$kind-$host-$ts.sqlite"
  local key="$URI/$kind/$name.gz"
  docker compose exec -T scanner python /app/backup_sqlite.py snapshot "$path" "/data/backups/$name" \
    --expect-table "$table" || return 1
  docker compose cp "scanner:/data/backups/$name" "$work/$name" || return 1
  docker compose exec -T scanner rm -f "/data/backups/$name" || return 1
  gzip -9 "$work/$name" || return 1
  aws s3 cp --only-show-errors --sse AES256 "$work/$name.gz" "$key" || return 1
  log "uploaded $kind to $key ($(wc -c < "$work/$name.gz" | tr -d ' ') bytes)"
  rm -f "$work/$name.gz"
}

# archive_bars: the last 7 completed UTC days of bars as parquet, missing days only
archive_bars() {
  local listing rel rows key n=0
  docker compose exec -T scanner rm -rf /data/backups/archive || return 1
  listing=$(docker compose exec -T scanner python /app/export_parquet.py \
    /datafeed/datafeed.sqlite /data/backups/archive --last-days 7) || return 1
  [ -n "$listing" ] || { log "archive: no bars in the last 7 days"; return 0; }
  docker compose cp scanner:/data/backups/archive "$work/archive" || return 1
  docker compose exec -T scanner rm -rf /data/backups/archive || return 1
  while read -r rel rows; do
    key="$URI/archive/bars/$rel"
    if aws s3 ls "$key" >/dev/null 2>&1; then continue; fi
    aws s3 cp --only-show-errors --sse AES256 "$work/archive/$rel" "$key" || return 1
    log "archived $rows bars to $key"; n=$((n + 1))
  done <<< "$listing"
  log "archive: $n new day(s)"
}

backup_one journal /data/scanner_decisions.sqlite alerts || fail "journal"
if docker compose exec -T scanner test -f /datafeed/datafeed.sqlite; then
  backup_one store /datafeed/datafeed.sqlite bars || fail "datafeed store"
  archive_bars || fail "parquet archive"
else
  log "no datafeed store (stream profile not running); journal only"
fi
printf 'last_ok=%s\n' "$(date +%s)" > "$STATE" || fail "writing $STATE"
log "backup done"
