# Decisions taken without the owner

One file per branch, each as it was logged when the branch was built. A
branch's file is the branch name with `/` replaced by `-`. Until 2026-10-21
these sections were one file at the root (`DECISIONS.md`, then
`docs/DECISIONS.md`); they were split here unchanged. Package and study
decision files stay with their package or study (e.g.
`backend/scoring/DECISIONS.md`, `research/*/DECISIONS.md`).

| File | Branch | Merged in |
|---|---|---|
| [ci-github-actions.md](ci-github-actions.md) | ci/github-actions | #6 |
| [chore-loose-ends.md](chore-loose-ends.md) | chore/loose-ends | #8 |
| [feat-deploy-stack.md](feat-deploy-stack.md) | feat/deploy-stack | #9 |
| [feat-langgraph-studio.md](feat-langgraph-studio.md) | feat/langgraph-studio | #15 |
| [research-level-target-spec.md](research-level-target-spec.md) | research/level-target-spec | #16 |
| [feat-agents-architecture.md](feat-agents-architecture.md) | feat/agents-architecture (agents v2, job 1) | #17 |
| [fix-journal-scoring.md](fix-journal-scoring.md) | fix/journal-scoring | #18 |
| [feat-agents-prompt-config.md](feat-agents-prompt-config.md) | feat/agents-prompt-config (agents v2, job 2) | #19 |
| [feat-status-board.md](feat-status-board.md) | feat/status-board | #20 |
| [feat-agents-discussion.md](feat-agents-discussion.md) | feat/agents-discussion (agents v2, job 4) | #23 |
| [feat-agents-metrics.md](feat-agents-metrics.md) | feat/agents-metrics (agents v2, job 5) | #24 |
| [feat-datafeed-spread.md](feat-datafeed-spread.md) | feat/datafeed-spread | #25 |
| [feat-agents-llm-client.md](feat-agents-llm-client.md) | feat/agents-llm-client (agents v2, job 3) | #21 (into its stack), #28 |
| [docs-runbook-cutover.md](docs-runbook-cutover.md) | docs/runbook-cutover | #30 |
| [refactor-backend-layout.md](refactor-backend-layout.md) | refactor/backend-layout (restructure phase 0 and PR 1) | #31 |
| [refactor-scanner-package.md](refactor-scanner-package.md) | refactor/scanner-package (restructure PR 2) | #32 |
| [chore-gitignore-data.md](chore-gitignore-data.md) | chore/gitignore-data | #33 |
| [chore-repo-root-tidy.md](chore-repo-root-tidy.md) | chore/repo-root-tidy | #34 |
| [docs-tidy.md](docs-tidy.md) | docs/tidy (restructure PR 3) | this PR |

**Found on the way, not fixed** (each for its own PR), as of 2026-10-21:
- **F2:** source-text tests; see `refactor-backend-layout.md`.
- **F4:** an intermittent hang of `python -m backend.datafeed.ingest`, the feed
  container's entry point; see `fix-narrow-excepts.md` (on #35, not merged yet).
- **F5:** `TWSConnector` swallows bar-callback errors; see `fix-narrow-excepts.md` (on #35, not merged yet).
