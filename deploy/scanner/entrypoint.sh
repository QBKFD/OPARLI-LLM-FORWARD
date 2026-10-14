#!/usr/bin/env bash
# Wait for IB Gateway's API to complete a handshake before starting the
# scanner. The gateway container takes ~1-2 min to launch + log in; without this
# the scanner would crash-loop against a gateway that isn't up. Combined with the
# compose restart policy, a mid-session gateway restart is also ridden out.
#
# A plain TCP connect is not enough: the image starts its socat relay before IB
# Gateway, so the port accepts connections within seconds while the API behind
# it is still down. Only a real API handshake proves the gateway is logged in.
set -euo pipefail

HOST="${IB_HOST:-ibgateway}"
PORT="${IB_PORT:-4004}"

echo "entrypoint: waiting for IB Gateway API at ${HOST}:${PORT} ..."
for i in $(seq 1 120); do
  if python -c "from ib_insync import IB; IB().connect('${HOST}', ${PORT}, clientId=999, timeout=5, readonly=True).disconnect()" >/dev/null 2>&1; then
    echo "entrypoint: gateway API up, giving it 10s to settle"; sleep 10
    break
  fi
  sleep 5
done

exec python /app/deterministic_scanner.py --live
