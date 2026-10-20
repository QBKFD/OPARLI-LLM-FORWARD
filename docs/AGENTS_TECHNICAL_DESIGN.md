# Technical agent (agents v1): design

**Status: plan only, nothing built.** The experiment is in
`research/agents_v1/PREREG.md` (DRAFT). The legacy defects cited below are
numbered as in `legacy/PORT_CANDIDATES.md`.

**Decided (owner, 2026-10-15): the Technical agent is deterministic code, with
no LLM call in v1** (§4). The owner's answers to the open questions are
applied throughout and listed in §8. This matches:
- the PORT_CANDIDATES verdict ("Technical agent node (code)");
- the skeleton's stub ("Technical agent (plain code)").

Code gives:
- byte-identical reruns;
- evidence refs that resolve by construction;
- zero cost per decision;
- no model memory of gold history.

## 1. What gets ported

| Legacy (`legacy/backend_v0/`) | Becomes | Fixes applied, then frozen |
|---|---|---|
| `services/technical_indicators.py` | `agents/technical/indicators.py` | RSI 14, Bollinger 20/2, ATR 14, EMA 9/21, S/R. **VWAP and every volume input removed** (technical fix 1). **NaN raises** → abstain, never $10 ATR or RSI 50 (fix 4). **S/R uses complete candles only** (fix 5). RSI and ATR use **Wilder smoothing** (fix 6; owner decision: old results aren't evidence, so comparability with them doesn't matter). No wall clock (fix 7) |
| `services/regime_detector.py` | `agents/technical/regime.py` | **Relative regime** (fix 2): volatility state = the VolBlock **L tercile**, cut-offs fixed from the sigma model's training window. This follows `research/regime_v2`, where L terciles beat the jump model. **`atr_trend` dropped** (fix 3: it was "decreasing" on 100 % of bars). Trend uses the EMA 9/21 spread in σ units |
| `services/confluence_checker.py` | `agents/technical/confluence.py` | **4 voters:** RSI, Bollinger, EMA, S/R (VWAP gone). Ladder: 3 of 4 agree → 0.60, 4 of 4 → 0.80. Comparisons inclusive (`>=`), as the legacy note requires. Thresholds stay relative: RSI 30/70; BB tolerance as a fraction of band width (legacy 0.01 VOLATILE / 0.02 RANGING / 0.10 TRENDING, default 0.05); EMA spread ≥ 0.001. The per-regime values are re-mapped onto the new regime states (L tercile × trend) and written down before the freeze |
| `services/timeframe_cascade.py` | `agents/technical/cascade.py` | Cases A–D kept: 4h/1h lead, 15m/5m confirm or veto. Not remapped: 5m comes from the snapshot (§2). **Complete candles only.** Lead gate 0.60, opposition 0.70, mid-TF 0.75, all inclusive. No `datetime.now()` (fix 7) |
| `services/technical_service.py` | `agents/voters/technical.py` (`TechnicalAgent.vote(snapshot)`) | Entry point. Takes the Snapshot only, with no data provider |
| `services/market_data_provider.py`, `clock.py` | — | Already superseded by `agents/snapshot.py` + `agents/resample.py`: one `<` rule, `complete` flags, injected clock (provider fixes 1–7 are covered there) |
| `services/analysis/meta_decision.py` (deterministic half) | `agents/nodes/aggregator.py` **v1 option** (below) | The two bugs fixed, plus fixes 4–7 |
| SL/TP calculators, `position_sizing.py`, `risk_service.py` | **not ported** | Replaced by the agent's own clipped proposal (§3) and by `risk/` |

### `meta_decision`: the two bugs, fixed (reading confirmed by the owner)

1. **Direction from the winning side.** Legacy took the score from the better
   direction but the direction from a priority list (Technical > Visual >
   Sentiment, `:473-481`).
   - **Fix:** direction = argmax of the per-direction weighted scores, and
     `none` on a tie.
   - **Test:** the PORT reproduction, Visual SHORT .9 + Sentiment SHORT .9 +
     Technical LONG .55 at weights .4/.2/.4, must give **SHORT**, not LONG.
2. **Agreement thresholds on the wrong scale.** A 2-vs-1 vote gives agreement
   0.6667, which fails the `>= 0.67` low tier. A 1-vs-1 split gives 0.5, which
   passes the `>= 0.5` high tier.
   - **Fix:** agreement is a vote count. The winning side needs strictly more
     non-abstaining votes than the other, and the low tier needs ≥ 2/3 of the
     non-abstaining votes, compared exactly as fractions.
   - **Tests:** LONG .95 + LONG .95 + SHORT .1 must pass the low tier.
     Visual SHORT .9 vs Technical LONG .95 must give `none`.

