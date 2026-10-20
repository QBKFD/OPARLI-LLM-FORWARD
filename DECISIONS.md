# Decisions taken without the owner

One section per branch, each as it was logged when the branch was built.

## ci/github-actions (merged in #6)

Overnight run, 2026-10-15, unattended. Rule: on any decision, take the
conservative option, log it here, continue. The workflow is
`.github/workflows/ci.yml`.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| C1 | Cut from `main` (05fe838), as asked. The stream and deploy branches add `tests/scanner` and `tests/deploy` to `testpaths`, so CI picks them up when they merge; their Redis tests use the same service container. | CI tests what is on `main` now. | Cut from `feat/deploy-stack`. |
| C2 | The **selftest runs only when the parquet is present**. It isn't in git (`database/ohlcv_data` is untracked), so on GitHub the step prints a warning annotation instead of failing. Checked locally: `main`'s selftest passes with the parquet. | CI doesn't go red over missing data, and the gap is visible on every run, not hidden. | (a) a data-free selftest subset (cases 2–9, 11, 12 use synthetic data); (b) a small tracked fixture parquet. Both change code or data and need the owner. |
| C3 | ruff in two passes. **Errors only** (`E9,F63,F7,F82`: syntax errors, undefined names) on the whole repo; **default rules** (`E4,E7,E9,F`, minus E741 because `l` is the codebase's OHLC low) on `agents`, `risk`, `tools` and `datafeed`. `backend/`, `research/` and `tests/` aren't style-linted: they carry 87 existing style findings (`l`, semicolons, lambdas) and their code is kept as run or calibrated. `legacy/` is excluded. | Green on `main` without editing any code, while still catching real errors. | Lint everything and fix 90 style findings, which touches the calibrated scanner and run research code. |
| C4 | One per-file ignore: F821 in `research/levels/run_levels.py`. `frames` is deleted (`del frames`) only after the last call of the closure that appends to it: a static false positive, not a runtime bug. | No edit to a study's code after its run. | Rewrite the closure. |
| C5 | **shellcheck at error severity.** `main`'s `watchdog.sh` and `entrypoint.sh` have 4 warning/info findings (SC2164, SC1090, SC2034, SC2086). `feat/deploy-stack` already fixes them and is clean at the default severity. | No edits to files `feat/deploy-stack` rewrites (no merge conflict). | Raise to `-S warning` once `feat/deploy-stack` is merged (one-line change). |
| C6 | **gitleaks** runs as the pinned release binary (8.30.1, SHA-256 checked), over the full history (`fetch-depth: 0`), not as `gitleaks-action`. | No third-party action with repo access; `gitleaks-action` needs a license key for organisation accounts. | `gitleaks/gitleaks-action@v2` with `GITLEAKS_LICENSE`. |
| C7 | Actions pinned by **commit SHA**: checkout v7.0.1 and setup-python v7.0.0, the current majors (bumped 2026-10-15; v4/v5 were on the deprecated Node 20). `permissions: contents: read`. No secrets. Runs on every push and PR. `ubuntu-24.04`, Python 3.13 (the lock's version). ruff pinned to 0.12.10. All versions are older than two weeks. | Supply-chain hygiene; nothing the workflow can write or leak. | Floating tags (`@v4`). |
| C8 | IBKR tests are deselected (`-m "not ibkr"`); they also skip without `DATAFEED_IBKR_LIVE=1`. | Never a gateway connection from CI. | none |
| C9 | Bumped to the current majors on the owner's request: checkout v4.2.2 → **v7.0.1**, setup-python v5.6.0 → **v7.0.0** (both released 2026-07-20). Neither changelog affects this workflow: v7 setup-python drops the `pip-install` input, which isn't used, and v7 checkout blocks fork checkouts for `pull_request_target`/`workflow_run`, which aren't used. Pinned by SHA. | Removes the Node 20 deprecation warning, with versions ≥ 2 weeks old. | Stay on v4/v5. |

## chore/loose-ends (merged in #8)

Overnight run, 2026-10-15, unattended. Rule: on any decision, take the
conservative option, log it here, continue.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| L1 | An undecodable bus entry is delivered as `Delivery(id, {"_undecodable": raw}, error=...)`, a dict that fails every consumer's validation, rather than `msg=None`. | Consumers that don't know the new `error` field reject it instead of crashing: the recorder via `validate_message`, and the scanner's `StreamFeed` on `feat/scanner-stream-source` as `invalid_msg`, which is acked. `msg=None` would crash both. | `msg=None`, with every consumer checking `error`. |
| L2 | The recorder stores them in `rejected_messages` with stage `invalid` and the error prefixed `undecodable:`, with the raw text JSON-encoded in `raw`. | No migration: `stage` has a CHECK constraint (`invalid`, `poison`), and SQLite can only change it by rebuilding the table. | A store migration adding stage `undecodable`. |
| L3 | The default Redis client is built with `encoding_errors="replace"`. | Invalid UTF-8 in an entry would otherwise raise inside redis-py's `XREADGROUP`, before the bus can catch it, and crash-loop every consumer of the stream. | Raw (bytes) client and manual decoding. |

## feat/deploy-stack

Overnight run, 2026-10-15, unattended. Rule: on any decision, take the
conservative option, log it here, continue.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| D1 | The branch is cut from `feat/scanner-stream-source` @ 45c674c, not `main`. | `main` doesn't contain 45c674c (the owner's stated rule). | Merge the stream branch first, then rebase this onto `main`. |
| D2 | The "previous prompt" with the detailed deploy spec is not in this session. The scope is the items listed tonight plus `docs/SCANNER_SWITCH_NOTES.md` (compose plan, entrypoint, §5). | Building only what's written avoids inventing requirements. | Owner compares against the earlier spec in the morning. |
| D3 | The scanner has **no compose dependency** on redis/feed/recorder. The order redis → feed → recorder is through healthchecks. In stream mode the scanner waits in-process (the entrypoint waits for Redis, `wait_for_store` for the store). | A gateway or datafeed problem must never keep the ibkr-mode scanner (the rollback) from starting. With `depends_on: recorder healthy` it would. | `depends_on: recorder: {condition: service_healthy, required: false}`: a stricter order, but blocks ibkr mode whenever the stream profile is on and the feed can't reach the gateway. |
| D4 | The stream services sit behind a compose **profile** `stream`. Plain `docker compose up -d` runs today's two services. | Deploying this PR changes nothing until the owner opts in (`COMPOSE_PROFILES=stream`). | Always run all five services. |
| D5 | Pinned `pyarrow==25.0.0` and `tzdata==2025.3` in the scanner image (they were unpinned). | Same versions as the tested lock; an unpinned build once pulled pandas 3. | Leave unpinned. |
| D6 | `datafeed/` is **copied** into the image (`/app/datafeed`), not pip-installed. Feed and recorder use the **same image** with an entrypoint override. | No build step, no setuptools in the slim image, one image to build and verify. | A separate datafeed image, or pip-install the package. |
| D7 | Redis has no password and no published port; it is reachable only on the compose network. | The network is private to the stack; a password in `.env` would be one more secret to leak. | `requirepass` from `.env`. |
| D8 | `backup.sh` never deletes from S3; retention is an S3 lifecycle rule set by the owner. Uploads use `--sse AES256`. AWS credentials come from an IAM instance role, never `.env`. | Nothing destructive runs unattended, and no long-lived keys sit on the host. | Prune old backups in the script; keys in `.env`. |
| D9 | `restore.sh` without `--yes` only downloads and verifies. With `--yes` the old file is kept as `.pre-restore-<ts>`, and the services are restarted even if the restore step fails. | A restore can't be run by accident, can't lose the current file, and can't leave the scanner stopped. | Restore directly. |
| D10 | A hung scanner (unhealthy heartbeat) gets a Telegram **alert only**, no automatic `docker compose restart scanner`. The message says a restart is safe. | No unattended action beyond what the watchdog already did (the gateway re-login). A restart could hide whatever hung it. | Auto-restart after N unhealthy checks. |
| D11 | Each new alert kind has its own hourly limit (heartbeat, datafeed crash loop, Redis) and its own recovery message. The quiet window (Fri 17:00 – Sun 17:00 NY) applies to all of them, as to the existing ones. The feed crash-loop alert only fires while the gateway probe succeeds. | One alert can't mute another. No duplicate alerts for one cause (the feed exits whenever the gateway is down, which the gateway alert already covers). | One shared limit (today's `last_alert`), or alerts in the quiet window too. |
| D12 | Found and fixed. `SCANNER_SWITCH_NOTES.md` §6 told a parallel `--dry-live` run to use "another consumer name", but consumers of one Redis group **split** the entries: next to a stream-mode production scanner it would take half of production's bars. New env `SCANNER_STREAM_GROUP` (default `scanner`, unchanged), and the runbook's dry run uses its own group. A test shows separate groups each get every entry, while one group splits them. | Production never shares its group; the default is unchanged. | Forbid parallel dry runs. |
| D13 | The branch is pushed, but **no pull request is opened**. | "Push the branch" was the instruction; opening a PR is an outward-facing step left to the owner. | Open the PR against `main` (after `feat/scanner-stream-source`). |
| D14 | Memory limits (`mem_limit`) per service for a 3.8 GB host: ibgateway 1536m, scanner 512m, feed 256m, recorder 192m, redis 384m (total 2.88 GB). They come from measured peaks (scanner 194 MB, feed 48 MB, recorder 14 MB) with 2–10× headroom. No `memswap_limit`, and the gateway's `JAVA_HEAP_SIZE` is left at the image default: whether the image honours it for IB Gateway isn't verified here. The runbook recommends a 2 GB swap file (`swappiness=10`). | Limits only cap runaway use; the headroom is generous, and an OOM-killed container is restarted and reported by the watchdog. | Tighter limits, or setting the Java heap. |
| D15 | The daily parquet export is the **datafeed store's bars** (all kinds, every column), one file per **completed** UTC day, under `$BACKUP_S3_URI/archive/bars/date=YYYY-MM-DD/XAUUSD.parquet`. Each night covers the last 7 days and uploads only missing days. A day is never re-exported or overwritten, so history bars that land in the store for that day later live only in the `store/` backups. The journal isn't exported to parquet; its SQLite backup is complete. | Never overwrites an archived file; self-healing after a missed night. The store backup stays the complete record. | Also export the journal tables; re-export changed days. |
| D16 | `BACKUP_S3_URI` (`s3://bucket/prefix`) replaces `BACKUP_S3_BUCKET` + `BACKUP_S3_PREFIX`: one variable, the name from the owner's spec. Nothing using the old names was ever deployed. | One source of truth, matching the spec. | Accept both names. |
| D17 | Watchdog health checks. Thresholds are named constants at the top of `watchdog.sh`: disk ≥ 85 % (the highest of `/` and Docker's data dir), MemAvailable < 10 %, last successful backup > 26 h (only when `BACKUP_S3_URI` is set; "never" counts), recorder lag ≥ 15 **market-open** minutes (computed in the recorder container with datafeed's `_market_open`; no alert before the first live bar). Disk, memory and backup alerts **ignore the quiet window**; they aren't market-related. Each kind: one alert per hour, plus one recovery message. | Alerts only, no automatic action. A market-aware lag gives no weekend or break false alarms. Nothing waits for Monday that can't. | Wall-clock lag; quiet window for everything. |
| D18 | `rejected_messages` growth: the first observation is the **baseline** (existing rows don't alert). After that, an alert reports the delta since the last alert, at most hourly. No "recovery" message, because rejections are events, not a state. | No alert storm on first install; nothing missed between alerts, since the delta accumulates. | Alert on every row. |
| D19 | The daily Telegram heartbeat goes out on the first run at or after 07:00 UTC, **every day, weekends included**. It summarises gateway, scanner health and restarts, disk, memory and swap, last backup, and recorder lag and rejected count. | Silence on any day means the watchdog or cron is broken. | Weekdays only. |
| D20 | In stream mode, the entrypoint's Redis wait times out after `REDIS_WAIT_TRIES` × 5 s (default 120, so 10 min, as the gateway wait). Then it sends one Telegram alert and exits 1; it doesn't start the scanner anyway. The alert is at most hourly, via a marker file on the `/data` volume, because Docker restarts it into the next 10-min wait. Sent with Python's urllib (the image has no curl); no token means no send. | One clear alert per hour instead of a crash loop without context; never one alert per restart. | Alert every time; start the scanner anyway (the old behaviour). |

## research/level-target-spec

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
