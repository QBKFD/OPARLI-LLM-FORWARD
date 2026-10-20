# PREREG (DRAFT): agents v1, the Technical agent

> **DRAFT. Not a pre-registration yet.**
> - It becomes binding only when the final text is committed, unchanged, as
>   `agents/experiments/<experiment_id>/PREREG.md`. That is the path live mode
>   requires (`agents/prereg.py`).
> - The commit must come before the first evidence decision, with the agent's
>   code frozen at a named commit.
> - The owner's answers of 2026-10-15 are applied. Two things are still to be
>   fixed before the final commit, and never from forward data: N (from the
>   dev run, §8) and c (from 2 weeks of live data, §4).
> - Draft written 2026-10-15. The design is in `docs/AGENTS_TECHNICAL_DESIGN.md`.
> - **Amended 2026-10-17** (owner decision): the primary horizon is **H = 240
>   minutes**; H = 60 is secondary (§6a). The expected close-crossing
>   exclusions are recomputed on 2007–2020 (§2), the dev backtest is cut to
>   2007–2020 (§10), and §13 sets the v2 scope. Unattended choices are in
>   `research/agents_v1/DECISIONS.md`.
> - **Owner decisions, 2026-10-17 (later):**
>   - the dev period 2007–2020 is confirmed, and 2021–2023 is a sanity check
>     (§10);
>   - the SL/TP structure moves to the 240-minute horizon (§3, design doc
>     §3).
>
>   Both were decided before any agent code or result existed.

## 1. Questions

1. **Direction.** Does the Technical agent's direction beat **both**
   always-long **and** always-short on the same trades? Random direction is
   reported, not tested.
2. **SL/TP.** On the trades it takes, do the agent's own SL/TP beat the neutral
   k·σ bracket on the same bars?

The unit is one scanner touch, and the outcome is a **240-minute** bracketed
trade (owner decision 2026-10-17). The same trades at 60 minutes are a
secondary analysis without α (§6a). Nothing else, such as other horizons,
aggregation with other agents or sizing, is tested here. Swing horizons (1–2
days) are v2 (§13).

## 2. Triggers (the events)

- **Source.** Scanner level touches from the deterministic scanner's journal
  (`alerts` table) with `status = 'sent'`, meaning the scanner saw the touch
  live.
  - `missed_while_down` and `missed_history` rows are never triggers.
  - One alert id = one event. A confluence alert (several level types in one
    15-minute bucket) is one event.
- **Decision time t.** The time the experiment runner receives the alert. The
  snapshot is built as of `floor(t, 1 min)` in live mode: `kinds =
  (live, history)`, `known_by = t`, `experiment_id` set.
- **Eligibility.** All of these are fixed before any outcome is known, and
  every exclusion is counted and reported by reason.
  - The snapshot is not stale.
  - The HAR state at t is valid (`reason is None`).
  - The holding window doesn't cross a close: no closed minute in
    `[entry_time, entry_time + 240 min)` (`crosses_close` of
    `sigma_over(model, state, 240)`), checked again on the scored window.
    A window that doesn't cross a close at 240 minutes doesn't cross one at
    60, so the secondary analysis (§6a) uses the same events.
  - At least one bar falls in the holding window. Otherwise it is an exclusion
    (`NoBarsError`), for example an unmodelled holiday.
- **Expected close-crossing exclusions** (recomputed 2026-10-17;
  `expected_exclusions.py`, `results/expected_exclusions.json`). The
  production level engine was replayed over `XAUUSD.txt` 2007–2020, with entry
  2 minutes after the bar that emits the alert:

  | | H = 60 | **H = 240** |
  |---|---:|---:|
  | alerts crossing a close | 1.7 % (379 of 23,020) | **4.2 %** (959) |
  | of which the daily break / the weekend | 312 / 67 | 836 / 123 |
  | range over the 14 years | 0.9–2.4 % | 3.5–5.2 % |

  - The share is far below the 4/23 ≈ 17 % that alerts spread evenly over the
    clock would give at 240 minutes. Alerts bunch at the session opens (00, 07
    and 12 UTC hold 42 % of entries) and are rare in the 4 hours before the
    17:00 New York close.
  - **Not modelled:** stale snapshots, invalid HAR states, `NoBarsError`,
    holidays and scanner downtime. The forward exclusion rate is reported by
    reason next to these values.
  - The replay gives 6.3 alerts per trading day in 2007–2020, above the ≈ 4.5
    seen on recent data. The scanner's touch tolerance is a fixed $1, which is
    wider relative to the price range when gold traded at $650–2,000. So the
    replayed rate doesn't carry over to the forward period; the crossing
    share, which depends on the time of day, carries over better.
