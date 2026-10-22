# chore/loose-ends (merged in #8)

Overnight run, 2026-10-15, unattended. Rule: on any decision, take the
conservative option, log it here, continue.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| L1 | An undecodable bus entry is delivered as `Delivery(id, {"_undecodable": raw}, error=...)`, a dict that fails every consumer's validation, rather than `msg=None`. | Consumers that don't know the new `error` field reject it instead of crashing: the recorder via `validate_message`, and the scanner's `StreamFeed` on `feat/scanner-stream-source` as `invalid_msg`, which is acked. `msg=None` would crash both. | `msg=None`, with every consumer checking `error`. |
| L2 | The recorder stores them in `rejected_messages` with stage `invalid` and the error prefixed `undecodable:`, with the raw text JSON-encoded in `raw`. | No migration: `stage` has a CHECK constraint (`invalid`, `poison`), and SQLite can only change it by rebuilding the table. | A store migration adding stage `undecodable`. |
| L3 | The default Redis client is built with `encoding_errors="replace"`. | Invalid UTF-8 in an entry would otherwise raise inside redis-py's `XREADGROUP`, before the bus can catch it, and crash-loop every consumer of the stream. | Raw (bytes) client and manual decoding. |
