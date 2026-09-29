# Pre-registration: level behaviour study

Committed **before any result**, and never edited afterwards. Research only.
Nothing in `backend/` or `regime_classifier/` changes.

## Framing

This is a **measurement study with a control group**, not a strategy backtest.
Per hypothesis: does price behave differently at real levels (PDH/PDL, WH/WL)
than at placebo levels that go through the identical pipeline? Null results are
valid results.

## Data, periods, seal

- `database/ohlcv_data/XAUUSD.txt`, 1-min, UTC (audited in
  `research/vol_features/DATA_AUDIT_LONG.md`). The timestamp is the 1-min bar's
  open time.
- **CONFIRMATION:** 2007–2020 (first use for this question). Eras 2007–2013 and
  2014–2020. **REPLICATION:** 2021–2023. The period is the trading date of the
  event.
- **SEALED:** no timestamp >= 2024-01-01 anywhere (asserts on the loader, the
  events and the outcome windows, plus a test that the assert fires). An event
  whose 240-minute outcome window would reach past the last available minute is
  dropped.
- **Trading day:** 17:00 → 17:00 America/New_York (DST handled by the tz
  database). The trading date is the New York date on which the session ends.
  **Trading week:** Sunday 17:00 → Friday 17:00 New York, i.e. trading dates
  Mon–Fri of one ISO week.
- **Excluded days:** fewer than 80 % of the expected 1380 minutes. An excluded
  day produces no events, and its high/low isn't used as a level (so the next day
  has no PDH/PDL). **Excluded weeks:** fewer than 80 % of 5 × 1380 minutes; such
  a week's WH/WL isn't used. Counts are logged.

## Levels (completed prior periods only)

- **PDH / PDL** = high / low of the previous (non-excluded) trading day. They
  must be the immediately preceding trading date in the calendar of trading
  days. Active for the whole current trading day.
- **WH / WL** = high / low of the previous (non-excluded) trading week. Active for
  the whole current week.
- **Families:** DAILY (PDH + PDL) and WEEKLY (WH + WL). Upper levels (PDH, WH) and
  lower levels (PDL, WL) are **mirrored**: for lower levels every price is
  negated (high ↔ −low, close → −close), so one code path handles "toward /
  beyond / away" for both sides.

## Volatility unit

- L comes from `research/vol_block` (`VolBlock`). It is fit once on 2007–2008 and
  rolled forward annually with an expanding window: events in year Y >= 2009 use
  a fit on 2007-01-01 .. Y-01-01, and events in 2007–2008 use the 2007–2008 fit.
  The unit is in-sample there; it only scales distances.
- **Conversion to dollars:** `VolBlock`'s mRV_24h is a mean of *deseasonalized*
  squared returns (dimensionless, about 1 in normal conditions), so the literal
  "price · sqrt(mRV_24h · 1440)" has no unit. The raw per-minute variance is
  restored with the fitted profile:
  **σ_d = price · sqrt(exp(L) · p̄ · 1440)**, where p̄ is the mean of the fitted
  seasonal profile over the 288 five-minute buckets of the current US-DST state
  (the average raw per-minute variance).
- **σ_d at time t** uses L from the last completed 1H bar (label <= t) and price =
  the level (for touch / eligibility) or the entry price (break / sweep). If L is
  missing at t (a rare short bar), the most recent finite L is used. It is never
  filled with a constant.

## Eligibility and first events

- At the start of the trading day (the open of its first 1-min bar), a level is
  eligible only if price is on the approach side by >= 0.1 σ_d (below an upper
  level, above a lower one). A gap-through at the open is not eligible.
- **Weekly levels** are checked for eligibility at the start of each trading day
  of the week, and only the **first** qualifying event per level per week counts.
  **Daily levels:** the first qualifying event per level per trading day. "First"
  applies separately to each event type (TOUCH, BREAK, SWEEP-RECLAIM).

## Events (identical code for real and placebo levels; upper side, mirrored for lower)

- **TOUCH:** the first 1-min bar of an eligible day with high >= level + fill,
  where fill = **$0.10**. Entry = the level.
- **BREAK:** the first 5-min bar (buckets [b, b + 5 min) of 1-min open times,
  close = the last 1-min close in the bucket) whose close is >= level + 0.1 σ_d.
  Event time = the end of that 5-min bucket. Entry = that close.