- **Overlap.** Every eligible event is scored independently, even when its
  window overlaps an earlier one. This is a measurement, not a P&L simulation.
  Dependence is handled in the inference (§7). A 240-minute window that
  doesn't cross a close stays inside one New York trading day, so clustering
  by trading day covers every overlap.

## 3. The agent under test

- **Version.** The frozen commit of `agents/voters/technical.py` and its
  dependencies, plus the config hash. It is recorded on every decision.
  - Any change to the code, config or model file means a new `experiment_id`
    and a new PREREG.
- **Output per event:**
  - `abstain` or a direction (`long` / `short`);
  - a confidence (reported, not tested);
  - SL and TP as distances from the reference price, each clipped to
    [0.5σ, 3σ] with σ = HAR `sigma_px` for H = 240 at t (the raw, unclipped
    distances are stored too; §6a uses them);
  - the structure behind them (design doc §3, owner decision 2026-10-17):
    - the stop goes 0.1σ₂₄₀ beyond the nearer of the touched level and the
      last complete 1h swing (the 20-bar low or high);
    - the target is the nearer of the next scanner level and the 4h 20-bar
      high or low of complete candles;
  - evidence refs.
- **Abstain is allowed.** The abstention rate is reported, overall and by
  level type.

## 4. Trades and scoring

- **Entry.**
  - `entry_time` = open time of the first 1-min bar opening at or after the
    moment the decision is stored.
  - `entry_price` = that bar's open.
  - This is the same for every arm.
- **Horizon.** H = 240 minutes from `entry_time` (primary). k = 1.
- **The agent's bracket.** SL = entry ∓ d_sl and TP = entry ± d_tp: the
  agent's distances, **re-anchored at the actual entry price** (owner
  decision).
  - Scored with `scoring.outcome.score(...)`.
- **Neutral bracket.** `score_neutral(..., k=1, max_hold_minutes=240, model,
  state)` on the same bars.
  - The state is as of `entry_time` and built with `model.params`.
  - The sigma model is the **HAR parquet fit**, `parquet2021_2023`, pinned by
    its SHA-256
    `dc0eb3c5fd31f1ee8f6e7a534b16930fbec453833aec9872b606afe963910dde`
    (`vol_block.SIGMA_MODELS`). The same model gives σ for the agent's
    clipping (§3).
  - The bracket is stop 1σ, target 2σ.
- **Scorer rules (fixed by `scoring/`):**
  - a bar that holds both levels is a stop;
  - a gap through the stop fills at the open;
  - a gap through the target fills at the target;
  - a timeout exits at the last close.
- **Metrics per trade:**
  - gross R;
  - net R = gross R − c / |entry − SL|;
  - hit (reason `target`);
  - MAE_R and MFE_R.
- **Cost:** c = the **measured IBKR spread + commission**, round trip, per
  oz.
  - The spread is measured over the first **2 weeks of live data after the
    cut-over**, before the evidence start. c is fixed in the final text from
    that window, which holds no agent outcomes.
  - The commission comes from IBKR's schedule for CMDTY XAUUSD at the account's
    tier.
  - **Dependency:** the feed stores only bid/ask midpoints, so bid/ask capture
    is needed for those 2 weeks (design doc §7).
  - Sensitivity is reported at c = 0 and c = 2× (not inferential).
- **Reported per arm:**
  - mean net R (primary), mean gross R, hit rate, share with R > 0;
  - mean MAE_R and MFE_R;
  - σ-normalised P&L (Σ (exit − entry)·sign / σ_px) as a secondary metric,
    because the two brackets have different stop distances.

## 5. Baselines

- **Always-long and always-short (tested).** Each is scored with the neutral
  bracket on **the agent's traded events**. So the baselines have the same
  abstention rate as the agent by construction, and the comparison is paired.
- **Random direction (reported, not tested).** It is the exact expectation
  0.5·R_long + 0.5·R_short per event, not a single draw.
