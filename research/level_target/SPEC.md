# level_target: specification (no code, no data touched)

**Status: SPEC, written 2026-10-17 overnight, unattended.**
- This is not a PREREG. No code exists and no data has been read for this
  study.
- Each phase gets its own PREREG, committed before that phase sees any
  outcome:
  1. development (2007–2020);
  2. the one-look holdout (2021–2023);
  3. the sealed forward test.
- Choices made without the owner are in [DECISIONS.md](DECISIONS.md) (T1–T15).
  Open questions are in §12.

## 1. Question

When the production scanner alerts a first touch of a level, does a
deterministic trade toward **the next scanner level** earn a positive
**mean R after costs**? The trade has:
- its stop beyond the touched level;
- its target at the next level in the trade direction;
- a fixed time cap.

Does it also beat **random entry times** carrying the same σ distances, the
same direction and the same time cap?

- The trade is the hypothesis. It isn't a model of why levels matter.
- The second comparison separates "this level structure helps" from "any
  bracket of this shape, at this time of day, in this direction, makes or
  loses this much".
- **Prior evidence is weak and mixed:**
  - `research/levels`: 0 of 6 hypotheses supported on 2007–2020.
  - `research/levels_confirm`: the near-open PDH/PDL break effect reversed
    sign on 2024–2026.
  - `research/levels_diag`: stopped at A/A validation.

  A null result here is a valid outcome.

## 2. Inputs

| Input | Source | Notes |
|---|---|---|
| 1-min bars, development | `XAUUSD.txt` 2007–2020 (UTC; `research/vol_features/DATA_AUDIT_LONG.md`), via `research/levels`' loader and cache | Midpoint-like vendor bars. The loader drops bad bars and asserts the seal |
| 1-min bars, holdout | `database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet`, 2021-01-01 .. 2023-12-31 | The parquet is built from the same file (close-for-close equal 2020–2023) |
| 1-min bars, forward | the datafeed store, kinds `live`/`history`, `known_by = t` | IBKR MIDPOINT bars. Bid/ask capture is needed for the cost window (§6) |
| Alerts (dev, holdout) | the production level engine, `build_levels` + `Scanner` from `backend/deterministic_scanner.py`, loaded by file path and replayed over the bars the live scanner keeps (`_valid_bar`) | Live is a continuous replay (`LiveLevels`), so replayed alerts are what the scanner would have sent. The scanner code is not edited |
| Alerts (forward) | the scanner journal, `alerts` with `status = 'sent'` | `missed_*` rows are never triggers |
| σ | HAR on (L, S_hd, T), `datafeed.features.vol_block.sigma_over(model, state, H)` for H ∈ {60, 240} | The model differs by phase (§7.3). σ exists only for 60 and 240 minutes |
| Market hours | `datafeed.ingest.xauusd_bars._market_open` | Holidays aren't modelled |
| Costs | §6 | Fixed in each phase's PREREG |

**Not used:** volume, news and the calendar, other instruments, the owner's
TAKE/SKIP replies, and any LLM.

## 3. Levels (exactly the scanner's)

All 12 types, as `build_levels` makes them. Days and sessions are UTC.

| Type | Value | Active |
|---|---|---|
| PDH / PDL | high / low of the previous **full** trading day (≥ 300 bars) | that UTC day |
| PWH / PWL | high / low of the previous ISO week | that UTC day |
| DOPEN | the day's first open | that UTC day; armed only after price has left it by 2 × TOL |
| WOPEN | the ISO week's first open | that UTC day; armed as DOPEN |
| ASIA_H/L, LONDON_H/L, NY_H/L | session high / low (00–07, 07–12, 12–21 UTC) | from the session close for 24 h |

- **Touch:** a bar whose range, widened by TOL = $1.00, holds the level.
- **First touch only:** a touched level instance is dead for alerts.
- **Collapsing:** touches in the same 15-minute bucket within TOL of each
  other become one alert. Its `level_price` is the mean of the cluster, and
  it carries every type in the cluster.
