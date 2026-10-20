# Scanner stack: deploy runbook

How to deploy `feat/deploy-stack` to the scanner host, flip the bar source, roll
back, and restore. Background is in `docs/SCANNER_SWITCH_NOTES.md`, the build
plan in `docs/DEPLOY_PLAN.md`, and the choices made without the owner in
`DECISIONS.md`.

Commands run on the host, in `~/oparli/deploy/scanner` (the directory of
`docker-compose.yml`), as the user that owns the stack. Nothing here was run
against the host when this was written. Docker wasn't available, so §10 lists
what has to be checked by hand the first time.

## 0. Before you start

- **Merge order.** `feat/scanner-stream-source`, then `feat/deploy-stack` (it is
  cut from the stream branch, because `main` didn't contain it). Deploy from
  `main` after both are merged.
- **What the host runs now.** Check with `git -C ~/oparli status -sb`. In
  September it tracked `pipeline-fixes-and-cleanup`, not `main`. Note the
  branch and commit, so you can go back to them.
- **Memory** (3.8 GB host). The compose limits total 2.88 GB: ibgateway 1536m,
  scanner 512m, feed 256m, recorder 192m, redis 384m. Keep a **2 GB swap file**,
  so a Java spike in the gateway doesn't trigger the kernel OOM killer. Check
  with `swapon --show`; if it's empty:

  ```bash
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile \
    && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  sudo sysctl vm.swappiness=10 && echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
  ```

  After deploying, watch `docker stats --no-stream` for a day. A container near
  its limit gets OOM-killed and restarted (the watchdog reports crash loops);
  raise its `mem_limit` if so.
- **S3 bucket** for backups:
  - private, with "Block all public access" on;
  - a lifecycle rule for retention, e.g. expire `journal/` and `store/`
    objects after 90 days. `backup.sh` never deletes anything.
- **IAM instance role** on the host, with nothing else on it:

  ```json
  {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"],
     "Resource": "arn:aws:s3:::YOUR-BUCKET/oparli/scanner/*"},
    {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::YOUR-BUCKET",
     "Condition": {"StringLike": {"s3:prefix": "oparli/scanner/*"}}}]}
  ```

  No AWS keys in `.env` or anywhere on the host.
- **AWS CLI** on the host (`aws --version`), e.g. `sudo snap install aws-cli
  --classic`. Then check that `aws s3 ls s3://YOUR-BUCKET/` works with the
  role.
- **`.env` additions** (see `.env.example`). Leave the bar source at `ibkr` for
  now:

  ```
  SCANNER_BAR_SOURCE=ibkr
  FEED_IB_CLIENT_ID=31
  SCANNER_STREAM_CONSUMER=scanner-1
  BACKUP_S3_URI=s3://YOUR-BUCKET/oparli/scanner
  ```

## 1. Back up the journal first (old image)

The new image migrates the journal on first start, and the old image can't
write to a migrated journal (switch notes §4). So back it up while the old
scanner still runs. The old image has no `backup_sqlite.py`, so use Python's
backup API directly:

```bash
docker compose exec -T scanner python -c "import sqlite3; s = sqlite3.connect('/data/scanner_decisions.sqlite'); d = sqlite3.connect('/data/pre-deploy.sqlite'); s.backup(d); d.close(); print(sqlite3.connect('/data/pre-deploy.sqlite').execute('PRAGMA integrity_check').fetchone())"
docker compose cp scanner:/data/pre-deploy.sqlite ~/pre-deploy.sqlite
gzip -k ~/pre-deploy.sqlite
aws s3 cp --sse AES256 ~/pre-deploy.sqlite.gz s3://YOUR-BUCKET/oparli/scanner/journal/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ).sqlite.gz
```

Expect `('ok',)`. Keep `~/pre-deploy.sqlite` on the host as well.

## 2. Deploy in ibkr mode (behaviour unchanged)

```bash
cd ~/oparli && git fetch && git switch main && git pull --ff-only
cd deploy/scanner
docker compose config >/dev/null && echo "compose file ok"      # validates the YAML and .env
docker compose build
docker compose up -d                       # no profile: ibgateway + scanner, as today
docker compose logs --tail 50 scanner
```

Check:
- the log says `LIVE (Telegram): scanning XAUUSD, N levels active, journal
  epoch 1`;
- the journal migrated:
  `docker compose exec -T scanner python -c "import sqlite3; c = sqlite3.connect('/data/scanner_decisions.sqlite'); print(c.execute('PRAGMA user_version').fetchone(), c.execute('SELECT * FROM source_switches').fetchall())"`
  prints `(4,)` and one row `(…, None, 'ibkr', 1)` (journal version 4: Telegram update ids
  and TAKE outcomes, `backend/README.md`);
- after ~1–2 min, `docker inspect -f '{{.State.Health.Status}}'
  scanner-scanner-1` says `healthy` (the heartbeat).

Watch one market session: alerts and replies should look as before.

## 3. Install the crons

`crontab -e`:

```
*/5 * * * * $HOME/oparli/deploy/scanner/watchdog.sh >> $HOME/scanner-watchdog.log 2>&1
30 22 * * * $HOME/oparli/deploy/scanner/backup.sh   >> $HOME/scanner-backup.log 2>&1
```

The watchdog line is today's. Its state file gains new keys; the old file still
loads.

The backup also archives the datafeed store's bars as daily parquet, under
`$BACKUP_S3_URI/archive/bars/date=YYYY-MM-DD/XAUUSD.parquet` (stream profile
only):
- It covers the last 7 completed UTC days and uploads only the days that are
  missing, so a missed night is caught up the next night.
- A day is exported once. History bars that reach the store for that day later
  stay in the `store/` backups only.
- The archive holds data from 2026-10-01 onward, which `research/SEALING.md`
  seals: no study may read it before the final strategy test.
- Each successful run writes `last_ok=<epoch>` to `~/.scanner-backup`. The
  watchdog alerts when that is over 26 h old.

Run the backup once by hand, then check S3:

```bash
./backup.sh && aws s3 ls --recursive s3://YOUR-BUCKET/oparli/scanner/
./restore.sh journal s3://YOUR-BUCKET/oparli/scanner/journal/<that file> # dry run: download + verify only
```

## 4. Stage the datafeed (scanner still ibkr)

Add `COMPOSE_PROFILES=stream` to `.env`, then:

```bash
docker compose up -d                       # adds redis, feed, recorder
docker compose ps                          # redis/feed/recorder become healthy
docker compose logs --tail 30 feed recorder
```

- The feed logs `ibkr: N history bars published, streaming XAUUSD`. The recorder
  logs a `health {...}` line every 60 s: `last_live_bar_ts` advances during
  market hours, and `lag_s` stays small.
- While staging, the gateway has two bar clients: the ibkr scanner (11/21) and
  the feed (31). That's expected.
- Optional dry-run comparison. It needs its **own group and its own journal**;
  never share the production group or journal:

  ```bash
  docker compose run --rm --no-deps \
    -e SCANNER_BAR_SOURCE=stream -e SCANNER_STREAM_GROUP=scanner-dry \
    -e SCANNER_STREAM_CONSUMER=scanner-dry -e SCANNER_DB=/data/dryrun.sqlite \
    -e SCANNER_HEARTBEAT= --entrypoint python scanner /app/deterministic_scanner.py --dry-live
  ```

  It prints alerts to the terminal, sends no Telegram, and touches nothing in
  production. Compare its alerts with the live ones over a session. Afterwards,
  delete its group: `docker compose exec redis redis-cli XGROUP DESTROY
  bars.xauusd.1m scanner-dry`.
- The store needs about two weeks of bars before a Thursday–Sunday warm-up is
  ≥ 80 % covered (switch notes 5c). Until then the stream scanner may wait for
  the store at start. Flip on a Monday–Wednesday, or after two weeks of staging.

## 5. Flip to stream

In `.env`, set `SCANNER_BAR_SOURCE=stream` (keep `COMPOSE_PROFILES=stream`), then:

```bash
docker compose up -d scanner
docker compose logs -f scanner
```

Expect, in order:
1. `entrypoint: stream mode, waiting for Redis ... (no gateway probe)`, then
   `Redis up`. If Redis doesn't answer within 10 min, the entrypoint sends one
   Telegram alert (at most hourly) and exits 1, and Docker retries;
2. `warm-up via store: ...` (if instead `warm-up: ...; scanner not started,
   retrying in 30s`, the reason is on the line);
3. `LIVE (Telegram): scanning XAUUSD from stream bars.xauusd.1m (group scanner,
   consumer scanner-1), ..., journal epoch 2`;
4. on the first start of the group, one-off `stream: batch of 500: ...
   before_warmup / duplicate` summary lines while it skips the backlog;
5. afterwards no `out_of_order` or `conflict` lines.

`source_switches` now has `(…, 'ibkr', 'stream', 2)`. The scanner no longer
connects to the gateway; only the feed (31) and the watchdog probe (996) do.

Optional test, in market hours: `docker compose stop feed`. Within 10
market-open minutes Telegram gets "no new XAUUSD bar from the datafeed stream".
Then `docker compose start feed`, and the "resumed" message follows.

## 6. Roll back to ibkr

```bash
sed -i 's/^SCANNER_BAR_SOURCE=.*/SCANNER_BAR_SOURCE=ibkr/' .env
docker compose up -d scanner
```

- That's the whole rollback. The scanner reconnects with 11/21 and writes a
  **new** epoch (3), logged as `(…, 'stream', 'ibkr', 3)`.
- The datafeed can keep running. To stop it, remove `COMPOSE_PROFILES=stream`
  and run `docker compose stop redis feed recorder`.
- **Never** roll back to an image from before the epoch migration (switch notes
  §4). To go back to the pre-deploy code anyway, first restore
  `~/pre-deploy.sqlite` (§7); journal rows written since are then lost.

## 7. Restore

```bash
aws s3 ls s3://YOUR-BUCKET/oparli/scanner/journal/          # pick a file
./restore.sh journal s3://YOUR-BUCKET/oparli/scanner/journal/<file>.sqlite.gz          # verify only
./restore.sh journal s3://YOUR-BUCKET/oparli/scanner/journal/<file>.sqlite.gz --yes    # restore
./restore.sh store   s3://YOUR-BUCKET/oparli/scanner/store/<file>.sqlite.gz --yes      # datafeed store
```

`--yes`:
1. stops the writer (journal: scanner; store: recorder and scanner);
2. keeps the current file as `<file>.pre-restore-<UTC ts>` in the same volume;
3. drops its `-wal` / `-shm`;
4. restores atomically;
5. starts the services again. It also restarts them if the restore step fails;
   the target is then unchanged.

To restore the pre-deploy journal from §1, use its S3 copy the same way.

## 8. The "10 D" check (open question, switch notes 5c)

Against a **paper** gateway only: a local TWS paper session on 7497, or a
local IB Gateway paper on 4002. Run it on a Thursday or Friday:

```bash
.venv/bin/python tools/ibkr_duration_check.py --port 7497
```

- The script refuses non-paper ports and non-`DU` accounts (exit 3) before it
  requests anything.
- It prints the first and last bar, the sessions, and a verdict (calendar days,
  trading days, or inconclusive). Record the result in switch notes 5c.
- If the verdict is "calendar days", the ibkr path builds PWH/PWL from part of
  a week from Thursday to Sunday. That's worth a follow-up.

## 9. Monitoring

- **Telegram, from the scanner**: the staleness message after 10 market-open
  minutes without bars, and the store-wait message.
- **Telegram, from the entrypoint** (stream mode): Redis not reachable for
  10 min.
- **Telegram, from the watchdog** (every 5 min, one alert per kind per hour,
  each with a recovery message):
  - gateway down;
  - scanner crash loop;
  - scanner hung (heartbeat);
  - feed or recorder crash loop;
  - Redis down;
  - disk ≥ 85 %;
  - memory available < 10 %;
  - last successful backup > 26 h;
  - recorder lag ≥ 15 market-open min;
  - new `rejected_messages` (as a delta).

  Thresholds are constants at the top of `watchdog.sh`.
- **Daily heartbeat** from the watchdog, at or after 07:00 UTC, every day: a
  one-line summary of all of the above. **No heartbeat by about 08:00 UTC
  means the watchdog or its cron is broken.**
- **Telegram, from backup**: a message if the nightly backup or archive fails.
- **Logs**: `~/scanner-watchdog.log`, `~/scanner-backup.log`,
  `docker compose logs`.
- **Journal summary** (read-only): alerts, TAKE, SKIP, MISSED and open
  outcomes for a UTC day, per epoch:
  `docker compose exec -T scanner python /app/journal_summary.py [--date YYYY-MM-DD] [--json]`.

## 10. Check by hand the first time

Nothing below could be run where this was written (no Docker, no host access):

- [ ] `docker compose config` accepts the file, with and without
      `COMPOSE_PROFILES=stream`.
- [ ] `docker compose build` succeeds. On the host, the pinned wheels
      (`pyarrow==25.0.0` and the others) install for Python 3.12 on x86_64.
- [ ] Scanner health goes `starting` → `healthy`. Freezing the process, e.g.
      `docker compose pause scanner` for > 5 min, makes it `unhealthy` and
      triggers the watchdog's heartbeat alert after two runs; `unpause` sends
      the recovery message.
- [ ] Stream profile: redis → feed → recorder become healthy in that order. The
      recorder writes `/datafeed/datafeed.sqlite`.
- [ ] The scanner can read the store through the read-write `datafeed_store`
      mount (`warm-up via store`).
- [ ] `backup.sh` uploads both files. `restore.sh ... ` (dry run) verifies them.
      A `--yes` restore on a copy is tested on purpose once.
- [ ] The IAM role allows exactly what's listed in §0.
- [ ] The watchdog cron's first runs log nothing unexpected
      (`~/scanner-watchdog.log`). The first daily heartbeat arrives after
      07:00 UTC, and its disk, memory and backup numbers look right.
- [ ] `docker stats --no-stream` after a day: every container well under its
      `mem_limit`; the swap file is active (`swapon --show`).
- [ ] The `archive/bars/date=.../XAUUSD.parquet` objects appear in S3 the
      morning after the stream profile's first full day.
- [ ] Journal, after the first replies:
      - a TAKE reply's `decision_ts_utc` is the message's time (as shown in
        Telegram), not the time it was processed;
      - the confirmation says `outcome not scorable: …` for a TAKE without
        sl/tp;
      - `journal_summary.py` lists the TAKE as open, then as tp/sl/timeout once
        it is scored;
      - a scanner restart re-confirms nothing.
