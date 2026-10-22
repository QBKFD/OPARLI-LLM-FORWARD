#!/usr/bin/env bash
# The options store to S3, under BACKUP_S3_URI (the scanner's, e.g.
# s3://bucket/oparli):
#
#   options/  options-<host>-<UTC ts>.sqlite.gz   the whole store, one file
#
# Called by run_snapshot.sh after every ok snapshot; can also run by hand.
# The store is snapshotted with backup_sqlite.py (SQLite's online backup API,
# integrity-checked, must hold option_runs) inside a one-off options
# container, gzipped and uploaded with server-side encryption. Nothing is
# deleted from S3; retention is an S3 lifecycle rule (deploy/options/README.md).
# Credentials come from the instance's IAM role.
#
# Success: writes last_ok=<epoch> to $OPTIONS_BACKUP_STATE (default
# ~/.options-backup). Any failure: one Telegram message and a non-zero exit.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
export PATH="$PATH:/usr/local/bin:/snap/bin"     # cron's PATH is /usr/bin:/bin; aws may be elsewhere

STATE="${OPTIONS_BACKUP_STATE:-$HOME/.options-backup}"
log() { echo "$(date -u '+%F %T') $*"; }
val() { grep -E "^$1=" .env 2>/dev/null | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z0-9_]*=//' | tr -d "\"'"; }
tg() {
  curl -s -m 15 "https://api.telegram.org/bot$(val TELEGRAM_BOT_TOKEN)/sendMessage" \
    --data-urlencode "chat_id=$(val TELEGRAM_CHAT_ID)" --data-urlencode "text=$1" >/dev/null \
    || log "telegram send failed"
}
fail() { log "options backup FAILED: $1"; tg "⚠️ options backup failed: $1, see ~/options-snapshot.log"; exit 1; }

URI="${BACKUP_S3_URI:-$(val BACKUP_S3_URI)}"
URI="${URI%/}"
case "$URI" in s3://?*) ;; *) fail "BACKUP_S3_URI is not set to an s3:// URI in .env" ;; esac
ts=$(date -u +%Y%m%dT%H%M%SZ)
host=$(hostname -s)
work=$(mktemp -d) || fail "mktemp"
trap 'rm -rf "$work"' EXIT
name="options-$host-$ts.sqlite"
key="$URI/options/$name.gz"

docker compose run --rm -T --no-deps -v "$work:/backup" --entrypoint python options-snapshot \
  /app/backup_sqlite.py snapshot /data/options.sqlite "/backup/$name" --expect-table option_runs \
  || fail "snapshot of the store"
gzip -9 "$work/$name" || fail "gzip"
aws s3 cp --only-show-errors --sse AES256 "$work/$name.gz" "$key" || fail "upload to $key"
log "uploaded options to $key ($(wc -c < "$work/$name.gz" | tr -d ' ') bytes)"
printf 'last_ok=%s\n' "$(date +%s)" > "$STATE" || fail "writing $STATE"