- **TOL is a fixed $1,** not σ-relative. With gold at $650–2,000 in 2007–2020
  and about $4,000 now, a touch was relatively looser then. The replay gives
  6.3 alerts per trading day on 2007–2020, against about 4.5 on recent data
  (`research/agents_v1/results/expected_exclusions.json`).
  - The study keeps the scanner's rule as is, because the forward test
    trades the production scanner's alerts.
  - Era and year breakdowns (§8) show whether results depend on it.

**Target candidates.** At entry, the target candidates are every level
instance that:
- is active at entry (`active_from ≤ entry < active_to`), touched or not;
- is not one of the alert's own levels, and not within TOL of
  `level_price`.

A level stays a price reference after its first touch, so touched levels
count.

## 4. Entry rule

1. **Trigger:** one scanner alert = one event.
2. **Approach side.** Let c₋ be the close of the last bar before the alert's
   first touch bar, and P = `level_price`.
   - **From below** if c₋ < P − TOL; **from above** if c₋ > P + TOL.
   - Otherwise the event is **excluded** (`ambiguous_side`). Price was
     already at the level, so "touch" doesn't say which side it came from.
3. **Direction**, by variant (§9):
   - **REV (fade):** from below → short; from above → long.
   - **CONT (break):** from below → long; from above → short.
4. **Decision time t.** The scanner emits an alert when the first bar of a
   later 15-minute bucket arrives, and that bar is final about 1 minute after
   it opens. So t = emitting bar's open + 1 min.
   - In the forward test, t is the time the runner receives the alert.
5. **Entry:** `entry_time` is the open of the first 1-min bar at or after t;
   `entry_price` is that open. Live, the decision is stored first. This is the
   same rule as agents v1 (`research/agents_v1/PREREG.md` §4).
   - Between the touch and the entry, up to ~16 minutes pass. Price may have
     moved, and the trade is placed from where it is.
6. **σ at entry:** `sigma_over(model, state_at(entry_time), H)`, with H = the
   variant's cap.
7. **Eligibility** (fixed before outcomes; every exclusion counted by
   reason):
   - the HAR state is valid;
   - the window `[entry, entry + H)` doesn't cross a close (`crosses_close`).
     At H = 240 that drops 4.2 % of alerts (2007–2020, PREREG agents v1 §2);
   - `ambiguous_side` (step 2);
   - **resolved before entry:** price at entry is already beyond the stop or
     the target (§5);
   - **stop too close:** the stop is nearer than 0.25σ to the entry;
   - **no target** within [d_min, 3σ] of the entry (§5);
   - at least one bar in the window (else `NoBarsError`).
8. **Overlap.** Every eligible event is scored independently, even when its
   window overlaps another's. It's a measurement, not a P&L simulation.
   Dependence is handled by day clustering (§8). A 240-minute window that
   doesn't cross a close stays in one New York trading day.

## 5. Exit rules

For a long (a short is mirrored):

- **Stop**
  - **REV:** P − b·σ (beyond the level, against the trade).
  - **CONT:** P − b·σ (back through the level).

  The same formula, so the stop is anchored to the level, not to the entry.
  The R unit is |entry − stop|.
- **Target:** the **nearest** target candidate (§3) above `entry_price` at
  a distance in [d_min·σ, 3σ]. If there is none, the event is excluded
  (`no_target`).
- **Time cap:** H minutes from entry. A timeout exits at the close of the
  last bar inside the window.
- **Scoring:** `scoring.outcome.score(entry_time, direction, entry_price,
  stop, target, max_hold_minutes=H, bars)`, unchanged. Its conservative fill
  rules are:
  - a bar holding both levels is a stop;
  - a gap through the stop fills at the open;
  - a gap through the target fills at the target;
  - bars are walked from entry only.
- **No trailing, no break-even, no partial exits, no re-entry.**
- **Recorded per trade:**
  - the stop and target distances in σ (a = |entry − stop|/σ, g =
    |target − entry|/σ);
  - the target's level type;
  - the exit reason;
  - gross R, MAE_R and MFE_R.

## 6. Cost model (per oz, round trip, in price units, then in R)

net R = gross R − (spread + commission + financing) / |entry − stop|

