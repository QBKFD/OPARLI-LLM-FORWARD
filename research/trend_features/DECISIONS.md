# Decisions log: trend features

Entries 1–9 were written before any model result.

1. **Branch base.** `research/trend-features` was created from
   `research/vol-features-r3` (6f73d7b). Phase A (vol block) was committed first
   (568d43c).
2. **Windows in bar rows.** "The last n hours" for Z_n means the last n 1H bar
   rows (trading time), as in the vol block. Clock time would put a weekend inside
   every 120h window and fail the 90 % coverage on most Mondays. Targets use clock
   minutes (t, t + h], as in the vol rounds. E4 windows that overlap the daily
   break fail coverage, and E24 windows keep it (95.8 %).
3. **5-min bucket rule.** A 5-min return needs the previous bucket to have data,
   and no gap-flagged 1-min bar inside its own bucket. This is the direct
   translation of "gap-excluded (any gap > 5 min)" to 5-min returns.
4. **No volume in M1+.** The 1H bars carry OHLC only, so the repo's
   `price_vwap_dist` falls back to equal weights. That matches production (IBKR
   spot volume is −1/0) and vol round 1.
5. **MV training span.** `VolBlock.fit` gets 1-min rows in [period start,
   test start − 24h), the same span as the fold's training rows.
6. **R5 is judged on the MV+MT model** (the model a recommendation would use),
   and only MT features count toward dropping. A FAIL (unstable sign) feature is
   also dropped. "Recommend a feature whose sign flips" is not a defensible
   reading of the mapping.
7. **R2(b) direction:** the MT gain from hour dummies must be <= the M1+ gain, on
   each target separately.
8. **The exploratory sign test** is declared in PREREG, so there is no freedom
   in choosing it after the fact.
9. **Data cache.** The CONF loader reuses vol round 2's gitignored cache of
   `XAUUSD.txt`, as round 3 did.

## During and after the run

10. **Test fix before the first run.** The alternating-returns test failed on the
    first Z24 window: it includes the series' very first bucket (no previous
    bucket, so its return is NaN), which left 287 alternating returns that don't
    cancel. The test now evaluates windows that start after the first bucket. No
    code change.

11. **`run_trend.py` frozen after its first full run** (2026-09-29 17:51).
    Tables come from `tables_trend.py` (reporting only), and the declared sign
    test from `exploratory_trend.py`.

12. **R2b applied literally.** At E24 the hour-dummy gain is −0.00056 for MT and
    −0.00063 for M1+. Hour dummies *hurt* both out of sample, MT slightly less,
    and "MT gain <= M1+ gain" then fails by 0.00007. The rule as pre-registered
    has no tolerance, so R2b = FAIL and MT is not recommended. The report
    discusses how thin this is. No override.

13. **R5 reported on MV+MT for every coefficient.** Only MT features matter for
    the mapping, and since R2b fails, the drop list isn't used.
