# Backfill 2026-02-01 .. 2026-09-30: the run, step by step

The plan and the checks are in `docs/BACKFILL_2026_PROPOSAL.md`. This page is
the exact sequence. **The owner runs every command; Claude never touches EC2.**

**Rules:**
- **Hard end 2026-09-30.** `backfill_ibkr.py` refuses any request for a later
  day, or any endDateTime after 2026-10-01 00:00 UTC, before IBKR is asked
  (exit 3). A bar at or after 2026-10-01 stops the run (exit 3).
- **No prices.** This period is a holdout nobody has read. Nothing below
  prints, plots or summarises a price, return or volume. The JSONL files
  hold prices, so **never `cat`, `head` or `less` them**. The commands check
  only their last line, with `grep`.
- **When:** a weekday, 07:00–20:00 UTC, with the live IBKR account logged out
  everywhere (error 10197). Don't restart the scanner during the run.

`<ip>` is the host, and `<bucket>` is the backup bucket (`BACKUP_S3_URI` in
`~/oparli/deploy/scanner/.env`).

## 0. On the Mac: update main, copy the downloader

After this PR is merged:

```bash
cd ~/Desktop/algo_project_levels && git switch main && git pull --ff-only
git log --oneline -1 -- tools/data/backfill_ibkr.py     # the commit with refuse_past_seal
scp -i ~/Downloads/oparli.pem tools/data/backfill_ibkr.py ubuntu@<ip>:/tmp/backfill_ibkr.py
```

Steps 3 and 5 also run in this checkout. It has `database/ohlcv_data/XAUUSD.txt`
and the manifest.

## 1. On EC2: check that IB Gateway is up and reachable

```bash
cd ~/oparli/deploy/scanner
git -C ~/oparli log --oneline -1                      # note the commit the host runs
grep -nE 'IB_HOST|IB_PORT|TRADING_MODE' docker-compose.yml .env    # host and port the scanner uses
docker compose ps                                     # ibgateway: Up (healthy); scanner: Up
docker compose logs --tail 20 ibgateway | grep -iE 'login|connected|error' || true
docker compose run --rm -T --no-deps --entrypoint python scanner -c \
  "import socket; socket.create_connection(('ibgateway', 4004), 5); print('gateway API port open')"
docker compose run --rm -T --no-deps --entrypoint python scanner -c "import ib_insync, sys; print(sys.version.split()[0], ib_insync.__version__)"
```

**Expect:**
- `ibgateway` is `Up (healthy)` or at least `Up`;
- the socket check prints `gateway API port open`;
- the last line shows Python 3.12+ and an ib_insync version.

**Failure:**
- a `ConnectionRefusedError` or `timeout` means the gateway isn't logged in,
  or the port differs. If `grep` showed another `IB_PORT` (or a host other
  than `ibgateway`), use those values in the socket check and in steps 2 and 4
  (`--host`, `--port`).
- `No module named ib_insync`: stop and tell me.

## 2. On EC2: plan, then the overlap probe (about 9 min)

```bash
mkdir -p ~/backfill
python3 /tmp/backfill_ibkr.py --plan
docker compose run --rm -T --no-deps --entrypoint python scanner - --probe-overlap --host ibgateway --port 4004 \
    < /tmp/backfill_ibkr.py > ~/backfill/overlap.jsonl
echo "exit $?"; tail -n 1 ~/backfill/overlap.jsonl | grep -o '"kind":"end"[^}]*'
```

**Expect:**
- `--plan` prints `probe: 44 requests, 2026-01-19 .. 2026-01-30, about 9 min`
  and `download: 208 requests, 2026-02-01 .. 2026-09-30, about 42 min`;
- then `exit 0`, and a line like `"kind":"end","ended_utc":"…","n_bars":…`.

**Failure:**
- `exit 4`: error 10197. Log the live account out everywhere and rerun step 2.
- `exit 5`: a pacing violation. Wait 10 minutes and rerun.
- `exit 3`: a sealed request or bar. Stop and tell me.
- `exit 1`, or no `end` line: stop and send me the stderr text. It holds no
  prices.

## 3. On the Mac: pick the type (counts and shares only)

```bash
mkdir -p ~/backfill && scp -i ~/Downloads/oparli.pem ubuntu@<ip>:backfill/overlap.jsonl ~/backfill/
cd ~/Desktop/algo_project_levels
.venv/bin/python tools/data/backfill_build.py pick ~/backfill/overlap.jsonl --out ~/backfill/pick.json
```

