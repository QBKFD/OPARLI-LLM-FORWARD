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
