# Scanner stack: deploy runbook

How to deploy `main` to the scanner host, flip the bar source, roll back, and
restore. **The first deploy of `main` onto a host still running
`pipeline-fixes-and-cleanup` @ c62810d has its own step-by-step: §11.** Use
it instead of §0–§5 for that jump. Background is in
`docs/SCANNER_SWITCH_NOTES.md`, the build plan in `docs/history/DEPLOY_PLAN.md`, and
the choices made without the owner in `docs/decisions/`.

Commands run on the host, in `~/oparli/deploy/scanner` (the directory of
`docker-compose.yml`), as the user that owns the stack. Nothing here was run
against the host when this was written. Docker wasn't available, so §10 lists
what has to be checked by hand the first time.

`<bucket>` stands for the backup bucket's name everywhere below.

## 0. Before you start

- **What the host runs now.** Check with `git -C ~/oparli status -sb` and
  `git -C ~/oparli log --oneline -1`. On 2026-10-21 it ran
  `pipeline-fixes-and-cleanup` @ c62810d, not `main`: for that jump, use §11.
  Note the branch and commit, so you can go back to them.
- **Memory** (3.8 GB host). The compose limits total 2.88 GB: ibgateway 1536m,
  scanner 512m, feed 256m, recorder 192m, redis 384m.
  - **Measure first:** `docker stats --no-stream`. The code before `main` set
    no `mem_limit`, so note what the gateway uses now. If it is near 1536m,
    raise its limit in `docker-compose.yml` before the gateway is recreated:
    an OOM-killed gateway means a re-login, and possibly 2FA.
  - Keep a **2 GB swap file**, so a Java spike in the gateway doesn't trigger
    the kernel OOM killer. Check with `swapon --show`; if it's empty:

    ```bash
    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile \
      && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    sudo sysctl vm.swappiness=10 && echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
    ```

  - After deploying, watch `docker stats --no-stream` for a day. A container
    near its limit gets OOM-killed and restarted (the watchdog reports crash
    loops); raise its `mem_limit` if so.
- **S3 bucket** for backups, private, with "Block all public access" on.
  Everything goes under `s3://<bucket>/oparli/`:

  | Prefix | Written by | Lifecycle |
  |---|---|---|
  | `oparli/journal/` | `backup.sh`, nightly | expire after **30 days**; noncurrent versions after **7 days** |
  | `oparli/store/` | `backup.sh`, nightly (stream profile) | expire after **30 days**; noncurrent versions after **7 days** |
  | `oparli/archive/bars/` | `backup.sh`, once per completed day | **never expires** (the sealed 2026-10+ data, `research/SEALING.md`) |
  | `oparli/pre-deploy/` | you, by hand before a deploy (§1, §11) | **never expires** |

  `backup.sh` never deletes anything; the lifecycle is the only retention.
  Set it from a machine with admin AWS credentials (the host's role can't).
  The noncurrent rule only matters if versioning is on.

  ```bash
  cat > lifecycle.json <<'JSON'
  {"Rules": [
    {"ID": "oparli-journal-30d", "Filter": {"Prefix": "oparli/journal/"}, "Status": "Enabled",
     "Expiration": {"Days": 30}, "NoncurrentVersionExpiration": {"NoncurrentDays": 7}},
    {"ID": "oparli-store-30d", "Filter": {"Prefix": "oparli/store/"}, "Status": "Enabled",
     "Expiration": {"Days": 30}, "NoncurrentVersionExpiration": {"NoncurrentDays": 7}}]}
  JSON
  aws s3api put-bucket-lifecycle-configuration --bucket <bucket> --lifecycle-configuration file://lifecycle.json
  ```

  `put-bucket-lifecycle-configuration` **replaces** every rule on the bucket.
  If the bucket has other rules, merge them into the file first. Then run the
  check in §11 step 0 (`lifecycle`): it prints the rules, and stops if any rule
  would expire `archive/` or `pre-deploy/`.
- **IAM instance role** on the host, with nothing else on it:

  ```json
  {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"],
     "Resource": "arn:aws:s3:::<bucket>/oparli/*"},
    {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::<bucket>",
     "Condition": {"StringLike": {"s3:prefix": "oparli/*"}}}]}
  ```

  No AWS keys in `.env` or anywhere on the host.
