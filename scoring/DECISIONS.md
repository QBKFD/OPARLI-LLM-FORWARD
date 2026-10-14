# Decisions: outcome scorer (feat/outcome-scorer, overnight 2026-10-15)

Made unattended. The rule was the conservative choice on any decision, logged
here.

1. **Branch base: `origin/main` (05fe838).** Local `main` is behind it at
   9042270, and `main` is not touched. The branch does not include
   feat/sigma-har; the scorer takes sigma from the caller, whichever sigma that
   is.

2. **A new top-level package, `scoring/`,** not `risk/`. The scorer reads
   store bars (`read_bars` uses `datafeed.store.query`), and `risk/` may import
   only `datafeed.features`.
   - Import rules: `scoring` → stdlib, `risk`, `datafeed.store`. Nothing in
     `datafeed/` or `risk/` imports `scoring`. Both are AST-tested.
   - `scoring*` is packaged, and `tests/scoring_tests` is in testpaths.

3. **Bars used.** A bar is used only if it opens at or after `entry_time` and
   ends by `entry_time + H`. A mid-minute entry skips the bar that is already
   forming, because its high and low may come from before the entry.

4. **Same bar holding both levels → stop** (as specified). The stop is
   checked first in every bar.

5. **Gap through the stop: exit at the bar's open,** worse than the stop.
   `gap = True` when the open is strictly beyond the stop.

6. **Gap through the target: exit at the target.** There is no credit for the
   better open, which is the less flattering choice.

7. **Timeout: exit at the close of the last bar inside the holding period.**
   `exit_time` is always the open time of the exit bar.

8. **MAE / MFE in the exit bar.** The order of its high and low is unknown:
   - MAE takes the bar's adverse extreme in full (more risk shown, not less),
     but at a stop it is clipped at the fill;
   - MFE counts only what is certain: the target at a target exit, else the
     bar's open.

9. **No bar in the holding period raises `NoBarsError`** (a `ValueError`).
   Inventing an exit at the entry price (R = 0) would fabricate an outcome.

10. **Inconsistent vendor bars are widened, not rejected.** A high below the
    open or close (or a low above) becomes max/min of the bar's prices. That
    can only make a stop more likely. The clean parquet has 0 such bars before
    2024, so this is defensive.

11. **Every argument is keyword-only.** Stop and target must be on the correct
    side of the entry, and H must be a positive int. Anything else raises.

12. **The real-bar tests use the research parquet, pre-2024 only, and skip
    without it.** The cases were found in the data, and each test asserts its
    own preconditions:
    - same bar: 2023-06-13 12:30 UTC, CPI;
    - gap through the stop: Fri 2023-10-20 20:59 to Sun 2023-10-22 22:00,
      a −13.5 reopen;
    - timeout: 2023-06-14 03:00:30;
    - target hit: 2023-06-14 03:00.
    The first version of the target test placed its stop where the data
    touched it. The test's own precondition caught that, and the stop was
    widened.
