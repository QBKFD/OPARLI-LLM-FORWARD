# Multi-market 1-min data, 2016-01-01 .. 2026-09-30

**Status (2026-10-28):**
- Seals and planned manifest entries were committed first (370a311).
- The downloader, builder and runner are ready and tested on a fake IBKR
  client.
- **Not downloaded yet:** this machine couldn't reach the EC2 host (SSH timed
  out), and there is no local IB Gateway. The owner starts the run with the
  commands below.

| Instrument | IBKR contract | whatToShow | File |
|---|---|---|---|
| XAGUSD | XAGUSD CMDTY SMART USD | MIDPOINT | `database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet` |
| EURUSD | EUR CASH IDEALPRO USD | MIDPOINT | `database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet` |
| USDJPY | USD CASH IDEALPRO JPY | MIDPOINT | `database/ohlcv_data/USDJPY_ibkr_1min_20160101_20260930.parquet` |
| GBPUSD | GBP CASH IDEALPRO USD | MIDPOINT | `database/ohlcv_data/GBPUSD_ibkr_1min_20160101_20260930.parquet` |
| USDCAD | USD CASH IDEALPRO CAD | MIDPOINT | `database/ohlcv_data/USDCAD_ibkr_1min_20160101_20260930.parquet` |
| USDSEK | USD CASH IDEALPRO SEK | MIDPOINT | `database/ohlcv_data/USDSEK_ibkr_1min_20160101_20260930.parquet` |
| USDCHF | USD CASH IDEALPRO CHF | MIDPOINT | `database/ohlcv_data/USDCHF_ibkr_1min_20160101_20260930.parquet` |
| DXY_PROXY | derived from the six FX pairs | — | `database/ohlcv_data/DXY_PROXY_1min_20160101_20260930.parquet` |

**Seals:** `research/SEALING.md`, "Instrument periods":
- dev: 2016–2023;
- holdout: 2024-01-01 .. 2026-09-30;
- sealed: from 2026-10-01.

Every file is "unused by any study" until a PREREG names it
(`research/data_guard.py`).

**DXY proxy:** DXY = 50.14348112 × EURUSD^−0.576 × USDJPY^0.136 ×
GBPUSD^−0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036. It is built only
on the minutes where all six pairs have a bar.

**Gold files are not touched.**

## Tools

- **Download:** `tools/data/backfill_ibkr.py --instrument SYM --out-dir DIR`,
  the #41 / #51 downloader, extended.
  - **Contracts:** one per instrument; MIDPOINT for all; `formatDate=2` (UTC).
  - **Chunks:** a whole week per request (`1 W`, ending Saturday 00:00 UTC,
    capped at 2026-10-01 00:00 UTC). A week with fewer than 3,450 bars, or
    with an error, is fetched again day by day (`1 D`).
  - **Checkpoints:** each week is written as one file per UTC day (written
    whole, then renamed), plus a marker in `_done/`. A rerun skips every
    marked week.
  - **Retries:** a lost connection, such as the nightly reset or a gateway
    restart, is retried every 2 min for 3 h (then exit 6). Error 10197 waits
    5 min, and a pacing violation 11 min.
  - **Limits:** ≤ 50 requests per 10 minutes; client id 65.
  - **Hard end:** any range or request past 2026-09-30 is refused before
    connecting, and again before each request (exit 3).
- **Runner:** `tools/data/multi_market_run.sh`. It runs the seven instruments
  in order inside tmux and resumes and retries on its own.
- **Build:** `tools/data/multi_market_build.py instrument SYM DAY_DIR
  --update-manifest`, run on the Mac. It writes the parquet, or a
  `.rejected` one, plus the manifest entry and an integrity report (no
  prices). The checks:
  - the day files are complete;
  - the minute grid, the range and the seal;
  - duplicates;
  - UTC by the weekend (Friday 16:00–17:14 → Sunday 17:00–18:59 New York;
    best whole-hour shift must be 0); XAGUSD also gets gold's daily-break
    check;
  - completeness, and gaps inside sessions.

  Then `dxy --update-manifest` builds the proxy from the six verified FX
  files.

