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
# - Host and data health:
#   - disk use >= DISK_ALERT_PCT on / or Docker's data dir;
#   - MemAvailable < MEM_AVAIL_MIN_PCT;
#   - last successful backup.sh older than BACKUP_MAX_AGE_H (when BACKUP_S3_URI
#     is set);
#   - in the stream profile: recorder lag >= RECORDER_LAG_MAX_MIN market-open
#     minutes, and any growth of rejected_messages.
# - At most one alert per kind per hour. One recovery message per alert.
# - Quiet from Fri 17:00 to Sun 17:00 New York time (market closed): state is
#   tracked, nothing market-related is sent or restarted. Disk, memory and
#   backup alerts ignore the quiet window.
# - One daily Telegram heartbeat (first run at or after
#   DAILY_HEARTBEAT_HOUR_UTC), every day, weekends included: a summary of all of
#   the above, so silence means the watchdog itself is broken.
#
# Install (as ubuntu):  crontab -e  and add
#   */5 * * * * $HOME/oparli/deploy/scanner/watchdog.sh >> $HOME/scanner-watchdog.log 2>&1
#
# - Every run also writes health.json (HEALTH_JSON, default ~/scanner-health.json):
#   green/yellow/red per component for the status board (python -m tools.status).
#   The alert thresholds below come from tools/status/thresholds.toml, the one
#   place thresholds are set; the numbers here are the fallback if it can't be
#   read (a test keeps them equal).
#
# Test hooks: WATCHDOG_STATE (state file), WATCHDOG_NY_DOW / WATCHDOG_NY_HOUR
# (override the New York weekday 1-7 and hour), WATCHDOG_NOW (epoch seconds),
# WATCHDOG_MEMINFO (default /proc/meminfo), WATCHDOG_DISK_PATHS (default
# "/ /var/lib/docker"), BACKUP_STATE (default ~/.scanner-backup), WATCHDOG_REPO
# (repo root, default ../..), STATUS_PYTHON (default python3), HEALTH_JSON.
set -uo pipefail
cd "$(dirname "$0")" || exit 1

STATE="${WATCHDOG_STATE:-$HOME/.scanner-watchdog}"
MAX_RELOGINS=2
DISK_ALERT_PCT=85
MEM_AVAIL_MIN_PCT=10
BACKUP_MAX_AGE_H=26
RECORDER_LAG_MAX_MIN=15
DAILY_HEARTBEAT_HOUR_UTC=7
now=${WATCHDOG_NOW:-$(date +%s)}
log() { echo "$(date -u '+%F %T') $*"; }

REPO=${WATCHDOG_REPO:-$(cd ../.. && pwd)}
STATUS_PY=${STATUS_PYTHON:-python3}
HEALTH_JSON=${HEALTH_JSON:-$HOME/scanner-health.json}
# alert thresholds from tools/status/thresholds.toml (the red values); keep the
# numbers above if it can't be read
if th=$(cd "$REPO" && "$STATUS_PY" -m tools.status.config --watchdog-env 2>&1); then
  while IFS='=' read -r k v; do
    case "$k" in
      DISK_ALERT_PCT|MEM_AVAIL_MIN_PCT|BACKUP_MAX_AGE_H|RECORDER_LAG_MAX_MIN)
        [[ "$v" =~ ^[0-9]+$ ]] && printf -v "$k" '%s' "$v" ;;
    esac
  done <<< "$th"
else
  log "thresholds.toml not read ($th); using the built-in alert thresholds"
fi

