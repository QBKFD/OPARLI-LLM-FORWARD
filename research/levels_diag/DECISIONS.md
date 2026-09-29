# Decisions log: levels diagnostic

1. **Branch `research/levels-diag`** in the worktree `/Users/x/Desktop/algo_project_levels`
   (from `research/levels-confirm` 03956eb). The plan was committed before
   anything ran (4e36123). The owner's Q5 + validation addendum was also committed
   before anything ran (a851caf).
2. **VolBlock for 2007** is fit on 2006 only: yearly expanding refits start at
   2006-01-01 and use only data before each year. 2006 fails the long audit's
   coverage rule, but it has enough rows for the profile.
3. **Code reuse:** `confirm.py` and `levels.py` are imported unchanged. The Q5
   builder is a copy of `confirm.build_units` with one extra filter on the
   candidate days (context class). The old design is `levels.build_units`
   (DAILY) under the same σ unit.
4. **Validation gate applied literally.** A/A NEAR coverage was 0.905, outside
   93–97 %, so the run stopped before Q1–Q5 (the code enforces this), and no
   Q1–Q5 number was computed. FAR passed (0.935); injection ±10 pp passed.
5. **Counts-only bias check (`bias_check.py`),** added after the stop to explain
   the bias as the plan asks ("report the bias"). It computes placebo-day sharing
   across bootstrap clusters from the unit definitions, with no outcomes.
6. **No attempt to fix the bootstrap and rerun.** The plan says stop. A fix is
   proposed in the report for the owner to decide on.