- **AWS CLI** on the host (`aws --version`), e.g. `sudo snap install aws-cli
  --classic`. Then check that `aws s3 ls s3://<bucket>/oparli/` works with
  the role.
- **Host `python3`.** The watchdog writes `~/scanner-health.json` with the
  host's `python3`, and it needs >= 3.11 (`tomllib`). See §11 step 0 for what
  an older one breaks.
- **`.env` additions** (see `deploy/scanner/.env.example`). Leave the bar source at `ibkr`:

  ```
  SCANNER_BAR_SOURCE=ibkr
  FEED_IB_CLIENT_ID=31
  SCANNER_STREAM_CONSUMER=scanner-1
  ```

  Add `BACKUP_S3_URI=s3://<bucket>/oparli` **in §3, just before the first
  `backup.sh`**, not now. Once `main` is checked out, the watchdog cron runs
  `main`'s `watchdog.sh`. With `BACKUP_S3_URI` set and no backup made yet, it
  alerts "last successful backup never" every hour.

## 1. Back up the journal first (old image)

The new image migrates the journal on first start, and the old image can't
write to a migrated journal (switch notes §4). So back it up while the old
scanner still runs. The old image has no `backup_sqlite.py`, so use Python's
backup API directly:

```bash
docker compose exec -T scanner python -c "import sqlite3; s = sqlite3.connect('/data/scanner_decisions.sqlite'); d = sqlite3.connect('/data/pre-deploy.sqlite'); s.backup(d); d.close(); print(sqlite3.connect('/data/pre-deploy.sqlite').execute('PRAGMA integrity_check').fetchone())"
docker compose cp scanner:/data/pre-deploy.sqlite ~/pre-deploy.sqlite
gzip -kf ~/pre-deploy.sqlite
aws s3 cp --sse AES256 ~/pre-deploy.sqlite.gz s3://<bucket>/oparli/pre-deploy/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ).sqlite.gz
aws s3 ls s3://<bucket>/oparli/pre-deploy/
```

- Expect `('ok',)`. Keep `~/pre-deploy.sqlite` on the host as well.
- **Rows written after this copy aren't in it.** The old scanner keeps running
  until §2 replaces it, and it can still journal replies and run the MISSED
  and counterfactual sweeps. Keep the gap to §2 short, or repeat this step
  right before §2.

Then **tag the old images**, so a full rollback (§6) has them. `:stable` on
the gateway moves, and a prune could delete the old scanner image:

```bash
OLD=$(git -C ~/oparli rev-parse --short HEAD)     # the commit the host runs now, e.g. c62810d
docker compose images                              # the old scanner image, e.g. scanner-scanner:latest
docker image tag scanner-scanner:latest scanner-scanner:rollback-$OLD
docker image tag ghcr.io/gnzsnz/ib-gateway:stable ib-gateway:rollback-$OLD
docker image ls | grep "rollback-$OLD"
```

`main`'s compose names its own image `oparli-scanner:local`, so building it
never overwrites `scanner-scanner`.

## 2. Deploy in ibkr mode (behaviour unchanged)

```bash
cd ~/oparli && git fetch && git switch main && git pull --ff-only
cd deploy/scanner
docker compose config >/dev/null && echo "compose file ok"      # validates the YAML and .env
docker compose build
docker compose up -d --no-deps scanner     # the scanner only; see below for the gateway
docker compose logs --tail 50 scanner
```

**Not `docker compose up -d` here.** `main` adds `mem_limit: 1536m` to
`ibgateway`, so a plain `up -d` also recreates the gateway container. That is
a full IBKR login, possibly with a 2FA prompt. Recreate the gateway on its
own, when you can tap 2FA and outside market hours (§11 step 4b):

```bash
docker compose up -d ibgateway
docker compose logs --tail 30 ibgateway
```

The scanner reconnects by itself (crash-only: it backs off 20 s, exits, and
Docker restarts it).

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

Add `BACKUP_S3_URI=s3://<bucket>/oparli` to `.env` now (not earlier: §0).
Then `crontab -e`:

```
*/5 * * * * $HOME/oparli/deploy/scanner/watchdog.sh >> $HOME/scanner-watchdog.log 2>&1
30 22 * * * $HOME/oparli/deploy/scanner/backup.sh   >> $HOME/scanner-backup.log 2>&1
```

The watchdog line is today's. Its state file gains new keys; the old file still
loads.

Each watchdog run also writes `~/scanner-health.json` for the status board
(`tools/status/README.md`). It needs the host's `python3` (>= 3.11, stdlib
only). Check it after the first run:

```bash
cd ~/oparli && python3 -m tools.status --once
```

The backup also archives the datafeed store's bars as daily parquet, under
`$BACKUP_S3_URI/archive/bars/date=YYYY-MM-DD/XAUUSD.parquet` (stream profile
only):
- It covers the last 7 completed UTC days and uploads only the days that are
  missing, so a missed night is caught up the next night.
- A day is exported once. History bars that reach the store for that day later
  stay in the `store/` backups only.
- The archive holds data from 2026-10-01 onward, which `research/SEALING.md`
  seals: no study may read it before the final strategy test. It never
  expires (§0).
- Each successful run writes `last_ok=<epoch>` to `~/.scanner-backup`. The
  watchdog alerts when that is over 26 h old.

Run the backup once by hand, then check S3:

```bash
./backup.sh && aws s3 ls --recursive s3://<bucket>/oparli/
./restore.sh journal s3://<bucket>/oparli/journal/<that file> # dry run: download + verify only
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
    -e SCANNER_HEARTBEAT= --entrypoint python scanner -m backend.scanner --dry-live
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
- **Never** run an image from before the epoch migration on the migrated
  journal (switch notes §4): its positional INSERTs fail. To go back to the
  pre-deploy code anyway, restore `~/pre-deploy.sqlite` into the volume first,
  then start the tagged old image (§1). Journal rows written since are then
  lost. The full sequence for c62810d is in §11, step 4a, "Rollback".

## 7. Restore

```bash
aws s3 ls s3://<bucket>/oparli/journal/          # pick a file
./restore.sh journal s3://<bucket>/oparli/journal/<file>.sqlite.gz          # verify only
./restore.sh journal s3://<bucket>/oparli/journal/<file>.sqlite.gz --yes    # restore
./restore.sh store   s3://<bucket>/oparli/store/<file>.sqlite.gz --yes      # datafeed store
```

`--yes`:
1. stops the writer (journal: scanner; store: recorder and scanner);
2. keeps the current file as `<file>.pre-restore-<UTC ts>` in the same volume;
3. drops its `-wal` / `-shm`;
4. restores atomically;
5. starts the services again. It also restarts them if the restore step fails;
   the target is then unchanged.

To restore the pre-deploy journal from §1, use its S3 copy under `oparli/pre-deploy/` the same way.

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

  Thresholds are in `tools/status/thresholds.toml` (the `red` values are the
  alert thresholds); `watchdog.sh` keeps the same numbers as a fallback.
- **Status board:** `python3 -m tools.status --once` (one line per component,
  exit 0/1/2), or the Textual board from a small venv, see
  `tools/status/README.md`. It reads `~/scanner-health.json`, which the
  watchdog writes every 5 min. Older than 15 min means everything shows red.
- **Daily heartbeat** from the watchdog, at or after 07:00 UTC, every day: a
  one-line summary of all of the above. **No heartbeat by about 08:00 UTC
  means the watchdog or its cron is broken.**
- **Telegram, from backup**: a message if the nightly backup or archive fails.
- **Logs**: `~/scanner-watchdog.log`, `~/scanner-backup.log`,
  `docker compose logs`.
- **Journal summary** (read-only): alerts, TAKE, SKIP, MISSED and open
  outcomes for a UTC day, per epoch:
  `docker compose exec -T scanner python -m backend.scanner.journal.summary [--date YYYY-MM-DD] [--json]`.

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
- [ ] After the first watchdog run, `~/scanner-health.json` exists, and
      `python3 -m tools.status --once` shows the components you expect
      green (redis/recorder OFF without the stream profile). The log has no
      `health.json not written` or `thresholds.toml not read` lines.
- [ ] `docker stats --no-stream` after a day: every container well under its
      `mem_limit`; the swap file is active (`swapon --show`).
- [ ] The `archive/bars/date=.../XAUUSD.parquet` objects appear in S3 the
      morning after the stream profile's first full day.
- [ ] Journal, after the first replies:
      - a TAKE reply's `decision_ts_utc` is the message's time (as shown in
        Telegram), not the time it was processed;
      - the confirmation says `outcome not scorable: …` for a TAKE without
        sl/tp;
      - the journal summary lists the TAKE as open, then as tp/sl/timeout once
        it is scored;
      - a scanner restart re-confirms nothing.

## 11. Cut-over from c62810d (first deploy of main)

The host ran `pipeline-fixes-and-cleanup` @ c62810d on 2026-10-21 (scanner and
ibgateway, built about three weeks earlier); nothing from `main` was deployed.
This is the step-by-step for that jump, planned for **Sat 24 Oct 2026**. It
uses §0–§7 and adds what they don't cover. Nothing here has been run on the
host.

**The scanner's commands** below are for `main` from the scanner restructure on
(`python -m backend.scanner`, `python -m backend.scanner.journal.summary`). On
a `main` from before it, they are `python /app/deterministic_scanner.py` and
`python /app/journal_summary.py`; nothing else in this section differs.

**Assumptions:**
- The checkout is `~/oparli`, and commands run in `~/oparli/deploy/scanner`.
- The compose project is `scanner` (the directory name, with no
  `COMPOSE_PROJECT_NAME`): containers `scanner-scanner-1` and
  `scanner-ibgateway-1`, journal volume `scanner_scanner_data`. Step 0 checks
  this.

**Market hours:** gold reopens **Sun 25 Oct at 22:00 UTC** (US DST ends on
1 Nov). Saturday sees no bars.

### 11.1 What changes between c62810d and main

**Journal** (`/data/scanner_decisions.sqlite`): four migrations, run once on
the first `main` start (`open_log` → `_migrate_journal`), each in its own
transaction. At c62810d the journal has `PRAGMA user_version` 0 and four
tables.

| Step | What changes |
|---|---|
| 1 | `epoch` on alerts, decisions, outcomes and counterfactuals (existing rows get 1); `alerts.bar_source` (existing rows get 'ibkr') |
| 2 | new table `source_switches`, with one row for the existing data: `(now, NULL, 'ibkr', 1)` |
| 3 | `alerts.status` (existing rows get 'sent'); new table `scanner_state` (last live bar, Telegram offset) |
| 4 | `decisions.telegram_update_id` (UNIQUE when set) and `received_ts_utc`; `outcomes` gets the scoring columns (`decision_rowid` UNIQUE when set, `entry_*`, `reason`, `mae_r`, `mfe_r`, `bars_used`, `gap`). Old rows get NULL. |

**The migrations are one-way.** c62810d writes with positional INSERTs (e.g.
`INSERT INTO alerts VALUES(?,?,?,?,?,?,?,datetime('now'))`, 8 values). After
step 1 the tables have more columns, so the old code fails on its first
insert. Going back means restoring the pre-deploy copy (step 4a, "Rollback"),
which loses every row written since. The journal summary, `backup_sqlite.py`
and the status probes open the journal read-only and never migrate it.

**Datafeed store** (`/datafeed/datafeed.sqlite`, volume
`scanner_datafeed_store`): new. The recorder creates it in step 6 and applies
store migrations 1–4. Nothing old is migrated, and no older store code exists
to go back to. The Redis volume (`scanner_redis_data`) is new and empty too.

**Telegram offset:** c62810d keeps it in memory, starting at 0. `main` saves
it in `scanner_state`, and its first start also begins at 0. Nothing to do.

**New `.env` variables** (names; values per `deploy/scanner/.env.example`):

| Variable | When |
|---|---|
| `SCANNER_BAR_SOURCE` | step 3 (`ibkr`); `stream` in step 7 |
| `FEED_IB_CLIENT_ID` | step 3 |
| `SCANNER_STREAM_CONSUMER` | step 3 |
| `BACKUP_S3_URI` (`s3://<bucket>/oparli`) | step 5, just before the first `backup.sh` (§0) |
| `COMPOSE_PROFILES` (`stream`) | step 6 |