| Item | Forward test | Development and holdout |
|---|---|---|
| **Spread** | measured: the bid/ask spread at entry plus at exit, half each (one full spread per round trip), from bid/ask captured live. Before the evidence start, a **2-week capture window** fixes the median spread by hour of day. That value goes into the forward PREREG, the same plan as agents v1 | no bid/ask in the data. A spread **proportional to price**, s = s_bp × price, with s_bp set from the 2-week window by hour of day. Until that exists, a provisional **1.0 bp** of price round trip (≈ $0.40 at $4,000; ≈ $0.07 at $650) |
| **Commission** | IBKR's published schedule for CMDTY XAUUSD at the account's tier: a rate on trade value with a per-order minimum. It is copied into the PREREG with its date and URL, and evaluated at a **fixed reference size** (also in the PREREG, e.g. 10 oz) so the minimum is included | the same schedule as a bp rate at the reference size. Provisional: **0.5 bp** round trip |
| **Overnight financing** | **zero for every v1 variant:** no window crosses a close, so no position is held over the 17:00 New York roll. For the swing variants (v2): notional × r_fin × nights / 360, with r_fin = IBKR's benchmark + spread for the side held, fixed in the PREREG. Both long and short are assumed to **pay** (conservative); a weekend counts its nights | zero (v1). v2: the same formula, with the PREREG's r_fin applied to every year |

- **Central cost:** the provisional total is 1.5 bp of price round trip.
- **Sensitivity** at 0× and 2× of the central cost is reported, not
  inferential. A result that is positive only at 0× is reported as such.
- **Why proportional.** A fixed $ spread would be 6× heavier in 2007 than
  now, relative to price and σ, and the forward test trades at today's
  prices. A proportional spread keeps the cost in σ units comparable across
  eras.
- **The provisional values are placeholders.** The dev PREREG states them.
  If the measured window comes later, the dev result is re-reported at the
  measured value as an amendment. The holdout and forward PREREGs use the
  measured value.

## 7. Data split

### 7.1 Phases

| Phase | Data | Role | Looks |
|---|---|---|---|
| **Develop** | `XAUUSD.txt` 2007-01-01 .. 2020-12-31 (levels warm-up from 2006-11) | build, debug, choose **one** variant by the rule in §9 | as many as needed; nothing here is evidence |
| **Holdout** | parquet 2021-01-01 .. 2023-12-31 | test the **one** chosen variant | **one look**, after its PREREG and the frozen code are committed. Only the chosen variant is ever computed on it |
| **Spent** | 2024-01-01 .. 2026-01-30 | none | **unusable:** read by `levels_confirm` and seen since. Loaders assert < 2024-01-01 |
| **Gap** | 2026-02 .. 2026-09 | none for now | not in our data (backfill pending). Using it needs an owner decision (§12) |
| **Sealed** | from 2026-10-01 | the **evidence** | forward test only, under its own PREREG, with fixed N, a calendar cap and no interim efficacy looks. `research/sealing.py` and the replay code refuse it |

### 7.2 Honesty notes on the holdout

- **2021–2023 isn't pristine.**
  - `research/levels` used it as replication: PDH/PDL touch, break and
    sweep outcomes were seen.
  - `sigma_compare` used it as DEV and fitted the production σ model on it.
  - The vol block's L terciles were looked at there.
- What hasn't been run there is **this** rule (level-to-level brackets with
  σ distances). So it is a holdout for the rule, not for the data.
- A pass on the holdout justifies the forward test; it isn't evidence by
  itself.
- The agents v1 Technical agent will also run once over 2021–2023, as a
  sanity check (agents v1 PREREG §10, owner decision 2026-10-17). It is a
  different rule, but it looks at the same years, which is one more reason
  not to treat them as pristine.

### 7.3 σ model per phase

- **Develop and holdout:** a HAR fit on 2007–2020 only (call it `har_txt2007_2020`).
  - It is fitted by sigma_compare's `fit_final.py` restricted to bars before
    2021-01-01, written as a model JSON, and pinned by SHA-256 in the dev
    PREREG before any dev result.
  - The two shipped models are both in sample for the holdout:
    `parquet2021_2023` was fitted on it, and `txt2007_2023` includes it.
    sigma_compare's post-hoc P1 showed a 2007–2020 HAR still beats the
    plug-in on 2021–2023.
