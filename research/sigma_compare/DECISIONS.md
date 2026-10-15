# Decisions log: sigma_compare

The choices the task left open are in PREREG.md ("Choices made here"). This
file logs what was decided while implementing (step 1), before any real-data
run.

1. **Market-open calendar, vectorised.** `open_flags` is
   `datafeed.ingest.xauusd_bars._market_open` written with pandas. This is for
   speed: the CONF grid has 7.4 M minutes. A test shows it equals
   `_market_open`:
   - minute by minute across both 2021 US DST changes;
   - on 20,000 random minutes from 2007–2023.

2. **EWMA via `scipy.signal.lfilter`.** It is tested equal (rel. 1e-12) to the
   naive loop of the PREREG's recursion.

3. **Loaders are reused unchanged:** `run_r3.load_conf` for CONF and
   `run_study.load_minutes` for DEV. A data audit before the run, which
   produced no results, found:
   - CONF: 4,983,505 bars, no non-positive or NaN prices, no high < low;
   - DEV: 1,417,321 bars;
   - both have a ns UTC index.

4. **Which bars a window uses.** It uses the bars opening in the window's open
   minutes only, for both the target and the path. In 2007–2011 the daily break
   was traded, and those break bars are left out of the window, as W(t, H) in
   the PREREG requires. close_ref is the last bar opening before t, of any
   minute.

5. **Grid.** It covers every calendar minute from the first label to the
   period end + 5 days. A window that can't collect H open minutes inside the
   grid is invalid. That can only happen past the period end, where windows are
   invalid anyway.

6. **EWMA without λ-selection rows.** If no training row is left after the
   60-day burn-in, the fold raises instead of choosing a λ. The synthetic tests
   hit this case first; on CONF and DEV the burn-in ends long before the first
   test.

7. **DM with identical losses.** When the loss differential is 0 everywhere,
   the DM statistic is 0/0. It is then reported as stat 0, p = 1 (not
   significant: ties go to the plug-in). This only arises in the synthetic rule
   test.

8. **Cross-check of the plug-in during the run.** In each fold and H, 50 random
   non-crossing test rows are checked. h_a must equal
   `sigma_over(state_at(t), H).sigma_ret²` (rel. 1e-9), from a 21-day slice of
   bars before t. Rows whose 21-day slice gives no valid state are skipped and
   counted. The random selection is seeded (20261014); it chooses which rows are
   checked and doesn't touch any result.

## Overnight run (2026-10-15, unattended; rule: the conservative choice, logged here)

9. **"The post-hoc checks" were not defined.** The repository (all branches),
   the notes and the earlier session logs were searched, with no definition
   found. Two checks were defined in POSTHOC.md and committed before either was
   run:
   - P1: a frozen model on a later era;
   - P2: parquet calibration, which also picks the default fit.
   They gate the datafeed change. The alternative, building nothing, was
   rejected because it leaves the gate the instruction asked for untested.
   Not building is still the outcome if either check fails.

10. **The parquet fit uses pre-2024 rows only.** It trains from 2021-01-01, as
    the study's DEV folds did. The parquet's 2024-01..2026-01 rows are spent
    and were not read, which is the reading that keeps every data rule of the
    study.

11. **Tie-break for the default fit.** It goes to LONG (`XAUUSD.txt`
    2007–2023), the fit the study validated walk-forward.

12. **Local `main` is behind `origin/main`** (9042270 vs 05fe838). `main` is
    not touched; the new branches start from `origin/main`.

13. **The parquet fit's provenance hash covers the whole file.** Its
    `input_sha256` is taken over the file's bytes, which include the spent
    rows. Hashing reads no values: only rows before 2024-01-01 are loaded and
    used.

14. **Default fit: txt2007_2023 (LONG).** P2 favours it on calibration (score
    0.0182 vs 0.0218). SHORT has the lower QLIKE on the DEV quarters. The
    instruction says to default to the calibration's choice, so LONG is the
    default and SHORT ships as a config option.

## Sync with feat/sigma-har (2026-10-15)

15. **`sigma_over` was renamed in the study's code.** On `feat/sigma-har`,
    `vol_block.sigma_over` became the HAR sigma and the plug-in became
    `vol_block.sigma_plugin` (same maths).
    - The study's plug-in cross-check (`run_sigma.check_plugin`) now calls
      `sigma_plugin`, which is what it always computed. That is the only code
      change.
    - The Markdown files keep the name as it was when they were written.
    - A rerun after the sync reproduces `results_sigma.json` and `coefs.csv`
      byte for byte.