The existing ones stay: `TWS_USERID`, `TWS_PASSWORD`, `TRADING_MODE`,
`IB_PORT`, `READ_ONLY_API`, `AUTO_RESTART_TIME`, `TIME_ZONE`,
`VNC_SERVER_PASSWORD`, `IB_CLIENT_ID`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`.

### Step 0: before touching anything (Friday, or Saturday morning)

On the host:

```bash
cd ~/oparli && git status -sb && git log --oneline -1    # pipeline-fixes-and-cleanup, c62810d
cd deploy/scanner
docker compose ps                                        # scanner-scanner-1, scanner-ibgateway-1 Up
docker volume ls | grep scanner_                         # scanner_scanner_data
docker compose images                                    # note the scanner image REPOSITORY:TAG and IMAGE ID
docker stats --no-stream                                 # memory baseline: the gateway's MEM USAGE (§0)
free -m && swapon --show                                 # swap (§0)
python3 --version                                        # >= 3.11 for health.json, see below
aws --version && aws sts get-caller-identity             # the instance role, no keys
aws s3 ls s3://<bucket>/oparli/                          # the role can list the prefix
df -h / /var/lib/docker
crontab -l                                               # today's watchdog line
```

**`lifecycle`: on a machine with admin AWS credentials** (the host's role has
no `s3:GetLifecycleConfiguration`, on purpose). This prints the bucket's
rules and **stops if any rule would expire `oparli/archive/` or
`oparli/pre-deploy/`**:

```bash
B=<bucket>
rm -f lifecycle-now.json
if aws s3api get-bucket-lifecycle-configuration --bucket "$B" > lifecycle-now.json 2> lifecycle.err \
   || { grep -q NoSuchLifecycleConfiguration lifecycle.err && echo '{"Rules": []}' > lifecycle-now.json; }; then
python3 - lifecycle-now.json <<'PY'
import json, sys
KEEP = ("oparli/archive/", "oparli/pre-deploy/")          # must never expire
WANT = ("oparli/journal/", "oparli/store/")               # 30 days (§0)
rules = json.load(open(sys.argv[1])).get("Rules", [])
def prefix(r):                                            # (prefix, has a tag/size filter)
    f = r.get("Filter")
    if f is None:
        return r.get("Prefix", ""), False
    if "Prefix" in f:
        return f["Prefix"], False
    if "And" in f:
        a = f["And"]
        return a.get("Prefix", ""), bool(a.get("Tags") or "ObjectSizeGreaterThan" in a or "ObjectSizeLessThan" in a)
    return "", True                                       # tag or size only: may match anything
def expires(r):
    return [k for k in ("Expiration", "NoncurrentVersionExpiration") if k in r]
bad = []
for r in rules:
    p, narrowed = prefix(r)
    on = r.get("Status") == "Enabled"
    print("rule %r: %s, prefix %r%s, expires: %s" % (r.get("ID", "?"), r.get("Status"), p,
          " (plus a tag/size filter)" if narrowed else "",
          json.dumps({k: r[k] for k in expires(r)}) if expires(r) else "no"))
    if on and expires(r):
        bad += ["%s would expire objects under %s" % (r.get("ID", "?"), k)
                for k in KEEP if k.startswith(p) or p.startswith(k)]
for k in WANT:
    if not any(r.get("Status") == "Enabled" and "Expiration" in r and prefix(r)[0] == k for r in rules):
        print("note: no enabled expiry rule on %s yet (§0)" % k)
if not rules:
    print("no lifecycle rules on this bucket")
if bad:
    print("STOP: " + "; ".join(bad))
    sys.exit(1)
print("ok: nothing expires under oparli/archive/ or oparli/pre-deploy/")
PY
else
  cat lifecycle.err; echo "STOP: can't read the lifecycle rules"
