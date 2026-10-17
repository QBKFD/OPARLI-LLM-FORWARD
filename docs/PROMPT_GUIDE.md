# Prompt guide (for the prompt author)

You write the agent prompts and choose how the agents behave. This guide is
the contract between your prompt files and the code around them:
- where files go;
- the file format;
- what you can reference;
- what the agent must return;
- what makes a new epoch.

The full design is `docs/AGENTS_ARCHITECTURE.md`.

**Status:** the contract for jobs 2–5.
- **Built (job 2):** prompt loading and checking, the placeholders, the
  config file, the epoch, and `python -m agents.prompting check` /
  `python -m agents.epoch`.
- Names marked *(job 5)* are fixed by this guide and implemented later.
- No agent calls a model until job 3.

## 1. Where things live

| File | What | Who writes it |
|---|---|---|
| `agents/prompts/visual.md` | Visual agent (LLM, charts) | you |
| `agents/prompts/technical.md` | Technical agent, the LLM half (interprets the metric table) | you |
| `agents/prompts/sentiment.md` | Sentiment agent (LLM, calendar/news) | you |
| `agents/prompts/gex.md` | reserved: GEX is code and doesn't read it while there is no options data | you (optional) |
| `agents/prompts/discussion.md` | the discussion turn, shared by all LLM agents | you |
| `agents.toml` (path from `$AGENTS_CONFIG`; keep it outside the repo or untracked) | models, effort and `max_tokens` **per agent and per round** (round 1 vs discussion; `AGENTS_ARCHITECTURE.md` §6.1), discussion rounds, Meta rule, LLM call settings, budget, the `{{bars_table}}` row counts | you |
| `agents/agents.example.toml` | every key of `agents.toml`, with comments and `"<...>"` placeholder values; never read at run time | code (job 2) |

**`agents.toml` is only yours.** The infrastructure settings (snapshot mode,
gate, timeouts) stay in their own file, `agents/config.example.toml`. So your
file holds your values and nothing else.
- **Every value is required,** except `discussion.rounds` (default 2) and
  `discussion.early_stop` (default true).
- **A value still reading `"<...>"`**, copied from the example and not filled
  in, is refused like any invalid value.
- **A problem in one section affects only what needs it.** A bad
  `agents.visual.round1` makes Visual abstain in round 1. A missing `[meta]`
  makes Meta abstain. Nothing crashes.

**The templates.** Job 2 ships each prompt file as a **template**. It holds the
front-matter, the section markers, and a comment listing the placeholders,
with no instructions.
- Its front-matter says `version: TEMPLATE`. While it does, the file is
  refused ("unfilled template"), so no template can ever reach a model.
- Write the sections, then set your own `version`. From then on, the content
  is yours; code never edits these files.

**If a file is missing or invalid, that agent abstains,** with a reason
naming the file and the problem. There is no default prompt, ever.

`bull.md` and `bear.md` (the old debate) stay untouched and unused after
job 4. Delete them when you like.

## 2. File format

```markdown
---
agent: visual
version: 2026-10-20.1
---

## system

(Your instructions. Static text only: no placeholders here.)

## user

(The per-alert message. Placeholders go here, e.g.)

Alert: {{alert_json}}

{{charts}}
```

**Front-matter**, between the two `---` lines:
- `agent`: required. It must equal the file name (`visual.md` → `visual`).
  `discussion.md` uses `agent: discussion`.
- `version`: required, a string of your choice (a date plus a counter works
  well). It's for people; the epoch comes from the file's hash (§7).
- `description`: optional, free text, never sent to the model.
- Any other key makes the file invalid.

**Sections:**
- `## system` and `## user`, each exactly once, in this order. The line must
  be exactly `## system` / `## user`. Other `##` headings inside a section
  are just text.
- `discussion.md` has **only** `## user`: a discussion turn reuses the
  agent's own system section (§3.3).
- **No placeholders in `## system`.** It stays byte-identical across alerts,
  which keeps the hash meaningful and the prefix cacheable.

