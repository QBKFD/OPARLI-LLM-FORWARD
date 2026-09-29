# Decisions log: level behaviour study

Entries 1–12 were written before any result.

1. **Separate git worktree.** Another Claude Code session commits into the main
   checkout (`/Users/x/Desktop/algo_project`). Last round its scanner fix
   (b03e17b) landed on the research branch. This study therefore runs in its own
   worktree, `/Users/x/Desktop/algo_project_levels`, on branch `research/levels`
   (from a0e0916). The untracked data is symlinked in (`database/ohlcv_data`,
   `research/vol_features_r2/cache`), and the symlinks are never committed.
2. **σ_d unit conversion.** `VolBlock`'s mRV is deseasonalized and
   dimensionless, so the literal formula can't give dollars. Multiplying by p̄
   (the mean fitted profile of the current DST state) restores raw per-minute
   variance. This keeps "from VolBlock" and gives a dollar unit (see PREREG).
3. **2007–2008 σ_d is in-sample** (the fit covers those years). It only scales
   distances and barriers; it predicts nothing.
4. **The touch bar's intrabar order is unknown.** For H1, the touch bar counts
   only toward the beyond barrier. Same-bar double hits count as stop first.
   Both rules are conservative and identical for placebos, so they don't bias the
   difference.
5. **H3 considers only the first pierce of the day.** Later pierces happen after
   the level has already been broken, which is a different situation.
6. **Weekly placebos are drawn once per week** (at the first day the real level
   is eligible) and held fixed, mirroring the real weekly level.
7. **Clusters:** trading date for DAILY and trading week for WEEKLY. A weekly unit
   and its placebos can span several days, so week clustering keeps them
   together.
8. **The effect pairs placebos with real events** at the level-period unit (the
   "same days" in the spec): only placebo events from level-periods where the
   real level produced an event of that type.
9. **The L-tercile thresholds for conditioning** are descriptive (CONF-wide
   terciles of L at the real events), not a forecast.
10. **Outcome windows near the end of the data** are dropped if they would pass
    the last available minute. CONF events at the end of 2020 may use early-2021
    minutes for their outcome path. That isn't leakage: REPLICATION isn't sealed,
    and no parameter is fit on it.
11. **No stale or holiday special-casing** beyond the 80 % minute rule.
12. **Robustness cells are point estimates only.** A bootstrap of every cell would
    add nothing to a sign count.

## During and after the run

13. **`run_levels.py` frozen after its first complete run** (2026-09-29 19:30).
    `tables_levels.py` is reporting only. `exploratory_levels.py` holds the
    distance check.

14. **Found after the run: the placebos are biased in distance from the open.**
    Placebo draws that land on the wrong side of the open are redrawn (the
    pre-registered eligibility rule). When a real level sits close to the open,
    that pushes its placebos farther out. Mean distance: DAILY real 0.49 σ_d vs
    placebo 0.78 σ_d; WEEKLY 1.14 vs 1.37. Distance from the open itself shapes
    event behaviour (far levels are reached on trending days). The pre-registered
    verdict stands as computed. A distance-stratified estimate is reported as
    exploratory. A future confirmatory test should draw placebos matched on
    distance from the open.

15. **Monte-Carlo variation of the placebo set.** The exploratory rerun draws
    placebos for DAILY + WEEKLY only, so its random stream differs from the main
    run (which also drew ROUND placebos). Its raw effects differ from the primary
    table by up to about 0.6 pp (e.g. DAILY H1 −1.8 vs −1.2 pp). That is the size
    of the placebo sampling noise.

16. **Secondary BH family:** 54 conditioning cells + 6 tradability cells = 60
    tests, BH at 5 %.

17. **Replication vs support.** DAILY H3 meets the REPLICATED criterion (+8.9 pp,
    p = 0.002), but it isn't SUPPORTED (BH p 0.085, era 1 negative). It is
    reported as "replicated but not supported".
