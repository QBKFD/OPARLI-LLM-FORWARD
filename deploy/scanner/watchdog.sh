#!/usr/bin/env bash
# Host-side watchdog for the scanner stack. Runs from cron every 5 minutes,
# OUTSIDE both containers, because the scanner's own heartbeat can't report a
# gateway that never lets the scanner start (2026-09-28: the daily auto-restart
# failed to re-login and the stack sat dead for ~19h with no notification).
#
# - Probes the gateway with a real API handshake (an open port proves nothing:
#   socat listens before IB Gateway is logged in).
# - Down for 2 checks in a row (~10 min): Telegram alert (at most hourly) and a
#   gateway restart, which does a full login from .env. At most 2 re-logins per
#   24h, so a genuinely wrong password can't lock the IBKR account.
# - Scanner restarting >= 3 times in 5 min while the gateway is fine: alert.
# - Recovery after an alert: one "reachable again" message.
# - Quiet from Fri 17:00 to Sun 17:00 New York time (market closed).
#
# Install (as ubuntu):  crontab -e  and add
#   */5 * * * * $HOME/oparli/deploy/scanner/watchdog.sh >> $HOME/scanner-watchdog.log 2>&1
set -uo pipefail
cd "$(dirname "$0")"

STATE="${WATCHDOG_STATE:-$HOME/.scanner-watchdog}"
MAX_RELOGINS=2
now=$(date +%s)
log() { echo "$(date -u '+%F %T') $*"; }

fails=0; last_alert=0; alerted=0; last_rc=-1; relogins=""
[ -f "$STATE" ] && . "$STATE"
save() {
  printf 'fails=%s\nlast_alert=%s\nalerted=%s\nlast_rc=%s\nrelogins="%s"\n' \
    "$fails" "$last_alert" "$alerted" "$last_rc" "$relogins" > "$STATE"
}

val() { grep -E "^$1=" .env | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z_]*=//' | tr -d "\"'"; }
tg() {
  curl -s -m 15 "https://api.telegram.org/bot$(val TELEGRAM_BOT_TOKEN)/sendMessage" \
    --data-urlencode "chat_id=$(val TELEGRAM_CHAT_ID)" --data-urlencode "text=$1" >/dev/null \
    || log "telegram send failed"
}
probe() {
  timeout 60 docker compose run --rm -T --no-deps --entrypoint python scanner -c \
    "import os; from ib_insync import IB; IB().connect(os.environ['IB_HOST'], int(os.environ['IB_PORT']), clientId=996, timeout=10, readonly=True).disconnect()" \
    >/dev/null 2>&1
}

# market closed (Fri 17:00 -> Sun 17:00 NY): track state, but don't alert or restart
u=$(TZ=America/New_York date +%u); h=$(TZ=America/New_York date +%H)
quiet=0
if { [ "$u" = 5 ] && [ "$h" -ge 17 ]; } || [ "$u" = 6 ] || { [ "$u" = 7 ] && [ "$h" -lt 17 ]; }; then quiet=1; fi

rc=$(docker inspect -f '{{.RestartCount}}' scanner-scanner-1 2>/dev/null || echo -1)

if probe; then
  if [ "$alerted" = 1 ]; then tg "✅ scanner: IB Gateway reachable again — scanning resumes."; log "recovered"; fi
  fails=0; alerted=0
  # scanner crash-looping while the gateway is fine
  if [ "$quiet" = 0 ] && [ "$last_rc" -ge 0 ] && [ "$rc" -ge 0 ] && [ $((rc - last_rc)) -ge 3 ] \
     && [ $((now - last_alert)) -ge 3600 ]; then
    tg "⚠️ scanner: restarted $((rc - last_rc)) times in the last 5 min while the gateway is up — check 'docker compose logs scanner'."
    last_alert=$now; log "crash-loop alert rc=$rc"
  fi
else
  fails=$((fails + 1)); log "probe failed ($fails in a row)"
  if [ "$fails" -ge 2 ] && [ "$quiet" = 0 ]; then
    # keep only re-logins from the last 24h
    kept=""; for t in $relogins; do [ $((now - t)) -lt 86400 ] && kept="$kept $t"; done; relogins="${kept# }"
    n=$(echo $relogins | wc -w | tr -d ' ')
    msg="⚠️ scanner: IB Gateway API unreachable for ~$((fails * 5)) min — no alerts are being produced."
    if [ "$n" -lt "$MAX_RELOGINS" ]; then
      docker compose restart ibgateway >/dev/null 2>&1
      relogins="$relogins $now"; relogins="${relogins# }"
      msg="$msg Restarting the gateway for a full re-login ($((n + 1))/$MAX_RELOGINS in 24h)."
      log "restarted gateway (relogin $((n + 1)))"
      tg "$msg"; last_alert=$now; alerted=1
    elif [ $((now - last_alert)) -ge 3600 ]; then
      tg "$msg Auto re-login limit reached — check the gateway (VNC) or credentials."
      last_alert=$now; alerted=1; log "alert, relogin limit reached"
    fi
  fi
fi
last_rc=$rc
save
