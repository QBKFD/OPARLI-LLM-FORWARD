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
| `historical_news/` | economic event calendars (`events_utc.parquet`, from the Forex Factory CSVs that `tools/scraper_fxfac` scrapes); `DATA_NOTES_events.md` describes them | by hand | nothing in the current code |

**Manifest:** `backend/datafeed/store/manifest.json` records, for `XAUUSD.txt`
and the clean parquet, the source, the cleaning steps, the date range, the row
count and the SHA-256. Check the files on this machine with
`python -m backend.datafeed.store.manifest check` (`--content` also checks the
parquet's bars). The datafeed's replay source refuses a file with a listed name
that doesn't match. The parquet can be rebuilt from `XAUUSD.txt`
(`tools/data/build_clean_parquet.py`, checked by content hash).
`tools/data/make_fixture.py` cuts the selftest's windows out of the parquet
(`docs/decisions/feat-data-manifest.md`).

**Git:** the data files are ignored by `.gitignore`:
- `ohlcv_data/*.parquet`, `*.csv`, `*.txt` and `*.zip`;
- `historical_news/*.csv`, `*.parquet` and `*.txt`;
- every `*.sqlite` and `*.parquet` directly in `database/`;
- all of `snapshots/` (written by `tools/data/fetch_data_xauusd.py`).

Only this README and `historical_news/DATA_NOTES_events.md` are tracked.

**Not ignored:** a file in another folder or of another type (e.g. a CSV
directly in `database/`). Check `git status` before committing.

The sealed period (`research/SEALING.md`) applies to these files: data from
2026-10-01 on may not be read by any study before the final strategy test.