Everything in a section, from the line after its marker to the next marker
or the end of the file, is sent as written, apart from two things:
- **placeholder substitution**;
- **`<!-- ... -->` comments, which are removed.** Use them for notes to
  yourself; a placeholder inside a comment doesn't count.

Leading and trailing blank lines are trimmed. Between the front-matter and
the first section, only comments and blank lines are allowed. The file's hash
covers its raw bytes, comments included, so editing a comment is a new epoch
too.

## 3. Placeholders

`{{name}}`, exactly, with no spaces inside the braces.
- An **unknown** placeholder → the agent abstains
  (`prompt_invalid: unknown placeholder {{name}}`).
- A placeholder used **in the wrong template** (a discussion one in a
  round-1 file) → abstain. That is how round 1 stays blind.
- You don't have to use any placeholder. Unused inputs simply aren't sent.

### 3.1 Round-1 placeholders (visual, technical, sentiment, gex)

| Placeholder | Renders | Notes |
|---|---|---|
| `{{as_of}}` | `2026-10-20T14:31:00+00:00` | the decision minute |
| `{{alert_json}}` | the alert's Telegram facts as JSON: `alert_id`, `alert_ts`, `session`, `level_types`, `level_price`, `price_at_alert`, `confluence_count` | never the raw journal row |
| `{{levels_json}}` | levels active at `as_of`: `id`, `type`, `value`, `active_from`, `touched` | PDH PDL PWH PWL DOPEN WOPEN ASIA/LONDON/NY_H/L |
| `{{trigger_levels_json}}` | ids of the levels this alert touched | |
| `{{features_json}}` | σ and the vol state (§4.2) | |
| `{{metrics_table}}` | the Technical metric table, one row per metric: `name`, `value`, `unit`, `available`, `reason` | *(job 5)* |
| `{{bars_table:<tf>}}` | the last complete bars of `<tf>` (`1m`, `15m`, `1h`, `4h`), one line each: open time, O, H, L, C | the row count per tf is set in `agents.toml` (`render.bars_rows`) |
| `{{events_json}}` | calendar events in [as_of − 6 h, as_of + 24 h]; actuals only if released before `as_of` | `[]` until the calendar store exists |
| `{{charts}}` | the 15m, 1h and 4h chart images, in that order, attached as image blocks at this position | no images until the renderer exists; reference them as `chart:<tf>` |
| `{{snapshot_json}}` | the whole snapshot as canonical JSON (bars, levels, features, metrics, events, alert) | large: ≈ 15–30k tokens with every bar |

Never available: `experiment_id`, the epoch id, the trigger payload, model
names, other agents' votes in round 1, and anything after `as_of`.

**Inputs that don't exist yet say so.** A slot whose builder doesn't exist
yet (levels, features, metrics, events, charts) renders as `[unavailable:
<slot>: <reason>]`, e.g. `[unavailable: events: no calendar store yet]`. It
never renders as an empty list that would look like data. The same applies to
the alert when a trigger carries none. `{{bars_table:<tf>}}` needs
`render.bars_rows.<tf>` in `agents.toml`; without it the agent abstains.

### 3.2 Discussion placeholders (`discussion.md` only)

| Placeholder | Renders |
|---|---|
| `{{agent}}` | the speaking agent's name (`visual`, `technical`, `sentiment`) |
| `{{round}}` / `{{max_rounds}}` | the discussion round (1-based) and N from `agents.toml` |
| `{{own_previous_vote_json}}` | this agent's latest vote: `direction`, `confidence`, `reason`, `evidence` (with validity flags) |
| `{{other_votes_json}}` | every other agent's latest vote, the same fields, with GEX and abstained agents included as they are. No model ids, timings or tokens |
| `{{change_rule}}` | one fixed sentence, written by code, stating the vote-change rule (§6). Use it if you want the model told the rule in exactly the words code enforces |

Round-1 placeholders are not allowed in `discussion.md`: the agent's round-1
content is already sent again before it (§3.3).