**ETA (INFERRED, UNVERIFIED):**
- **If IBKR serves a week of 1-min bars per request:** about 562 requests per
  instrument, so about 1.9 h each and about 13 h for all seven.
- **If it doesn't:** every week falls back to days (about 3,300 requests), so
  about 11 h per instrument.
- **Disk:** the JSONL is about 0.3–0.5 GB per instrument on the host.

## Run it (the owner)

**When:** any time the gateway is up. The run sits through the nightly reset.

**0. On the Mac:** copy the downloader and the runner.

```bash
cd ~/Desktop/algo_project_levels && git fetch origin
git show origin/data/multi-market:tools/data/backfill_ibkr.py > /tmp/backfill_ibkr.py
git show origin/data/multi-market:tools/data/multi_market_run.sh > /tmp/multi_market_run.sh
scp -i ~/Downloads/oparli.pem /tmp/backfill_ibkr.py /tmp/multi_market_run.sh ubuntu@<ip>:/tmp/
```

**1. On EC2: check the gateway** (as in `docs/BACKFILL_2026_RUNBOOK.md` step 1).

```bash
cd ~/oparli/deploy/scanner && docker compose ps
docker compose run --rm -T --no-deps --entrypoint python scanner -c \
  "import socket; socket.create_connection(('ibgateway', 4004), 5); print('gateway API port open')"
df -h ~ | tail -1                                    # about 3 GB free needed
```

**2. On EC2: start it in tmux.**

```bash
chmod +x /tmp/multi_market_run.sh
tmux new -d -s mm '/tmp/multi_market_run.sh; exec bash'
sleep 60; tail -n 5 ~/multi_market/run.log; cat ~/multi_market/status.log
```

**Expect** in `run.log`:
- `XAGUSD: 562 week chunks, 0 already done, contract conId …`;
- then every 10 chunks, `XAGUSD: 10/562 chunks this run, … bars, ETA … min`.

`status.log` gets a line per start and exit.

**Watch it, or resume it:**

```bash
tail -n 3 ~/multi_market/run.log; cat ~/multi_market/status.log   # progress, no prices
ls ~/multi_market/*/_complete.json                                  # instruments done
tmux attach -t mm                                                   # Ctrl-b d to detach
tmux new -d -s mm '/tmp/multi_market_run.sh; exec bash'             # resume after any stop
```

**Failure:**
- `exit 3`: a sealed request. Stop and tell me.
- `exit 6`: no gateway for 3 h. The runner retries after 30 min, up to 8
  times.
- `qualify` errors in `run.log` (e.g. no XAGUSD CMDTY): stop and tell me.

**3. As each instrument finishes: on the Mac**, in the checkout where this PR
is merged:

```bash
rsync -az -e "ssh -i ~/Downloads/oparli.pem" ubuntu@<ip>:multi_market/XAGUSD ~/multi_market/
.venv/bin/python tools/data/multi_market_build.py instrument XAGUSD ~/multi_market --update-manifest \
  | tee ~/multi_market/XAGUSD_report.txt
```

**4. S3 backup** (through the host, as in `docs/BACKFILL_2026_RUNBOOK.md` step 6):

```bash
scp -i ~/Downloads/oparli.pem database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet ubuntu@<ip>:multi_market/
# on EC2:
cd ~/multi_market && f=XAGUSD_ibkr_1min_20160101_20260930.parquet && SHA=$(sha256sum $f | cut -d' ' -f1)
aws s3 cp --only-show-errors --sse AES256 --metadata sha256=$SHA $f s3://<bucket>/oparli/pre-deploy/data/$f
aws s3api head-object --bucket <bucket> --key oparli/pre-deploy/data/$f --query '{bytes: ContentLength, sha256: Metadata.sha256}'
```

**5. After all six FX pairs:** run `.venv/bin/python
tools/data/multi_market_build.py dxy --update-manifest` on the Mac, then back up
`DXY_PROXY_1min_20160101_20260930.parquet` as in step 4.

**Paste back:**
- each `<SYM>_report.txt` (its first line is `INTEGRITY REPORT (no prices)`);
- the dxy lines;
- `status.log`.

I commit the manifest changes through a PR.
