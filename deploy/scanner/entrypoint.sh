#!/usr/bin/env bash
# Wait for what the scanner needs, then exec it.
#
# SCANNER_BAR_SOURCE=stream: wait for Redis (the datafeed bus). Never probe the
# gateway: in stream mode the datafeed feed is the only gateway client, and the
# scanner itself waits for the datafeed store (wait_for_store). If Redis doesn't
# answer within REDIS_WAIT_TRIES x 5 s (default 10 min): one Telegram alert (at
# most hourly, via a marker file on the /data volume) and exit 1. Docker's
# restart policy then tries again. TELEGRAM_PREFIX (e.g. [DRYRUN]) starts the
# alert, as it does every scanner message.
#
# Otherwise (ibkr, the default): wait for IB Gateway's API to complete a
# handshake. The gateway container takes ~1-2 min to launch + log in; without
# this the scanner would crash-loop against a gateway that isn't up. Combined
# with the compose restart policy, a mid-session gateway restart is also ridden
# out. A plain TCP connect is not enough: the image starts its socat relay
# before IB Gateway, so the port accepts connections within seconds while the
# API behind it is still down. Only a real API handshake proves the gateway is
# logged in.
set -euo pipefail

# one Telegram message, at most hourly (marker mtime); never fails the caller
ALERT_PY="import os, time, urllib.parse, urllib.request
marker = os.environ.get('ENTRYPOINT_ALERT_MARKER', '/data/.redis-wait-alerted')
try:
    if time.time() - os.path.getmtime(marker) < 3600:
        raise SystemExit(0)
except OSError:
    pass
tok, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
if tok and chat:
    data = urllib.parse.urlencode({'chat_id': chat, 'text': os.environ['ALERT_TEXT']}).encode()
    urllib.request.urlopen('https://api.telegram.org/bot' + tok + '/sendMessage', data=data, timeout=15)
open(marker, 'w').close()"

if [ "${SCANNER_BAR_SOURCE:-ibkr}" = "stream" ]; then
  tries="${REDIS_WAIT_TRIES:-120}"
  echo "entrypoint: stream mode, waiting for Redis at ${REDIS_URL:-<unset>} (no gateway probe) ..."
  for _ in $(seq 1 "$tries"); do
    if python -c "import os, redis; redis.Redis.from_url(os.environ['REDIS_URL'], socket_timeout=5).ping()" >/dev/null 2>&1; then
      echo "entrypoint: Redis up"
      exec python /app/deterministic_scanner.py --live
    fi
    sleep 5
  done
  ALERT_TEXT="${TELEGRAM_PREFIX:+$TELEGRAM_PREFIX }⚠️ scanner: Redis not reachable for $((tries * 5 / 60)) min — stream mode cannot start (the scanner retries). Check: docker compose ps redis. To fall back, set SCANNER_BAR_SOURCE=ibkr."
  export ALERT_TEXT
  echo "entrypoint: Redis not reachable after $((tries * 5)) s; alerting and exiting 1" >&2
  python -c "$ALERT_PY" || echo "entrypoint: telegram alert failed" >&2
  exit 1
fi

HOST="${IB_HOST:-ibgateway}"
PORT="${IB_PORT:-4004}"

echo "entrypoint: waiting for IB Gateway API at ${HOST}:${PORT} ..."
for _ in $(seq 1 120); do
  if python -c "from ib_insync import IB; IB().connect('${HOST}', ${PORT}, clientId=999, timeout=5, readonly=True).disconnect()" >/dev/null 2>&1; then
    echo "entrypoint: gateway API up, giving it 10s to settle"; sleep 10
    break
  fi
  sleep 5
done

exec python /app/deterministic_scanner.py --live
