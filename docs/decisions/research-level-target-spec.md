# research/level-target-spec

Overnight run, 2026-10-17, unattended. Rule: on any decision, take the
conservative option, log it here, continue. This branch holds two documents,
neither with any code:
- `research/level_target/SPEC.md`, whose decisions are in
  `research/level_target/DECISIONS.md` (T1–T13);
- `docs/AGENT_LLM_SCANNER_DESIGN.md`, decided below.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| L1 | **The runner reads the scanner journal read-only** (`mode=ro`, a 5 s poll). The scanner isn't changed. | The scanner is production and another session's area; a read-only reader can't break it. | Publish alerts on the Redis bus from the scanner. |
| L2 | **A 5-minute decision deadline and a common entry** (alert + 5 min) for both arms. The owner's later replies keep their extra information. | It measures selection and direction, not speed. The leftover bias favours the owner, so it can only make the agent look worse. | Score each arm at its own decision time (reported as a secondary). |
| L3 | **No refusal fallbacks**, although the Claude API guidance enables them by default for Opus 5.5. A refusal is an abstention. | A fallback answers with another model, which breaks the frozen-model epoch. | Fallbacks on, with the fallback model recorded (it mixes two models in one arm). |
| L4 | **The owner never sees the agent's decisions during an epoch**; only integrity counts are shared, weekly. | The owner's replies are the comparison arm; seeing the agent would contaminate them. | Reveal after each reply (a different question; §14 Q2). |
| L5 | **Primary outcome: selection value with the neutral 1σ/2σ bracket at 240 min**, the same scorer for both arms. Each arm's own SL/TP is reported only. | One exit rule for both arms isolates TAKE/SKIP and direction, which is the question. | Each arm's own exits as primary (it mixes exit skill in). |
| L6 | **Opus 5.5 is the proposed model** (the current default), with effort `medium`, pinned. Sonnet 5.5 and Haiku 5.5 are priced for comparison. | Model choice is the owner's; the doc prices all three. | Choose Sonnet on cost. |
| L7 | **Prompt caching off.** | One call an hour or more apart; neither TTL pays. | A 1-hour cache on the system prompt. |
| L8 | **Self-consistency repeats** on 10 % of alerts (2 extra samples after the deadline), stored and never scored. | They measure sampling noise without touching the comparison. | Majority of 3 as the decision (3× cost, a different agent). |
| L9 | **The calendar must come from a point-in-time store** (each scrape with its fetch time). It isn't built, and is listed first in the build order. | Calendar data without `known_by` leaks revisions and actuals into the snapshot. | Use the historical calendar export (not point-in-time). |
| L10 | **The snapshot is the only input:** no tools, no web, no news text. | Every claim must resolve to a ref; outside sources can't. | Web search (unverifiable, and not frozen). |
| L11 | **A hard cap of 40 calls a day** (≈ $5 on Opus), then abstain `budget`. | It bounds a runaway loop's cost. | No cap. |
| L12 | **Deploy wiring (compose, watchdog) is left to the deploy stack's owner.** The doc only sizes it (384 MB). Nothing in `deploy/` is touched. | `deploy/` is another session's area tonight. | Draft compose changes here. |
| L13 | **The token counts are estimates**, images at ≈ w·h/750 tokens. `count_tokens` on the real prompt replaces them before the freeze. Prices are the list prices as of 2026-10-06. | The cost table is labelled as an estimate. | none |
| L14 | **Owner decisions of 2026-10-17 applied** (design §14): Opus 5.5 at effort `medium`; blinding as designed; a 5-minute deadline; the point-in-time calendar store built first, with a launch without it if it isn't ready within 2 weeks (`events = []`, flagged; adding it later is a new epoch); the neutral bracket as the primary outcome; no agent sees the Technical vote. | They are the owner's choices, recorded where the design uses them. | none |
