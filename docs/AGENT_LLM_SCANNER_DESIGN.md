# LLM scanner agent: design (draft, no code)

**Status: design draft, written 2026-10-17 overnight, unattended. Nothing is
built, nothing calls an LLM, and no PREREG exists yet.**
- The experiment it describes needs its own PREREG under
  `agents/experiments/<id>/` before the first evidence decision, like agents
  v1.
- Choices made without the owner are in the root `DECISIONS.md`, section
  `research/level-target-spec` (L1–L13). The owner's answers of 2026-10-17
  are in §14 (L14).
- The multi-agent flow agreed later that day (`docs/AGENTS_ARCHITECTURE.md`,
  PR #17) reuses this document's frozen-model, cache and epoch rules.

## 1. Purpose

When the deterministic scanner sends an alert, an LLM agent looks at a
frozen snapshot of what was knowable at that moment and answers **TAKE or
SKIP**. A TAKE comes with a direction, a stop and target in σ, and evidence
references into the snapshot. It runs in **shadow mode**: it trades
nothing, and the owner doesn't see it.

**The question:** on the same alerts, are the agent's TAKE/SKIP choices
worth more than the owner's own? And are they worth more than the
deterministic benchmarks (take-all, and the agents v1 Technical agent)?

This is the planned "LLM scanner agent" of agents v2 (agents v1 PREREG
§13).

**Why forward only.** An LLM can't be backtested honestly:
- it has seen gold prices and news up to its training cutoff, so it may
  "remember" a dated period;
- its sampling isn't reproducible (§9).

So the evidence comes only from alerts after the freeze, all of them after
2026-10-01 and after the model's training cutoff (Claude Opus 5.5: June
2026). History is used for plumbing and dry runs only, never as evidence.

## 2. Trigger and timing

- **Source:** the scanner journal's `alerts` table, rows with
  `status = 'sent'`, the same alerts the owner gets on Telegram.
  - The runner reads the journal **read-only** (`sqlite3` URI `mode=ro`) and
    polls it every 5 s.
  - It never writes to the journal, and the scanner isn't changed.
- **One alert, one decision.** Idempotent by `(journal epoch, alert_id)`. A
  restart resumes from the agent store, never re-asking an alert already
  decided or abstained.
- **Decision time t:** when the runner picks the alert up. The snapshot is
  built as of `floor(t, 1 min)` in live mode (`known_by = t`,
  `experiment_id` set). This is the existing agents snapshot rule.
- **Deadline:** an answer must be stored within **5 minutes** of the
  alert's journal insert time. Otherwise the agent abstains with reason
  `deadline`. A late answer is stored but not scored.
- **Common entry for scoring both arms:** the first 1-min bar opening at or
  after alert insert + 5 min.
  - The owner often replies later, and with more information (price kept
    moving). Scoring both at the same entry measures **selection and
    direction**, not execution speed.
  - The owner's later information is a bias **in the owner's favour**,
    conservative for any claim that the agent is better. The owner's reply
    lag is reported.

## 3. The frozen snapshot

Built and hashed **before** the call, and stored before anything else sees
it (the existing `agents.snapshot` rules).

| Part | Content | Source |
|---|---|---|
| Alert | exactly the Telegram fields: id, time, session, level types, level price, price, confluence | journal row |
| Bars | 1m (240), 5m, 15m (192), 1h (120), 4h (120) with `complete` flags | `bars_before(..., known_by=t)`; resampled by `agents/resample.py` |
| Charts | three PNGs rendered **from the snapshot's bars only**: 15m over 2 days, 1h over 5 days, 4h over 20 days. Each has the scanner levels active at t drawn as lines, the touched level highlighted, and no indicators. Fixed size (1000 × 600 px) and style; `plt.close` after each | a port of legacy `_render_chart` (PORT_CANDIDATES: render only, from the snapshot, levels as of t) |
| Levels | every level instance active at t, with id, type, value, `active_from` and touched-or-not, plus `trigger_levels` | the shared level library (`LevelSource`, agents v1 design §2) |
| σ and vol state | `sigma_px_60`, `sigma_px_240`, `crosses_close_*`, L, S_hd, T, the L tercile | `vol_block` with the pinned HAR model (`parquet2021_2023`, SHA-256 checked) |
| Calendar | events in [t − 6 h, t + 24 h]: UTC time, currency, tier (from the ported `news_events` tiers), name from an allowlist, and actual / forecast / previous **only if released before t** | a point-in-time calendar store: each scrape stored with its fetch time, read with `known_by = t`. **Not built yet.** It is built first (§13); if it isn't ready within 2 weeks, the agent launches without it (§14) |
| Provenance | snapshot schema version, renderer version and source hash, model and prompt hashes (§8) | the runner |