fails=0; last_alert=0; alerted=0; last_rc=-1; relogins=""
hfails=0; halerted=0; last_halert=0
rfails=0; ralerted=0; last_ralert=0
last_rc_feed=-1; last_rc_rec=-1; last_salert=0
al_disk=0; last_disk=0; al_mem=0; last_mem=0; al_backup=0; last_backup=0; al_lag=0; last_lag=0
rej_reported=-1; last_rej=0; last_daily=-1; rej_seen=-1; rej_grow_ts=0
# shellcheck source=/dev/null
[ -f "$STATE" ] && . "$STATE"
save() {
  {
    printf 'fails=%s\nlast_alert=%s\nalerted=%s\nlast_rc=%s\nrelogins="%s"\n' \
      "$fails" "$last_alert" "$alerted" "$last_rc" "$relogins"
    printf 'hfails=%s\nhalerted=%s\nlast_halert=%s\nrfails=%s\nralerted=%s\nlast_ralert=%s\n' \
      "$hfails" "$halerted" "$last_halert" "$rfails" "$ralerted" "$last_ralert"
    printf 'last_rc_feed=%s\nlast_rc_rec=%s\nlast_salert=%s\n' "$last_rc_feed" "$last_rc_rec" "$last_salert"
    printf 'al_disk=%s\nlast_disk=%s\nal_mem=%s\nlast_mem=%s\nal_backup=%s\nlast_backup=%s\n' \
      "$al_disk" "$last_disk" "$al_mem" "$last_mem" "$al_backup" "$last_backup"
    printf 'al_lag=%s\nlast_lag=%s\nrej_reported=%s\nlast_rej=%s\nlast_daily=%s\n' \
      "$al_lag" "$last_lag" "$rej_reported" "$last_rej" "$last_daily"
    printf 'rej_seen=%s\nrej_grow_ts=%s\n' "$rej_seen" "$rej_grow_ts"
  } > "$STATE"
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
# alert KIND BAD IGNORE_QUIET MESSAGE RECOVERY: while BAD=1 at most one alert per
# hour; one recovery message once it clears. State: al_KIND, last_KIND.
alert() {
  local kind=$1 bad=$2 anytime=$3 msg=$4 rec=$5
  local al="al_$kind" last="last_$kind"
  if [ "$bad" = 1 ]; then
    if { [ "$anytime" = 1 ] || [ "$quiet" = 0 ]; } && [ $((now - ${!last})) -ge 3600 ]; then
      tg "$msg"; printf -v "$last" '%s' "$now"; printf -v "$al" '%s' 1; log "alert $kind"
    fi
  elif [ "${!al}" = 1 ]; then
    tg "$rec"; printf -v "$al" '%s' 0; log "recovered $kind"
  fi
}
disk_pct() {          # highest use % over / and Docker's data dir
  local p v m=0
  for p in ${WATCHDOG_DISK_PATHS:-/ /var/lib/docker}; do
    [ -e "$p" ] || continue
    v=$(df -P "$p" 2>/dev/null | awk 'NR == 2 { sub("%", "", $5); print $5 }')
    [ -n "$v" ] && [ "$v" -gt "$m" ] && m=$v
  done
  echo "$m"
}
meminfo() {           # MemAvailable % and swap used MB, or "-1 -1"
  awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} /^SwapTotal:/ {st=$2} /^SwapFree:/ {sf=$2}
       END { if (t) printf "%d %d\n", a * 100 / t, (st - sf) / 1024; else print "-1 -1" }' \
    "${WATCHDOG_MEMINFO:-/proc/meminfo}" 2>/dev/null || echo "-1 -1"
}
# recorder lag (market-open minutes since the newest live bar ended; -1: no live
# bar yet) and the rejected_messages count, read inside the recorder container
STORE_STATS_PY="import sqlite3
from datetime import datetime, timedelta, timezone
from datafeed.ingest.xauusd_bars import _market_open
c = sqlite3.connect('file:/datafeed/datafeed.sqlite?mode=ro', uri=True)
last = c.execute(\"SELECT MAX(bar_ts) FROM bars WHERE kind = 'live'\").fetchone()[0]
rej = c.execute('SELECT COUNT(*) FROM rejected_messages').fetchone()[0]
lag, now = -1, datetime.now(timezone.utc)
if last:
    t, lag = datetime.fromisoformat(last) + timedelta(minutes=1), 0
    while t + timedelta(minutes=1) <= now and lag < 100000:
        lag += _market_open(t)
        t += timedelta(minutes=1)
print(lag, rej)"
health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo missing; }
cstate() { docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || echo missing; }
# restarts since the previous run, or -1 when either count is unknown
delta() { if [ "$1" -ge 0 ] && [ "$2" -ge 0 ]; then echo $(($2 - $1)); else echo -1; fi; }

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
rc_delta=$(delta "$last_rc" "$rc")
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
feed_delta=$(delta "$last_rc_feed" "$rcf")
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