Also applied:
- **fix 4:** a minimum number of participants, made explicit;
- **fix 5:** equal weights;
- **fix 6:** strict pydantic validation, where invalid → abstain with reason,
  stored apart from PASS;
- **fix 7:** no wall clock.

**Ported and tested, but not used in v1.** v1 scores the Technical vote
directly: with one active voter, any aggregator either needs a quorum of 1 or
always returns `none`.

**Aggregator choice deferred** (owner decision): the choice between D2 (v0) and
this fixed aggregator is decided, and pre-registered, when a second agent
exists.

## 2. Snapshot inputs, with known_by enforced

- **One input.** The agent receives a deep copy of the `Snapshot` and nothing
  else, under the existing voter contract (`voters/base.py`). There is no
  store access, no trigger payload and no clock.
- **Time.**
  - `as_of = floor(t, 1 min)`.
  - **Evidence runs are live:** `kinds=("live","history")`, `known_by = t`,
    `experiment_id` set (D1).
  - The experiment runner refuses to store an evidence decision whose snapshot
    has `known_by != t` or `kinds` other than live/history.
  - Replay (`known_by=None`) is tagged development and never counts.
- **Bars.**
  - Timeframes are 1m, 5m, 15m, 1h and 4h (4h on the NY trading day, D4).
  - **5m is new** (owner decision): resampled from 1m by
    `agents/resample.py`, with only complete candles used. The cascade keeps
    its 5m role.
  - Indicators use only `complete=True` candles. The forming candle is
    visible to the agent but never feeds an indicator or S/R.
  - No volume.
- **Sigma model.** The HAR **parquet fit** `parquet2021_2023`, pinned by its
  SHA-256 (`dc0eb3c5…`; the full value is in the PREREG and in
  `vol_block.SIGMA_MODELS`). The builder refuses any other model in an
  evidence experiment.
- **Features, computed by the snapshot builder** (not the agent) from the
  **same** fetched 1-min bars as the snapshot, so there is one query and the
  same `known_by`:
  - `vb.state_at(model.params, bars_1m, t)`;
  - `sigma_over(model, state, 240)` (primary, owner decision 2026-10-17)
    and `sigma_over(model, state, 60)` (secondary, PREREG §6a).
  - Stored in `Snapshot.features`: `L`, `S_hd`, `T`, `sigma_px_240`,
    `sigma_ret_240`, `crosses_close_240`, `sigma_px_60`, `sigma_ret_60`,
    `crosses_close_60`, `sigma_model` (name), and `sigma_model_sha256`.
  - The builder fails (stale or invalid), rather than leaving σ out, when the
    state isn't valid.
- **Levels.** `Snapshot.levels` holds the scanner's levels as of t: PDH, PDL,
  PWH, PWL, DOPEN, WOPEN and the session highs/lows, each with
  `active_from ≤ as_of`.
  - They are computed by a pure function ported from the scanner's level
    engine.
  - A `trigger_levels` field lists the ids of the levels the alert touched.
    The agent learns which level was touched without seeing the raw payload.
  - **Owner decision:** the touched level comes through `trigger_levels`.
    T1 builds the `levels/` package.
  - **Until then, a stub interface.** A `LevelSource` protocol
    (`levels_as_of(bars_1m, as_of) -> tuple[Level, ...]` and
    `trigger_level_ids(alert, levels) -> tuple[str, ...]`), with a `NoLevels`
    stub returning `()`.
  - With the stub, every proposal lacks its structural level, so the agent
    abstains ("no levels"), counted. This is plumbing only; no evidence run
    uses the stub.
- **Development: a historical backtest, 2007–2020** (dev only, never
  evidence; cut from 2007–2023 on 2026-10-17 to keep the parquet 2021–2023
  holdout unused, PREREG §10).
  - It runs on unmasked history (`XAUUSD.txt`), with scanner touches replayed
    by the same level engine.
  - It gives σ_d and the rates for PREREG §8.
  - Masking (no dates, rescaled prices) applies only to future LLM agents.
  - The agent may use time of day and weekday, never the date.

## 3. Outputs

- **`Vote` (existing schema):**
  - `direction` `long` / `short` / `none`, or `abstain` with a reason;
  - `confidence` 0–1 from the cascade, reported only;
  - `reason`.
- **New frozen `Proposal`,** attached to the Technical vote. `Vote` gains an
  optional `proposal`, and the agent store needs **migration 3**:
  - `ref_price`: the snapshot's last complete 1m close;
  - `sigma_px`: `features.sigma_px_240` (the 60-minute clip of PREREG §6a is
    computed from the raw distances at scoring time);
  - `sl_dist`, `tp_dist`: price distances > 0, **clipped to [0.5σ, 3σ]**;
  - `sl_raw_dist`, `tp_raw_dist`, and `clipped` (`low` / `high` / None for
    each side);
  - `evidence`: a tuple of refs in the existing grammar (`level:<id>`,
    `bars:<tf>:<start>..<end>`, `feature:<name>`).
  - Every ref must resolve with `agents.debate.evidence.check_ref`. An
    unresolved ref → abstain with the reason; there's no silent drop.
