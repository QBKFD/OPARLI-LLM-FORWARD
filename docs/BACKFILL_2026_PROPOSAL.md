# Proposal: IBKR backfill of XAUUSD 1-min bars, 2026-02-01 .. 2026-09-30

**Status: approved by the owner 2026-10-22 (go; 99.5 % confirmed); tools
built and tested on a fake IBKR client; not run.** The owner runs it on the
EC2 paper gateway (section "Run it"). Claude never touches EC2.

## Why and what

`XAUUSD.txt` ends on 2026-01-30 21:59 UTC. The sealed period starts on
2026-10-01. The gap in between has no local data (docs/ARCHITECTURE_CURRENT.md,
section (e)). This fills it from the same source, as a **separate file that no
study uses**:

- `database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet`: the clean
  parquet's columns and dtypes, UTC, git-ignored (IBKR data, own use only).
- Its own manifest entry, with `"status": "unused by any study"`. A study may
  read it only once its PREREG names it.
- `XAUUSD.txt` and the clean parquet are not touched or extended.

## Request

| | |
|---|---|
| Contract | `Contract(symbol="XAUUSD", secType="CMDTY", exchange="SMART", currency="USD")`, qualified first; its conId is recorded |
| whatToShow | **the one that reproduces `XAUUSD.txt`**: it isn't recorded anywhere, so step 1 picks it on the overlap, by a rule fixed here |
| Bar size | `1 min`, `useRTH=False` |
| Timezone | `formatDate=2` (epoch seconds, UTC), so the TWS login's time zone can't leak in. That is how `XAUUSD_COMPLETE.csv` broke on 2026-02-01: it used `formatDate=1`. Stored as `datetime64[ns, UTC]` |
| Range | trade days 2026-02-01 .. 2026-09-30. The tool refuses any request or row at or after 2026-10-01 (`SEAL_START`) |
| Requests | `durationStr="2 D"` (IBKR counts D in trading sessions, so "1 D" ending at 00:00 UTC returns only the evening session; changed 2026-10-28), one per calendar day, `endDateTime` = the next day 00:00:00 UTC, walking backwards. About 170 days plus the overlap probes, about 230 requests in all. Paced at 50 per 10 minutes and never the same request twice within 15 s: **about 50 minutes** |
| Connection | read-only, client id 63 (61 probe, 62 options snapshot), stops on error 10197 or 162 (no data), never retries in a loop |

## Steps and validation (all decided before any data is read)

1. **Pick whatToShow on the overlap.** Request the last 10 trade days of
   `XAUUSD.txt` (2026-01-19 .. 2026-01-30) as MIDPOINT, BID, ASK and TRADES
   (TRADES may be refused for CMDTY; that is recorded). For each, take the
   share of minutes where open, high, low and close all equal `XAUUSD.txt`
   (to 1e-6), and whether VOL matches.
   - The chosen type is the one with the highest share, and the share must be
     **≥ 99.5 %**.
   - If none reaches it, **stop: no backfill**, and report the table.
   - If the prices match but VOL matches for no type, store that type's volume
     (or NaN for −1) and record that `XAUUSD.txt`'s VOL can't be reproduced.
   - The table goes into the manifest entry, and `what_to_show` in
     `XAUUSD.txt`'s entry is filled in from it.