# host and data health
disk=$(disk_pct)
alert disk "$([ "$disk" -ge "$DISK_ALERT_PCT" ] && echo 1 || echo 0)" 1 \
  "⚠️ scanner host: disk ${disk}% used (alert at ${DISK_ALERT_PCT}%) — clean up (docker system df / prune images, old logs)." \
  "✅ scanner host: disk back to ${disk}%."
read -r mem swap_mb < <(meminfo)
alert mem "$([ "$mem" -ge 0 ] && [ "$mem" -lt "$MEM_AVAIL_MIN_PCT" ] && echo 1 || echo 0)" 1 \
  "⚠️ scanner host: only ${mem}% memory available, swap ${swap_mb} MB used — check 'docker stats'." \
  "✅ scanner host: memory available back to ${mem}%."
backup_info="not configured"
if [ -n "$(val BACKUP_S3_URI)" ]; then
  last_ok=$(sed -n 's/^last_ok=//p' "${BACKUP_STATE:-$HOME/.scanner-backup}" 2>/dev/null)
  last_ok=${last_ok:-0}
  age_h=$(( (now - last_ok) / 3600 ))
  if [ "$last_ok" -gt 0 ]; then backup_info="${age_h} h ago"; else backup_info="never"; fi
  alert backup "$([ "$age_h" -ge "$BACKUP_MAX_AGE_H" ] && echo 1 || echo 0)" 1 \
    "⚠️ scanner backup: last successful backup ${backup_info} (alert after ${BACKUP_MAX_AGE_H} h) — see ~/scanner-backup.log." \
    "✅ scanner backup: a backup succeeded again."
fi
lag=-1; rej=-1; datafeed_info=""
if [ "$(health scanner-recorder-1)" != missing ]; then
  read -r lag rej < <(timeout 60 docker compose exec -T recorder python -c "$STORE_STATS_PY" 2>/dev/null || echo "-1 -1")
  lag=${lag:--1}; rej=${rej:--1}
  alert lag "$([ "$lag" -ge "$RECORDER_LAG_MAX_MIN" ] && echo 1 || echo 0)" 1 \
    "⚠️ scanner datafeed: the store's newest live bar is ${lag} market-open min old (alert at ${RECORDER_LAG_MAX_MIN}) — check 'docker compose logs feed recorder'." \
    "✅ scanner datafeed: recorder caught up (lag ${lag} market-open min)."
  if [ "$rej" -ge 0 ]; then
    if [ "$rej_seen" -ge 0 ] && [ "$rej" -gt "$rej_seen" ]; then rej_grow_ts=$now; fi
    rej_seen=$rej
    if [ "$rej_reported" -lt 0 ]; then
      rej_reported=$rej                       # first look: the baseline, no alert
    elif [ "$rej" -gt "$rej_reported" ] && [ $((now - last_rej)) -ge 3600 ]; then
      tg "⚠️ scanner datafeed: $((rej - rej_reported)) new rejected_messages (total $rej) — the recorder dropped entries; check its log."
      rej_reported=$rej; last_rej=$now; log "rejected alert total=$rej"
    fi
  fi
  if [ "$lag" -ge 0 ]; then lag_txt="${lag} market-open min"; else lag_txt="no live bar yet"; fi
  datafeed_info="; datafeed: recorder lag ${lag_txt}, rejected ${rej}"