- **SL/TP rule (deterministic, long; short is mirrored).** Moved to the
  H = 240 horizon by the owner on 2026-10-17, before any code or result
  existed (PREREG §12, DECISIONS A11). σ below is σ₂₄₀ = `features.sigma_px_240`.
  - **SL.** Below the nearest support among:
    - the touched level, if it is below `ref_price`;
    - the low of the last complete **1h** swing: the lowest low of the last
      20 complete 1h candles. This is the same 20-bar window definition as
      before, moved from 15m to 1h.

    The stop goes 0.1σ₂₄₀ below that support.
  - **TP.** At the nearest resistance above, whichever of these is closer:
    - the next scanner level above;
    - the **4h** 20-bar high of complete candles (moved from 1h to 4h).
  - **Clipping.** Each distance is clipped to [0.5σ₂₄₀, 3σ₂₄₀]. Clipped
    sides are flagged, and the refs keep the structural source.
  - **No structure.** If no structural level exists on a side → **abstain**
    with reason `no_structure_sl` / `no_structure_tp`, **counted** separately
    from other abstentions (owner decision: a clip-bound fallback would blur
    Q2).
  - There is no R:R filter: legacy's 1.5R TP filter was a bias, so it isn't
    ported.
- **Decision table.** `Decision.sl` / `tp` / `sigma` stay null in v1. The
  experiment stores both scored brackets next to the vote (PREREG §11). The
  risk node fills them once an aggregator passes a direction through.

## 4. Does it call an LLM?

**No, in v1** (owner decision). If an LLM variant is wanted later, it is a
new experiment with its own PREREG, and these are its rules:

- **Model.** Pinned by exact id for the whole experiment: `claude-opus-5-5`,
  or `claude-sonnet-5-5` on cost.
  - Its training cutoff must precede 2026-10-01. Opus 5.5's is June 2026.
  - No aliases.
  - **Refusal fallbacks off.** A fallback would switch model silently, so a
    refusal → abstain with the reason.
- **Temperature 0 is not available.** Current models (Opus 5.5, Sonnet 5.5,
  Haiku 5.5) reject non-default `temperature` / `top_p` / `top_k` with a 400,
  and Opus 5.5's thinking can't be disabled; only `effort` is set (pinned, e.g.
  `low`).
  - So sampling is **not** reproducible. Exact reruns come only from the
    response cache below.
  - The older Opus 4.6 / Sonnet 4.6 accept `temperature`, but even
    temperature 0 isn't guaranteed deterministic.
- **Hashed prompt.**
  - The template file is versioned, with its sha256 recorded.
  - `prompt_hash` = sha256 of the canonical request body: model, system,
    messages, `output_config` (effort and the JSON schema of Vote +
    Proposal), `max_tokens`.
  - `experiment_id` is never rendered (D1).
- **Cached responses.**
  - An append-only table `llm_responses(prompt_hash PK, model, request_id,
    response_json, usage, stop_reason, created_at)`.
  - Live mode calls the model, then stores the response before using it.
  - **Rerun mode is cache-only.** A miss is an error and never triggers a new
    call, so a rerun is byte-identical.
- **Output.** Structured outputs (`output_config.format`) with the Vote +
  Proposal schema. A parse or validation failure, `max_tokens` or `refusal` →
  abstain with the reason.
- **Cost per decision (estimate).** ~4k input tokens (≈ 2k static system +
  ≈ 2k compact technical summary) and ~0.4k visible output plus ~1–2k thinking
  at low effort.
  - **Opus 5.5** ($4 / $20 per MTok): ≈ $0.016 + $0.03–0.05 ≈
    **$0.05–0.07**.
  - **Sonnet 5.5** ($2 / $10): ≈ **$0.03**.
  - At ~4–5 decisions a day, that is < $0.40/day, or ≈ $100/year on Opus.
  - Prompt caching barely helps at hourly spacing (the 5-min TTL expires). A
    1h TTL would cut the static part.
- **Leakage.** Development on masked history (no dates, rescaled prices)
  applies to this variant only. Evidence is forward-only, as in PREREG §10.

## 5. Determinism and provenance

- **The vote is a pure function of the Snapshot.** The same snapshot gives
  byte-identical vote JSON, across processes.
- **Recorded per decision:**
  - the agent code commit and a source hash of `agents/technical/`;
  - the config hash;
  - the sigma model name and SHA-256;
  - the snapshot hash.

