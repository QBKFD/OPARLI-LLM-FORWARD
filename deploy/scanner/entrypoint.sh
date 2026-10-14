#!/usr/bin/env bash
# Wait for what the scanner needs, then exec it.
#
# SCANNER_BAR_SOURCE=stream: wait for Redis (the datafeed bus). Never probe the
# gateway: in stream mode the datafeed feed is the only gateway client, and the
# scanner itself waits for the datafeed store (wait_for_store).
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

if [ "${SCANNER_BAR_SOURCE:-ibkr}" = "stream" ]; then
  echo "entrypoint: stream mode, waiting for Redis at ${REDIS_URL:-<unset>} (no gateway probe) ..."
  for _ in $(seq 1 120); do
    if python -c "import os, redis; redis.Redis.from_url(os.environ['REDIS_URL'], socket_timeout=5).ping()" >/dev/null 2>&1; then
      echo "entrypoint: Redis up"
      break
    fi
    sleep 5
  done
  exec python /app/deterministic_scanner.py --live
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