### 3.3 How a discussion request is built

```
system:   <the agent's own ## system>
user:     <the agent's own round-1 ## user, rendered exactly as in round 1, images included>
user:     <discussion.md ## user, rendered for this agent and round>
```

So the agent always sees its round-1 inputs again, then the discussion
message. Your round-1 file defines what each agent knows; `discussion.md`
defines how they talk.

## 4. What you can reference

### 4.1 Snapshot fields

| Field | Meaning |
|---|---|
| `symbol` | `XAUUSD` |
| `as_of` | decision minute (UTC); every bar opens before it |
| `bars.<tf>` | `1m`, `15m`, `1h`, `4h` (4h buckets open at NY 17:00 and every 4 h). Each bar: `ts` (open, UTC), `open`, `high`, `low`, `close`, `n_1m`, `complete`. A `complete=false` bar is still forming |
| `levels[]` | `id`, `type`, `value`, `active_from`, `touched` |
| `trigger_levels[]` | level ids touched by this alert |
| `features` | §4.2 |
| `metrics` | §4.3 *(job 5)* |
| `events[]` | `id`, `ts`, `currency`, `tier`, `name`, `actual`, `forecast`, `previous` |
| `charts[]` | `tf`, `sha256`, `width`, `height` |
| `alert` | the Telegram facts (§3.1) |

There is no volume. The bars are a CFD bid/ask midpoint.

### 4.2 Feature names

| Name | Meaning |
|---|---|
| `sigma_px_60`, `sigma_px_240` | HAR σ of the price change over the next 60 / 240 min, in $ |
| `sigma_ret_60`, `sigma_ret_240` | the same, as a log return |
| `crosses_close_60`, `crosses_close_240` | 1 if that window crosses the daily close (17:00 NY), else 0 |
| `L`, `S_hd`, `T` | VolBlock state: volatility level, half-day shape, trend |
| `L_tercile` | 1, 2 or 3: the volatility regime (cut-offs fixed from the σ model's training window) |
| `sigma_model`, `sigma_model_sha256` | which σ model (provenance) |

### 4.3 Metric names *(job 5)*

| Name | Timeframes | Meaning | Unit |
|---|---|---|---|
| `atr14_<tf>` | 15m, 1h, 4h | ATR(14), Wilder | $ |
| `atr14_<tf>_sigma` | 15m, 1h, 4h | ATR / `sigma_px_60` | σ |
| `rsi14_<tf>` | 15m, 1h, 4h | RSI(14), Wilder | 0–100 |
| `ema_spread_<tf>` | 15m, 1h, 4h | (EMA9 − EMA21) / `sigma_px_60` | σ |
| `sigma_px_60`, `sigma_px_240` | — | as in §4.2 | $ |
| `L`, `S_hd`, `T` | — | as in §4.2 | |
| `dist_sigma_<level_id>` | — | (last complete 1m close − level) / `sigma_px_240`, signed: > 0 means price is above the level | σ |
| `volume`, `vwap` | — | always `available = false` (no volume source) | — |

- All metrics use **complete candles only**.
- A metric that can't be computed (too few candles, NaN) has
  `value = null`, `available = false` and a reason. It is never filled in.

## 5. Required output

The model's answer is constrained by the API (structured outputs) to this
schema. Code then checks the bounds and the refs. It is the same for round
1 and every discussion round:

```json
{
  "direction": "long | short | none",
  "confidence": 0.0,
  "reason": "at most 600 characters",
  "evidence": [
    {"ref": "level:PDH-2026-10-20", "claim": "at most 200 characters"}
  ]
}
```

- `direction`: `none` means "no position". It is a real answer and counts
  as a vote in Meta.
- `confidence`: 0–1. Whether it matters depends on the Meta rule you choose.
- `evidence`: 0–6 items. **A `long` or `short` vote needs at least one ref
  that resolves**; without one, the agent abstains
  (`invalid: direction without evidence`). A ref that doesn't resolve is
  kept and marked invalid; it is never silently dropped.
- The model can't abstain. Abstain is what the infrastructure records when
  it couldn't get a valid answer (error, timeout, refusal, truncation,
  invalid output, budget). It's stored apart from `none`.
