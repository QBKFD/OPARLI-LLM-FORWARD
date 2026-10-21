# chore/gitignore-data

Fixes F3 of [refactor-backend-layout.md](refactor-backend-layout.md): data in
`database/` that git did not ignore.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| GI1 | Two lines in `.gitignore`: `database/snapshots/` (the whole folder; `tools/data/fetch_data_xauusd.py` writes `XAUUSD_COMPLETE.csv` there) and `database/*.parquet` (parquet files directly in `database/`, not in subfolders). Nothing else changes; `database/README.md` now lists them. Checked: no tracked file becomes ignored (`git ls-files -ci --exclude-standard` is empty), and the two paths are ignored (`git check-ignore`). | Only the two cases found; ignoring all of `database/` would also hide the tracked README and `historical_news/DATA_NOTES_events.md`. | `database/*` with `!` exceptions for the tracked files. |
