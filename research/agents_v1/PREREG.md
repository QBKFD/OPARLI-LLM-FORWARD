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

## 1. Questions

1. **Direction.** Does the Technical agent's direction beat **both**
   always-long **and** always-short on the same trades? Random direction is
   reported, not tested.
2. **SL/TP.** On the trades it takes, do the agent's own SL/TP beat the neutral
   k·σ bracket on the same bars?

The unit is one scanner touch, and the outcome is a 60-minute bracketed trade.
Nothing else, such as other horizons, aggregation with other agents or sizing,
is tested here.

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
    `[entry_time, entry_time + 60 min)` (`crosses_close` of
    `sigma_over(model, state, 60)`), checked again on the scored window.
  - At least one bar falls in the holding window. Otherwise it is an exclusion
    (`NoBarsError`), for example an unmodelled holiday.
- **Overlap.** Every eligible event is scored independently, even when its
  window overlaps an earlier one. This is a measurement, not a P&L simulation.
  Dependence is handled in the inference (§7).

## 3. The agent under test

- **Version.** The frozen commit of `agents/voters/technical.py` and its
  dependencies, plus the config hash. It is recorded on every decision.
  - Any change to the code, config or model file means a new `experiment_id`
    and a new PREREG.
- **Output per event:**
  - `abstain` or a direction (`long` / `short`);
  - a confidence (reported, not tested);
  - SL and TP as distances from the reference price, each clipped to
    [0.5σ, 3σ] with σ = HAR `sigma_px` for H = 60 at t;
  - evidence refs.
- **Abstain is allowed.** The abstention rate is reported, overall and by
  level type.

## 4. Trades and scoring

- **Entry.**
  - `entry_time` = open time of the first 1-min bar opening at or after the
    moment the decision is stored.
  - `entry_price` = that bar's open.
  - This is the same for every arm.
- **Horizon.** H = 60 minutes from `entry_time`. k = 1.
- **The agent's bracket.** SL = entry ∓ d_sl and TP = entry ± d_tp: the
  agent's distances, **re-anchored at the actual entry price** (owner
  decision).
  - Scored with `scoring.outcome.score(...)`.
- **Neutral bracket.** `score_neutral(..., k=1, max_hold_minutes=60, model,
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
  rates, MAE/MFE.

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

| δ (mean net R) | n trades | at ≈ 2.4 trades/day (≈ 4 eligible alerts × 60 % taken) |
|---:|---:|---|
| 0.10 | ≈ 830 | ≈ 16 months |
| 0.15 | ≈ 370 | ≈ 7 months |
| 0.20 | ≈ 210 | ≈ 4 months |

- **Inputs from history.** The scanner averages about 4.5–5 first-touch
  alerts per day. The eligible and taken fractions come from the development
  backtest (§10).
- **What is fixed:**
  - **δ = 0.15 R** (owner decision).
  - σ_d for each test comes from the dev backtest (2007–2023), and DE from its
    day clustering. They depend only on the bracket mechanics and the agent's
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
  have been scored. A trade counts once its 60-minute window has closed.
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
- **Development uses a full historical backtest, 2007–2023** (owner decision).
  - It is dev only and is never evidence.
  - The deterministic agent runs unmasked: masking applies only to future LLM
    agents.
  - The data is `XAUUSD.txt` 2007–2023; the parquet holds the same bars from
    2020.
  - The triggers are scanner touches replayed by the same level engine.
  - It gives σ_d, the design effect, and the eligible and taken rates for §8.
  - It is reported as development: the HAR parquet fit is in sample for
    2021–2023.
- **Why only forward data is evidence.**
  1. **Developer leakage.** The deterministic agent's rules are written by
     people (and models) who have seen 2007–2026, so they may fit that history.
     A 2007–2023 backtest therefore can't confirm them, however it comes out.
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
- The 2007–2023 dev backtest is unmasked for the deterministic agent.

**Still open (fixed before the final commit):**
- N, from the dev run;
- c, from the 2-week window;
- the `experiment_id`;
- the freeze commit.
