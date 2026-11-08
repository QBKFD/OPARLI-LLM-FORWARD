# news_surprise (Study B): Claude's proposals for the draft

The owner's brief (2026-11-08): "the Study B draft, FOMC-first as in
docs/STUDY_B_FEASIBILITY.md, with the pilot comparison source left OPEN." Everything below is
a proposal; none is approved.

| # | Proposal | Why | Alternative |
|---|---|---|---|
| B1 | **XAUUSD dev 2016–2023, `XAUUSD.txt`**, as Study A. | The same bars, period and seal as Study A, whose windows and σ this reuses. | Add XAGUSD and EURUSD now (feasibility §5 says later, as a replication). |
| B2 | **Scheduled statements only**: the 2020-03-03 and 2020-03-15 actions are out. | No market expected them at a known minute, so "surprise relative to the schedule" doesn't apply, and 03-15 came on a Sunday with gold closed. Study A kept them (N8) because it asked about scheduled windows' vol. | Keep 03-03 (gold was open). |
| B3 | **Primary surprise: Bauer–Swanson's MPS** (FRBSF); secondary: USMPD's front fed funds change in the statement window. | MPS covers path as well as target news, and it is the published, maintained series. A target-only surprise is near zero at meetings with fully priced decisions. Fixed from column definitions, before any value is seen. | USMPD as primary; our own ZQ/SR3 computation (paid data, feasibility §3). |
| B4 | **Origin at the statement minute**, not Study A's hourly origins. | The question is about one event's reaction. An hourly origin would start up to 59 minutes early and dilute it. | Study A's 13:00 NY origin with H = 240. |
| B5 | **z = return / HAR σ from Study A's walk-forward fit**; 2016–2017 use the 2018 fold's model; check without them. | Puts reactions on calm and stormy days on one scale. Within a fold the news-aware σ is HAR's σ times a constant here (every window holds FOMC), so it adds nothing. The 2018 fold is the earliest out-of-sample model. | Drop 2016–2017 (48 events); raw returns as primary. |
| B6 | **H1 one-sided (hawkish → gold down)**, with a permutation p. | The direction is the standard prediction (higher expected rates: a stronger dollar, higher real yields, a higher carry cost of gold), and stating it gains power on few events. The permutation test needs no normality with 64 events. | Two-sided, as Study A's Q3. |
| B7 | **BH over four tests** (H1, H1', H2, H2'), q = 0.05. | Study A's practice: one family with the verdict test in it. | Bonferroni; H1 alone. |
| B8 | **The verdict rests on H1 at H = 60 only.** | 60 minutes holds the statement and the press conference and doesn't cross the daily break. | H = 240 primary, as Study A. |
| B9 | **Coverage ≥ 90 % of the window's minutes**, else the event drops out and is listed. | Study A's rule. | — |
| B10 | **Sign convention: s > 0 = hawkish** (the FRBSF series' own sign is checked on download). | One stated direction for H1. | — |
| B11 | **Meetings matched to FRBSF rows by New York date**; a meeting without a row is listed, not imputed. | The calendar's statement time is anchor-checked (#60); the FRBSF files are per meeting. | Match by timestamp. |
| B12 | **The draft reads no data and has no code.** It is named `PREREG_DRAFT.md`, so `data_guard` refuses the guarded calendar under it. The bars aren't guarded; no code reads them. | The owner's rule: the PREREG is merged before any outcome is read, and the FRBSF download needs a go-ahead. | — |