- **SWEEP-RECLAIM:** the day's first *pierce* (the first 1-min bar with high >=
  level + 0.05 σ_d). Then the first 5-min bar whose close is < level and whose end
  is within 30 minutes of the pierce bar's close. Event time = that bar's end.
  Entry = that close. If no reclaim comes within 30 minutes, there is no H3 event
  for that level that day. Only the first pierce is considered.

## Outcomes (first passage from entry; b = 0.3 σ_d; horizon 240 min)

The path is the 1-min bars opening in [event time, event time + 240 min). For
TOUCH, the touch bar itself is included only for the *beyond* barrier (its high
comes after the touch, its low may not), and the away target is checked from the
next bar on. If both barriers fall in the same bar, it counts as **stop first**
(conservative, and identical for placebos).

- **H1 REACTION (TOUCH):** target = entry − b (away), stop = entry + b (beyond).
- **H2 BREAK ACCELERATION (BREAK):** target = entry + b (further beyond), stop =
  entry − b.
- **H3 SWEEP-RECLAIM:** target = entry − b (reversal), stop = entry + b.
- Categories: target / stop / timeout. **Primary metric:** P(target first) among
  all events (a timeout counts as not-target).
- Also: the mean signed move at +60 and +240 min in σ_d units, signed in the
  hypothesis's target direction (close of the last bar opening before event +
  60 / + 240 min, minus entry).

## Placebos

- For each real eligible **daily** level on each day: 20 placebo levels on the
  same side, level + δ, δ ~ U([−1.0, −0.2] ∪ [+0.2, +1.0]) σ_d (σ_d at the day
  start). A draw is rejected and redrawn if it lies within 0.15 σ_d of any real
  level of that day (PDH, PDL, WH, WL) or if it fails the eligibility rule at the
  day start (up to 1000 draws).
- **Weekly:** 20 placebos per real WH / WL per week, drawn at the first trading
  day of the week on which the real level is eligible. They use σ_d then and are
  excluded near that day's PDH, PDL, WH and WL. They stay fixed for the week and
  go through the same daily-eligibility and first-per-week logic.
- Seed: `numpy.random.default_rng(20240101)`, drawn in a fixed order (date,
  family, level).

## Effect and inference

- **Unit:** a real level-period with a real event of that type. Its placebo
  events are the events of its own 20 placebos in the same period (paired by
  day, or by week for WEEKLY). Effect = mean(real outcome) − mean(pooled placebo
  outcomes over the paired units).
- **Bootstrap:** 5,000 draws, resampling trading days (clusters = trading date
  for DAILY, trading week for WEEKLY), with the real and placebo events of a
  cluster kept together. 95 % percentile CI. One-sided p = (#{draws with effect
  <= 0} + 1) / (B + 1), in the direction real > placebo for H1, H2 and H3. Seed
  `20240102`.
- **Primary tests:** 3 hypotheses × 2 families = 6, with BH-FDR at 5 % on
  CONFIRMATION.
- **SUPPORTED:** BH-adjusted p < 0.05 on CONFIRMATION, AND effect >= +3
  percentage points, AND the same (positive) sign in both eras.
- **REPLICATED:** on 2021–2023, a positive effect with one-sided p < 0.10.

## Robustness (report only)

b ∈ {0.2, 0.3, 0.5} σ_d × horizon ∈ {60, 240} min, and for TOUCH the fill buffer
∈ {$0, $0.10, $0.30}. For each cell: the point estimate of the effect. Reported:
the share of cells with a positive effect.

## Secondary (BH-FDR across all secondary cells, clearly labelled)

- **Conditioning:** effect per L tercile (thresholds = CONFIRMATION-wide terciles
  of L at the real events; descriptive), per session of the event (UTC hours:
  Asia 00–07, London 07–12, NY 12–17, late 17–24), and upper vs lower.
  Bootstrap p per cell, BH across all cells.
- **Tradability:** per hypothesis and family, the implied trade: entry as
  defined, symmetric barriers ±b, exit at timeout at the last close, $0.30 per
  side. Expectancy in $ per event, real vs placebo, with a bootstrap CI. This
  matters for SUPPORTED hypotheses, but it is reported for all six and labelled.

## Exploratory (declared)

Round-number levels ($10 and $50 multiples, the nearest above and below the day
open with >= 0.1 σ_d margin) as an extra family through the same pipeline, with
their own placebos. Anything else added later is labelled exploratory.
