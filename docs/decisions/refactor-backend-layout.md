# refactor/backend-layout

Decisions taken without the owner while building this branch. Rule: on any
choice, take the conservative option, log it here with the alternative. The
plan is [docs/RESTRUCTURE_PLAN.md](../RESTRUCTURE_PLAN.md).

From this branch on, decisions go to `docs/decisions/<branch>.md`, one file
per branch, not to the root `DECISIONS.md`. The file name is the branch name
with `/` replaced by `-`.

## Phase 0: plan and golden replay (2026-10-21)

Drawn from `main` @ c556533. No code changed in this phase.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| BL1 | **The golden harness runs the scanner through its own CLI** (`main()` with `--date`, `--live`, `--dry-live`, `--selftest`, as the Dockerfile and runbook start it), and fakes only the outside world: the clock (`pd.Timestamp.now`, `time.time` and `time.sleep` on the main thread, SQLite's `datetime('now')`), Telegram's HTTP API (`urllib.request.urlopen`) and IBKR (`sys.modules["ib_insync"]`). Redis is a real server and the datafeed store a real SQLite file. | The restructure moves the wiring (`run_live`, `run_live_stream`, `main`), so the harness must exercise it. Faking at the edge leaves every line of scanner code as it runs in production. | Call the scanner's functions directly, as the tests do: faster, but it would not see a wiring mistake in PR 2. |
| BL2 | **Bar sources.** "replay" is `--date D --db J` (journal `bar_source` 'parquet'). "stream" is `--live` with `SCANNER_BAR_SOURCE=stream`, bars from a Redis stream and the warm-up from a datafeed store. **ibkr is covered too** (`--live` with `SCANNER_BAR_SOURCE=ibkr`): the fake gateway sends ticks, and `tws_connector.TWSConnector` builds the bars. | PR 2 moves `tws_connector.py` into `sources/ibkr.py`. Without an ibkr run, nothing would prove that move. | The two named sources only. |
| BL3 | **28 days plus the selftest, fixed before the first run** (list in the plan, §2.3): every US DST switch from 2023 to 2025 (Sunday and Monday), both EU switches in 2024–2025, 7 holidays or early closes, 8 Sunday reopens, the 2025-06-16 history-fill day with the restart cases of `tests/scanner`, the selftest's days, and the parity days. Days from 2023, 2024 and 2025. | It covers every case the request names, and more than the minimum of 25. | 25 days. |
| BL4 | **One run** goes from the last bar before the day + 1 min (as `tests/scanner/test_parity.py` starts) to the next day at 04:30. A checkpoint every 15 min is one pass of the scanner's main loop. Exception: 2025-07-04 runs to the Sunday reopen, so the staleness and silence messages and their recovery are inside the run. | The day's late alerts get their 4 h counterfactuals and their outcomes inside the run. | Stop at the day's end. |
| BL5 | **Scenarios on top of the plain runs** (29 extra runs): restarts with downtime (stream and ibkr; 07:05→07:13 and 07:05→09:00 on 2025-06-16; 10:30→10:37 on 2024-03-08); history fills at 00:25 and 00:31; a start at 00:00:30; dry runs; the datafeed store not ready at start; odd stream entries (undecodable, schema, no UTC offset, wrong source, duplicate, conflict, invalid bars, out of order, a history fill, a gap); a Redis crash with a restart; an IBKR gateway drop with a restart; Telegram send and poll failures; and a bar-source switch ibkr → stream → ibkr on one journal. | Each moved path (AlertSink, StreamFeed, wait_for_store, epochs, replies) is exercised by its own run. | Plain days only. |
| BL6 | **Telegram replies are scripted from the alert texts**, in a fixed cycle of 7: TAKE long with entry, SKIP, TAKE short at market, none (auto MISSED), TAKE without stop (unscorable), SKIP then TAKE (a revision), TAKE short on the wrong side. Each process also gets 5 odd updates (not a reply, unknown id, another chat, an edited message, no date). The first poll after a restart gets one already-confirmed reply again. | It drives decisions, outcomes (tp, sl, unscorable), revisions, MISSED and the dedupe through the real reply path, the same way on every checkout. | No replies: decisions and outcomes would not be covered. |
| BL7 | **Only two things are normalised before hashing**: Redis entry ids (`<ID>`) and the run's work directory (`<WORK>`). Everything else is compared byte for byte: alerts, every journal table, row and column (with schema and `user_version`), every Telegram call, every IBKR call, stdout, stderr (logging), the heartbeat file, `journal_summary` (text and `--json`) and how each process ended. | Log lines are behaviour too: a restructure that changes one would show up. | Compare only the journal. |
| BL8 | **Harness failures are a `BaseException`** (`GoldenError`), never an ordinary exception, and the harness steps the feed thread by a counter. Two harness bugs were found and fixed before the before-state was taken. **(a)** The first full pass ran 8 workers on this 8 GB machine. In 5 runs a 120 s wait inside the harness timed out and raised a `RuntimeError` from inside the fake Telegram. The scanner caught it as a poll failure, so the output changed and no error was reported (F1 below). **(b)** The gate watched a `waiting` flag, polling every 50 ms. When the feed thread took a step and was back at the gate within one poll, the harness never saw the step and waited for ever: 3 runs hung, and the thread dump showed `waiting=True permits=0`. It now waits on a counter of steps taken, the wait gives up after 30 min with a `GoldenError` and a thread dump, and 4 workers run. Both passes were redone with the fixed harness. | A harness problem fails the run loudly, and can never pass as a scanner difference or as an identical output. | Keep the timeout as an `Exception`. |
| BL9 | Workers run with `PYTHONHASHSEED=0`. | Set iteration order can't vary between runs. | The default random seed. |
| BL10 | **The harness and its outputs stay in the scratchpad**, not in git: `golden/golden.py`, outputs under `golden/before_a/` (and the repeat `golden/before_b/`). PR 2 commits a smaller golden test (a synthetic part for CI, and a real-data part that skips without the parquet). | The harness fakes the clock, `urlopen` and `ib_insync` process-wide. That is fine for a one-off proof, but not for code in the repo. | Commit the harness under `tools/`. |
| BL11 | **The plan is `docs/RESTRUCTURE_PLAN.md`**, and PR 3 moves it to `docs/history/`, as asked. | As specified. | none |
| BL12 | The file name of this decision file is the branch name with `/` replaced by `-`. | A file name can't contain `/` without a subdirectory per branch prefix. | `docs/decisions/refactor/backend-layout.md`. |

## Owner's answers (2026-10-21)

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| BL13 | **Q1:** PR 2's `config.py` holds the paths (`SCANNER_DB`), `bar_source()` and the shared constants. `run_live` and `run_live_stream` keep their own env reads, verbatim. **The source-text tests are brittle and are logged for a follow-up PR** that replaces them with behaviour tests (below). | No assertion changes in the restructure. | Move every env read into `config.py` and rewrite the 2 assertions that check the source text. |
| BL14 | **Q2:** level `id`s stay as they are. `LevelSource` exposes a derived key, `f"{type}@{active_from:%Y-%m-%dT%H:%MZ}"`, documented in the plan (§4.4), with a unit test that it is stable across rebuilds. | The Scanner's state is keyed by `id`, so changing ids would change behaviour. | A new id scheme inside the Scanner. |
| BL15 | **Q3:** EC2 runs `pipeline-fixes-and-cleanup` @ c62810d. The owner deploys `main` on Sat 24 Oct and the restructure later, as its own deploy. PR 2's runbook section assumes `main` is already deployed and says so. | The one-way journal migrations belong to the `main` deploy, not to the restructure. | none |
| BL16 | **Q4:** `docker compose config` runs with the standalone `docker-compose` binary (a pinned release at least two weeks old, checksum-verified, kept in the scratchpad). No image build here: no Docker on this Mac. | As answered. | none |

## Found on the way, not fixed (each for its own PR)

### F1. Any error under the Telegram poll is logged as "telegram poll failed", and output changes silently

- **Where:** `backend/deterministic_scanner.py:1678` (`run_live_stream`) and
  `:1174` (`run_live`), on `main` @ c556533:

  ```python
  try:
      msgs, offset = tg.poll(offset)
  except Exception as e:
      print(f"telegram poll failed ({e!r}); retrying", flush=True)
      _time.sleep(5)
      msgs = []
  ```

- **What the golden runs showed:**
  - The first pass ran 8 workers on an 8 GB machine. A wait inside the
    harness timed out and raised `RuntimeError` from inside the fake Telegram
    endpoint, which `tg.poll` calls.
  - This clause caught it. The run logged
    `telegram poll failed (RuntimeError('gate: timed out …')); retrying`,
    slept 5 s and went on.
  - The output differed (one extra log line, the run ending 5 s later), and
    no error was reported.
  - The error came from the harness, not from scanner code, so no scanner
    output was wrong. But the same clause would hide a scanner bug in exactly
    this way.
- **Why it's a bug:** `tg.poll` is more than the network call. It is
  `Telegram._get` (HTTP and `json.load`) **plus `parse_updates`**, which is
  scanner code. `except Exception` treats every error under it as a network
  hiccup. Suppose `parse_updates` raised on some update, e.g. a `KeyError` on
  an update without `update_id`, or an `AttributeError` if `message` isn't a
  dict. Then:
  - `offset` never moves past that update;
  - the same batch fails every ~5 s for ever;
  - no reply is journaled again;
  - the only sign is one log line every 5 s. The scanner stays healthy (the
    heartbeat still beats), so the watchdog doesn't alert.
- **Fix (not applied):**
  1. Split the poll into fetch and parse: `Telegram.fetch(offset)` does HTTP
     and JSON only, and `parse_updates` runs **outside** the `try`.
  2. Catch only transport errors around the fetch: `OSError` (which covers
     `urllib.error.URLError`/`HTTPError`, timeouts and connection resets),
     `http.client.HTTPException` (e.g. `IncompleteRead`) and
     `json.JSONDecodeError`. These are the hiccups the comment is about.
  3. Anything else propagates. The loop is crash-only, so the process exits,
     Docker restarts it, the traceback is in the log, and the watchdog's
     crash-loop alert fires if it repeats.
  4. Optionally, count consecutive transport failures and send one Telegram
     note (or a log line the watchdog reads) after N, so a long Telegram
     outage is visible too.
  5. A test: `parse_updates` raising must not be logged as
     "telegram poll failed".
- **The same pattern** (broad `except Exception` that logs and continues) is
  at `:416` (`_alert_sender`: "telegram send failed for …"), `:1049`
  (`_send_quietly`), `:1146` (`tws.disconnect()`, `pass`) and `:1162` (the
  silence warning's `tg.send`, `pass`). On the send side, swallowing
  transport errors is intended, since the alert is already journaled, but
  those clauses also hide programming errors. Same fix: narrow to transport
  errors.

### F2. Source-text tests are brittle (owner, Q1)

These tests assert on `inspect.getsource(...)` of the live loops, not on
behaviour:
- `test_heartbeat.py:57-58`;
- `test_bar_source.py:96-97`;
- `test_journal_epoch.py:233-238`;
- `test_downtime.py:120-126`;
- `test_history_fill.py:211`;
- `test_warmup.py:114-118`.

A harmless rename or reformat breaks them, and a behaviour change that keeps
the text passes them. **Follow-up PR:** replace each with a behaviour test.
Run `run_live` or `run_live_stream` with fakes at the edge, as the golden
harness does (fake clock, fake Telegram, fake IBKR or in-memory bus), and
assert on what happens:
- the heartbeat is written every pass;
- progress is recorded after each live bar;
- the epoch comes from `start_epoch`;
- stream mode never imports `ib_insync`;
- the group comes from `SCANNER_STREAM_GROUP`.
