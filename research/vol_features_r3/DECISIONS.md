# Decisions log, round 3

Entries 1–6 were written before any model result.

1. **Branch base.** `research/vol-features-r3` was created from
   `research/vol-features-r2` (acfa967).

2. **Repo check only.** `git status` shows two modified tracked files: round 1's
   `REPORT.md` (189 lines removed) and `notebooks/backtest.ipynb` (6 lines).
   `git ls-files --deleted` is empty. The CSVs missing from
   `database/ohlcv_data/` were gitignored and never tracked, so git doesn't list
   them. Nothing was restored, as instructed.

3. **"Reuse the round 2 code, change only what is listed."** `run_r3.py` starts
   as a byte copy of `run_r2.py`, and only the listed parts are edited. The full
   diff is kept in `results/diff_run_r2_vs_r3.txt`, so the changes can be
   checked line by line.

4. **mRV windows stay in bar rows** (the last n 1H bars, as in round 2), so the
   only change is sum → mean. The 50 % coverage is counted against 60 minutes
   per bar row.

5. **No unseen confirmation.** No 1-min file for another instrument exists.
   `GC_FUTURES_1MIN.csv` (gold futures) starts 2025-12-16, after the seal. Only
   its first data line was seen, while listing files in round 2.

6. **Tests that no longer apply.** Round 2's W-related parts are dropped (W is
   removed). The HAR test now uses log mRV terms. The two new tests (mRV with a
   20-minute gap, and S = 0 under constant per-minute variance with varying
   minute counts) are added.

## During and after the run

7. **Shared data cache.** `run_r3.py` reads round 2's gitignored cache of
   `XAUUSD.txt` (`research/vol_features_r2/cache/`). If it is missing, it is
   rebuilt with round 2's `audit_long.load_raw()`. This is one of the two
   non-listed edits in the diff (a path, plus the `sys.path` line needed for it).
   The other is the output file names (`results_r3.json`, `rho_LST_*.csv`).

8. **Test tolerance (before the first run).** The round 2 gap test compared
   features with `rtol=1e-9`. With means, S sits near 0, so a 1.3e-13 absolute
   floating-point difference failed the relative check. `atol=1e-10` was added.
   No code change.

9. **Rows.** The common sample grew slightly in CONF (y4 69,496, against 69,049 in
   round 2) because W's 60-bar warm-up is gone. The 50 % mRV coverage drops a
   few short bars. DEV rows are unchanged.

10. **`run_r3.py` frozen after its first full run** (2026-09-29 17:27). The
    exploratory check is in `exploratory_r3.py`. `tables_r3.py` is reporting only
    (a sed-edited copy of `tables_r2.py`).

11. **Rules applied literally.** R2 fails in CONF on the y4 hour-dummy gain
    (0.0119 against 0.01; 0.0105 and 0.0126 per era), while S's η² passes by a
    small margin (0.049). In DEV, R2 fails on S's η² (0.120). Per the PREREG
    mapping, (L, S, T) is not recommended. No override.
