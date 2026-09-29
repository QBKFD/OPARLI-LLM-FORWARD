# Decisions log: vol block (Phase A)

1. **Restore.** `git checkout -- research/vol_features/REPORT.md` was run, as
   the task asked. That discards the uncommitted emptying of the file (not my
   edit). `notebooks/backtest.ipynb` is also modified and was not asked about,
   so it is left alone and uncommitted.

2. **HOUSEKEEPING paths.** Two references build their path with `Path` joins
   (`PROJECT_ROOT / 'data' / 'snapshots' / ...`), which a text grep reports as
   "name only". Those rows were corrected by hand after reading the lines.

3. **The first return of the training data.** `VolBlock.fit()` only sees the
   rows it is given, so it can't know whether the first row follows a gap, and it
   drops that return. Round 3 computed the gap flag on the full series, so it kept
   the first return of a training window that didn't start after a gap. The
   profile can therefore differ by at most one return from round 3's. The
   equality test starts the training window just after a weekend gap, where the
   two agree exactly (profile to 1e-18, L/S/T to 1e-10). This is the causally
   safe choice for production use.

4. **S_hd hour means** come from training bars with label <= the last training
   minute + 1 min. Hours with no training bar would give NaN; with any realistic
   training window every trading hour is present.

5. **transform() windows count bar rows**, like round 3. `bar_times` is only a
   reindex at the end, so the rolling windows are never built on the requested
   times.