- **Hashing:** the snapshot hash covers the canonical JSON and the SHA-256 of
  each chart PNG. The PNGs are stored content-addressed next to the agent
  store.
- **Same information as the owner?** No, and the PREREG says so.
  - The owner sees the Telegram facts and their own charts, and knows the
    news.
  - The agent sees this snapshot and nothing else: no web, no tools, no
    news text.
  - The comparison is "this agent with this snapshot" against "the owner as
    they trade".
- **Never in the snapshot:** the agents v1 Technical agent's vote, or any
  other agent's (owner decision 2026-10-17). The arms stay independent.

## 4. The call

- **Model:** pinned by exact id: **`claude-opus-5-5`**, effort `medium`
  (owner decision 2026-10-17), with no alias and no date suffix. A response whose `model` field differs from
  the pin is an error (abstain).
- **Request**, all pinned and hashed:
  - `system`: the frozen prompt file;
  - one `user` message: the snapshot JSON, then the three images;
  - `output_config`: `effort` pinned (proposal: `medium`) and `format`, the
    JSON schema of §5 (structured outputs);
  - `max_tokens` 16000 and adaptive thinking (Opus 5.5 can't turn thinking
    off).
  - No `temperature` (current models reject sampling parameters with a 400),
    no tools, no web search.
- **No refusal fallbacks.** A server-side fallback would answer with a
  different model, which breaks the frozen-model rule. A `refusal` stop
  reason → abstain with reason `refusal` and its category.
- **Client:** the official `anthropic` Python SDK.
  - A 120 s timeout per attempt and at most 2 retries (the SDK's own policy
    for 429, 5xx and connection errors), inside the 5-minute deadline.
  - Each attempt logs its `request_id`, usage and stop reason.
  - **Prompt caching off:** alerts arrive hours apart, past both TTLs, and
    the static prefix is too short to pay for a 1-hour write (§10).
- **The prompt asks for:**
  - a decision on this alert, at the fixed 240-minute horizon;
  - SKIP as the default when the evidence is thin;
  - every claim tied to a ref.

  It has **no worked example with a direction** (legacy's bullish example
  biased the old Visual agent; PORT_CANDIDATES), no "institutional activity"
  framing and no confidence gate.

## 5. Structured decision

```json
{
  "decision": "TAKE | SKIP",
  "direction": "long | short | null",          // required iff TAKE
  "stop_sigma": 0.5..3.0 | null,               // distance from entry in sigma_240 units, iff TAKE
  "target_sigma": 0.5..6.0 | null,             // iff TAKE
  "confidence": 0.0..1.0,                      // reported, never gated on
  "evidence": [ {"ref": "<ref>", "claim": "<= 200 chars"} ],   // 1-6 items; >= 1 iff TAKE
  "rationale": "<= 600 chars"
}
```

- **Refs** use the existing grammar (`agents/debate/evidence.py`):
  `bars:<tf>:<start>..<end>`, `level:<id>`, `feature:<name>`, `event:<id>`,
  plus one new kind, `chart:<tf>`, which names one of the three images.
- **Validation in code, after the schema:**
  - TAKE needs a direction, both distances and at least one ref;
  - SKIP has none of them;
  - every ref must resolve in the snapshot (`check_ref`).

  Any failure → **abstain** with the reason, stored apart from SKIP. An
  unparseable or `max_tokens`-truncated reply is never turned into a
  direction (legacy's keyword fallback "fabricated trades"; PORT_CANDIDATES).
- **A resolving ref can still carry a false claim.** That can't be checked
  in code. A random 10 % of TAKEs are listed for the owner's spot-check after
  the epoch ends (§6), and the count of wrong claims is reported.

## 6. Shadow mode and blinding

- **No broker, no orders, no Telegram message.** The agent's decisions go
  only to the agent store (append-only, as today).
- **The owner doesn't see the agent's decisions** during the epoch. Seeing
  them would change the owner's replies, which are the comparison arm.
- **Weekly integrity checks only, never outcomes:**
  - alerts seen;
  - decisions, abstentions by reason, deadline misses;
  - API errors and spend.

  These are the same rules as agents v1 §9.
- **The owner's replies** are read from the journal's `decisions` table: the
  first decision row per alert in its epoch. `MISSED` (no reply in 4 h)
  counts as no decision by the owner.

## 7. Paired comparison with the owner (for the PREREG)

- **Unit:** an alert where **both** arms decided: the owner replied TAKE or
  SKIP, and the agent answered validly by the deadline. Owner `MISSED`,
  agent abstentions and the overlap of both are counted and reported, not
  imputed.
- **Outcome per arm and alert** (the same scorer, the same bars, the common
  entry of §2):
  - V = net R of the **neutral bracket** (1σ stop / 2σ target, σ = HAR at
    240 min, `score_neutral`) in the arm's direction, if TAKE; 0 if SKIP.
  - Net of the measured cost, as agents v1 §4.
- **Primary statistic:** Δ = mean over alerts of (V_agent − V_owner). The
  test is a paired one-sided t-test with errors clustered by New York trading
  day: H1 Δ > 0.
- **The owner's direction:** a TAKE without one (`parse_reply` allows it) is
  excluded from Δ and counted.
- **Reported, not tested:**
  - the 2 × 2 agreement table and Cohen's κ;
  - the take rate per arm;
  - mean V on the alerts where the arms disagree;
  - each arm's own SL/TP outcome (the agent's σ distances, the owner's
    journal stop/target), against the neutral bracket;
  - V against **take-all** (every alert, the agent's direction) and against
    the **agents v1 Technical agent** on the same alerts. The deterministic
    agent is the benchmark an LLM agent has to beat (context, 2026-10-17).
- **Rough size:**
  - Many alerts will be SKIP for both, so most differences are 0. With σ_d ≈
    0.8 R per alert, DE 1.3 and δ = 0.10 R per alert, n ≈ (2.80 × 0.8 / 0.1)²
    × 1.3 ≈ **650 paired alerts**.
  - At ≈ 3.5 paired alerts a trading day, that is ≈ 9 months.
  - The PREREG fixes N from a pilot's σ_d (the first 2 weeks, outcomes not
    looked at), with a 12-month cap.

