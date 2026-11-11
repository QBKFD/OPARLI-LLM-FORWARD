# deploy/options: the daily OG options snapshot

Every weekday at 05:00 UTC, host cron runs `run_snapshot.sh`. It does one
`docker compose run` of `python -m backend.options`, which takes about 11
minutes:

- the full OG chain (every strike, calls and puts) of the next 3 expiries at
  least 2 days away;
- live data first, then frozen data for the contracts that sent no tick;
  50 market-data lines at a time; everything cancelled at the end;
- stored in `/data/options.sqlite` in the volume `oparli-options_options_data`.
  Per contract: OI, IV, gamma, delta, vega, bid, ask, the data type and the
  time of each value. Per underlying GC future: bid, ask, last, close and OI.

Each invocation writes one `option_runs` row, whatever happens. Nothing is
retried. Exit 3 (COMEX closed, a CME holiday) is only logged. Exit 4 (IBKR
error 10197: the live account is logged in somewhere) sends one Telegram
notice. Any other failure sends one Telegram message. After an ok run,
`backup.sh` uploads the whole store to `s3://<bucket>/oparli/options/`.

It is its own compose project (`oparli-options`) with its own image
(`oparli-options:local`), built from its own checkout. It joins the scanner
project's network only to reach the gateway as `ibgateway`, with client id
62. So it changes nothing in the scanner stack: no scanner image, no scanner
container, no `deploy/scanner` file, no scanner `.env`. It works with the
host on c62810d or on `main`.

The code is in `backend/options/` (`chain.py` is shared with
`tools/options_probe.py`), and the tests are in `tests/options/`.

## Deploy on EC2 (the owner, by hand)

Run these as `ubuntu` on the host (`ssh -i ~/Downloads/oparli.pem ubuntu@<ip>`).
Replace `<branch>` with `feat/options-snapshot` until it's merged, then use `main`.

**0. Check the preconditions.** Every check prints something.

```bash
timedatectl | grep 'Time zone'              # must be UTC (Etc/UTC): the cron line is in host time
docker network ls --format '{{.Name}}' | grep scannernet   # expect: scanner_scannernet
docker ps --format '{{.Names}}' | grep ibgateway            # the gateway is up
command -v aws && aws sts get-caller-identity --query Arn   # the instance role, for backups
```

- If the network has another name, set `GATEWAY_NETWORK=<that name>` in step 2.
- If the clock isn't UTC, either change the hour in the step 6 cron line, or
  run `sudo timedatectl set-timezone Etc/UTC`. The second also moves the
  scanner's backup cron.

**1. Make a separate checkout.** It's a worktree of the same repo, so
`~/oparli` (which the scanner builds from) is not switched.

```bash
cd ~/oparli && git fetch origin
git worktree add --detach ~/oparli-options origin/<branch>
cd ~/oparli-options && git log --oneline -1
```

**2. Write the `.env`.** It holds the values from the scanner's `.env`, and
prints names only.

```bash
cd ~/oparli-options/deploy/options
grep -E '^(IB_PORT|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|BACKUP_S3_URI)=' ~/oparli/deploy/scanner/.env > .env
chmod 600 .env && cut -d= -f1 .env          # expect TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (+ IB_PORT, BACKUP_S3_URI)
```

- If `BACKUP_S3_URI` is missing (the c62810d `.env` may not have it), add it:
  `echo 'BACKUP_S3_URI=s3://<bucket>/oparli' >> .env`.
- A missing `IB_PORT` means 4004 (paper), the scanner's default.
- Only if step 0 showed another network name:
  `echo 'GATEWAY_NETWORK=<name>' >> .env`.

**3. Build the image.** This builds `oparli-options:local` only.

```bash
docker compose config -q && docker compose build
docker compose run --rm -T options-snapshot --help | head -3         # the CLI starts
docker compose run --rm -T options-snapshot --list-runs 5            # "no store yet" (exit 1) the first time
```

