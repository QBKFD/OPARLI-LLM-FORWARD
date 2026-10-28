#!/usr/bin/env bash
# The multi-market download on the EC2 host, one instrument after the other,
# in the owner's order (docs/MULTI_MARKET_DATA.md). Run it inside tmux. It is
# resumable: rerun the same command after any stop, and finished chunks are
# skipped (backfill_ibkr.py --instrument checkpoints every week to day files).
#
#   tools/data/multi_market_run.sh                 # all seven
#   tools/data/multi_market_run.sh EURUSD USDJPY   # only these
#
# Output: $MM_OUT (default ~/multi_market)/<SYM>/YYYY-MM-DD.jsonl (IBKR data:
# never cat, head or share them), plus run.log (the tool's stderr: counts and
# errors, no prices) and status.log (one line per run and exit code).
# Exit codes of the tool: 0 done; 3 a sealed request or bar (stop); 2 bad
# arguments (stop); 6 no gateway for 3 h; anything else: retried after 30 min,
# up to 8 times per instrument.
set -u
OUT=${MM_OUT:-$HOME/multi_market}
SCRIPT=${MM_SCRIPT:-/tmp/backfill_ibkr.py}
HOST=${MM_IB_HOST:-ibgateway}
PORT=${MM_IB_PORT:-4004}
COMPOSE_DIR=${MM_COMPOSE_DIR:-$HOME/oparli/deploy/scanner}
if [ "$#" -eq 0 ]; then set -- XAGUSD EURUSD USDJPY GBPUSD USDCAD USDSEK USDCHF; fi
mkdir -p "$OUT" && chmod 777 "$OUT"
cd "$COMPOSE_DIR" || exit 2
for sym in "$@"; do
  tries=0
  while [ ! -f "$OUT/$sym/_complete.json" ]; do
    echo "$(date -u +%FT%TZ) $sym start (try $((tries + 1)))" >> "$OUT/status.log"
    docker compose run --rm -T --no-deps -v "$OUT:/out" --entrypoint python scanner - \
      --instrument "$sym" --out-dir /out --host "$HOST" --port "$PORT" < "$SCRIPT" 2>> "$OUT/run.log"
    rc=$?
    echo "$(date -u +%FT%TZ) $sym exit $rc" >> "$OUT/status.log"
    case $rc in
      0) break ;;
      2|3) echo "$sym: exit $rc, stopping (see $OUT/run.log)"; exit "$rc" ;;
      *) tries=$((tries + 1))
         if [ "$tries" -ge 8 ]; then echo "$sym: exit $rc after $tries tries, stopping"; exit "$rc"; fi
         sleep 1800 ;;
    esac
  done
done
echo "$(date -u +%FT%TZ) all done: $*" >> "$OUT/status.log"
