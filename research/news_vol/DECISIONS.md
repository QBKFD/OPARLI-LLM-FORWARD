# news_vol (Study A): decisions Claude took

These were taken during the unattended run of 2026-11-01. The owner's rule: take
the conservative option, record it, continue. D1–D9 are the owner's, approved
2026-11-01. The N rows below fill gaps D1–D9 leave open. They are part of the
PREREG once it is merged. The owner has since approved N1 (2026-11-02).

| # | Decision | Why | Alternative |
|---|---|---|---|
| N1 | **A release at minute e is in origin t's window when t ≤ e < t+H**, i.e. when its reaction bar (the 1-min bar opening at e) closes in (t, t+H]. | The window's returns are the bars opening in [t, t+H), so the window spans (t, t+H] in bar-close time, the convention of `vol_block`'s labels. Read with e as the release instant, "(t, t+H]" puts a whole-hour release on the wrong side: FOMC at 14:00 NY would flag the window ending at 14:00, with no FOMC in it, and leave the 14:00 window with the reaction unflagged. Both readings agree for every release that isn't on a whole hour (CPI and NFP, 8:30). `results/counts.md` gives the literal reading's counts too. **APPROVED (owner, 2026-11-02):** a release counts when its reaction bar falls in the window (t ≤ e < t+H). | The literal instant reading (counted only). |
| N2 | Q1 and Q3 run at **H = 240** only. | D3 makes 240 primary, and D9 has one p for Q1 and one for Q3. Q1 at H = 60 is reported outside BH. | Both H in BH (a family of 6). |
| N3 | "Release window" in Q1 and Q3 means **any** grouped event (data or FOMC). | D6 and D8 say "release" without a type. | Data releases only. |
| N4 | **Training rows are non-crossing too**, the same for both arms. | D4 excludes crossing windows; fitting on them would mix two populations. This is sigma_compare's post-hoc amendment (a). | Fit on all rows (sigma_compare's original). |
| N5 | **Bootstrap p:** Q1 p = (1 + #{diff\* ≤ 0}) / (B + 1), the form of breakout_fade B6. Q3 two-sided p = min(1, 2·min(1 + #{ρ\* ≤ 0}, 1 + #{ρ\* ≥ 0}) / (B + 1)). Seed 20261101; blocks = UTC calendar months of t. | D6 and D8 fix the bootstrap but not the p formula; this is the form the repo already uses. | A centred (null-imposed) bootstrap. |
| N6 | **DM:** rows pooled over folds in time order, NW lag 8 at both H. | "Lag as sigma_compare" (D7): sigma_compare used 8 at both H. | Lag by H. |
| N7 | **Grouping:** a minute is FOMC if any of its rows is FOMC. The press conference (14:30 NY) is a separate FOMC event. | D2 groups same-minute releases; the press conference is 30 minutes later. No minute mixes FOMC and data (counts). | One FOMC event per meeting. |
| N8 | The **unscheduled FOMC actions** (2020-03-03 10:00 NY, 2020-03-15 17:00 NY) **stay in**. | D2 says the usd_tier1 set with a timestamp. The first is a look-ahead (it wasn't on any schedule); it is one event in about 60. The second is a Sunday at 17:00 NY, while the market is closed, so it is in no window. | Drop the first. |
| N9 | **The calendar was looked at before this PREREG was committed**, for its schema and the count of usd_tier1 rows per event and flag. | Needed to write §3. These are timestamps and names only: no value, no price. | — |
| N10 | **The synthetic check's parameters** (×4 for 30 minutes, the AR(1), the profile, the ×0.5–×2 band) were set by Claude. While the code was written, before the PREREG was fixed, it ran on made-up events (short) and once on the full period with the real event times (passed: Q1 0.88× the truth). These runs used synthetic bars and timestamps only, no real price. | The check tests machinery, not data. Its parameters are fixed in this PREREG before the formal run from the merged commit. | — |
| N11 | **The bars come from `XAUUSD.txt`** (the audit_long cache), not the clean parquet. | D1 says dev 2016–2023, and the parquet starts in 2020. The same source is used by variance_ratio and pullback_structure on the same period. | — |

## feat/news-sigma: the live fit (`fit_final.py`, 2026-11-08)

After the verdict, not part of the PREREG, and no outcome test: a model fit for live
use, as `research/sigma_compare/fit_final.py` was. The owner asked for `feat/news-sigma`
in the plan of 2026-11-08; NS1–NS6 were Claude's choices, and the owner's review the
same day changed NS3 and added NS7 and NS8.

| # | Decision | Why | Alternative |
|---|---|---|---|
| NS1 | **The bars, VolParams and training labels of the default live model `parquet2021_2023`**: the clean parquet from 2020 (warm-up), training labels from 2021-01-01, windows ending by 2024-01-01. The script refuses to write unless its base fit reproduces that model exactly (VolParams hash, β, c): it did. | One `state_at(model.params)` serves both σ's. Owner decision #12 (features/DECISIONS.md) picked this data and window for live. | Study A's own data and window (XAUUSD.txt, expanding from 2016): about 2.5× as many FOMC windows, but other VolParams, so a second state. |
| NS2 | **Study A's augmented arm, as tested:** OLS of ln(RV/P) on [1, L, S_hd, T, d_data, d_fomc] plus Duan smearing; all six terms refit. Study A's groups (D2, N7): data = CPI m/m, CPI y/y, Core CPI m/m, NFP; FOMC = statement, rate, press conference; USD, High impact, timed. | It is the design Study A validated out of sample. | Keep the base β and c fixed and fit only the two release terms. |
| NS3 | **OWNER (2026-11-08): train without the windows that cross the daily break, as Study A (N4).** A release counts when t ≤ e < t + H. Live, a crossing window gets σ by the same formula (its release flag over the window's open minutes, [t, E)) and `crosses_close`; it is outside the training rows. | Owner. | Claude's first version kept crossing rows, as the default model does: its coefficients are in `model/fit_final_result.json` (`fit_with_crossing_rows_superseded`). |
| NS4 | **The calendar's coverage is the caller's statement:** `sigma_over_news` takes `known_until` and raises when the window ends after it. | A missing or stale calendar must not read as "no release" (missing config → abstain, never default). | Treat a missing calendar as no release. |
| NS5 | **Off:** nothing calls it. `sigma_over`, the default model, the scanner, `risk/` and the agents are unchanged. **OWNER (2026-11-08): the flag stays off.** | Wiring σ into brackets or sizing is the owner's decision. | Make it the default. |
| NS6 | **`fit_final_result.json` holds in-sample descriptives only:** multipliers, the news/default variance ratio on and off releases, the superseded fits. No QLIKE or other out-of-sample comparison. | Study A and its replication tested the design; a new test would be a new study. | — |
| NS7 | **OWNER (2026-11-08): production uses the H = 240 terms only; the 60-min FOMC term is off.** The model file has H = 240 only, so `sigma_over_news(..., H=60)` raises; `sigma_over` serves 60. The H = 60 fit is in `fit_final_result.json`, not shipped. | Owner. The 60-min FOMC term rested on 24 windows. Reading "H = 240 terms only" as no 60-min news σ at all, the 60-min data term is off too. | A 60-min model with the data term only (would need its own fit). |
| NS8 | **OWNER (2026-11-08): document the shift outside news windows and the period.** Both are in the module docstring, the model's `definition`, this table and the README. | Owner. | — |

**What the shipped fit gives** (H = 240; the model file and `model/fit_final_result.json`):
- **Training:** 2021–2023 only (NS1), 14,608 non-crossing windows, 280 with a data release and 72 with FOMC (24 meetings × the 11:00, 12:00 and 13:00 NY origins).
- **Multipliers on variance:** ×2.6 (data) and ×7.7 (FOMC). They are 2021–2023's; Study A's walk-forward folds (2016 on, XAUUSD.txt) gave ×2.2–2.7 and ×5.6–7.2.
- **Outside news windows, σ shifts too:** the news model's variance is a median 0.94× the default's (σ about 3 % lower), the same with and without a crossing. All six terms are refit, and part of the release vol sat in the default model's intercept.
- **Not shipped, H = 60:** ×4.5 (data), ×16.3 (FOMC), on 70 and 24 windows.
