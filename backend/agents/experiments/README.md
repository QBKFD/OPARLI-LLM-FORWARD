# Experiments

One folder per experiment: `agents/experiments/<experiment_id>/PREREG.md`.

- **Live mode** (`snapshot.mode = "live"`) runs only with
  `snapshot.experiment_id` set to a folder here that holds a non-empty
  `PREREG.md`.
  - Live data comes after the research seal (2026-10-01), and every live
    decision is scored later. So the hypotheses, arms, scoring and stopping
    rules must be written down, and committed, before the first live run.
  - The code checks only that the file exists. Committing it first is up to
    you.
- **Replay mode** may also name an experiment. The same check applies. The seal
  still holds: no replay after 2026-10-01, with or without an experiment.
- **The experiment_id is stored** on every snapshot and decision of a run
  (agent store columns `snapshots.experiment_id`, `decisions.experiment_id`).
- **ID format:** letters, digits, `.`, `_`, `-`, starting with a letter or
  digit, at most 64 characters.
- **Keep ids opaque** (e.g. `exp-0001`). The id is stored inside the snapshot,
  and future prompts must never render it, so it can't hint at a hypothesis.