- **Secondary, selection plus direction:** the agent's mean net R on its
  traded events, against each baseline over **all** eligible events. Random
  abstention at the agent's rate has that mean in expectation, so a
  permutation test over random subsets of the same size is used.
  - This is reported only.

## 6. Hypotheses (one-sided)

- **Q1.** Write Δ_b = mean net R(agent's direction, neutral bracket) −
  mean net R(baseline b, neutral bracket), over the agent's traded events.
  - H1: Δ_b > 0 for **both** b ∈ {always-long, always-short}.
  - Q1 is supported only if both tests reject (an intersection–union test).
  - Δ_random is reported with an unadjusted interval.
- **Q2.** Write Δ_SLTP = mean net R(own SL/TP) − mean net R(neutral bracket),
  on the agent's traded events, both in the agent's direction.
  - H1: Δ_SLTP > 0.
- **Not tested:** random direction, random selection (§5 secondary), hit
  rates, MAE/MFE, and everything at H = 60 (§6a).

## 6a. Secondary horizon: H = 60 (reported, no α)

- **Events:** the same traded events as the primary analysis (§2 makes every
  240-eligible event 60-eligible). No event is added or dropped.
- **Q1 at 60:** Δ_b with the neutral bracket `score_neutral(..., k=1,
  max_hold_minutes=60)`, σ = HAR σ for H = 60, on the same entries.
- **Q2 at 60:** the agent's raw SL/TP distances clipped to [0.5σ₆₀, 3σ₆₀],
  re-anchored at entry and held 60 minutes, against the 60-minute neutral
  bracket. The agent produces one proposal; it isn't asked again.
- **Reported:** the same metrics and Δs as §4–§6, with day-clustered
  intervals labelled unadjusted. They can't change the decision table (§7),
  whatever their sign or size.

## 7. Tests and multiplicity

- **Test statistic.** For each test, a paired one-sided t-test on the per-event
  differences, with standard errors clustered by New York trading day.
  - Touches cluster within days and their windows can overlap.
  - With fewer than 30 clusters there is no test, only a report.
- **α = 0.05 familywise, Bonferroni across the two questions:** Q1 at 0.025
  (each of its two tests at 0.025; the intersection–union test needs no
  further correction) and Q2 at 0.025.
- **Secondary analyses** have no α. Their intervals are labelled unadjusted.
- **Decision table** (literal, no override):

| Q1 | Q2 | Reading |
|---|---|---|
| supported | supported | direction and SL/TP both add value |
| supported | not supported | use the agent's direction with the neutral bracket |
| not supported | either | no evidence of direction skill; Q2 is reported but not acted on |

## 8. Sample size (δ fixed; N from the dev run)

The required number of trades is n = ((z₁₋α + z₁₋β) · σ_d / δ)² · DE, with:
- α = 0.025 one-sided and power 0.8, so z₁₋α + z₁₋β ≈ 2.80;
- DE = design effect from clustering, *(provisional)* 1.3;
- σ_d = SD of the per-event difference, *(provisional)* ≈ 0.9 R. This is
  replaced by the dev-run value for each of the three tests (2 for Q1, 1 for
  Q2).

Recomputed for H = 240 (2026-10-17). The forward alert rate stays ≈ 4.5 a
day. The close-crossing exclusion at 240 minutes is 4.2 % (§2), so ≈ 4.3
eligible alerts a day, and with 60 % taken (provisional) ≈ **2.6 trades a
day**, ≈ 55 a month at 21 trading days. Windows four times longer overlap
more within a day, so DE is likely above 1.3. The table shows DE = 1.6 as a
sensitivity:

| δ (mean net R) | n trades, DE 1.3 | months | n trades, DE 1.6 | months |
|---:|---:|---:|---:|---:|
| 0.10 | ≈ 830 | ≈ 15 | ≈ 1,020 | ≈ 19 |
| 0.15 | ≈ 370 | ≈ 7 | ≈ 450 | ≈ 8 |
| 0.20 | ≈ 210 | ≈ 4 | ≈ 250 | ≈ 5 |

At δ = 0.15 R both fit inside the 12-month cap (§9); at δ = 0.10 neither
would. σ_d ≈ 0.9 R is still the 60-minute guess. A 240-minute bracket has
fewer timeouts, and σ_d may differ; the dev run replaces it.

- **Inputs from history.** The scanner averages about 4.5–5 first-touch
  alerts per day on recent data (6.3 in the 2007–2020 replay; see §2 for why
  that doesn't carry over). The eligible and taken fractions come from the
  development backtest (§10).
- **What is fixed:**
  - **δ = 0.15 R** (owner decision).
  - σ_d for each test comes from the dev backtest (2007–2020) at H = 240, and
    DE from its day clustering. They depend only on the bracket mechanics and the agent's
    choices, never on forward data.
  - **N = the largest n over the three tests** at δ = 0.15 R. The table above
    is only a guide.
- **Reported from the dev backtest,** because N and the expected duration
  depend on them (both go into the final text next to N):
  - **eligible trades per trading day** (alerts that pass §2), overall and by
    year;
  - **the abstention rate** (share of eligible alerts where the agent
    abstains), overall, by year and by reason, with `no_structure_*` counted
    separately;
  - from these, the agent's **trades per day** = eligible per day × (1 −
    abstention rate), and the expected months to reach N.
  The forward abstention rate and trades per day are reported in the final
  analysis next to these dev values. A difference is reported; it doesn't
  change N or the cap.

## 9. Stopping rule

- **Fixed size.** The analysis runs **once**, when N eligible agent trades
  have been scored. A trade counts once its 240-minute window has closed.
- **Calendar cap: 12 months after the evidence start** (owner decision).
  - If N isn't reached by the cap, the analysis runs **at the cap with the N
    achieved**, using the same tests, α and decision table.
  - **Its power is reported:** the power each test had, at the achieved n,
    to detect δ = 0.15 R, using the dev backtest's σ_d and DE. It is computed
    before the outcomes are analysed, from n alone.
  - **There is no extension,** of the window or of N, for any reason.
- **No interim efficacy looks.** Weekly integrity checks are allowed:
  - event counts;
  - exclusions by reason;
  - abstention rate;
  - pipeline errors;
  - stale snapshots.
  They never use R, hit rates or any outcome.
- **Stopping early** is allowed only for an operational failure (feed, store,
  scanner), documented in DECISIONS.md. The partial data is then not analysed
  as evidence. A fixed agent restarts as a new experiment with a new PREREG.

## 10. Data, development and LLM leakage

- **Evidence comes only from live shadow decisions made after 2026-10-01**,
  and after the final PREREG commit and the agent freeze. In practice the
  start is max(2026-10-01, PREREG commit, freeze, end of the 2-week cost
  window).
  - Replay of sealed data is refused by the code (`SealedError`), so no sealed
    period can be scored after the fact.
- **Development uses a historical backtest, 2007–2020** (amended
  2026-10-17, **confirmed by the owner** the same day).
  - It is dev only and is never evidence.
  - **Why not 2007–2023, as decided on 2026-10-15:** the data rules of
    2026-10-17 keep the parquet 2021–2023 out of development
    (`DECISIONS.md` A2, A10).
- **2021–2023 is a sanity check** (owner decision 2026-10-17), run once on
  the parquet:
  - **When:** after the agent and the dev outputs (σ_d, DE, the rates) are
    frozen, and before the final PREREG commit.
  - **What is reported:** next to the dev values, the eligible trades per
    day, the abstention rate by reason, σ_d and DE.
  - **What it can't do:** change the agent, N, the cap or any rule. It is
    never evidence.
  - **If it shows a bug** (not a weak result): the fix is a dated amendment,
    the dev run is redone, and both runs are reported.
  - The deterministic agent runs unmasked: masking applies only to future LLM
    agents.
  - The data is `XAUUSD.txt` 2007–2020.
  - The triggers are scanner touches replayed by the same level engine.
  - It gives σ_d, the design effect, and the eligible and taken rates for §8.
  - σ comes from the pinned parquet fit, whose parameters were fitted on
    2021–2023: later data than the dev period. That only scales σ; the dev run
    is never evidence.
- **Why only forward data is evidence.**
  1. **Developer leakage.** The deterministic agent's rules are written by
     people (and models) who have seen 2007–2026, so they may fit that history.
     A 2007–2020 backtest therefore can't confirm them, however it comes out.
  2. **LLM memory (for future LLM agents).** An LLM has seen gold prices, dates
     and news up to its training cutoff, and could recognise a dated period
     and "know" what came next. For those agents, development runs on masked
     history (no dates, prices rescaled). Masking lowers the risk but doesn't
     prove it away.
  3. **Neither kind of leakage reaches data that didn't exist at the freeze.**
     That data is also after the training cutoff of any current model (Claude
     Opus 5.5: June 2026).
- **A property test, not masking.** The agent is σ-relative, so price
  rescaling must leave its direction unchanged and scale SL/TP by the same
  factor. This is checked in the agent's tests (design doc §6).

## 11. Records and amendments

- **Stored per event:**
  - trigger, snapshot (hash), vote (with evidence refs);
  - both brackets and both outcomes;
  - σ, the model name and hash;
  - the exclusion reason.
  All are append-only (agent store, plus the scorer's outputs).
- **Deviations** go to "Amendments" in the REPORT, dated, and can't change the
  decision table.
- **Analysis code** is written and tested on synthetic data and committed
  before the analysis runs.

## 12. Owner decisions (2026-10-15) and what is still open

**Decided:**
- Q1 = beats always-long **and** always-short; random is reported only.
- δ = 0.15 R; N comes from the dev run; the calendar cap is 12 months.
- c = measured spread + commission (2 weeks after the cut-over), with
  sensitivity at 0 and 2×.
- SL/TP are re-anchored at the actual entry.
- σ comes from the HAR parquet fit, pinned by its SHA-256.
- No structure → abstain, counted.
- The touched level is given through `trigger_levels`.
- 5m candles are added.
- Indicators use Wilder smoothing.
- The dev backtest is unmasked for the deterministic agent.

**Decided 2026-10-17:**
- **Primary horizon H = 240 minutes; H = 60 is secondary** (§6a).
- Swing horizons (1–2 days) are v2 and need a daily sigma fit (§13).

**Still open (fixed before the final commit):**
- N, from the dev run;
- c, from the 2-week window;
- the `experiment_id`;
- the freeze commit.

**Decided 2026-10-17, later (owner), before any code or result:**
- **Dev period 2007–2020** is confirmed. 2021–2023 is a sanity check, run
  once (§10).
- **SL/TP structure at 240 minutes:**
  - the stop goes 0.1σ₂₄₀ beyond the nearer of the touched level and the
    last complete 1h swing;
  - the target is the nearer of the next scanner level and the 4h 20-bar
    extreme of complete candles;
  - both are clipped to [0.5σ₂₄₀, 3σ₂₄₀] (§3, design doc §3).

**Still a guess:** the 60 % taken rate in §8; the dev run replaces it.

## 13. v2 scope (not part of this experiment)

v2 is planned only. Nothing here is tested by v1, and each item needs its own
PREREG.

- **Swing horizons (1–2 days).**
  - **A daily sigma fit is needed first.** HAR is fitted for H = 60 and 240
    minutes only, and `sigma_over` refuses any other H (sigma_compare
    PROPOSAL, decision 2). A multi-day σ has to forecast across the daily
    break and weekends, where every current forecast was too high on DEV
    (ΣRV/Σh 0.58–0.75, sigma_compare REPORT §5). So it is a new
    pre-registered fit and comparison against the current forecasts, on
    2007–2020 with the parquet 2021–2023 as its one-look holdout, not an
    extrapolation.
  - **Close crossing becomes the rule, not an exclusion.** A 1–2 day trade
    always crosses at least one daily break, so the reopen gap enters the
    outcome, and the cost model gains overnight financing
    (`research/level_target/SPEC.md` §6).
  - **The scorer** walks bars across closes as it is; what changes is the σ
    and the eligibility rule.
- **A level-target agent** (deterministic, so it gets a full historical
  development run before any forward test).
  - **Trigger:** the same scanner alert.
  - **Exit:** the target is the next scanner level in the trade direction,
    the stop sits beyond the touched level, and a time cap applies. Distances
    are in σ and costs are explicit.
  - **Benchmark:** random entry times at the same σ distances and time cap.
  - **Its study is specified in `research/level_target/SPEC.md`** (develop on
    2007–2020, one look at the parquet 2021–2023, evidence only from a sealed
    forward test under its own PREREG).
  - **Relation to v1:** the Technical agent of v1 is the deterministic
    benchmark a level-target agent and any LLM agent have to beat on the same
    alerts.
- **An LLM scanner agent** (forward-only evidence, paired with the owner's
  TAKE/SKIP on the same alerts) is designed in
  `docs/AGENT_LLM_SCANNER_DESIGN.md`.
