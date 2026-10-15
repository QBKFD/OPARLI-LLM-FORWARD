# Deployment PR: plan

Branch `feat/deploy-stack`. Written 2026-10-15, unattended, before building.
Decisions taken without the owner are in `DECISIONS.md` at the repo root.

**Base.** `main` doesn't contain `45c674c` (the stream-mode work), so this branch
is cut from `feat/scanner-stream-source` @ 45c674c, not from `main`. Merge
`feat/scanner-stream-source` first, or review this PR as including it.

**Scope.** Everything is in the repo and tested locally. Nothing touches EC2.
Docker isn't installed on this machine, so no image is built and no compose
stack is run here. Those checks are listed for the owner in
`docs/DEPLOY_RUNBOOK.md`.

## 1. Compose stack (`deploy/scanner/docker-compose.yml`)

- **Default (no profile)**: exactly today's two services, `ibgateway` and
  `scanner`, with `SCANNER_BAR_SOURCE` defaulting to `ibkr`. A plain
  `docker compose up -d` after this PR runs the same live path as today, plus
  the journal migration (see the switch notes §4).
- **Profile `stream`** (`COMPOSE_PROFILES=stream` in `.env`, or `--profile
  stream`):
  - `redis` (7.4.2, AOF `everysec` on a volume, `maxmemory 256mb`,
    `noeviction`);
  - `feed` (`python -m datafeed.ingest`, `DATAFEED_SOURCE=ibkr`, its own
    clientId, default 31);
  - `recorder` (`python -m datafeed.recorder`, store on the `datafeed_store`
    volume).
- **Start order**: redis → feed → recorder → scanner, via `depends_on`
  conditions on healthchecks. The scanner's dependency on `recorder` is
  `required: false`, so the default profile still starts without it.
- **One image** for scanner, feed and recorder: the scanner image gains
  `datafeed/` and `redis==5.2.1`. Feed and recorder override the entrypoint. Its
  unpinned `pyarrow` and `tzdata` get pinned to the lock's versions.
- **Volumes**: `scanner_data` (journal, heartbeat, backup staging),
  `datafeed_store` (mounted read-write in the scanner at `/datafeed`; the
  scanner opens it `mode=ro`), `redis_data`.

## 2. Entrypoint (`deploy/scanner/entrypoint.sh`)

- `SCANNER_BAR_SOURCE=stream`: wait for Redis (`PING` via the `redis` package),
  never probe the gateway, then `exec` the scanner.
- Otherwise: today's gateway handshake wait (clientId 999), unchanged.

## 3. Heartbeat and watchdog

- **Scanner heartbeat**: optional `SCANNER_HEARTBEAT` file path. The scanner
  writes the current UTC time to it on every main-loop pass (both bar sources)
  and while it waits for the store. Unset means no file, which is today's
  behaviour.
- **Compose healthcheck** on `scanner`: the heartbeat is younger than 300 s.
- **`watchdog.sh`** keeps everything it does today, and adds:
  - the scanner `unhealthy` for 2 checks in a row (a hung loop): one Telegram
    alert per hour, and a recovery message;
  - in the stream profile:
    - `feed` / `recorder` restarting ≥ 3 times in 5 min: alert;
    - `redis` not answering `PING` for 2 checks: alert.
  - Same quiet window as today (Fri 17:00 → Sun 17:00 New York).
  - Test hooks: `WATCHDOG_NY_DOW` and `WATCHDOG_NY_HOUR` override the clock.

## 4. Backups to S3 and restore

- `deploy/scanner/backup_sqlite.py`, inside the image:
  - `snapshot SRC DST`: SQLite online backup API (consistent under WAL with a
    live writer), `PRAGMA integrity_check`, refuses to overwrite.
  - `restore SRC DST`: integrity-checks SRC, keeps the current DST as
    `DST.pre-restore-<ts>`, removes `-wal` / `-shm`, replaces atomically.
- `deploy/scanner/backup.sh` (host cron, daily). It:
  1. snapshots the journal (and the store, if the stream profile is up) inside
     the scanner container;
  2. copies it out, gzips it, and uploads it with
     `aws s3 cp --sse AES256` to `$BACKUP_S3_URI/<kind>/<host>-<ts>.sqlite.gz`;
  3. removes the local copies.

  It never deletes from S3; retention is an S3 lifecycle rule (runbook). On
  failure it sends a Telegram alert and exits non-zero.
- `deploy/scanner/restore.sh <journal|store> <s3-uri> --yes`: downloads, stops
  the writer (`scanner`, or `recorder` and `scanner`), restores through
  `backup_sqlite.py restore` in a one-shot container on the same volume, then
  starts them again. Without `--yes` it only downloads and verifies.
- AWS credentials: an instance role (runbook). No keys in `.env`.

## 5. "10 D" check, paper only (`tools/ibkr_duration_check.py`)

- Requests `10 D` / `1 min` / `MIDPOINT` for XAUUSD CMDTY once and reports:
  - first and last bar;
  - calendar span;
  - the number of trading sessions (17:00 New York rollover);
  - a verdict: calendar days, trading days, or inconclusive.
- **Paper-only guards**, checked before any data request:
  - the port must be a paper port (4002, 4004, 7497);
  - every managed account must start with `DU`;
  - `readonly=True`, its own clientId (default 41).
- The analysis is a pure function, tested on synthetic bars. It is not run
  against any gateway tonight.

## 6. Runbook

`docs/DEPLOY_RUNBOOK.md`:
- prerequisites (IAM role, S3 bucket and lifecycle, `.env` keys);
- the backup first;
- deploy in ibkr mode;
- staging the stream profile;
- the flip, verification, rollback, restore;
- cron lines for the watchdog and backup;
- the "10 D" check;
- what to check by hand.

## 7. Tests (local, no Docker)

`tests/deploy/`, added to `testpaths`:
- **compose structure** (PyYAML): profiles, start order, healthchecks, Redis
  flags, no inline secrets, clientIds unique;
- **`entrypoint.sh`** with a stub `python` (which command it waits for and
  `exec`s, per mode);
- **`watchdog.sh`** with stub `docker` / `curl` / `timeout`;
- **`backup_sqlite.py`** (snapshot under a live WAL writer, integrity, restore);
- **`backup.sh` / `restore.sh`** with stub `docker` / `aws`;
- **the scanner heartbeat**;
- **the "10 D" analysis and its paper guards**.

Shell scripts are linted with `shellcheck` if it can be installed locally
(official release binary, checksum-verified, in the scratchpad).

Done when: full pytest with Redis is green, selftest is green, there's a secret
scan, and the branch is pushed.
