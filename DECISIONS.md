# Decisions taken without the owner (feat/deploy-stack)

Overnight run, 2026-10-15, unattended. Rule: on any decision, take the
conservative option, log it here, continue.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| D1 | The branch is cut from `feat/scanner-stream-source` @ 45c674c, not `main`. | `main` doesn't contain 45c674c (the owner's stated rule). | Merge the stream branch first, then rebase this onto `main`. |
| D2 | The "previous prompt" with the detailed deploy spec is not in this session. The scope is the items listed tonight plus `docs/SCANNER_SWITCH_NOTES.md` (compose plan, entrypoint, §5). | Building only what's written avoids inventing requirements. | Owner compares against the earlier spec in the morning. |
