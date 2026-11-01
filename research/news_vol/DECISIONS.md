# news_vol (Study A): decisions Claude took

These were taken during the unattended run of 2026-11-01. The owner's rule: take
the conservative option, record it, continue. D1–D9 are the owner's, approved
2026-11-01. The N rows below fill gaps D1–D9 leave open. They are part of the
PREREG once it is merged.

| # | Decision | Why | Alternative |
|---|---|---|---|
| N1 | **A release at minute e is in origin t's window when t ≤ e < t+H**, i.e. when its reaction bar (the 1-min bar opening at e) closes in (t, t+H]. | The window's returns are the bars opening in [t, t+H), so the window spans (t, t+H] in bar-close time, the convention of `vol_block`'s labels. Read with e as the release instant, "(t, t+H]" puts a whole-hour release on the wrong side: FOMC at 14:00 NY would flag the window ending at 14:00, with no FOMC in it, and leave the 14:00 window with the reaction unflagged. Both readings agree for every release that isn't on a whole hour (CPI and NFP, 8:30). `results/counts.md` gives the literal reading's counts too. **This is the one reading of an approved decision; the owner should check it first.** | The literal instant reading (counted only). |
| N2 | Q1 and Q3 run at **H = 240** only. | D3 makes 240 primary, and D9 has one p for Q1 and one for Q3. Q1 at H = 60 is reported outside BH. | Both H in BH (a family of 6). |
| N3 | "Release window" in Q1 and Q3 means **any** grouped event (data or FOMC). | D6 and D8 say "release" without a type. | Data releases only. |
| N4 | **Training rows are non-crossing too**, the same for both arms. | D4 excludes crossing windows; fitting on them would mix two populations. This is sigma_compare's post-hoc amendment (a). | Fit on all rows (sigma_compare's original). |
| N5 | **Bootstrap p:** Q1 p = (1 + #{diff\* ≤ 0}) / (B + 1), the form of breakout_fade B6. Q3 two-sided p = min(1, 2·min(1 + #{ρ\* ≤ 0}, 1 + #{ρ\* ≥ 0}) / (B + 1)). Seed 20261101; blocks = UTC calendar months of t. | D6 and D8 fix the bootstrap but not the p formula; this is the form the repo already uses. | A centred (null-imposed) bootstrap. |
| N6 | **DM:** rows pooled over folds in time order, NW lag 8 at both H. | "Lag as sigma_compare" (D7): sigma_compare used 8 at both H. | Lag by H. |
| N7 | **Grouping:** a minute is FOMC if any of its rows is FOMC. The press conference (14:30 NY) is a separate FOMC event. | D2 groups same-minute releases; the press conference is 30 minutes later. No minute mixes FOMC and data (counts). | One FOMC event per meeting. |
| N8 | The **unscheduled FOMC actions** (2020-03-03 10:00 NY, 2020-03-15 17:00 NY) **stay in**. | D2 says the usd_tier1 set with a timestamp. The first is a look-ahead (it wasn't on any schedule); it is one event in about 60. The second is a Sunday at 17:00 NY, while the market is closed, so it is in no window. | Drop the first. |
| N9 | **The calendar was looked at before this PREREG was committed**, for its schema and the count of usd_tier1 rows per event and flag. | Needed to write §3. These are timestamps and names only: no value, no price. | — |
| N10 | **The synthetic check's parameters** (×4 for 30 minutes, the AR(1), the profile, the ×0.5–×2 band) were set by Claude. A short version ran on made-up events while the code was written, before the PREREG was fixed. | The check tests machinery, not data. Its parameters are fixed in this PREREG before the formal run from the merged commit. | — |
| N11 | **The bars come from `XAUUSD.txt`** (the audit_long cache), not the clean parquet. | D1 says dev 2016–2023, and the parquet starts in 2020. The same source is used by variance_ratio and pullback_structure on the same period. | — |
