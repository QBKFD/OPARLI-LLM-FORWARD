#!/usr/bin/env bash
# Back up the DRY-RUN stack with production's backup.sh, pinned to the dry run:
# its compose project, an s3://.../dryrun... prefix, and its own state file, so a
# dry-run backup can never look like production's (watchdog.sh reads
# ~/.scanner-backup). Optional; run by hand or from cron (README.md).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd) || exit 1
val() { grep -E "^$1=" "$here/.env" 2>/dev/null | tail -1 | sed -E 's/[[:space:]]+#.*$//; s/\r$//; s/^[A-Z0-9_]*=//' | tr -d "\"'"; }
uri=$(val BACKUP_S3_URI)
case "$uri" in
  s3://*/dryrun*) ;;
  *) echo "dryrun backup: BACKUP_S3_URI in $here/.env must be under a dryrun/ prefix (got '${uri:-unset}')" >&2; exit 1 ;;
esac
export COMPOSE_FILE="$here/docker-compose.yml" COMPOSE_PROJECT_NAME=dryrun
export BACKUP_S3_URI="$uri" BACKUP_STATE="${DRYRUN_BACKUP_STATE:-$HOME/.dryrun-backup}"
exec "$here/../scanner/backup.sh"
