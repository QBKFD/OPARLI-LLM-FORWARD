# Decisions log

These are ambiguities in the task, and the option chosen for each. Entries 1–15 were
written before any result was computed. Later entries are marked with when they
were made.

## Before any results

1. **Branch base.** `research/vol-features` was created from the current HEAD of
   `pipeline-fixes-and-cleanup` (875c640), which is the working branch.
   `regime_classifier/` is identical on that branch and on `main`. The
   uncommitted change to `notebooks/backtest.ipynb` stays out of every commit.

2. **Looking at the data before pre-registering.** While checking the parquet's
   schema, `head(3)` and `tail(3)` were printed. The tail shows January 2026
   prices. No statistic was computed on any data before PREREG.md was committed,
   and the study itself loads only timestamps < 2024-01-01, using a pyarrow
   filter at read time.

3. **Python environment.** `backend/venv/bin/python3` (Python 3.13, numpy 2.2.6,
   pandas 2.3.3, scipy 1.17.1, matplotlib 3.10.8) is used read-only. Nothing was
   installed. statsmodels is not available, so OLS, the Newey–West variance and
   the Diebold–Mariano test are implemented in numpy/scipy (about 20 lines, with
   tests).

4. **Importing the existing feature code.** `regime_classifier/__init__.py`
   imports `regime_classifier.py`, which needs `jumpmodels`, `scikit-learn` and
   `joblib`. None of them are installed. So the study registers a bare package
   object with the real directory as its path and imports
   `regime_classifier.regime_features` and `regime_config` directly. This runs the
   repository's unmodified code without executing `__init__`. A test checks that
   the volatility block computed this way is identical to the columns of
   `RegimeFeatureEngine.compute()`.

5. **Only the volatility methods are called**, not the full `compute()`, which
   would also run the slow Hurst, variance-ratio and autocorrelation loops. The
   three rv ratios are three inline lines in `compute()`, and they are copied
   verbatim. The exactness test above covers them.

6. **Timezone bug.** It doesn't apply. The known bug is in `XAUUSD_COMPLETE.csv`
   (UTC switching to US-Eastern on 2026-02-01), which is outside this data and
   outside the sealed window. The clean parquet is UTC throughout. The only
   volatility feature that depends on the clock is `rv_adjusted` (hour-of-day
   buckets), and it gets UTC hours as the code intends.

7. **13 comparison features, not ~11.** The code's volatility block also contains
   `atr_percentile`, so M1 uses all 13 (see PREREG).

8. **Gap handling for the existing features.** They are computed exactly as the
   classifier computes them, on the 1H bars with no gap exclusion, because the
   existing code has none and the question is about the existing features as
   they are. The new features and the HAR/targets use gap-excluded returns, as
   pre-registered. Whatever part of the difference between M1 and M2 comes from
   gap handling is part of the comparison. It can be checked as an exploratory
   amendment.

9. **Target coverage rule.** "Next 240/1440 minutes" is clock time, as the
   alignment test requires. Windows that span the weekend would give a partial
   sum that only a calendar variable could predict, so the target is NaN when
   less than 90 % of the minutes have a valid return. y24 windows always contain
   the ~60-minute daily break (95.8 % coverage) and are kept. y4 windows that
   overlap the break are dropped. Target windows that extend to or past
   2024-01-01 are set to NaN explicitly.

10. **HAR windows in trading time.** "Past 1H, 24H and 120H" means the last 1, 24
    and 120 1H bar rows (a trading day and a trading week, as in Corsi's
    day/week components). Clock time would cover a weekend and give a
    "week" of about 3 trading days.

11. **Rolling windows count bar rows** (trading time) for all features, like the
    existing code. log_gk60 and wick60 need all 60 bars. rv_n uses the existing
    min_periods = max(5, n // 2).

12. **QLIKE forecast.** The models forecast log RV. The variance forecast is
    exp(ŷ + s²/2), a log-normal mean correction using the training residual
    variance. Without it, exp(ŷ) is a median forecast and QLIKE penalises it
    unevenly across models.

13. **D2's window and rows.** "The training period" for D2 is the initial
    training window (2021). D4 checks all 8 windows. Rows are those where all 16
    features are non-missing. The 2021–2023 matrix is descriptive.

14. **Concept labels for Step 1** (used only to judge whether the clusters
    "match the concepts", decided before seeing correlations):
    - LEVEL: atr, bb_width, parkinson_vol, rv_15, rv_60, rv_240, rv_adjusted,
      log_gk60
    - TERM (current relative to recent history): atr_ratio, atr_percentile,
      bb_width_percentile, rv_ratio_short_long, rv_ratio_mid_long,
      rv_ratio_short_mid, log_rv_ratio
    - SHAPE: wick60

    The percentile features rank the current value within the last 100 bars. That
    compares current with recent volatility, so they are labelled TERM.
    "Correlated with nothing" means max |ρ| < 0.5 with every other feature.

15. **DM conventions.** d = e²_A - e²_B. A negative statistic favours A, and p is
    one-sided for "A is better". D1 uses A = M1, B = M2. D3 uses A = M3, B = M0.
    Newey–West lags count rows. Rows are not equally spaced in time (weekends,
    dropped targets), which is accepted.

## During and after the run

16. **Pre-registered script frozen after the first run.** `run_study.py` was not
    changed after its first full run (2026-09-29 00:41). Everything beyond the
    pre-registration lives in `exploratory.py` and REPORT.md section 7.

17. **D4 verdict is applied literally.** The two coefficients that flip sign
    (`wick60` for y4, `log_rv_ratio` for y24) are about 0.00–0.01 in
    standardised units, compared with about 0.3 for `log_gk60`. That reads as "no
    information at this horizon" rather than instability, but the rule says fail,
    so D4 is reported as FAIL.

18. **Reading "recommended block, strictly following the rules".** D1 fails, so
    the rules do not license replacing the 13 features with the 3. The
    confirmatory recommendation is therefore the status quo. The exploratory
    finding (the 4h gap is time-of-day information) is reported as a hypothesis
    for a new pre-registration, not as a recommendation.

19. **Per-row predictions are not committed.** `results/predictions_*.csv`
    (about 3 MB) are reproducible in seconds and are listed in a local
    `.gitignore`. Metrics, coefficients, correlations and DM results are
    committed.

20. **Exploratory hour-of-day controls** use 23 UTC hour dummies. The bar label
    hour is the bar's close hour, which is known at t, so there is no look-ahead.
    Dummies are standardised with the training scaler like every other feature.