## 6. Test plan

| Area | Tests |
|---|---|
| Port fixes | One test per PORT_CANDIDATES technical fix, with the reproductions as cases: no VWAP or volume anywhere; NaN ATR or RSI → abstain; S/R ignores the forming candle; Wilder RSI and ATR against reference values; relative regime (×10 prices → same regime); no `atr_trend`; no wall clock in outputs |
| `meta_decision` | The two bug reproductions above, plus: min participants; equal weights; confidence 1.2 → invalid → abstain (fix 6) |
| Snapshot | as_of floor; `known_by = t` hides a later backfill; evidence runs refuse replay snapshots; features equal `sigma_over(read_state(...))` with the same `known_by`; levels as of t (a corrupt-future test, like the scanner selftest); 5m candles complete-only |
| No look-ahead | Perturbing or appending bars at or after as_of → identical vote and proposal |
| Determinism | The same snapshot twice, and in a fresh process → identical bytes |
| Scale invariance (a property test) | Prices × c → same direction and confidence; distances × c |
| Proposal | Distances within [0.5σ, 3σ]; clip flags; every evidence ref passes `check_ref`; no structure → abstain with `no_structure_sl` / `no_structure_tp` (counted) |
| Levels stub | `NoLevels` gives `levels == ()` and `trigger_levels == ()` → abstain "no levels"; a fake `LevelSource` gives the touched level's id in `trigger_levels`; the interface carries no payload fields |
| Sigma model pin | An evidence experiment refuses any model but the pinned `parquet2021_2023` SHA |
| Eligibility | A window crossing the close → excluded with its reason; stale → skipped; `NoBarsError` → excluded |
| Integration | A pre-2024 replay week end to end: trigger → snapshot → vote → `score` and `score_neutral` → stored; skipped without the parquet |
| Dev backtest | Over 2007–2020: each event scored once; no bar at or after as_of reaches the agent; the outputs (σ_d, DE, rates) are reproducible byte for byte; it refuses dates ≥ 2021-01-01 |
| LLM variant (only if chosen) | Cache-only rerun is byte-identical; a cache miss errors; refusal, parse failure and `max_tokens` → abstain; the prompt hash is stable |

All tests go in `tests/agents_tests/` with unique basenames, and are covered by
CI's pytest and ruff (`agents` is linted).

## 7. Build order (later steps, for reference)

1. The `LevelSource` stub interface and `Snapshot.trigger_levels`. T1 builds
   the real `levels/` package.
2. Snapshot features (σ from the pinned parquet fit, L, S_hd, T) and the 5m
   timeframe.
3. Indicators (Wilder), regime, confluence and cascade ports, each with its
   fix tests.
4. `TechnicalAgent` with `Proposal`, and store migration 3.
5. The dev backtest over 2007–2020, which gives σ_d, DE and the rates →
   N in the PREREG.
6. Bid/ask capture for the 2-week cost window after the cut-over → c in the
   PREREG.
7. Final PREREG and freeze.
8. Live shadow runner (scanner journal → graph → scorer).

## 8. Owner decisions (2026-10-15)

1. **Touched level:** given through `Snapshot.trigger_levels`. T1 builds
   `levels/`; until then, a stub interface.
2. **5m:** added to the snapshot, resampled from 1m, complete candles only.
   The cascade isn't remapped.
3. **Indicators:** Wilder. Old results aren't evidence.
4. **No structure:** abstain, counted. A clip-bound fallback would blur Q2.
5. **Aggregator:** v1 scores the Technical vote directly. The aggregator is
   decided and pre-registered when a second agent exists.
6. **`meta_decision` bugs:** the reading of both bugs is confirmed.
7. **Freeze plan:** c = measured spread + commission; δ = 0.15 R; N from the
   dev run; cap 12 months; SL/TP re-anchored at entry; σ = HAR parquet fit,
   pinned by SHA.
8. **Development:** a full 2007–2023 backtest for the deterministic agent;
   masking only for future LLM agents. *(Cut to 2007–2020 on 2026-10-17 and
   confirmed by the owner the same day; 2021–2023 is a sanity check, PREREG
   §10.)*
9. **LLM:** none in v1.

**2026-10-17:** the primary horizon is H = 240 minutes, H = 60 secondary;
swing horizons are v2 (PREREG §13).

**2026-10-17, later (owner):**
- The dev period 2007–2020 is confirmed; 2021–2023 is a sanity check
  (PREREG §10).
- The SL/TP structure moves to the 240-minute horizon: the 1h swing low or
  the touched level − 0.1σ₂₄₀ for the stop; the next scanner level or the 4h
  20-bar high for the target; clipped with σ₂₄₀ (§3). This was decided
  before any code or result existed.
