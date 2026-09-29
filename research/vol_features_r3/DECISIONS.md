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