**Expect:** one line per type (`bars`, `OHLC exact …%`, `+volume …%`, `errors`),
then `picked <TYPE>`.

**Failure:** `no type reaches 99.5%: STOP, no backfill`. Send me those lines;
the backfill ends there.

## 4. On EC2: the download (about 42 min)

```bash
cd ~/oparli/deploy/scanner
docker compose run --rm -T --no-deps --entrypoint python scanner - --download <TYPE> --host ibgateway --port 4004 \
    < /tmp/backfill_ibkr.py > ~/backfill/download.jsonl
echo "exit $?"; tail -n 1 ~/backfill/download.jsonl | grep -o '"kind":"end"[^}]*'
```

Expect and failure are as in step 2. A rerun starts from scratch.

## 5. On the Mac: validate, write, register

```bash
scp -i ~/Downloads/oparli.pem ubuntu@<ip>:backfill/download.jsonl ~/backfill/
.venv/bin/python tools/data/backfill_build.py build ~/backfill/download.jsonl --pick ~/backfill/pick.json --update-manifest \
    | tee ~/backfill/integrity_report.txt
git diff --stat backend/datafeed/store/manifest.json
shasum -a 256 database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet
```

**Expect:**
- the report starts `BACKFILL INTEGRITY REPORT (no prices)`, with
  `passed: yes`;
- `best whole-hour shift 0 h`;
- stderr says `passed: …parquet, N bars`;
- only the manifest changed;
- the `shasum` equals the report's `sha256`.

**Failure** (exit 1, `REJECTED, written as …rejected.parquet`): the report's
`failures:` line gives the reason.
- `timezone: … shifted by N h` or `UTC session check` means the US-Eastern
  switch leaked in.
- Others: an incomplete download, conflicting duplicates, completeness.

Send me the report either way.

## 6. Back up to S3 (through the host, which has the IAM role)

```bash
scp -i ~/Downloads/oparli.pem database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet ubuntu@<ip>:backfill/
```

On EC2:

```bash
cd ~/backfill && SHA=$(sha256sum XAUUSD_ibkr_1min_20260201_20260930.parquet | cut -d' ' -f1) && echo "$SHA"
aws s3 cp --only-show-errors --sse AES256 --metadata sha256=$SHA XAUUSD_ibkr_1min_20260201_20260930.parquet \
    s3://<bucket>/oparli/pre-deploy/data/XAUUSD_ibkr_1min_20260201_20260930.parquet
aws s3api head-object --bucket <bucket> --key oparli/pre-deploy/data/XAUUSD_ibkr_1min_20260201_20260930.parquet \
    --query '{bytes: ContentLength, sha256: Metadata.sha256, sse: ServerSideEncryption}'
```

**Expect:** `head-object` shows the file's byte count, the same `sha256` as
step 5, and `"sse": "AES256"`. `oparli/pre-deploy/` never expires (runbook §0).

## 7. Clean up the host

```bash
rm -rf ~/backfill /tmp/backfill_ibkr.py
```

On the Mac, `~/backfill/*.jsonl` is IBKR data: keep it off any shared place,
or delete it once the parquet is backed up.

## What to paste back

1. The `pick` lines (step 3).
2. `~/backfill/integrity_report.txt` (step 5). Its format:

```
BACKFILL INTEGRITY REPORT (no prices)
file: XAUUSD_ibkr_1min_20260201_20260930.parquet
sha256: <64 hex>
passed: yes
rows: <n>   first UTC: 2026-02-01T23:00:00+00:00   last UTC: 2026-09-30T20:59:00+00:00
timestamps tz-aware UTC: True   off minute grid: 0   at/after 2026-10-01: 0   outside range dropped: <n>
duplicates: exact dropped <n>, conflicting 0
timezone: best whole-hour shift 0 h (must be 0); session check dst <share> of <n> days, std <share> of <n> days
completeness: <full> of <n> trade dates full (share <x>)
rows per trade date: min <n>, median <n>, max <n>
short days (< 90 % of 1380): {<date>: <rows>, …}
gaps inside sessions: <n> gaps, <n> missing minutes
longest gaps: <UTC time> +<n> min; …
bad OHLC bars (counted, not fixed): <n>
requests without bars: [<dates>]
requests with IBKR errors: {<date>: [<codes>]}
seam: XAUUSD.txt last 2026-01-30T21:59:00+00:00, backfill first <UTC time>
failures: none
```

3. The `head-object` output (step 6).

I commit the manifest change from your checkout's diff, on a branch and through
a PR.