fi
```

- A rule counts if its prefix covers part of `archive/` or `pre-deploy/`.
  So does any rule with no prefix, or with only a tag or size filter. Fix the
  rules (§0) and run it again.

**Python on the host.** If `python3 --version` is older than 3.11 (Ubuntu
22.04 ships 3.10):
- **What fails:**
  - the watchdog can't write `~/scanner-health.json`, and logs
    `health.json not written` every run;
  - the status board (`python3 -m tools.status`, also `--once`) doesn't run;
  - the watchdog can't read `tools/status/thresholds.toml`. It logs
    `thresholds.toml not read …; using the built-in alert thresholds` and uses
    the same numbers built into `watchdog.sh`.
  - All three need `tomllib`, which is 3.11+.
- **What still works:** every watchdog alert and its recovery message
  (gateway, crash loop, heartbeat, Redis, disk, memory, backup age, recorder
  lag, rejected messages), the daily heartbeat, the gateway re-login,
  `backup.sh` and `restore.sh` (their Python runs inside the scanner
  container), and the scanner itself.
- **Fix, any time later:**
  - install a Python >= 3.11 next to the system one;
  - point the cron line at it: `*/5 * * * * STATUS_PYTHON=/path/to/python3.12 $HOME/oparli/deploy/scanner/watchdog.sh >> …`
    (`STATUS_PYTHON` is read from the environment, not from `.env`).

**Healthy:**
- the branch and commit are as above;
- two containers are Up;
- the role works;
- `lifecycle` prints `ok`;
- swap is active (or set it up now, §0);
- you know the gateway's memory use.

**Stop if** the role, the bucket or the AWS CLI is missing, or `lifecycle`
says STOP.

### Step 1: back up the journal, and tag the old images (Saturday)

§1 as written: the copy to `s3://<bucket>/oparli/pre-deploy/` and the image
tags (`OLD` is `c62810d`).

**Healthy:**
- `('ok',)`;
- the S3 object has the same size as `~/pre-deploy.sqlite.gz`;
- `scanner-scanner:rollback-c62810d` and `ib-gateway:rollback-c62810d` are
  listed with the image ids from step 0.
- Note the row counts:
  `python3 -c "import sqlite3; c = sqlite3.connect('$HOME/pre-deploy.sqlite'); print([c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in ('alerts', 'decisions', 'outcomes', 'counterfactuals')])"`.

**Rollback:** nothing has changed.

### Step 2: pull main and build (Saturday; the old containers keep running)

```bash
cd ~/oparli && git fetch && git switch main && git pull --ff-only && git log --oneline -1
cd deploy/scanner
docker compose build
docker image ls oparli-scanner
```

**Healthy:**
- the build succeeds and `oparli-scanner:local` exists. On this host, the
  pinned wheels (`pyarrow==25.0.0` and the others) must install for Python
  3.12 on its architecture (§10);
- `docker compose ps` still shows the old two containers Up.

**Rollback:** `cd ~/oparli && git switch --detach c62810d`. No container was
touched.

From the `git pull` on, the 5-minute cron runs `main`'s `watchdog.sh` against
the old containers. That's harmless (a container without a healthcheck isn't
an alert), but don't leave a long gap before step 4a.

### Step 3: `.env` (Saturday)

Add `SCANNER_BAR_SOURCE=ibkr`, `FEED_IB_CLIENT_ID=31` and
`SCANNER_STREAM_CONSUMER=scanner-1`. Not `BACKUP_S3_URI` (step 5), and not
`COMPOSE_PROFILES` (step 6).

```bash
docker compose config >/dev/null && echo "compose file ok"
```

**Rollback:** remove the three lines.

### Step 4a: start main in ibkr mode, scanner only (Saturday)

If more than a few hours passed since step 1, repeat step 1's journal copy
first (§1).

```bash
docker compose up -d --no-deps scanner
docker compose logs --tail 50 scanner
docker compose exec -T scanner python -c "import sqlite3; c = sqlite3.connect('/data/scanner_decisions.sqlite'); print(c.execute('PRAGMA user_version').fetchone(), c.execute('SELECT * FROM source_switches').fetchall(), c.execute('SELECT COUNT(*) FROM alerts').fetchone())"
sleep 120; docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' scanner-scanner-1
docker stats --no-stream
```

The old gateway container keeps running untouched.

**Healthy:**
- the log shows, in order:
  - `entrypoint: waiting for IB Gateway API at ibgateway:4004 ...`;
  - `gateway API up, giving it 10s to settle`;
  - `LIVE (Telegram): scanning XAUUSD, N levels active, journal epoch 1.`;
- the check prints `(4,)`, one row `('…', None, 'ibkr', 1)`, and step 1's
  alert count;
