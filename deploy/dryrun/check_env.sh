#!/usr/bin/env bash
# Pre-flight for the dry-run stack: refuses (exit 1) anything that could
# disturb production. Run it before every `docker compose up`. Read-only: it
# changes nothing and prints no secret (tokens are compared, never shown).
#
#   ./check_env.sh            (from deploy/dryrun, with .env filled in)
#
# Checks:
#   - .env exists and is not readable by others (chmod 600);
#   - a Telegram bot token and chat id are set, and the token is NOT
#     production's (PROD_ENV, default ~/oparli/deploy/scanner/.env): a second
#     getUpdates poller on one bot takes production's TAKE/SKIP replies;
#   - the feed's client id is not one the stack uses (11 21 31 41 43 996 999);
#   - production's network exists and production's gateway container runs
#     (the feed reuses it; there is no gateway in the dry run);
#   - BACKUP_S3_URI, if set, is under a dryrun/ prefix;
#   - INSTRUMENT is not XAUUSD (a gold dry run would duplicate production's alerts).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

PROD_ENV=${PROD_ENV:-$HOME/oparli/deploy/scanner/.env}
PROD_GATEWAY=${PROD_GATEWAY:-scanner-ibgateway-1}
TAKEN="11 21 31 41 43 996 999"
problems=0
val() { grep -E "^$1=" "$2" 2>/dev/null | tail -1 | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z0-9_]*=//' | tr -d "\"'"; }
bad() { echo "check_env: NO - $*"; problems=$((problems + 1)); }
good() { echo "check_env: ok - $*"; }

if [ ! -f .env ]; then
  echo "check_env: NO - no .env here (cp .env.example .env && chmod 600 .env, then fill it in)"
  exit 1
fi
perm=$(stat -c %a .env 2>/dev/null || stat -f %Lp .env)
case "$perm" in 600|400) good ".env is private ($perm)" ;; *) bad ".env is mode $perm; run chmod 600 .env" ;; esac

tok=$(val TELEGRAM_BOT_TOKEN .env); chat=$(val TELEGRAM_CHAT_ID .env)
if [ -z "$tok" ] || [ -z "$chat" ]; then
  bad "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (a separate dry-run bot)"
elif [ -f "$PROD_ENV" ] && [ "$tok" = "$(val TELEGRAM_BOT_TOKEN "$PROD_ENV")" ]; then
  bad "the Telegram bot token is production's; create a separate bot (@BotFather) for the dry run"
elif [ ! -f "$PROD_ENV" ]; then
  bad "production's .env not found at $PROD_ENV (set PROD_ENV) - can't compare the bot tokens"
else
  good "Telegram: a bot other than production's"
fi

cid=$(val DRYRUN_FEED_IB_CLIENT_ID .env); cid=${cid:-44}
if ! [[ "$cid" =~ ^[0-9]+$ ]]; then
  bad "DRYRUN_FEED_IB_CLIENT_ID=$cid is not a number"
elif [[ " $TAKEN " == *" $cid "* ]]; then
  bad "client id $cid is taken (in use: $TAKEN)"
else
  good "feed client id $cid"
fi

net=$(val PROD_NETWORK .env); net=${net:-scanner_scannernet}
if docker network inspect "$net" >/dev/null 2>&1; then good "production network $net"; else bad "production network $net not found"; fi
state=$(docker inspect -f '{{.State.Status}}' "$PROD_GATEWAY" 2>/dev/null || echo missing)
if [ "$state" = running ]; then good "production gateway $PROD_GATEWAY is running"; else bad "production gateway $PROD_GATEWAY is $state"; fi

uri=$(val BACKUP_S3_URI .env)
if [ -n "$uri" ] && [[ "$uri" != s3://*/dryrun* ]]; then bad "BACKUP_S3_URI=$uri must be under a dryrun/ prefix"; fi

inst=$(val INSTRUMENT .env); inst=${inst:-BTCUSD}
if [ "$inst" = XAUUSD ]; then bad "INSTRUMENT=XAUUSD would duplicate production's gold alerts"; else good "instrument $inst"; fi

if [ "$problems" -gt 0 ]; then
  echo "check_env: $problems problem(s); not safe to start the dry run"
  exit 1
fi
echo "check_env: all checks passed"
