# database/: local data, not in git

`database/` holds data files on the Mac. It is not a Python package and
stays at the repo root, so every path below is the same on the Mac and on
the EC2 host. On the host the scanner runs in Docker and uses its own volumes
instead (`/data`, `/datafeed`, see `deploy/scanner/docker-compose.yml`).

| Path | What | Written by | Read by |
|---|---|---|---|
| `ohlcv_data/XAUUSD_1min_UTC_clean.parquet` | 1-min XAUUSD bars, 2020-01-01 to 2026-01-30 | a one-off export | the scanner's `--date` replay and `--selftest`, the datafeed's replay source, tests, research |
| `ohlcv_data/XAUUSD.txt` | 1-min bars from 2007 | a one-off download | research (`sigma_compare`, `vol_features`) |
| `scanner_decisions.sqlite` | the scanner's decision journal, when run locally | the scanner (default of `$SCANNER_DB`) | the journal summary (`python -m backend.scanner.journal.summary`) |
| `datafeed.sqlite` | a local datafeed store | the recorder (`$DATAFEED_DB` in `deploy/.env.example`) | the stream scanner, agents |
| `historical_news/forex_factory_calendar_<year>.csv` | the scraped Forex Factory calendars, 2015–2026, in the manifest as "unused by any study" (licence unclear: never committed) | `tools/scraper_fxfac` (by hand) | `tools/data/macro_calendar.py` |
| `historical_news/macro_calendar_utc.parquet` | the clean macro calendar: UTC times, parsed values, surprises ([docs/MACRO_CALENDAR.md](../docs/MACRO_CALENDAR.md)); "unused by any study" | `tools/data/macro_calendar.py build` | no study yet (`research/data_guard.py`) |
| `historical_news/events_utc.parquet` | an earlier calendar build, described by `DATA_NOTES_events.md` | by hand | nothing in the current code |

**Manifest:** `backend/datafeed/store/manifest.json` records every price file
code reads: the source, the cleaning steps, the date range, the rows and the
hashes. `XAUUSD.txt` is IBKR data (XAUUSD CMDTY, 1 min, UTC), **licensed for
the owner's own use only, so no real bar is ever committed** (the repo is
public). Check the files on this machine with
`python -m backend.datafeed.store.manifest check` (`--content` also checks the
parquet's bars). The datafeed's replay source refuses a file with a listed name
that doesn't match.
- `tools/data/build_clean_parquet.py` rebuilds the clean parquet from
  `XAUUSD.txt`, checked by content hash.
- Fixtures:
  - `ohlcv_data/XAUUSD_selftest_fixture.parquet` holds the real bars of the
    selftest's windows. It is local and git-ignored (`tools/data/make_fixture.py`).
  - `tests/fixtures/xauusd_synthetic_selftest.parquet` holds made-up bars. It
    is the only one in git, for CI (`tools/data/make_synthetic_fixture.py`).
- A backfill of 2026-02-01 .. 2026-09-30 from IBKR is approved and prepared,
  not run yet (`docs/BACKFILL_2026_PROPOSAL.md`). Its file,
  `ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet`, is marked "unused
  by any study". `research/data_guard.py` refuses it to any study whose
  committed PREREG doesn't name it.

**Git:** the data files are ignored by `.gitignore`:
- `ohlcv_data/*.parquet`, `*.csv`, `*.txt` and `*.zip`;
- `historical_news/*.csv`, `*.parquet` and `*.txt`, and the CSVs and
  parquets of any `historical_news*/` sibling (e.g. a `historical_news_1/` copy);
- every `*.sqlite` and `*.parquet` directly in `database/`;
- all of `snapshots/` (written by `tools/data/fetch_data_xauusd.py`).

Only this README and `historical_news/DATA_NOTES_events.md` are tracked.

**Not ignored:** a file in another folder or of another type (e.g. a CSV
directly in `database/`). Check `git status` before committing.

The sealed period (`research/SEALING.md`) applies to these files: data from
2026-10-01 on may not be read by any study before the final strategy test.