## 8. Frozen model and prompt; epochs

An **epoch** is one fixed set of:
- the model id;
- effort, `max_tokens` and the thinking setting;
- the SHA-256 of the system prompt file;
- the SHA-256 of the output JSON schema;
- the snapshot builder version;
- the chart renderer's source hash and its rendering settings;
- the calendar source version;
- the runner code commit;
- the σ model SHA-256;
- the scanner journal epoch.

- **Any change** to any of them, including a model deprecation, a scanner
  restart into a new journal epoch, or a prompt typo fix, **ends the epoch**.
  A new `experiment_id` starts with a PREREG addendum.
- **Results are never pooled across epochs.** An epoch shorter than its
  planned N is reported as underpowered, with its power at the achieved n.
- **Recorded on every decision:** the epoch id, `prompt_hash` (the SHA-256
  of the canonical request body), `model`, `request_id`, usage (input,
  output and thinking tokens), stop reason and latency. The agent store
  already has `model_id`, `prompt_hash`, token and latency columns
  (`agents/store/sqlite.py`); `request_id`, the epoch id and the stop reason
  need a store migration.

## 9. Reproducibility

- **Sampling isn't reproducible.** There is no `temperature` on current
  models, and thinking is always on. The same snapshot can get a different
  answer.
- **A response cache gives exact reruns.** An append-only table:
  `llm_responses(prompt_hash PK, model, request_id, response_json, usage,
  stop_reason, created_at)`. Live mode calls the model, stores the response,
  then uses it. **Rerun mode is cache-only**: a miss is an error, never a new
  call. This is the plan already written for an LLM Technical variant (agents
  v1 design §4).
- **Self-consistency is measured.** On a random 10 % of alerts, chosen by
  hashing the alert id, the same request is sent 2 more times after the
  deadline. They are stored as repeats and never scored. The agreement rate
  of the three answers is reported. It says how much of the agent's
  "decision" is sampling noise.

## 10. Estimated cost per day

Per decision (estimate; `count_tokens` is run on the real prompt before the
freeze):

| Part | Tokens |
|---|---:|
| System prompt (static) | ≈ 2,500 |
| Snapshot JSON (alert, levels, σ/L/S/T, calendar, a compact 15m table) | ≈ 3,000 |
| 3 charts at 1000 × 600 px (≈ w·h/750 each) | ≈ 2,400 |
| **Input** | **≈ 8,000** |
| Visible output (the JSON) | ≈ 400 |
| Thinking at `medium` effort (billed as output) | ≈ 1,500–4,000 |
| **Output** | **≈ 2,000–4,500** |

Prices are the current list prices per million tokens (input / output), as
of 2026-10-06:

| Model | Per decision | Per day at 5 alerts (+10 % repeats) | Per year (≈ 260 trading days) |
|---|---:|---:|---:|
| Claude Opus 5.5 ($4 / $20) | ≈ $0.07–0.12 | ≈ $0.40–0.70 | ≈ $100–175 |
| Claude Sonnet 5.5 ($2 / $10) | ≈ $0.04–0.06 | ≈ $0.20–0.35 | ≈ $50–90 |
| Claude Haiku 5.5 ($0.10 / $0.50) | ≈ $0.003 | ≈ $0.02 | ≈ $5 |

