# Proposal: IBKR backfill of XAUUSD 1-min bars, 2026-02-01 .. 2026-09-30

**Status: proposed, not built, not run.** It needs the owner's go (the
questions at the end).

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
| Requests | `durationStr="1 D"`, one per calendar day, `endDateTime` = the next day 00:00:00 UTC, walking backwards. About 170 days plus the overlap probes, about 230 requests in all. Paced at 50 per 10 minutes and never the same request twice within 15 s: **about 50 minutes** |
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
   - the seam: the first backfilled day against `XAUUSD.txt`'s last day
     (price level, informational only).
4. **Manifest entry:** bytes, sha256, content_sha256, rows and range. Also the
   source (provider, contract and conId, whatToShow and the overlap table, bar
   size, `formatDate=2`, request times, client id, number of requests), every
   check result, `"in_git": false`, `"status": "unused by any study"`, and the
   licence (own use only).
5. **Copy** to S3 `oparli/pre-deploy/` (never expires; the host role can
   write it), optional.

## The tool (to be written on a go)

`tools/data/backfill_ibkr.py`, in the pattern of `backend/options`:
- a `--plan` dry run that prints every request and connects to nothing;
- step 1 as a separate `--probe-overlap` run, so its table can be read
  before the download;
- tests on a fake IBKR client (pacing, the 2026-10-01 refusal, the UTC
  session check on a shifted series, the overlap rule, 10197 and 162 stops).
  No network.

## Owner questions

1. Go or no go. If go: where does it run? It could run on the EC2 paper
   gateway, in the options image, after the 05:00 snapshot; or on a local
   TWS. Also: the 99.5 % threshold, and the 10-day overlap.
2. Should the file be kept off-limits in code too, with `research/sealing.py`
   refusing it by name until a PREREG names it? Or is the manifest status
   enough?
