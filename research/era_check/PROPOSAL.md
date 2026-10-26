# Proposal: does sigma_compare's result hold on 2012–2020 only? (not run)

**Status: proposed 2026-10-26, not run.** Nothing in it has been computed. Every setting below is a proposal for
the owner. Running it would be a post-hoc check on seen data (2012–2020 is part of sigma_compare's CONF period),
so it can't change sigma_compare's verdict. It can only say whether the verdict leans on the 2007–2011 era.

## Why

- **The eras differ.** `XAUUSD.txt` has three eras (research/README.md, "Data eras in XAUUSD.txt"). 2007–2011 have
  no daily break, trade through CME holidays and have Friday-evening sessions (OBSERVED in
  `research/squeeze_oco/01_vol_state.ipynb` at commit `338c180`).
- **The verdict was fitted with that era.** sigma_compare chose HAR over the plug-in on CONF 2007–2020. Its folds
  train on expanding windows from 2007-01-01 (CITED: `research/sigma_compare/run_sigma.py:53-60`). Its era split
  (2008–2012 / 2013–2020, `run_sigma.py:58`) reports the test rows by era, but every fold's VolParams and β were
  fitted with 2007–2011 data in them (INFERRED from the expanding window).
- **Two things could differ in that era (INFERRED).** No daily break means more open minutes in the seasonal
  profile and in P(t, H). Holiday and Friday-evening trading put low-activity minutes into the training data.
- **What already points one way.** CITED: in the 2013–2020 era HAR still beats the plug-in (DM −17.3 at 60 min,
  −10.9 at 240 min; `research/sigma_compare/REPORT.md:24-25`). That era's test rows are 2012+-convention data,
  but its fits are not.

## The check (proposed settings)

| Item | Proposal | Reason |
|---|---|---|
| Code | `research/sigma_compare/run_sigma.py` unchanged, called from a new `research/era_check/run.py` that passes its own PERIODS entry | The study's code stays as it was run; only the period differs |
| Load and training start | 2012-01-01 | The first year of the 2012+ convention |
| Test fold edges | half-years from 2014-01-01 to 2021-01-01 (14 folds) | Two years of training before the first fold, as sigma_compare had (2007 start, first edge 2009-01-01, `run_sigma.py:57`) |
| Candidates, metrics, embargo, coverage, NW lag, BH q, the half-life grid | as in `run_sigma.py:38-46` | The same method, so any difference is the data's |
| What it reports | QLIKE plug-in / HAR / EWMA on non-crossing windows; DM HAR vs plug-in; folds won; HAR's β per H against the study's; mean RV/h | Directly comparable with `REPORT.md:15-25` |
| Reading (fixed before running) | "Holds" if HAR beats the plug-in on QLIKE at both H with BH p < 0.05 and in most folds; otherwise "doesn't hold on 2012–2020 alone", reported next to the original | The study's own rule, without the fold count; a 14-fold run isn't the 24-fold study |
| Runs | once, with the attempt recorded | Post-hoc, on seen data |
| Seal | `sealing.assert_before_global_seal` plus the study seal (`run_sigma.py:80-86`), and refuse < 2012-01-01 | Reuses the guards |

## What it would decide, and what it wouldn't

- **If it holds:** nothing changes. The default model (`parquet2021_2023`) never used 2007–2011, so it's
  unaffected either way.
- **If it doesn't hold:** sigma_compare's verdict stands as pre-registered, but it rests on the 2007–2011 era.
  These would then be the owner's calls: whether the txt fit (`txt2007_2023`) stays selectable, and whether the
  vol_features rounds deserve the same check.
- **Out of scope:** vol_features r2/r3, trend_features, regime_v2 and levels also used 2007–2011 (README rows
  2–7). Each would need its own check.