- **Forward:** the production pin, `parquet2021_2023` (SHA-256
  `dc0eb3c5…`; full value in `vol_block.SIGMA_MODELS`), the same as agents
  v1.
  - Distances are in σ, so a different σ model moves stops and targets
    slightly.
  - The forward PREREG reports the dev result recomputed with the production
    model, as a check that the variant's numbers don't depend on the fit.

### 7.4 Seal mechanics

- Every loader asserts its phase's bounds.
- Every outcome window must end by its phase's end.
- Asserts run on the frames and on the events, and a test checks that they
  fire (as in `research/levels`).
- The holdout loader refuses to run unless a file
  `research/level_target/HOLDOUT_PREREG.md` exists **and** is committed.

## 8. Metrics

### 8.1 Primary

**Mean net R per trade** of the chosen variant, at the central cost.

### 8.2 The empirical baseline: random entry times

- **Placebos.** For each real trade i, K = 20 placebo trades with **the same
  direction, the same stop and target distances in σ (aᵢ, gᵢ), the same cap
  H and the same cost rule**. Each one is placed:
  - at the **same clock minute (UTC hh:mm)**;
  - on a **different** trading day drawn uniformly from the same calendar
    quarter;
  - with σ recomputed at the placebo's own entry, so the distances are
    aᵢ·σ and gᵢ·σ there.

  A draw is redone (up to 50 tries) if the placebo window crosses a close,
  its HAR state is invalid or it has no bars. A trade with fewer than K
  valid placebos is reported and dropped from the comparison.
- **Why this design.**
  - The same clock minute holds the intraday pattern of drift, spread and
    volatility fixed.
  - A different day avoids the same-day placebo bias that sank the
    `research/levels` design (`levels_confirm` §5).
  - The same quarter holds the regime.
  - The same σ distances and cap hold the bracket's shape. So the only
    difference left is that the real trade sits at a scanner level and
    aims at the next one.
- **Statistic.** Δ = mean over i of [net Rᵢ − mean over k of net R_placebo(i,k)].
- **Inference.** A **day-block bootstrap**, 10,000 resamples of New York
  trading days, each day taken with all its real trades and their
  placebos. The confidence interval is the percentile interval, and the
  test is one-sided.

### 8.3 Validation gates (before any real outcome is read)

They run first in development, and again on the holdout data **without
real outcomes**: placebos against placebos only.
1. **A/A.** Replace each real trade by one of its own placebos and keep the
   rest as placebos. Over 500 such draws, the 95 % interval must cover 0 in
   **93–97 %** of draws, and the mean Δ must be within ±0.01 R of 0.
2. **Injection.** Add +0.10 R to every real trade's net R. The estimate
   must recover +0.10 R within ±0.01 R. Its power at that size is reported.

If either gate fails, the phase stops and reports the bias instead of
results. This is the rule `research/levels_diag` followed.

### 8.4 Reported, not tested

- gross R, hit rate (target), stop and timeout shares, mean win and mean
  loss in R, profit factor;
- MAE_R and MFE_R;
- trades per trading day and the exclusions by reason;
- every metric by level family (daily, weekly, open, session), by target
  level type, by approach side, by direction, by session of entry, by era
  (2007–13 / 2014–20) and by year;
- Δ against a **random-direction** baseline at the same entry (the mean of
  long and short);
- net R at cost 0× and 2×.

## 9. Variants to pre-register

**The development grid (16 variants, all at H = 240):**

| Factor | Values |
|---|---|
| Direction | REV, CONT |
| Stop buffer b | 0.25σ, 0.5σ beyond the level |
| Target minimum distance d_min | 0.5σ, 1.0σ (maximum 3σ) |
| Level family that triggers | all 12 types; PDH/PDL/PWH/PWL only |

**Fixed across variants:**
- the nearest qualifying target;
- the stop-too-close floor of 0.25σ;
- the 15-minute alert delay;
- H = 240. H = 60 is reported in development for the chosen variant only,
  and isn't selected on.

