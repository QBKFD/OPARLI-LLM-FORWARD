# feat/datafeed-spread

Built 2026-10-15 as WIP (3a9e0d9, b05bafe), finished 2026-10-20 in the ops
terminal: bid/ask spread per live 1-min bar, to measure the trading cost c of
the agents PREREG. `origin/main` was merged in (no rebase); no conflicts.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| SP1 | Built on the existing WIP branch, as the owner asked, after merging `origin/main` (f654005). Main had no migration 4, so the WIP's number stands. | Keeps the reviewed history; no renumbering. | A fresh branch off main. |
| SP2 | Spread = ask − bid **as quoted** for every quote that sets the midpoint (so `n_quotes == n_ticks`); a crossed quote stays negative, not clipped. Per bar: mean, median, max, n_quotes. | No silent cleaning of the very cost being measured; crossed quotes show up and can be counted. | Clip at 0; drop crossed quotes. |
| SP3 | Four nullable columns on `bars` (migration 4), not a side table, and outside `VALUE_FIELDS`: a redelivered bar with other spread stats is a duplicate, not a conflict; `bars_before` doesn't return them. | Existing bar values, their conflict rules and every reader stay as they were. | A `bar_spreads` table; spread in the conflict comparison. |
| SP4 | The message fields are optional and come as a set of four; the recorder rejects a partial or non-finite set (stage `invalid`). History and replay messages carry none: **replay parity checked**, 30,087 replay messages for May 2025 identical to `main`. | Only additive change on the bus; no consumer has to change. | Always-present nullable fields (changes every message). |
| SP5 | `tools/spread_by_hour.py`: per hour of day (UTC or `--tz`) bars, quotes, quote-weighted mean, median of bar medians, p90 of bar means, max. Read-only; a missing store or one from before migration 4 exits 2 with the reason (it never migrates). | Production's store is still at version 3 until the recorder restarts; the tool must not write it. | Migrate on open. |
| SP6 | mypy: the tool passes `--strict`; the three findings in `xauusd_bars.py` predate the branch (one sits on the `Bar(...)` line the WIP re-wrapped, unchanged). | No unrelated edits. | Retype the module. |
