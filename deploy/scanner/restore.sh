#!/usr/bin/env bash
# Restore the scanner journal or the datafeed store from an S3 backup made by
# backup.sh. Run by hand on the host, next to docker-compose.yml.
#
#   ./restore.sh journal s3://BUCKET/PREFIX/journal/<file>.sqlite.gz         # download + verify only
#   ./restore.sh journal s3://BUCKET/PREFIX/journal/<file>.sqlite.gz --yes   # ... and restore
#   ./restore.sh store   s3://BUCKET/PREFIX/store/<file>.sqlite.gz --yes
#
# With --yes it:
#   1. stops the writer (journal: scanner; store: scanner and recorder);
#   2. restores in a one-shot container on the same volumes
#      (backup_sqlite.py restore). That keeps the current file as
#      <file>.pre-restore-<UTC ts>, removes its -wal/-shm, and replaces it
#      atomically;
#   3. starts the stopped services again.
# Without --yes nothing is stopped or changed.
set -euo pipefail
cd "$(dirname "$0")"

usage() { echo "usage: $0 journal|store s3://bucket/key.sqlite.gz [--yes]" >&2; exit 2; }
[ $# -ge 2 ] || usage
kind=$1; uri=$2; yes=${3:-}
case "$kind" in
  journal) target=/data/scanner_decisions.sqlite; table=alerts; services="scanner" ;;
  store)   target=/datafeed/datafeed.sqlite;      table=bars;   services="recorder scanner" ;;
  *) usage ;;
esac
case "$uri" in s3://*.sqlite.gz) ;; *) usage ;; esac
[ -z "$yes" ] || [ "$yes" = "--yes" ] || usage

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
aws s3 cp --only-show-errors "$uri" "$work/restore.sqlite.gz"
gunzip "$work/restore.sqlite.gz"
run() { docker compose run --rm --no-deps -T -v "$work:/restore:ro" --entrypoint python scanner /app/backup_sqlite.py "$@"; }
run check /restore/restore.sqlite --expect-table "$table"
echo "verified $uri"

if [ "$yes" != "--yes" ]; then
  echo "dry run: nothing changed. Rerun with --yes to restore $kind ($target)."
  exit 0
fi
# shellcheck disable=SC2086  # $services is a word list on purpose
docker compose stop $services
if ! run restore /restore/restore.sqlite "$target" --expect-table "$table"; then
  # the target is untouched when restore fails: bring the services back as they were
  # shellcheck disable=SC2086
  docker compose up -d $services
  echo "restore FAILED; $target unchanged; started again: $services" >&2
  exit 1
fi
# shellcheck disable=SC2086
docker compose up -d $services
echo "restored $kind from $uri; started: $services"
