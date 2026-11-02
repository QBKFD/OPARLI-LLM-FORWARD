# fix/narrow-excepts

Fixes F1 of [refactor-backend-layout.md](refactor-backend-layout.md): any
error under the Telegram poll was logged as "telegram poll failed" and
retried for ever, so a parsing bug would silently stop replies from being
journaled. Decisions taken without the owner, each with its alternative.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| NE1 | **Transport errors are `TRANSPORT_ERRORS = (OSError, http.client.HTTPException, json.JSONDecodeError)`** in `backend/scanner/telegram.py`. `OSError` covers urllib's `URLError` and `HTTPError`, timeouts and connection resets. `HTTPException` covers e.g. `IncompleteRead`. `JSONDecodeError` is a reply that isn't JSON (a proxy's HTML error page). | It is everything a network call can raise without our code being wrong. | A narrower list (`URLError` only), which would turn some real outages into crash loops. |
| NE2 | **One poll step for both loops: `telegram.poll_replies(tg, offset)`.** The HTTP call (`Telegram.fetch`, new) is inside the `try`; parsing (`parse_updates`) is outside. A transport error logs the same line as before (`telegram poll failed (…); retrying`), sleeps 5 s and keeps the offset, as before. `Telegram.poll` stays for other callers. | The same behaviour on every transport error; a parse bug now raises. The crash-only loop exits, Docker restarts it, the traceback is in the log, and the watchdog's crash-loop alert fires if it repeats. | Keep the broad `except` and add a counter. |
| NE3 | **Send sites narrowed the same way:** the alert sender (`engine._alert_sender`, which now builds the alert text before the `try`), confirmations (`telegram._send_quietly`) and the ibkr loop's silence warning. The log lines are unchanged. | Sending stays best-effort for network failures (the alert is already journaled); bugs show. | Leave the send sites broad. |
| NE4 | **Not changed:** `tws.disconnect()` in the ibkr loop's crash path keeps `except Exception: pass`. It isn't a Telegram call, and nothing there may stop the backoff and exit. Neither do `TWSConnector`'s own handlers, which catch and log every error in a bar callback, including the scanner's. That last one is a separate finding: F5 below. | Only the sites listed in F1. | Narrow them too. |
| NE5 | **Tests** (`tests/scanner/test_telegram_errors.py`): eight transport errors are retried with the same offset and logged; two malformed updates (no `update_id`; a message that isn't a dict) raise and are not logged as poll failures; a non-transport error in the fetch raises; confirmations and alerts survive transport errors; a bug in a send raises. No source-text test is added (F2). | Behaviour is tested at the function the loops call. | A test that runs a whole live loop with fakes. |
| NE6 | **mypy:** the scanner has two findings fewer (the loops no longer call `poll` on `Telegram \| None`); the baseline file is re-recorded at 16. | The gate only fails on new findings. | none |

**Golden replay:** byte-identical to `main` (see the PR). No golden run injects a
non-transport error at a Telegram site: the injected Telegram failures are
`OSError`s, and the injected Redis error is in the stream feed, not Telegram.
So there is no run where a difference is expected.

## Found on the way, not fixed

- **F4. `python -m backend.datafeed.ingest --stdout` can hang.** On #33's CI
  (2026-10-21), `tests/datafeed/test_sources.py::test_entry_point_stdout` hit
  pytest's 120 s timeout. It was waiting for that subprocess (a replay of
  three bars, `REDIS_URL` unset) to finish. A re-run of the same job passed,
  and so did every local run. That command is also the `feed` container's
  entry point (without `--stdout`), so it matters. To do: loop the test under
  load locally, find the cause and fix it in its own PR (queued after PR 3).
- **F5. `TWSConnector._on_pending_tickers` catches every exception from a bar
  callback** ("Error in bar callback") and logs it at error level. In the
  ibkr loop that callback is the scanner's `on_bar`, so a scanner bug there
  is logged and swallowed, never raised: the same class of problem as F1.
  `TWSConnector` was moved verbatim, so it isn't changed here.
