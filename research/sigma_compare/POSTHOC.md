# Post-hoc checks (after the verdict; not pre-registered)

**Written and committed before either check was run.** PREREG.md is unchanged.
These checks gate the datafeed change: HAR goes into `sigma_over` only if both
pass. Results go in POSTHOC_RESULT.md.

**Why these two.** The overnight instruction refers to "the post-hoc checks"
without defining them, and no definition exists in the repository or the notes
(DECISIONS.md #9). They are chosen to target two weaknesses of the verdict
(REPORT.md §2 and §8):
- the verdict used per-fold refits on 2007–2020, but production uses one frozen
  model on later data;
- it said nothing about calibration on the parquet era, the data closest to
  live. That calibration also chooses the default fit.

**What has already been seen.** The study's DEV results (walk-forward fits on
the parquet from 2021) are already in REPORT.md, including HAR's mean RV/h on
DEV: 0.994 at 60 min and 0.982 at 240 min. So P2's SHORT family is partly
seen. P1 (frozen model) and P2's LONG family have not been computed.

**Rules shared by both checks.**
- Pre-2024 data only. The parquet's 2024-01..2026-01 rows are spent and are not
  read; ≥ 2026-10-01 is sealed. The same asserts as the study apply.
- Targets, windows (next H market-open minutes), the row rules, QLIKE, and DM
  (Newey–West lag 8) are as in PREREG.md.
- Only non-crossing windows, H ∈ {60, 240}.

## P1: frozen model on a later era

- **Fit once** on `XAUUSD.txt` from 2007-01-01 to 2020-12-31 (CONF loader):
  `VolParams` and HAR (β, smearing c). The training rows are every row whose
  window ends by 2021-01-01, using the same code as `fit_final.py`.
- **Freeze it.** Score it on the parquet's labels from 2021-01-01 to
  2023-12-31. Features come from the frozen `VolParams` over the parquet
  2020–2023, and nothing is refit.
- **Compare** with the plug-in, which uses the same frozen `VolParams`.
- **Pass:** at both H, HAR's mean QLIKE is below the plug-in's, and the
  one-sided DM p-value is < 0.05 after Benjamini–Hochberg over the 2 tests.

## P2: parquet calibration (also chooses the default fit)

- **Two fit families, walk-forward** over the 8 DEV test quarters
  (2022Q1–2023Q4), so each is out of sample:
  - **LONG:** VolParams and HAR fitted on `XAUUSD.txt` from 2007-01-01 to
    s_k − 24h (expanding). This is the shipped "txt" fit's recipe.
  - **SHORT:** VolParams and HAR fitted on the parquet from 2021-01-01 to
    s_k − 24h. These are the study's DEV folds, and the shipped "parquet" fit's
    recipe.
- **Rows:** the test labels that are valid, finite and non-crossing in both
  families. The two files hold the same bars from 2020.
- **Metrics,** per family and H, on HAR's forecast h:
  - mean RV/h, which is variance calibration (1 = calibrated for QLIKE);
  - the RMS z-ratio √mean(R²/h), with R = ln(C_end / close_ref), i.e. the move
    over the window against σ. This is the `tools/sigma_calibration`
    definition: C_end is the close of the last bar opening before the window
    end, and close_ref is the close of the last bar opening before t.
- **Favoured family:** the one with the smaller mean of |ln(metric)| over its 4
  numbers (2 metrics × 2 H). If the difference is < 0.005, LONG is favoured as
  the conservative default: it is the fit the study validated.
- **Pass:** all 4 numbers of the favoured family are within [0.9, 1.1]. That is
  the "close to 1" band fixed in `tools/sigma_calibration` before its run.
- **Reported, not part of the gate:** the plug-in's metrics and each family's
  QLIKE against the plug-in on the same rows.

## Shipped fits if both checks pass

- **"txt2007_2023":** `model/sigma_har_v1.json` (step 3, unchanged).
- **"parquet2021_2023":** HAR fitted on the parquet, training from 2021-01-01
  to 2023-12-31 (2020 as warm-up), rows < 2024-01-01. Same code as
  `fit_final.py`.
- **Default:** the final fit of P2's favoured family.