- health is `healthy`, with 0 restarts;
- no Telegram message from the scanner: the market is closed;
- the journal summary runs:
  `docker compose exec -T scanner python -m backend.scanner.journal.summary`.

**Rollback** (the journal is migrated, so the old code can't write it):

```bash
cd ~/oparli/deploy/scanner
docker compose stop scanner
docker run --rm --entrypoint sh -v scanner_scanner_data:/data -v $HOME/pre-deploy.sqlite:/restore.sqlite:ro \
  scanner-scanner:rollback-c62810d -c 'cp /data/scanner_decisions.sqlite /data/scanner_decisions.sqlite.post-main-$(date -u +%Y%m%dT%H%M%SZ) && rm -f /data/scanner_decisions.sqlite-wal /data/scanner_decisions.sqlite-shm && cp /restore.sqlite /data/scanner_decisions.sqlite'
cd ~/oparli && git switch --detach c62810d && cd deploy/scanner
docker image tag scanner-scanner:rollback-c62810d scanner-scanner:latest
docker compose up -d --no-build
```

- Rows written by `main` stay only in the `.post-main-…` copy in the volume.
- Remove the `.env` lines from step 3.
- The c62810d compose has no `mem_limit`, so this also recreates the gateway:
  have 2FA at hand.

### Step 4b: recreate the gateway (Sunday, before 22:00 UTC)

`main` adds `mem_limit: 1536m` to `ibgateway`, so the container must be
recreated once. That is a full IBKR login, possibly with a 2FA prompt. Do it
on Sunday, early enough to deal with a login or 2FA problem before the
22:00 UTC reopen. If the gateway's step-0 memory use was near 1536m, raise
the limit first (§0).

```bash
cd ~/oparli/deploy/scanner
docker compose up -d ibgateway
docker compose logs -f ibgateway                         # until it logs in; tap 2FA if asked
docker compose logs --tail 30 scanner                    # the scanner reconnects by itself
docker stats --no-stream
```

**Healthy:**
- the gateway logs in;
- within a few minutes the scanner log shows a clean start again (it backs
  off 20 s, exits, and Docker restarts it): `gateway API up`, then
  `LIVE (Telegram): … journal epoch 1`;
- health `healthy`;
- the gateway well under 1536m.
- **After 22:00 UTC:** alerts arrive as before, a reply gets
  `logged TAKE/SKIP for …`, and the journal summary lists it.

**Rollback:** `docker image tag ib-gateway:rollback-c62810d
ghcr.io/gnzsnz/ib-gateway:stable && docker compose up -d ibgateway` brings
the old gateway image back, still with the new memory limit. For the old code
as a whole, use 4a's rollback.

### Step 5: crons, first backup, status board

Add `BACKUP_S3_URI=s3://<bucket>/oparli` to `.env`, then §3: the crontab, the
first `./backup.sh`, the restore dry run, and `python3 -m tools.status --once`.

**Healthy:**
- `backup.sh` exits 0, and an object appears under `oparli/journal/`;
- the restore dry run verifies it;
- `~/.scanner-backup` has `last_ok=`;
- after the next watchdog run, its log has no `health.json not written` or
  `thresholds.toml not read` (unless `python3` < 3.11, step 0);
- the board shows the gateway and scanner green, and redis/feed/recorder OFF;
- the daily heartbeat arrives after 07:00 UTC.

**Rollback:** remove the `backup.sh` cron line and `BACKUP_S3_URI`. The
watchdog line runs whatever the checkout holds.

### Step 6: stage the datafeed (the scanner stays ibkr)

§4: add `COMPOSE_PROFILES=stream` and `docker compose up -d`. After 4a and 4b
nothing else is recreated.

**Healthy:**
- redis, then feed, then recorder become healthy;
- the feed logs `ibkr: N history bars published, streaming XAUUSD`;
- the recorder logs `health {...}` every 60 s, with `last_live_bar_ts`
  advancing in market hours and `lag_s` small;
- the board shows redis, feed and recorder green;
- the gateway has two bar clients, the scanner (11/21) and the feed (31);
- the nightly backup adds `oparli/store/`, and `oparli/archive/bars/` after
  the first full day;
- `docker stats` stays under the limits for a day.

**Rollback:** remove `COMPOSE_PROFILES=stream`, then
`docker compose stop redis feed recorder`. The scanner isn't affected.

### Step 7: switch the scanner to stream (not on the deploy weekend)

§5. Flip on a Monday to Wednesday, or after two weeks of staging (§4: the
store needs about two weeks of bars before a Thursday-to-Sunday warm-up is
≥ 80 % covered).

**Healthy:**
- the log shows, in order:
  1. `stream mode, waiting for Redis`, then `Redis up`;
  2. `warm-up via store: …`;
  3. `LIVE … from stream bars.xauusd.1m …, journal epoch 2`;
  4. one-off backlog batch lines;
  5. then no `out_of_order` or `conflict` lines;
- `source_switches` gains `(…, 'ibkr', 'stream', 2)`.

**Rollback:** §6 (a new epoch 3; no restore).

## 12. Deploying the restructure

For a host that already runs `main` from **before** the scanner restructure
(PR 2 of `docs/history/RESTRUCTURE_PLAN.md`). On a host still on c62810d, use §11:
deploying `main` from §11 includes the restructure.

**What changes.** Code layout only:
- the scanner runs as `python -m backend.scanner` (the entrypoint does this);
- the journal summary is `python -m backend.scanner.journal.summary`.

**What stays:**
- the flags and env vars;
- the Telegram texts;
- the journal (`PRAGMA user_version` 4, no migration) and the datafeed store;
- the volumes and the S3 layout.

```bash
cd ~/oparli
OLD=$(git rev-parse --short HEAD)
docker image tag oparli-scanner:local oparli-scanner:rollback-$OLD     # the image you run now
git fetch && git switch main && git pull --ff-only
cd deploy/scanner
docker compose config >/dev/null && echo "compose file ok"
docker compose build
docker compose up -d --no-deps scanner                 # stream profile: also `docker compose up -d feed recorder`
docker compose logs --tail 30 scanner
docker compose exec -T scanner python -m backend.scanner.journal.summary
```

**Healthy:**
- the same start-up lines as before the deploy (`LIVE (Telegram): scanning
  XAUUSD …, journal epoch N` with the **same** epoch N, because the bar source
  didn't change);
- health `healthy` after about 2 min;
- the journal summary prints;
- `PRAGMA user_version` is still `(4,)`.

**Rollback:** the journal wasn't migrated, so the previous image can still
write it.

```bash
docker image tag oparli-scanner:rollback-$OLD oparli-scanner:local
cd ~/oparli && git switch --detach $OLD && cd deploy/scanner
docker compose up -d --no-build --no-deps scanner      # and feed recorder in the stream profile
```

## 13. Repo root tidy (new paths)

`chore/repo-root-tidy` moved seven files out of the repo root:

| Old path | New path |
|---|---|
| `requirements.lock` | `requirements/requirements.lock` |
| `requirements.cutoff.txt` | `requirements/requirements.cutoff.txt` |
| `requirements-studio.in` | `requirements/requirements-studio.in` |
| `requirements-studio.lock` | `requirements/requirements-studio.lock` |
| `.env.example` (the local development template) | `deploy/.env.example` |
| `DECISIONS.md` | `docs/DECISIONS.md`, since split into `docs/decisions/<branch>.md` (`docs/tidy`) |
| `langgraph.json` | `tools/langgraph_studio/langgraph.json` |

**On EC2, no command changes.**
- The compose build context, both Dockerfiles, `deploy/scanner/.env` and
  its template `deploy/scanner/.env.example` are where they were. The
  Dockerfiles don't read the lock.
- The status board installs from `tools/status/requirements-status.lock`,
  which also didn't move.
- `git pull --ff-only` moves the files in the checkout, and the next
  `docker compose build` gives the same image.
- Don't confuse the two templates:
  - `deploy/.env.example` is for local development (datafeed replay, a local
    paper gateway);
  - the host's `.env` comes from `deploy/scanner/.env.example`.

**Commands that do change** (run from the repo root):
- a hash-locked install: `pip install --require-hashes -r requirements/requirements.lock`;
- LangGraph Studio (local only): `langgraph dev --config tools/langgraph_studio/langgraph.json`,
  see `docs/LANGGRAPH_STUDIO.md`;
- a local `.env`: `cp deploy/.env.example .env`.

