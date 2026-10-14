#!/usr/bin/env bash
# Host-side watchdog for the scanner stack. Runs from cron every 5 minutes,
# OUTSIDE the containers, because the scanner's own heartbeat can't report a
# gateway that never lets the scanner start (2026-09-28: the daily auto-restart
# failed to re-login and the stack sat dead for ~19h with no notification).
#
# - Probes the gateway with a real API handshake (an open port proves nothing:
#   socat listens before IB Gateway is logged in).
# - Down for 2 checks in a row (~10 min): Telegram alert (at most hourly) and a
#   gateway restart, which does a full login from .env. At most 2 re-logins per
#   24h, so a genuinely wrong password can't lock the IBKR account.
# - Scanner restarting >= 3 times in 5 min while the gateway is fine: alert.
# - Scanner container "unhealthy" (its heartbeat file is > 5 min old, i.e. the
#   main loop is hung) for 2 checks in a row: alert, no automatic restart.
# - Stream profile, when its containers exist:
#   - feed or recorder restarting >= 3 times in 5 min (the feed only while the
#     gateway is fine, since it exits whenever the gateway is down): alert;
#   - redis not answering PING for 2 checks in a row: alert.
# - At most one alert per kind per hour. One recovery message per alert.
# - Quiet from Fri 17:00 to Sun 17:00 New York time (market closed): state is
#   tracked, nothing is sent or restarted.
#
# Install (as ubuntu):  crontab -e  and add
#   */5 * * * * $HOME/oparli/deploy/scanner/watchdog.sh >> $HOME/scanner-watchdog.log 2>&1
#
# Test hooks: WATCHDOG_STATE (state file), WATCHDOG_NY_DOW / WATCHDOG_NY_HOUR
# (override the New York weekday 1-7 and hour).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

STATE="${WATCHDOG_STATE:-$HOME/.scanner-watchdog}"
MAX_RELOGINS=2
now=$(date +%s)
log() { echo "$(date -u '+%F %T') $*"; }

fails=0; last_alert=0; alerted=0; last_rc=-1; relogins=""
hfails=0; halerted=0; last_halert=0
rfails=0; ralerted=0; last_ralert=0
last_rc_feed=-1; last_rc_rec=-1; last_salert=0
# shellcheck source=/dev/null
[ -f "$STATE" ] && . "$STATE"
save() {
  printf 'fails=%s\nlast_alert=%s\nalerted=%s\nlast_rc=%s\nrelogins="%s"\n' \
    "$fails" "$last_alert" "$alerted" "$last_rc" "$relogins" > "$STATE"
  printf 'hfails=%s\nhalerted=%s\nlast_halert=%s\nrfails=%s\nralerted=%s\nlast_ralert=%s\n' \
    "$hfails" "$halerted" "$last_halert" "$rfails" "$ralerted" "$last_ralert" >> "$STATE"
  printf 'last_rc_feed=%s\nlast_rc_rec=%s\nlast_salert=%s\n' \
    "$last_rc_feed" "$last_rc_rec" "$last_salert" >> "$STATE"
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
restarts() { docker inspect -f '{{.RestartCount}}' "$1" 2>/dev/null || echo -1; }
health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo missing; }

# market closed (Fri 17:00 -> Sun 17:00 NY): track state, but don't alert or restart
u=${WATCHDOG_NY_DOW:-$(TZ=America/New_York date +%u)}
h=${WATCHDOG_NY_HOUR:-$(TZ=America/New_York date +%H)}
h=$((10#$h))
quiet=0
if { [ "$u" = 5 ] && [ "$h" -ge 17 ]; } || [ "$u" = 6 ] || { [ "$u" = 7 ] && [ "$h" -lt 17 ]; }; then quiet=1; fi

rc=$(restarts scanner-scanner-1)
gateway_ok=0

if probe; then
  gateway_ok=1
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
    n=$(echo "$relogins" | wc -w | tr -d ' ')
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

# scanner heartbeat (compose healthcheck): a hung main loop
hs=$(health scanner-scanner-1)
if [ "$hs" = unhealthy ]; then
  hfails=$((hfails + 1)); log "scanner unhealthy ($hfails in a row)"
  if [ "$hfails" -ge 2 ] && [ "$quiet" = 0 ] && [ $((now - last_halert)) -ge 3600 ]; then
    tg "⚠️ scanner: no heartbeat for over 5 min (container unhealthy) — the main loop looks hung. Check 'docker compose logs scanner'; 'docker compose restart scanner' is safe."
    last_halert=$now; halerted=1; log "heartbeat alert"
  fi
else
  if [ "$halerted" = 1 ] && [ "$hs" = healthy ]; then tg "✅ scanner: heartbeat back."; log "heartbeat recovered"; halerted=0; fi
  hfails=0
fi

# stream profile: only when its containers exist
rcf=$(restarts scanner-feed-1); rcr=$(restarts scanner-recorder-1)
if [ "$quiet" = 0 ] && [ $((now - last_salert)) -ge 3600 ]; then
  msg=""
  if [ "$gateway_ok" = 1 ] && [ "$last_rc_feed" -ge 0 ] && [ "$rcf" -ge 0 ] && [ $((rcf - last_rc_feed)) -ge 3 ]; then
    msg="feed restarted $((rcf - last_rc_feed)) times in 5 min while the gateway is up"
  fi
  if [ "$last_rc_rec" -ge 0 ] && [ "$rcr" -ge 0 ] && [ $((rcr - last_rc_rec)) -ge 3 ]; then
    msg="${msg:+$msg; }recorder restarted $((rcr - last_rc_rec)) times in 5 min"
  fi
  if [ -n "$msg" ]; then
    tg "⚠️ scanner datafeed: $msg — check 'docker compose logs feed recorder'."
    last_salert=$now; log "datafeed crash-loop alert: $msg"
  fi
fi
last_rc_feed=$rcf; last_rc_rec=$rcr

if [ "$(health scanner-redis-1)" != missing ]; then
  if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
    if [ "$ralerted" = 1 ]; then tg "✅ scanner datafeed: Redis answering again."; log "redis recovered"; fi
    rfails=0; ralerted=0
  else
    rfails=$((rfails + 1)); log "redis ping failed ($rfails in a row)"
    if [ "$rfails" -ge 2 ] && [ "$quiet" = 0 ] && [ $((now - last_ralert)) -ge 3600 ]; then
      tg "⚠️ scanner datafeed: Redis not answering for ~$((rfails * 5)) min — in stream mode no bars reach the scanner."
      last_ralert=$now; ralerted=1; log "redis alert"
    fi
  fi
fi
save