**Selection rule (development, literal):**
1. Keep the variants with ≥ 1,000 eligible trades in each era.
2. Of those, keep the ones whose mean net R > 0 **and** Δ > 0 **in both
   eras**, at the central cost.
3. Choose the one with the highest **lower 95 % bootstrap bound of Δ** on
   2007–2020. A tie goes to the variant with the most trades.
4. If none survives step 2, the study **stops at development** with "no
   candidate", and the holdout stays unread.

**Stop rule (owner decision 2026-10-17).** The idea is **dropped**, with no
holdout look and no forward test, if the chosen variant's **historical net
edge** on 2007–2020 is either:
- **below 0.10 R:** mean net R per trade at the central cost < 0.10 R; or
- **not significant:** the one-sided day-block bootstrap test of mean net R
  > 0 doesn't reject at **α = 0.05 / 16 ≈ 0.0031**. Bonferroni is applied
  over the 16 variants because the variant was selected out of them on the
  same data (DECISIONS T14).

This rule runs after the selection rule, on the variant it chose. It is a
necessary condition, not evidence: passing it only opens the holdout.

**Holdout test (one look, the chosen variant only).** Supported only if
**both** of these hold, one-sided at α = 0.05 each (an intersection–union
test, so no further correction):
- H1a: mean net R > 0;
- H1b: Δ > 0.

The decision table in the holdout PREREG maps supported → write the forward
PREREG; not supported → stop, report.

**The forward test** (its own PREREG):
- the same variant, frozen code, the production σ pin and the measured cost;
- the same two hypotheses;
- N from the holdout's σ of the per-trade difference, at δ fixed in the
  PREREG;
- a 12-month calendar cap, as in agents v1.

**Rough size**, to be replaced by the dev run's σ_d and DE: with σ_d ≈
1.2 R and DE 1.3:
- δ = 0.15 R needs ≈ 650 trades;
- δ = 0.10 R needs ≈ 1,500.

At an assumed 2 trades a day, that is about 15 and 35 months. A small edge
would take longer than the cap to confirm. This is the main practical risk
of the study.

## 10. What is out of scope

- Sizing, portfolio and P&L simulation (overlapping trades are scored
  independently).
- Trailing stops and partial exits.
- Swing horizons. They need a daily σ fit first (agents v1 PREREG §13);
  financing is already specified in §6 for when they come.
- News filters.
- LLM judgement.
- Any instrument but XAUUSD.

## 11. Process (as for every study here)

1. **Development PREREG** (variant grid, selection rule, gates, costs, σ
   model hash) committed before any dev code touches outcomes. The code is
   then built, tested on synthetic data, and the gates run.
2. **Development run.** A clean rerun from scratch must be byte-identical.
   The selection rule is applied literally.
3. **Holdout PREREG** with the frozen code commit, then **one** holdout run
   of the chosen variant, and the REPORT.
4. **Forward PREREG,** committed before the evidence start: the 2-week cost
   window first, then evidence from max(2026-10-01, PREREG commit, freeze,
   end of the cost window).
5. **DECISIONS.md** logs every judgment call. Exploratory analyses are
   labelled and can't change a verdict.

## 12. Open questions for the owner

1. **The 2026-02 .. 2026-09 gap.** If it's backfilled before the forward
   test starts, it is unseen data. It could serve as a second holdout (one
   look) before the sealed test, or stay unused. This spec leaves it unused.
2. **The level families in the grid.** Is "PDH/PDL/PWH/PWL only" the right
   second family, or should session levels be the alternative?
3. **The reference order size** for the commission minimum (e.g. 10 oz).
4. **The scanner's fixed $1 TOL is kept** for this study (owner decision
   2026-10-17), because the forward test uses the production scanner. A
   σ-relative touch stays an **open item for a later scanner epoch**. It
   would need a scanner change, a new journal epoch, and a new study.
5. **The provisional costs** (1.0 bp spread + 0.5 bp commission round trip),
   until the 2-week capture exists.
6. **The forward δ:** 0.15 R as in agents v1, or larger, given the duration
   estimate in §9.
