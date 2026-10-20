# level_target: decisions taken without the owner

Overnight run, 2026-10-17, unattended. Rule: on any decision, take the
conservative option, log it here, continue. Branch
`research/level-target-spec`, off `origin/main` @ 9cbf4fc. These are spec
choices; nothing has been run.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| T1 | **Alerts come from the production scanner's level engine,** replayed unchanged (fixed $1 TOL, first touch only, 15-minute collapse). | The forward test trades the production scanner's alerts, so dev and holdout must see the same events. | A σ-relative touch rule (a scanner change and a new journal epoch; §12 Q4). |
| T2 | **Entry waits for the alert** (emitting bar + 1 min, then the next open), up to ~16 min after the touch. | Live can't enter earlier; entering at the touch would credit the replay with a move no live run gets. | Enter at the touch bar's close. |
| T3 | **Ambiguous approach side** (the pre-touch close within TOL of the level) is an exclusion, counted. | A direction rule needs a side; guessing one adds noise to both variants. | Use the touch bar's open. |
| T4 | **The stop is anchored to the level** (P ∓ b·σ), and R = |entry − stop|. A stop nearer than 0.25σ to the entry is an exclusion. | It's the trade the hypothesis describes. The floor stops tiny R units from inflating R. | Stop anchored to the entry (a plain σ bracket, which is what the placebo baseline already measures). |
| T5 | **Target = the nearest qualifying level in [d_min, 3σ]**, touched levels included. No target → exclusion. | One target rule, fixed in advance; no fallback that would blur "level to level" into "fixed σ". | Second-nearest, or a σ fallback target. |
| T6 | **Primary H = 240 min only**, with no window crossing a close. H = 60 is reported in dev for the chosen variant and isn't selected on. | It matches the owner's 2026-10-17 horizon decision and HAR's fitted horizons. A smaller grid means fewer chances to overfit dev. | Select over H ∈ {60, 240}. |
| T7 | **Costs proportional to price in history** (provisional 1.0 bp spread + 0.5 bp commission, round trip). Measured values replace them (the same 2-week bid/ask window as agents v1). Financing is zero in v1 because no window crosses a close; the formula for v2 assumes **both sides pay**. | A fixed $ cost would be 6× heavier in 2007 than now relative to σ. Paying on both sides can only understate a result. | A fixed $ spread; financing at the actual long/short rates. |
| T8 | **The placebo baseline uses the same clock minute on a different day in the same quarter,** with the same direction, the same σ distances and the same cap. | It holds time of day, regime and bracket shape fixed, and avoids the same-day placebo bias found in `research/levels`. | Random minutes on any day; same-day placebos. |
| T9 | **A/A and injection gates** run before any real outcome, and stop the phase on failure (93–97 % coverage, ±0.01 R). | `research/levels_diag` showed the inference can be biased; the gates come first. | Trust the bootstrap. |
| T10 | **Development selects exactly one variant by a literal rule.** It must pass ≥ 1,000 trades per era, mean net R > 0 and Δ > 0 in both eras, and it is picked by the highest lower bound of Δ. The holdout computes **only** that variant. If none qualifies, the holdout stays unread. | A one-look holdout stays one look; the grid's multiplicity is paid for in dev. | Report every variant on the holdout (16 looks). |
| T11 | **σ for dev and holdout is a HAR fit on 2007–2020 only**, pinned before dev. The forward test uses the production pin. | Both shipped models were fitted on data that includes the holdout years. | Use `parquet2021_2023` throughout (σ in sample on the holdout). |
| T12 | **The 2026-02..09 gap is left unused** until the owner decides (§12 Q1). | Unseen data stays unseen. | Use it as a second holdout. |
| T13 | **Jobs 3 and 4 share this branch,** as the task said ("push to the same branch as job 3"), with one PR. This overrides the general one-branch-per-job rule. | The explicit instruction for these two jobs is followed; both are docs. | Two branches. |

**Owner decisions, 2026-10-17 (later):**

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| T14 | **Stop rule (owner):** if the historical net edge on 2007–2020 is below 0.10 R or not significant, the idea is dropped: no holdout, no forward test. **Reading:** "net edge" is the chosen variant's mean net R per trade at the central cost (the primary metric). "Significant" is the one-sided day-block bootstrap test of mean net R > 0 at **α = 0.05 / 16**, Bonferroni over the 16 variants, because the variant was chosen out of them on the same data. | The stricter reading makes it harder to carry a selection artefact into the holdout. | Unadjusted α = 0.05; or "edge" = Δ against the placebos. |
| T15 | **The $1 TOL is kept** for this study; a σ-relative touch is an open item for a later scanner epoch (SPEC §12 Q4). | The forward test keeps the production scanner's alerts. | Change the scanner now. |
