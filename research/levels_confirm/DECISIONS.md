# Decisions log: levels confirmation

Entries 1–9 were written before any outcome on 2024+ data.

1. **Worktree and branch.** This work runs in the existing worktree
   `/Users/x/Desktop/algo_project_levels`, switched to a new branch
   `research/levels-confirm` (from `research/levels`, 1136125). The main checkout
   and the other session are untouched.
2. **Order of commits:** sealing (4dc0187), then the counts-only audit (5b7edad),
   then PREREG. SEALING.md gets the PREREG hash in a follow-up commit, because the
   hash can't exist before the file is committed.
3. **Reading the raw text file.** 2024+ rows exist only in `XAUUSD.txt`, so the
   parser scans the file. Rows are kept for file dates 2023-11-01 .. 2026-01-30
   and cached (gitignored). The file has no rows after January 2026.
4. **January 2026 timezone flag** (range-profile shift +1, corr 0.71) was
   resolved with exact session edges (23:00 / 21:59 UTC, as in December 2025).
   UTC is kept. Documented in the audit.
5. **Placebo placement formula.** p = open_P / (1 − s · d · f_P) puts the placebo at
   exactly distance d in the same σ convention as the real level (σ priced at the
   level). That makes the "same distance" test exact and eligibility automatic
   (d >= 0.1).
6. **Session cells by each event's own break time** (not the parent's): the
   comparison is "real breaks in Asia vs placebo breaks in Asia".
7. **A fourth verdict label (SMALLER THAN CLAIMED)** covers p < 0.05 with
   −5 < effect < 0, which the task's mapping doesn't assign. Added before any data.
8. **The declared dry run on 2021–2023** reduces the risk of a bug in the
   one-shot run. It uses seen data only.
9. **MDE formula:** (z_0.95 + z_0.80) · bootstrap SE.

## During and after the run
