# variance_ratio: decisions taken without the owner

Unattended run, 2026-10-31/11-01. The owner's rule: take the most
conservative reading, write it down, keep going.

| # | Decision | Why | Alternative |
|---|---|---|---|
| D1 | **The analysis plan is committed as `PREREG.md`, marked as a descriptive plan, not a pre-registration.** It names the four files. | `research/data_guard.py` lets a study read a guarded file (silver, EURUSD, USDJPY) only when a committed `PREREG.md` names it. The brief says to go through data_guard and also to write no new PREREG for another study. The plan fixes every choice before any read. | Read gold only (the one unguarded file) and skip the others. |
| D2 | **The plan is not merged to main before the run.** The standing rule (a PREREG merged before any outcome run) is about hypothesis tests. This analysis has none: the owner asked for it explicitly, marked EXPLORATORY, no pass/fail, and said never merge. | The run would otherwise be impossible tonight. Recorded so the owner can judge it. | Wait for a merge. |
| D3 | **The run counts in `ATTEMPTS.json` as an outcome analysis of kind `descriptive_analysis`.** | It reads returns; counting it is the conservative side. | A separate counter. |
| D4 | **The daily break and every other gap of more than 15 minutes exclude a return.** So **metals have no valid 24-hour window**: every one spans the daily break. They also have fewer 8-hour windows. | The brief: exclude returns spanning a gap. Read literally, the daily break is one. | Join the hours on either side of the break (trading-time returns). |
| D5 | Sessions use fixed UTC hours with no DST shift: Asia [22, 7), London [7, 12), Overlap [12, 16), NewYork [16, 22). Every hour belongs to one session. | The brief names four sessions without hours. Fixed UTC hours are reproducible and match the hour split. | DST-aware sessions. |
| D6 | **σ:** gold's pinned HAR fit (`parquet2021_2023`) on every market's own bars, read at the window's start. Terciles come from each market's own dev distribution. | The brief says "pinned model"; breakout_fade B4 did this for silver. Terciles are ordinal, so the model's scale on FX doesn't matter. The cut-offs see the whole dev period, which is fine for a descriptive split. | A fit per market. |
| D7 | **News day** = a UTC date with a High-impact USD release of the repo's frozen usd_tier1 set (CPI ×3, FOMC ×3, NFP). Calendar: PR #60 at 12f65c6, SHA-256 pinned; its anchor check passed. | It is an existing frozen definition; none is invented. Claims and PCE aren't in it. | All five families of PR #60. |
| D8 | **The 5-min check runs on the whole-sample cell only**, with q = 12k. | It is a check of the 1h base, not a second set of splits. | Every split at 5 min. |
| D9 | **BH runs over every cell with a p-value**, both bases and every market, in one family. | It is the largest family, so the most conservative. | A family per market. |
| D10 | **No warm-up data before 2016.** Starts without a HAR state at the beginning of 2016 are in no σ cell, and they are counted. | Dev only, as the brief says. | Read late 2015 for the warm-up. |
| D11 | Trade dates roll at 17:00 New York. A short day has fewer than 1000 1-min rows, as the brief says. | The FX and metals daily roll. | 18:00 (`audit_long.session_date`). |
