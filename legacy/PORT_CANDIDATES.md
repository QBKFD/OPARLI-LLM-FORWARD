# Port candidates for the LangGraph agent rebuild

What in `legacy/` is worth carrying into the new agents, and what has to change
first. Paths are relative to `legacy/backend_v0/` unless stated otherwise.
Line numbers refer to tag `legacy-agents-v0`; the move didn't change file
contents.

**How this was checked (2026-10-14).** Every defect below was read in the code
at its legacy path. Defects marked **(ran)** were reproduced by executing the
legacy code, loaded by file path, against synthetic inputs or the real parquet.
Claims that couldn't be confirmed were dropped.

**Verdicts:**

| Verdict | Meaning |
|---|---|
| **PORT WITH FIXES** | Logic is worth keeping; apply the listed fixes, with tests, before use |
| **REWRITE** | The idea is needed; the code isn't. Write new code against tests |
| **DO NOT PORT** | Not needed by the new design, or harmful |
| **REFERENCE ONLY** | Not needed for shadow decisions; read it when that capability is built later (live trading) |

## Summary

| File | Verdict | Role in the new design |
|---|---|---|
| services/analysis/meta_decision.py (+ tests_agents/test_meta_renormalization.py) | PORT WITH FIXES (deterministic half only) | Aggregator node (code, fixed rules) |
| services/market_data_provider.py, services/clock.py | PORT WITH FIXES (`HistoricalDataProvider`, `_aggregate_1min`, `Clock`) | Snapshot builder: multi-timeframe bars as of t |
| services/technical_service.py, timeframe_cascade.py, confluence_checker.py, technical_indicators.py, regime_detector.py | PORT WITH FIXES | Technical agent node (code) |
| services/chart_generator.py | PORT WITH FIXES (render only) | Snapshot builder: deterministic chart PNG for the Visual agent |
| services/analysis/visual_analysis.py | REWRITE (prompt + parser) | Visual agent node (LLM) |
| agents/sentimentanalyst_agent.py | REWRITE | Sentiment agent node (LLM), fed point-in-time calendar events |
| config/news_events.py | PORT WITH FIXES | Calendar tiering for the Sentiment agent and the gate |
| config/llm_provider.py | REWRITE | LLM client layer (LangChain chat models) |
| services/stop_loss_calculator.py, take_profit_calculator.py, position_sizing.py, risk_service.py | DO NOT PORT | Replaced by one shared σ bracket for scoring; sizing later |
| services/hard_limit_checker.py | REFERENCE ONLY | Account limits, when real orders exist |
| agents/position_manager.py | REFERENCE ONLY | Exit manager, when real positions exist |
| agents/meta_agent.py, trademanager_agent.py, execution_agent.py, core/*, live_trading.py, services/weight_manager.py, services/session_tracker.py | DO NOT PORT | Bus actors, broker/paper plumbing, adaptive weights, a third session definition |

---

## services/analysis/meta_decision.py → aggregator node: PORT WITH FIXES

**What it is.** It maps analyst signals (BUY/BULLISH/LONG → LONG, unknown →
PASS: `:233-260`) to a weighted score renormalised over the analysts that
actually voted (`:262-361`), an agreement measure (`:327-346`), and fixed
thresholds (`:116-119`, `:483-489`). The deterministic half is the fixed-rule
aggregator the new design needs, and `tests_agents/test_meta_renormalization.py`
(6 tests) already pins the renormalisation. Port both. Don't port the LLM half
(`_make_llm_decision`, `:363-416`).

**Required fixes:**

1. **Direction must come from the winning side.**
   - The score is the max over both directions (`:325`), but the direction
     comes from a priority list, Technical > Visual > Sentiment (`:473-481`).
     **(ran)** With weights 0.4/0.2/0.4, Visual SHORT 0.9 + Sentiment SHORT
     0.9 + Technical LONG 0.55 gives **LONG** with score 0.72 (SHORT's
     score). Default weights mask it.
   - Fix: direction = argmax of the per-direction scores; PASS on a tie.
2. **A 2-vs-1 vote can never pass the low tier.**
   - Agreement for 2-vs-1 is 1 − 1/3 = 0.6667 (`:346`), and the low tier
     requires `>= 0.67` (`:119`, `:486`). **(ran)** Equal weights, LONG .95 +
     LONG .95 + SHORT .1 → score 0.633, agreement 0.6667 → PASS.
   - Fix: define agreement thresholds as fractions (≥ 2/3) or as vote counts.
3. **A 1-vs-1 split passes the agreement gate.**
   - A tie gives agreement 0.5 (`:338-346`), and the high tier accepts
     `>= 0.5` (`:118`, `:484`). **(ran)** Weights .2/.75/.05: Visual SHORT .9
     vs Technical LONG .95 → **LONG**.
   - Fix: require the winning side to have strictly more votes.
4. **One voter can decide alone** because of renormalisation (`:317-321`).
   **(ran)** Visual LONG 0.7 with two PASS → LONG at score 0.7, agreement
   1.0. It's intended (`:121-130`), but the new design must decide a minimum
   number of participants explicitly.
5. **Start with equal weights.** The defaults .35/.50/.15 (`:109-113`) were
   never validated.
6. **Strict input validation** (pydantic).
   - Confidence > 1 is divided by 100 (`:257-258`), so **(ran)** 1.2 becomes
     0.012 while 70 becomes 0.7.
   - A malformed LLM reply becomes PASS with confidence 0 (`:443-451`), which
     looks the same as a deliberate abstention.
   - Fix: invalid input = abstain with reason, recorded separately from PASS.
7. **No wall clock in outputs.** `datetime.now()` goes into the LLM prompt
   (`:396`). That path isn't ported, so just don't carry it over.

**Tests to port.** `tests_agents/test_meta_renormalization.py`, plus new
tests for fixes 1–4 (the reproductions above are the test cases).

## services/market_data_provider.py + services/clock.py → snapshot data access: PORT WITH FIXES

**What it is.** `HistoricalDataProvider` serves multi-timeframe OHLCV with a
strict `< as_of` cutoff by binary search (`:128`). `_aggregate_1min` resamples
1-min bars to 5m/15m/1h/4h (`:45-69`). `Clock` / `SimulatedClock`
(`clock.py:22-60`) give injectable time. In the new repo, 1-min bars come from
`datafeed.store.query.bars_before`, and only the resampling and the clock need
porting.

**Required fixes:**

1. **One bound everywhere.** The Live provider uses `timestamp <= as_of`
   (`:203`, `:248`); Historical uses `< as_of` (`:128`, `:148`). Drop
   `LiveDataProvider` (`:154-257`, Postgres, and it swallows errors into an
   empty result at `:231-234`) and keep one `<` rule.
2. **`get_price(as_of=None)` returns the frame's last close** (`:146-147`),
   despite the class docstring saying as_of is required (`:95-96`). **(ran)**
   It returned the final bar of the frame. Fix: raise.
3. **Partial higher-timeframe candles.**
   - The last 5m/1h/4h candle is built from whatever 1-min bars exist before
     `as_of` (`:59-64`), so it's usually partial. **(ran)** At 10:07, the 5m
     candle 10:05 had 2 of 5 bars and the 1h candle 10:00 had 7 of 60. Both
     were returned as the latest candle, and every indicator treats them as
     complete.
   - Fix: drop the forming candle, or flag it and have every consumer decide
     explicitly.
4. **The forming 1-min bar when `as_of` isn't on a minute boundary.**
   - `< as_of` keeps the bar that opened at, e.g., 10:07 for `as_of` 10:07:30.
     In historical data that bar's OHLC covers up to 10:08: up to 59 s of
     look-ahead.
   - Fix: snapshot times on minute boundaries, or the rule
     `bar_ts + 1 min <= as_of`.
   - The same applies to `datafeed.store.query.bars_before`. Raise it in the
     datafeed follow-up, not here.
5. **Unknown timeframes silently become 1-min.** `TIMEFRAME_MINUTES.get(tf, 1)`
   (`:134`); **(ran)** `'2h'` returned 1-minute bars. Fix: raise.
6. **Volume is summed** (`:62`). Historical bars have vendor volume, and live
   bars have none (see the technical stack). Drop volume from snapshots.
7. **`SimulatedClock` (`clock.py:40`) is never instantiated anywhere in the
   repo.** The only `Clock` user is the Trade Manager (`agents/trademanager_agent.py:18`, `:92`, `LiveClock`).
   - Many components still call `datetime.now()` directly (5 in
     `agents/scanner_agent.py`, 4 in `agents/meta_agent.py`, 4 in
     `agents/position_manager.py`, …).
   - Port the clock and make the snapshot builder its only time source.

## Technical stack → Technical agent node: PORT WITH FIXES

Covers `technical_service.py` (entry point, `:40-68`), `timeframe_cascade.py`
(cases A–D across 5 timeframes), `confluence_checker.py` (5 voters → 0.60 /
0.75 / 0.90), `technical_indicators.py` and `regime_detector.py`.

The Technical agent must be frozen before the first shadow decision. Every fix
below changes its output, so apply them first, then freeze.

**Required fixes:**

1. **VWAP is meaningless live and never resets daily.**
   - `confluence_checker.py:33`: VWAP is one of the 5 voters.
   - `technical_indicators.py:142`: VWAP is a `cumsum` over whatever window was
     passed in, with no session reset.
   - With zero live volume, VWAP is NaN, which becomes `None`
     (`technical_indicators.py:329`), and the voter returns NEUTRAL
     (`confluence_checker.py:271-272`). So live, at most 4 voters can agree,
     and the 0.90 tier (`confluence_checker.py:393`) is reachable only on
     historical data that has volume.
   - The regime detector also uses VWAP: with `vwap` None, `price_range` is
     forced to 5 and `range_bound` is always False (`regime_detector.py:81-82`).
   - Fix: remove VWAP and everything volume-based.
2. **Regime thresholds are absolute dollars.**
   - The VOLATILE cut-off is `atr > $15` (`regime_detector.py:31`, `:110`,
     `:184`).
   - **(ran, real parquet)** The share of 4h bars with ATR > $15 is 2.5% in
     2023 and 73.4% in 2025. For 1h it's 0% vs 21.7%.
   - Fix: relative thresholds (ATR / price, or the VolBlock `L` feature).
3. **"ATR trend" is never anything but "decreasing" (new).**
   - `regime_detector.py:68-69` compares the mean absolute *percent* change ×
     100 (≈ 0.01–0.1) with the ATR in *dollars* (≈ 0.4–20).
   - **(ran, real parquet, 2023 and 2025, 1m/5m/1h/4h)** `atr_trend` was
     `decreasing` for 100% of bars. The strong TRENDING (0.85) and strong
     RANGING (0.80) branches (`:188-196`) can therefore never fire, and the
     regime comes only from the weaker fallbacks.
   - Fix: compare like with like, or drop the feature.
4. **ATR must raise on NaN.** A NaN ATR silently becomes $10.00
   (`technical_indicators.py:323`), which feeds the regime thresholds and
   (in the old pipeline) stop distances. RSI NaN silently becomes 50 (`:317`).
5. **Support/resistance includes the forming bar.** The 20-bar max high / min
   low uses `tail(20)` of the frame, including its last bar
   (`technical_indicators.py:177-181`). Together with provider fix 3 above,
   that bar is a partial candle on higher timeframes. Fix: exclude it.
6. **Indicator definitions.**
   - RSI uses a simple rolling mean, not Wilder smoothing
     (`technical_indicators.py:39-40`).
   - ATR uses a simple rolling mean (`:104`).
   - Keep or change them, but write the choice down. Changing them breaks
     comparability with old backtests.
7. **No wall clock in results.** Each timeframe result carries
   `datetime.now()` (`timeframe_cascade.py:165`). Use the snapshot time.

## services/chart_generator.py → chart for the Visual agent: PORT WITH FIXES (render only)

**What it is.** It fetches from Postgres, computes pandas_ta indicators,
overlays session levels, and renders with mplfinance. Port only `_render_chart`
(`:459-545`) and its styling. The data must come from the snapshot.

**Required fixes:**

1. **The `timestamp` argument doesn't bound the data.**
   - It defaults to `datetime.now()` (`:141`) and is only written into the
     metadata (`:202`).
   - The query is "latest N rows" (`:224-239`).
   - Fix: render from the snapshot's bars, never query.
2. **Session levels come from a second data path, with look-ahead.**
   - `_get_session_levels` (`:370-388`) calls
     `session_tracker.get_current_levels` (`services/session_tracker.py:252`),
     which reads Postgres `session_levels` for the chart's *date*.
   - `_prepare_hlines` draws every level returned, with no `is_complete`
     check (`:436`).
   - In a replay, today's daily/weekly high and low are the full-period
     values: future data drawn on the chart.
   - Those sessions are also Asia 00–09, London 08–17, NY 13–22 UTC
     (`services/session_tracker.py:27-45`), which is not the scanner's 00–07 /
     07–12 / 12–21.
   - Fix: draw levels from the one level library, as of t.
3. **Bollinger Bands are silently missing today.**
   - The column names are string templates, `BBU_{length}_{std}.0`
     (`:301-303`).
   - **(ran)** The installed pandas_ta 0.4.71b0 names them `BBU_20_2.0_2.0`,
     so this raises KeyError, which is swallowed (`:318`).
   - pandas_ta itself is optional (`:16-21`: `ta = None` when missing), and
     then every indicator fails the same silent way.
   - Fix: compute indicators in our own code, and fail loudly.
4. **Figures are never closed.** There's no `plt.close(fig)` after `savefig`
   (`:535-540`), so memory grows in a long-running process.
5. **Only the first chart was ever used.** `agents/scanner_agent.py:329-335`
   sends only the first chart (1-min, 200 bars ≈ 3h20m) to the Visual
   analyst, so there's no evidence about multi-timeframe images. Choose the
   chart set deliberately and keep it fixed.
6. **The price axis reveals the era.** Absolute prices date any historical
   chart. That's acceptable only because LLM agents are evaluated forward-only.

## services/analysis/visual_analysis.py → Visual agent node: REWRITE (prompt + parser)

1. **The keyword fallback fabricates trades.**
   - When JSON parsing fails, `_parse_text_response` (`:238-281`) picks a
     direction from keywords.
   - **(ran)** "No clear setup here, I wouldn't buy at these levels." →
     **LONG 0.80** ("buy" → LONG 0.70 at `:255`, "clear" adds 0.10 at
     `:259`). "Price is not bullish. I would not go long." → **LONG 0.70**.
   - Fix: delete it. An unparseable reply = abstain with reason.
2. **Errors look like NEUTRAL decisions.**
   - API errors and parser exceptions return `NEUTRAL, confidence 0`
     (`_fallback_analysis`, `:350-362`; caught at `:156-158` and `:346-348`).
   - **(ran)** `"confidence": "85%"` raises TypeError and a null signal raises
     AttributeError inside the parser (`:215`, `:223`), and both end up as
     NEUTRAL 0.
   - Fix: record errors separately from votes.
3. **Biased prompt.**
   - The only worked example is bullish (confidence 85, "Strong bullish
     setup targeting 2680") at 2024 prices (`:50-62`).
   - It tells the model to "Pay special attention to FVG/IFVG as they indicate
     institutional activity" (`:48`). The repo's own FVG/order-block tests were
     rejected (`legacy/scripts/no_go_strategies/`).
4. **Two confidence gates.** "Only recommend BUY/SELL if … >70%" (`:44`) gates
   a self-reported, uncalibrated number, and the aggregator applies its own
   thresholds on top. Keep one: the aggregator's.
5. **Ambiguous confidence scale.** Values > 1 are divided by 100 (`:223-224`),
   so `1` means 100% and `1.5` means 1.5%. Fix: one schema with a fixed 0–1
   scale.
6. **Context lines that mislead.**
   - The prompt context prints current price and "Volume" (`:171-173`), and
     live volume is meaningless.
   - Fix: send only what's in the snapshot, and no volume.

## agents/sentimentanalyst_agent.py → Sentiment agent node: REWRITE

1. **It receives no news or calendar data at all.**
   - `_build_market_context` (`:207-249`) sends only price, % change, SMA20/50,
     volume and a trend label.
   - So "sentiment" is a second technical opinion, and every macro reason it
     gives comes from the model's memory (a look-ahead source in any
     historical run, a hallucination source live).
   - The prompt example "Fed signaling rate cuts in 2024" (`:75`) primes it.
2. **New input:** point-in-time calendar events as of t (`config/news_events.py`
   tiers), with times converted to UTC. The output schema is the same as the
   other agents.

## config/news_events.py → calendar tiering: PORT WITH FIXES

Tier-1/Tier-2 event lists (`:3-89`) and blackout windows (`:94-95`) are
reusable. Nothing outside legacy uses it today.

1. **The Tier-1 after-window is 45 min, not 30.**
   - `(tier_events - timestamp).abs() <= before` (`:135`) also matches events
     up to `before` minutes in the past, so the `after` window is dead code
     whenever `after < before`.
   - **(ran)** An NFP release blacks out −45 … +45 min, not −45 … +30.
2. **Times are left naive.** `tag_events` parses `Combined DateTime` without a
   timezone (`:122`). `database/historical_news/DATA_NOTES_events.md` (repo
   root) says the raw scrapes are fixed UTC+8 (2015–2023) and Europe/Brussels
   (2024–2025), so naive use is off by 7–9 hours. The converter that produced
   the verified UTC file isn't in the repo. Fix: convert at ingest and reject
   naive times.

## config/llm_provider.py → LLM client layer: REWRITE

Replace with LangChain chat model classes (pinned model ids, explicit
timeouts and retries, token usage and request ids logged).

1. **Legacy Gemini SDK** `google.generativeai` (`:96`, `:109`, `:136`, `:180`).
2. **The provider depends on the environment.** `ENV=production` → Claude,
   otherwise Gemini (`:482`, `:516`). Dev and prod run different models.
3. **The process-wide singleton** keeps whichever provider was created first
   (`:472`).
4. **An unknown provider silently falls back to Gemini** (`:491`).
5. **Model ids.**
   - The Gemini default is the unpinned alias `gemini-2.5-pro`
     (`DEFAULT_MODELS`, `:431`), and the class default is a different model,
     `gemini-1.5-pro` (`:89`).
   - Claude is pinned to `claude-sonnet-4-20250514` (`:247`), which is now
     superseded.
6. **Only text is returned.** The calls return `message.content[0].text`
   (`:276`, `:320`, `:391`), dropping token usage, stop reason and request id,
   so a reply truncated at `max_tokens` can't be detected. Image calls
   hard-code `max_tokens=2000` (`:296`, `:380`), and the Gemini image path
   sets none.
7. **Generic system prompt.** It defaults to "You are a helpful assistant."
   (`:270`, `:298`, `:382`).
8. **No explicit timeout or retry policy.** Behaviour is whatever each SDK
   defaults to.

## SL/TP calculators, position_sizing.py, risk_service.py → DO NOT PORT

The new design scores every arm with one shared exit: a fixed horizon in
VolBlock σ units, plus a bracket stop k·σ / target 2k·σ. These files encode
strategy choices that would differ by arm:

1. **TP rejects breakouts.** A LONG target is the 20-bar high, which includes
   the forming bar (see technical fix 5). Any trade with < 1.5R to that level
   is rejected (`take_profit_calculator.py:83`, `:110`). A LONG at a 20-bar
   high has resistance ≈ entry, so momentum signals are filtered out and
   mean-reversion ones kept.
2. **The stop depends on which timeframe led.** The stop is the wider of
   2×ATR and support − 0.5×ATR (`stop_loss_calculator.py:69-98`), clamped to
   1.5–3×ATR (`:164-184`). The ATR is the lead timeframe's, falling back through
   15m/5m/1m/1h/4h (`agents/meta_agent.py:473-474`), so R changes meaning
   from trade to trade.
3. **Half of clamped trades are rejected by float rounding (new).**
   - `stop_distance` is rounded to cents (`stop_loss_calculator.py:121`) and
     then checked against the unrounded `1.5*ATR <= d <= 3.0*ATR`
     (`risk_service.py:133`).
   - **(ran, real calculator)** For LONG trades whose support is more than
     2.5 ATR below entry (so the 3×ATR clamp binds), **50.0%** of 20,000 were
     rejected as `stop_distance_reasonable` failures. It's a coin flip on the
     rounding direction.
4. **Sizing: the 3% notional cap always binds.**
   - Risk % comes from confidence tiers (`position_sizing.py:29-33`), size =
     risk $ / stop distance (`:88`), then capped at 3% of balance in notional
     (`:36`, `:96-99`).
   - **(ran)** $10k balance, entry 4895, stop 4883 → **0.06 oz, $0.74 risk
     (0.01%) at every confidence tier**. The cap stops binding only for stops
     wider than ~$816 (0.5% tier) at this price.
   - The implicit multiplier is 1 (P&L = price difference × units), which is
     correct for IBKR spot XAUUSD in ounces. `live_trading.py:113` assumes
     ×1000.
   - Size is rounded to 0.01 (`:109`, `:122`).
5. **Absolute-dollar defaults.** Spread defaults to $0.30
   (`take_profit_calculator.py:49`, `risk_service.py:74`), and the acceptance
   test is `spread < 0.50` (`risk_service.py:130`).

When real orders come, write a fresh fixed-fractional sizer with an explicit
contract multiplier and IBKR's minimum increment.

## services/hard_limit_checker.py → REFERENCE ONLY

**Limits:**
- daily loss −6%
- drawdown −15%
- 2 open positions
- 10 trades/day
- $1,000 minimum balance

These are at `:32-37`, and it's a pure function of an injected
`account_state`, apart from best-effort DB logging (`:133-134`). Not needed
for shadow decisions.

If reused for live trading, note:
- **Missing fields default to 0** (`:74`, `:86`, `:98`, `:110`, `:122`). It
  fails open on loss, drawdown, positions and trades, but a missing balance
  means "STOP_ALL_TRADING_PERMANENTLY".
- **Make missing fields an error.**

## agents/position_manager.py → REFERENCE ONLY

An in-memory exit engine that nothing imports. It does:
- SL/TP checks;
- a trailing stop after +$20 at 2×ATR (`:158-163`);
- breakeven after +$15 (+$2) (`:168-170`);
- Friday 20:00 close and 48 h max hold (`:182-184`);
- an RSI ≥ 75 / ≤ 25 exit (`:189`);
- MFE/MAE tracking.

The ideas are useful for a later live exit manager; the code isn't:

1. **Wall-clock time** for entry time, time exits and hold time (`:246`,
   `:436`, `:523`, `:129`). It can't replay or score after the fact.
2. **ATR and RSI come from Postgres** "latest rows" (`:550`, `:586`), with
   no as_of.
3. **Fixed dollar thresholds** set at a much lower gold price (`:158-170`).
4. **It widens the caller's stop** to 2.5×ATR when that's wider (`:225-239`),
   so planned R ≠ actual R.
5. **Short indicators.** The ATR is a simple mean of 14 one-minute true
   ranges, and the RSI is a simple mean, not Wilder (`:611`).
6. **One price per update.** With bars, a bar touching both stop and target
   has no defined order.

## DO NOT PORT

| File | Why (verified) |
|---|---|
| agents/meta_agent.py | Its pre-trade LLM path never runs. It hands its own template, which contains `{evolved_instructions}`, to `MetaDecisionService` (`:284-287`). That service formats it without that key (`services/analysis/meta_decision.py:391-398`): **(ran)** `KeyError: 'evolved_instructions'`, caught at `meta_decision.py:217-218`, so it always falls back to the deterministic rule |
| agents/trademanager_agent.py | The first trade crashes it. It reads `action`, `quantity`, `entry_time` (`:170-176`), but Execution sends `direction`, `position_size`, `fill_time` (`agents/execution_agent.py:182-191`), so it hits a KeyError on `'action'`. `entry_atr` defaults to 10.0 (`:177`) |
| agents/execution_agent.py | Broker plumbing; the mock fill price is a hard-coded 2650.5 (`:246`). The new design has no broker |
| core/* (message bus, scheduler, orchestrator) | Replaced by LangGraph |
| live_trading.py | Paper simulator with margin = volume × 1000 / leverage (`:113`), inconsistent with the ounce-based sizing above |
| services/weight_manager.py | Adaptive weights ×1.05 per win / ×0.95 per loss (`:23-24`). Fixed rules must not adapt during an evaluation |
| services/session_tracker.py | A third session definition (`:27-45`), Postgres-backed, used for the chart overlay's look-ahead above |
