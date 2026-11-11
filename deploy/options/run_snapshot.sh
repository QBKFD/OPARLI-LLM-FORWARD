#!/usr/bin/env bash
# The daily OG options snapshot, from the host's cron, weekdays 05:00 UTC
# (a run takes about 11 minutes). Install (as ubuntu, host clock in UTC):
#   crontab -e  and add
#   0 5 * * 1-5 $HOME/oparli-options/deploy/options/run_snapshot.sh >> $HOME/options-snapshot.log 2>&1
#
# One `docker compose run` of backend.options, never retried: every outcome is
# one option_runs row in the store (see backend/options/snapshot.py).
#   exit 0  ok (or the trade date already had one): backup.sh uploads the store
#   exit 3  COMEX closed or a CME holiday: skipped, logged only
#   exit 4  IBKR error 10197, the live account is logged in somewhere:
#           skipped, one Telegram notice; run this script by hand to take the
#           day once the live session is logged out
#   other   failed: one Telegram message
# A second copy started while one runs exits at once (flock).
#
# Writes last_rc=<exit> last_run=<epoch> (and last_ok=<epoch> on exit 0) to
# $OPTIONS_STATE (default ~/.options-snapshot).
set -uo pipefail
cd "$(dirname "$0")" || exit 1
export PATH="$PATH:/usr/local/bin:/snap/bin"     # cron's PATH is /usr/bin:/bin; aws may be elsewhere

STATE="${OPTIONS_STATE:-$HOME/.options-snapshot}"
log() { echo "$(date -u '+%F %T') $*"; }
val() { grep -E "^$1=" .env 2>/dev/null | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z0-9_]*=//' | tr -d "\"'"; }
tg() {
  curl -s -m 15 "https://api.telegram.org/bot$(val TELEGRAM_BOT_TOKEN)/sendMessage" \
    --data-urlencode "chat_id=$(val TELEGRAM_CHAT_ID)" --data-urlencode "text=$1" >/dev/null \
    || log "telegram send failed"
}

exec 9>"${OPTIONS_LOCK:-/tmp/options-snapshot.lock}"
if ! flock -n 9; then
  log "another options snapshot is running; not starting a second one"
  exit 0
fi

log "options snapshot: start"
docker compose run --rm -T options-snapshot
rc=$?
last_ok=$(grep -E '^last_ok=' "$STATE" 2>/dev/null | head -1)
case $rc in
  0)
    log "options snapshot: ok"
    last_ok="last_ok=$(date +%s)"
    ./backup.sh || rc=1                      # backup.sh sends its own Telegram on failure
    ;;
  3)
    log "options snapshot: skipped (COMEX closed or a CME holiday)"
    ;;
  4)
    log "options snapshot: skipped (IBKR error 10197, competing live session); not retried"
    tg "ℹ️ options snapshot skipped today: IBKR error 10197, the live IBKR account is logged in somewhere. Not retried. To take today's snapshot, log out everywhere and run ~/oparli-options/deploy/options/run_snapshot.sh"
    ;;
  *)
    log "options snapshot: FAILED (exit $rc)"
    tg "⚠️ options snapshot failed (exit $rc), see ~/options-snapshot.log. Not retried."
    ;;
esac
{ printf 'last_rc=%s\nlast_run=%s\n' "$rc" "$(date +%s)"; [ -n "$last_ok" ] && printf '%s\n' "$last_ok"; } > "$STATE" \
  || log "writing $STATE failed"
exit "$rc"