**4. Take the first snapshot by hand.** COMEX must be open, any time on a
regular weekday except 21:00–22:00 UTC (22:00–23:00 from 2026-11-01). Log the
live IBKR account out everywhere (TWS, mobile, web) first, or you'll get
exit 4.

```bash
./run_snapshot.sh 2>&1 | tee -a ~/options-snapshot.log     # ~11 minutes, then the backup
echo "exit ${PIPESTATUS[0]}"                               # 0
docker compose run --rm -T options-snapshot --list-runs 5
```

- Expect one line `run 1 <date> ok exit 0 ... 4324 options (4324 live, 0 frozen, 0 none) [...]`.
  The count varies from day to day.
- The log ends with `uploaded options to s3://.../oparli/options/options-<host>-<ts>.sqlite.gz`.
- If you rerun it the same trade date, it records a `skipped ... already has an
  ok snapshot` row, requests nothing, and backs up again.

**5. Check the backup.**

```bash
aws s3 ls "$(grep '^BACKUP_S3_URI=' .env | cut -d= -f2)/options/"
```

**6. Install the cron line.** It runs weekdays at 05:00 UTC.

```bash
( crontab -l 2>/dev/null; echo '0 5 * * 1-5 $HOME/oparli-options/deploy/options/run_snapshot.sh >> $HOME/options-snapshot.log 2>&1' ) | crontab -
crontab -l | grep run_snapshot
```

**7. Optional: S3 retention.** No lifecycle rule covers `oparli/options/`,
so every daily copy is kept. Each copy is the whole store, so the total grows
over time. To keep 30 days, as for `journal/`, add this rule. It does not
replace the others:

```bash
B=<bucket>
aws s3api get-bucket-lifecycle-configuration --bucket "$B" > lc.json 2>/dev/null || echo '{"Rules": []}' > lc.json
python3 - lc.json <<'PY'
import json, sys
p = sys.argv[1]; lc = json.load(open(p))
lc["Rules"] = [r for r in lc["Rules"] if r.get("ID") != "oparli-options-30d"] + [
    {"ID": "oparli-options-30d", "Filter": {"Prefix": "oparli/options/"}, "Status": "Enabled",
     "Expiration": {"Days": 30}, "NoncurrentVersionExpiration": {"NoncurrentDays": 7}}]
json.dump({"Rules": lc["Rules"]}, open(p, "w"), indent=1); print([r["ID"] for r in lc["Rules"]])
PY
aws s3api put-bucket-lifecycle-configuration --bucket "$B" --lifecycle-configuration file://lc.json
```

The host's role can't set lifecycle rules (docs/DEPLOY_RUNBOOK.md §0), so run
this from a machine with admin AWS credentials. Then run the runbook's
`lifecycle` check.

## Day to day

- Last runs: `cd ~/oparli-options/deploy/options && docker compose run --rm -T options-snapshot --list-runs 10`.
- Log: `~/options-snapshot.log`. State: `~/.options-snapshot` (`last_rc`,
  `last_run`, `last_ok`) and `~/.options-backup` (`last_ok`).
- **After an exit 4 notice:** log the live account out, then run
  `~/oparli-options/deploy/options/run_snapshot.sh >> ~/options-snapshot.log 2>&1`
  by hand. Cron never retries.
- **Update the code:**

  ```bash
  cd ~/oparli-options && git fetch origin && git checkout --detach origin/main
  cd deploy/options && docker compose build
  ```

  The store migrates itself on the next run.
- **The holiday calendar** (`backend/options/chain.py`, `HOLIDAYS`) covers
  2026–2027. From 2028 every run fails with "update HOLIDAYS" (Telegram) until
  it's extended.

## Remove

```bash
crontab -l | grep -v options/run_snapshot.sh | crontab -
cd ~/oparli-options/deploy/options && docker compose down --rmi local     # keeps the volume (the data)
cd ~/oparli && git worktree remove ~/oparli-options
```

To delete the data as well: `docker volume rm oparli-options_options_data`.
The S3 copies stay.