fi

# daily heartbeat (UTC day/hour by arithmetic: no GNU/BSD date differences)
day=$((now / 86400)); hour_utc=$(( (now % 86400) / 3600 ))
if [ "$day" != "$last_daily" ] && [ "$hour_utc" -ge "$DAILY_HEARTBEAT_HOUR_UTC" ]; then
  if [ "$gateway_ok" = 1 ]; then gw="up"; else gw="DOWN"; fi
  tg "💓 scanner daily: gateway ${gw}; scanner ${hs}, ${rc} restarts; disk ${disk}%; memory ${mem}% available, swap ${swap_mb} MB; last backup ${backup_info}${datafeed_info}."
  last_daily=$day; log "daily heartbeat"
fi

# health.json for the status board: the facts above plus two probes that run
# inside the containers, evaluated against thresholds.toml by tools/status
write_health() {
  local bs relogin_limit=0 stream_expected=0 backup_configured=0 backup_age=-1 n line
  local -a facts probe
  bs=$(val SCANNER_BAR_SOURCE); bs=${bs:-ibkr}
  if [ "$bs" = stream ] || [[ "$(val COMPOSE_PROFILES)" == *stream* ]]; then stream_expected=1; fi
  n=$(echo "$relogins" | wc -w | tr -d ' ')
  [ "$n" -ge "$MAX_RELOGINS" ] && relogin_limit=1
  if [ -n "$(val BACKUP_S3_URI)" ]; then
    backup_configured=1
    [ "${last_ok:-0}" -gt 0 ] && backup_age=$age_h
  fi
  facts=("now=$now" "market_open=$((1 - quiet))" "bar_source=$bs" "stream_expected=$stream_expected"
         "gateway_ok=$gateway_ok" "gateway_fails=$fails" "gateway_relogin_limit=$relogin_limit"
         "scanner_state=$(cstate scanner-scanner-1)" "scanner_health=$hs"
         "scanner_unhealthy_checks=$hfails" "scanner_restarts_5min=$rc_delta"
         "feed_state=$(cstate scanner-feed-1)" "feed_restarts_5min=$feed_delta"
         "redis_state=$(cstate scanner-redis-1)" "redis_fails=$rfails"
         "recorder_state=$(cstate scanner-recorder-1)" "recorder_lag_min=$lag"
         "rejected_total=$rej" "rejected_last_growth_ts=$rej_grow_ts"
         "backup_configured=$backup_configured" "backup_age_h=$backup_age"
         "disk_pct=$disk" "mem_avail_pct=$mem" "swap_mb=$swap_mb" "agents_deployed=0")
  if [ "$(cstate scanner-scanner-1)" = running ]; then
    probe=()
    while IFS= read -r line; do probe+=("$line"); done < <(timeout 60 docker compose exec -T scanner \
      python - < "$REPO/tools/status/probes/journal_stats.py" 2>/dev/null)
    [ "${#probe[@]}" -gt 0 ] || probe=("journal_ok=0" "journal_error=journal probe gave no output")
  else
    probe=("journal_ok=0" "journal_error=scanner container not running")
  fi
  facts+=("${probe[@]}")
  if [ "$stream_expected" = 1 ] && [ "$(cstate scanner-recorder-1)" = running ]; then
    probe=()
    while IFS= read -r line; do probe+=("$line"); done < <(timeout 60 docker compose exec -T recorder \
      python - < "$REPO/tools/status/probes/stream_stats.py" 2>/dev/null)
    [ "${#probe[@]}" -gt 0 ] && facts+=("${probe[@]}")
  fi
  (cd "$REPO" && "$STATUS_PY" -m tools.status.write --out "$HEALTH_JSON" "${facts[@]}") \
    || log "health.json not written"
}
write_health
save