- **Busy days** (up to ~10 alerts) double the day's figure.
- **A hard cap** of 40 calls a day (repeats included) bounds a runaway at
  ≈ $5/day on Opus. Past it, the agent abstains with reason `budget`.
- **Prompt caching** isn't worth it here: one call every hour or more,
  while a cache entry lives 5 minutes (or 1 hour at 2× the write price).
- **The model is Opus 5.5 at effort `medium`** (owner decision, §14), so
  the Opus row applies.

## 11. Failure handling

Each of these is logged with context (alert id, request id, exception), and
none crashes the runner.

| Failure | Result |
|---|---|
| Stale snapshot / invalid HAR state | no call; abstain `stale` / `no_sigma` (the existing snapshot rules) |
| API timeout, 429, 5xx after retries | abstain `api_error` |
| `refusal` | abstain `refusal:<category>` |
| `max_tokens` | abstain `truncated` |
| Schema or ref validation fails | abstain `invalid:<reason>`, the raw reply stored |
| Response `model` ≠ pin | abstain `model_mismatch`, and the epoch is flagged |
| Deadline passed | abstain `deadline` (the answer is stored, not scored) |
| More than 20 alerts queued | the oldest abstain `backlog` (bounded queue) |
| Daily call cap reached | abstain `budget` |
| Journal unreadable | retry every 30 s and log each failure; no decisions. The heartbeat goes stale, so the deploy stack's watchdog can alert on it |

## 12. Where it runs

- **A separate process** next to the scanner on the EC2 host. It only reads
  the journal and the datafeed store, and writes its own agent store and
  chart files.
- **Memory:** about 200–300 MB (Python, pandas, matplotlib, the SDK). Charts
  are rendered one at a time and closed. A container limit of 384 MB fits
  the 3.8 GB host next to today's 2.88 GB of limits (root DECISIONS.md, D14).
  This is to be measured before it is deployed.
- **Network:** egress to the Anthropic API only.
- **The key:** `ANTHROPIC_API_KEY` lives in the host's `.env`, never in the
  repo or in logs. gitleaks runs in CI.
- **Health:** a heartbeat file, as the scanner's.
- **Deploy and watchdog wiring** belong to the deploy stack (`deploy/`,
  another session's area) and are left to that work. Nothing in `deploy/`
  changes here.

## 13. Build order and tests

1. **The point-in-time calendar store:** the scraper output saved with its
   fetch time and read with `known_by`. Tests: an event scraped after t is
   invisible at t; actuals appear only after release.
2. **The chart renderer** from snapshot bars. Tests:
   - the same snapshot gives byte-identical PNGs;
   - no level from after t is drawn;
   - figures are closed (no memory growth over 1,000 renders).
3. **Snapshot extension:** charts, calendar, levels, σ at 60 and 240.
   Tests: `known_by` hides later backfills; the hash covers the PNGs.
4. **The `llm_responses` cache and an `LLMClient` protocol** with a fake for
   tests. Tests:
   - cache-only reruns are byte-identical;
   - a miss errors;
   - refusal, `max_tokens`, a schema failure, a model mismatch and a timeout
     all abstain;
   - **no unit test makes a network call.**
5. **The validator** (schema + refs + the TAKE/SKIP rules), with property
   tests.
6. **The runner:** journal poll, idempotence across restarts, the deadline,
   the bounded queue, the budget cap. Tests run against a temporary journal
   built with the scanner's own `open_log`.
7. **The scorer glue:** the common entry and the paired V for both arms,
   tested on synthetic bars.
8. **A dry run on replay data** (pre-2021 only, masked dates and rescaled
   prices for the LLM), for plumbing and cost measurement only, never
   evidence.
9. **PREREG, freeze, epoch 1.**

## 14. Owner decisions (2026-10-17)

1. **Model:** `claude-opus-5-5`, effort `medium` (≈ $100–175 a year, §10).
2. **Blinding as designed:** the owner never sees the agent's decisions
   during an epoch (§6).
3. **Deadline:** 5 minutes, and the common entry at alert + 5 min (§2).
4. **Calendar:** the point-in-time calendar store is built **first** (§13
   step 1).
   - If it isn't ready **within 2 weeks** of the start of the build, the
     agent launches without it: `events = []`, flagged in every snapshot of
     that epoch.
   - Adding the calendar later changes the snapshot, so it is a new epoch
     (§8).
5. **Primary outcome:** selection value with the **neutral bracket** (§7).
   Each arm's own SL/TP is reported only.
6. **The Technical vote is not visible to other agents.** No agent sees the
   agents v1 Technical agent's vote (§3).

Nothing in §14 is open any more. The cost figures in §10 are still
estimates until `count_tokens` runs on the real prompt.