2. **Download** the range with the chosen type.
3. **Checks on the result.** Every one is recorded, and any failure means the
   file is written as `<name>.rejected.parquet` with the reason, not under its
   real name:
   - minute grid; no duplicate timestamps; no row ≥ 2026-10-01;
   - bad OHLC bars (high < max(open, close) or low > min(open, close)) counted,
     **never fixed**;
   - **UTC check against the session:**
     - the last bar before the daily break is at 21:59 UTC up to 2026-03-07,
       and at 20:59 UTC from 2026-03-08 (US DST);
     - the first bar after the break is at 23:00 and 22:00 UTC respectively;
     - both must hold on ≥ 90 % of full days in each season. This is the check
       that catches a 2026-02-01-style shift;
   - per-day completeness: the share of days with ≥ 90 % of the expected
     minutes (the audit's U2), and the holiday and short days listed;
   - the seam: the first backfilled bar's time against `XAUUSD.txt`'s last
     bar (times only; the price-level gap was dropped on 2026-10-27, because
     the period is a holdout nobody has read);
   - gaps inside sessions, exact and conflicting duplicates, rows per trade
     date, and timestamps tz-aware UTC. A best whole-hour shift other than 0
     is a failure.
4. **Manifest entry:** bytes, sha256, content_sha256, rows and range. Also the
   source (provider, contract and conId, whatToShow and the overlap table, bar
   size, `formatDate=2`, request times, client id, number of requests), every
   check result, `"in_git": false`, `"status": "unused by any study"`, and the
   licence (own use only).
5. **Copy** to S3 `oparli/pre-deploy/` (never expires; the host role can
   write it), optional.

## The tools

- `tools/data/backfill_ibkr.py`, the download, runs on EC2. It is one
  self-contained file (stdlib + ib_insync), fed on stdin to the scanner image
  already on the host, as the GEX probe was. Modes:
  - `--plan`: prints the requests, connects to nothing;
  - `--probe-overlap`: step 1's 44 requests, about 9 min;
  - `--download TYPE`: 208 requests, about 42 min.

  It writes JSON lines to stdout and nothing inside the container. A file
  without its final `end` line is refused later.
  - Exit codes: 3 a bar in the sealed period; 4 error 10197; 5 pacing
    violation. It never retries.
  - Error 162 "no data" (e.g. TRADES for CMDTY, or a holiday) is recorded, not
    fatal.
- `tools/data/backfill_build.py` runs on the Mac, where `XAUUSD.txt` is.
  - `pick` applies step 1's rule.
  - `build` applies step 3's checks, writes the parquet (or `.rejected`), and
    with `--update-manifest` fills in its entry. It also fills `XAUUSD.txt`'s
    `what_to_show`, on a pass only.
  - The entry holds counts, shares, times and hashes, never a price, so it can
    be committed.
- `tests/datafeed/test_backfill.py`, all on a fake IBKR client: the plan, the
  pacing, UTC requests, error 162 recorded, the 10197 / pacing / seal / naive-time
  stops, the pick and its 99.5 % stop, a passing build, a 5-hour-shifted
  download rejected by the session check, an incomplete file, conflicting
  duplicates, and no price in the entry. No network.
- **Research guard:** `research/data_guard.py`. A study may read the file only
  once its committed PREREG names it (decided by the experiment registry when
  it exists, see that module).

## Run it (the owner)

**The current step-by-step is `docs/BACKFILL_2026_RUNBOOK.md`** (2026-10-27).
It adds the gateway check, a hard refusal past 2026-09-30, no-price checks of
the JSONL, the integrity report and the S3 copy. The steps below are the
original plan.

**When:** a weekday, 07:00–20:00 UTC. That is after the 05:00 options
snapshot, away from the gateway's 17:15 New York restart, and not on Sunday
(the weekly cold restart). Log the live IBKR account out everywhere first
(error 10197). Don't restart the scanner during the run: its history load
shares IBKR's pacing (the backfill keeps to 50 of the 60 requests allowed per
10 minutes).

**1. On the Mac:** copy the downloader to the host.

```bash
cd <repo checkout with this branch, or main after the merge>
scp -i ~/Downloads/oparli.pem tools/data/backfill_ibkr.py ubuntu@<ip>:/tmp/backfill_ibkr.py
```

**2. On EC2: the overlap probe** (about 9 min).

```bash
mkdir -p ~/backfill && cd ~/oparli/deploy/scanner
python3 /tmp/backfill_ibkr.py --plan                      # 44 + 208 requests; connects to nothing
docker compose run --rm -T --no-deps --entrypoint python scanner - --probe-overlap \
    < /tmp/backfill_ibkr.py > ~/backfill/overlap.jsonl
echo "exit $?"; tail -c 200 ~/backfill/overlap.jsonl     # exit 0, and the last line is {"kind":"end",...}
```

**3. On the Mac: pick the type.** Run it in a checkout with
`database/ohlcv_data/XAUUSD.txt` and the repo's venv (pandas, pyarrow).

```bash
mkdir -p ~/backfill
scp -i ~/Downloads/oparli.pem ubuntu@<ip>:backfill/overlap.jsonl ~/backfill/
python tools/data/backfill_build.py pick ~/backfill/overlap.jsonl --out ~/backfill/pick.json
```

It prints one line per type, then either `picked <TYPE>` or
`STOP, no backfill`. If it stops, the backfill ends there; send me the table.

**4. On EC2: the download** (about 42 min), with the picked type:

```bash
cd ~/oparli/deploy/scanner
docker compose run --rm -T --no-deps --entrypoint python scanner - --download <TYPE> \
    < /tmp/backfill_ibkr.py > ~/backfill/download_<TYPE>.jsonl
echo "exit $?"; tail -c 200 ~/backfill/download_<TYPE>.jsonl
```

- On exit 4 (10197): log out, then rerun step 4.
- On exit 5 (pacing): wait 10 minutes, then rerun step 4.
- A rerun starts from scratch and overwrites the file.

**5. On the Mac: validate and write.**

```bash
scp -i ~/Downloads/oparli.pem ubuntu@<ip>:backfill/download_<TYPE>.jsonl ~/backfill/
python tools/data/backfill_build.py build ~/backfill/download_<TYPE>.jsonl --pick ~/backfill/pick.json --update-manifest
git diff --stat backend/datafeed/store/manifest.json      # only the manifest changed
```

- **On a pass:** `database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet`
  is written (git-ignored), its manifest entry replaces the planned one, and
  `XAUUSD.txt`'s `what_to_show` is filled in.
- **On a failure:** `...rejected.parquet` is written, with its own entry and
  the reasons.
- Either way, the manifest change is safe to commit (no prices), on a branch
  and through a PR as usual. I can do that from the files' paths.

**6. Clean up the host:**

```bash
rm -rf ~/backfill /tmp/backfill_ibkr.py
```

The JSONL files are IBKR data: keep them off any shared or public place.

## Owner decisions (2026-10-22)

1. Go, on the EC2 paper gateway, with the 99.5 % threshold. The owner runs
   the commands.
2. Research code refuses the file by name until a PREREG names it
   (`research/data_guard.py`). It reads the experiment registry once that
   exists.