- You don't need to restate the schema in the prompt; the API enforces the
  shape. Describing the fields in your own words is fine.

## 6. Evidence refs

```
ref      = bars | level | feature | metric | event | chart | vote
bars     = "bars:" tf ":" ts ".." ts       tf in the snapshot; both ts are open times of bars in that series,
                                            ISO-8601 with offset; start <= end
level    = "level:" level_id               an id in levels[]
feature  = "feature:" name                 a key of features (§4.2)
metric   = "metric:" name                  a metric name (§4.3) whose available = true
event    = "event:" event_id               an id in events[]
chart    = "chart:" tf                     15m, 1h or 4h; only if that chart was attached
vote     = "vote:" agent ":" round         discussion only: another agent's vote in an earlier round
```

Examples:
- `bars:15m:2026-10-20T13:00:00+00:00..2026-10-20T14:15:00+00:00`
- `level:PDH-2026-10-20` (illustrative: the level library sets the id format)
- `metric:rsi14_1h`
- `chart:4h`
- `vote:visual:1`

Rules, checked in code:
- A ref resolves only inside this snapshot (or, for `vote:`, the votes shown
  in this discussion round).
- A `vote:` ref never counts on its own. To **change** a vote in the
  discussion (a different direction or confidence), the new vote needs at
  least one snapshot ref (any kind but `vote:`) that wasn't in the agent's
  previous vote.
  - Without one, the change is refused: the previous vote stands, marked
    `change_rejected`.
  - Keeping the same vote needs nothing new.
- `chart:` says "this image". Code can't check what the claim says about it.
  A random sample of claims is listed for your spot check.

## 7. Epochs: what a change does

The **epoch id** is a hash of, among others (full list:
`AGENTS_ARCHITECTURE.md` §8):
- every prompt file's bytes;
- the validated `agents.toml` values;
- the model ids, effort and `max_tokens`, per agent and per round;
- the output schema;
- the input-building code;
- the Meta rule;
- the σ model;
- the scanner journal epoch.

- **Any edit to a prompt file is a new epoch.** That includes a typo fix, a
  whitespace change inside a section, or a front-matter change.
- **Any change to an `agents.toml` value is a new epoch.** Comments and
  formatting don't count.
- It happens automatically. Bumping `version` is good practice. If the bytes
  change and `version` doesn't, a warning is logged; the epoch changes
  anyway.
- Results are **never pooled across epochs.**
- A live evidence experiment pins one epoch id in its PREREG. Runs under any
  other epoch are stored as development and don't count. To change a prompt
  during an experiment, end the epoch: write a PREREG addendum and start a
  new `experiment_id`.
- `python -m agents.epoch [--config agents.toml] [--db agents.sqlite]` prints
  the current epoch id and its inputs. With `--db`, it also prints which
  files or values differ from the last stored run, and warns about a prompt
  whose bytes changed while its `version` didn't.

## 8. Before you freeze a prompt

- [ ] Front-matter has `agent` (= the file name) and `version`; no other keys
      but `description`.
- [ ] `## system` has no placeholders; `## user` uses only the names in §3.
- [ ] Nothing in the prompt names a date, an experiment, or what happened
      after `as_of`. The model may remember gold history up to its training
      cutoff, so evidence is forward-only.
- [ ] A worked example, if any, doesn't show a direction. The legacy Visual
      agent's bullish example biased it (`legacy/PORT_CANDIDATES.md`).
- [ ] Run the checks:
      - `python -m agents.prompting check [--prompts DIR] [--config agents.toml]`:
        every prompt file (OK with its version, hash and placeholders, or
        the reason it's invalid), every config problem, and the epoch id;
      - `python -m agents.llm count-tokens` (real token counts for the cost
        estimate);
      - a cache-only rerun on development data.
