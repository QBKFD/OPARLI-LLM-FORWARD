# Study B (news_surprise): does the FOMC surprise predict gold's direction? PREREG DRAFT

**Status: DRAFT for the owner, 2026-11-08. Not a PREREG.**
- The file is named `PREREG_DRAFT.md` on purpose: `research/data_guard.py` accepts only a
  committed `PREREG.md` naming a guarded file, so the macro calendar stays closed to this
  study even if the draft is merged. The bars aren't a guarded file. What keeps them unread
  is that this branch has no code.
- **No price or surprise value was read.** The FRBSF files were downloaded on 2026-11-08 (owner's
  go-ahead) and registered "unused by any study" (`docs/FOMC_SURPRISES.md`); only their sheet
  names, headers, row counts and date columns were read. The other numbers are Study A's
  committed event counts (`research/news_vol/results/counts.md`).
- **Updated 2026-11-08 after the owner's review:** the extension to 2012–2023 is **blocked** by
  the period rules (§2a); the design stays on 2016–2023 until the owner decides.
- **OPEN** marks what must be fixed before the PREREG. **B1–B13** are Claude's proposals
  (reasons and alternatives in `DECISIONS.md`); the owner approves, changes or rejects each one.

Basis: `docs/STUDY_B_FEASIBILITY.md` (#65), Study A (`research/news_vol`, PASS, replicated in
`research/news_vol_rep`), and the owner's decisions of 2026-11-08 (`docs/DECISIONS.md`,
OD4–OD6; PR #70).

## 1. Question

**Part 1, FOMC (this draft):** at a scheduled FOMC announcement, does the sign and size of
the monetary-policy surprise predict gold's return over the next 60 minutes, and over the
next 240 market-open minutes?

**Part 2, data releases (CPI, NFP), later:** this part runs only if the pilot (§9) passes,
with surprises scaled by OD5. It is not designed here.

**What a PASS would and wouldn't say:**
- **Would say:** gold's reaction to the FOMC has a direction that the surprise, once known,
  explains.
- **Wouldn't say:** that the direction is tradeable. The surprise is measured from futures
  around the statement and the press conference. It is known only after the release, so it isn't a
  forecast.

## 2. Data

| Item | Proposal | Status |
|---|---|---|
| Market | XAUUSD, dev 2016-01-01 .. 2023-12-31 (B1). Bars: `XAUUSD.txt`, as Study A (N11), through data_guard, the manifest and `sealing.assert_period(..., "dev")` | Same files as Study A |
| Events | Scheduled FOMC statements in #60's calendar (`FOMC Statement` / `Federal Funds Rate`, grouped by minute as Study A's N7): **64 meetings**, 8 a year. The two unscheduled 2020 actions are excluded (B2) | Counts from Study A, below |
| Surprise | The FRBSF files (`docs/FOMC_SURPRISES.md`). **Primary: `ME` in `mps.csv`**, the "monetary event" surprise (statement and press conference together, Acosta et al. 2025), because the 60-min window holds both. **Secondary:** Bauer–Swanson's `MPS` (statement window; ends 2023-12-13). **Check:** `STMT` (statement only). All three are normalized so that +1 moves the 1-year Treasury yield +1 pp: **positive = hawkish** (B3, revised from the column definitions; B10) | **Downloaded 2026-11-08**, hashes and terms in the manifest, "unused by any study". Both cover 2012–2023 in full. **No value read.** OPEN: the PREREG names the files |
| Seal | Nothing from 2024-01-01 is read; the global seal (2026-10-01) holds anyway | — |

**Scheduled statements per year** (Study A's counts, minus the 2 unscheduled):

| 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | total |
|---|---|---|---|---|---|---|---|---|
| 8 | 8 | 8 | 8 | 8 | 8 | 8 | 8 | **64** |

Study A's FOMC release minutes also hold press conferences: 4 a year in 2016–2018, then one
per meeting. **OPEN:** match each meeting's FRBSF row to its statement minute by NY date, and
list any meeting the FRBSF files lack.

## 2a. The 2012–2015 extension (owner, 2026-11-08): blocked by the rules as written

The owner asked to extend Part 1 to FOMC 2012–2023 if the sealing and era rules allow 2012–2015 for gold, and
otherwise to stop and report. **They don't, so the draft stays on 2016–2023.**

| Rule | 2012–2015 | Blocks? |
|---|---|---|
| **Instrument periods** (`research/SEALING.md`, owner 2026-10-28; `sealing.py`) | Gold has three periods: dev 2016–2023, holdout 2024–2026-09, sealed. 2012–2015 is in **none**, so `sealing.assert_period("XAUUSD", ts, "dev")` refuses those rows, and no other period name exists. They aren't sealed, and they have been read before (`squeeze_oco` 2012–2020, `sigma_compare` 2007–2020) | **Yes:** it needs a period from the owner (e.g. "pre-dev 2012–2015, seen") |
| **Data eras of `XAUUSD.txt`** (`research/README.md`, owner 2026-10-26) | 2012–2014 is the era with a 60-min daily break against 61 min from 2015; the owner called the difference cosmetic | No |
| **Event times** (`docs/DECISIONS.md` MC2) | #60's calendar covers 2015–2026 only. FOMC statements were at 12:30 or 14:15 NY before 2013, so "the 14:00 anchor would need an era rule". USMPD has each statement's time, so a source exists | **Yes, until a rule says which source gives 2012–2014's times** |
| **σ** | Study A's walk-forward HAR starts in 2016 (first out-of-sample fold 2018). 2012–2015 needs a σ rule too (for example sigma_compare's folds, which start in 2007) | A design choice, not a rule |

**Power if the owner opens 2012–2015** (96 scheduled meetings, 8 a year; §5's method): about **0.25** at
α = 0.05, and 0.31 at BH's strictest rank (0.0125), against 0.31 and 0.38 with 64.

## 3. Windows and outcome

- **Origin:** the statement minute e (14:00 NY) (B4).
- **Window:** the first H market-open minutes from e, [e, E), with H = 60 (primary) and 240
  (secondary).
  - At 60 minutes the window holds the statement and, from 2019 or on quarterly meetings
    before, the 14:30 press conference.
  - At 240 minutes it crosses the 17:00–18:00 NY break and ends at 19:00 NY. Gap returns are
    dropped, as in vol_block.
- **Return:** r(e, H) = the sum of the 1-min log returns of the bars opening in [e, E).
  - Coverage: at least 90 % of the H minutes have a return (Study A's rule), else the event
    drops out and is listed.
- **Scaled return (primary outcome):** z(e, H) = r(e, H) / σ(e, H) (B5).
  - **σ is HAR's σ over the same window, as of e**, from Study A's walk-forward fit: the
    model of the test year that contains e, trained up to the year before. Events of 2016 and
    2017 use the 2018 fold's model; that is look-ahead in the parameters only, and a check
    without those years is in §6.
  - **Why not the news-aware σ:** every window here holds an FOMC release, so d_fomc = 1 in
    all of them. The news-aware σ is then HAR's σ times one constant (exp(β_fomc / 2) within a
    fold). That changes no test below, so plain HAR is used. What the scaling does do is put
    a calm and a stormy day's reactions on one scale.

## 4. Hypotheses and tests

Let s be the primary surprise (positive = hawkish, higher expected rates).

| # | Hypothesis | Test | Role |
|---|---|---|---|
| **H1** | **A hawkish surprise moves gold down:** slope b < 0 in z(e, 60) = a + b·s + ε | OLS slope; **permutation p**, one-sided (B6): s shuffled across the events, B = 10,000, p = (1 + #{b* ≤ b}) / (B + 1), seed fixed in the PREREG | **Verdict** |
| H1' | The same at H = 240 | as H1 | BH family |
| H2 | **Size:** a larger \|s\| goes with a larger \|z(e, 60)\| | Spearman ρ(\|s\|, \|z\|), one-sided ρ > 0, permutation p | BH family |
| H2' | The same at H = 240 | as H2 | BH family |

- **BH** over H1, H1', H2, H2', q = 0.05 (B7).
- **Verdict (D7-style, B8):**
  - **PASS** if H1's b < 0 with BH p < 0.05.
  - **FAIL** otherwise.
  - The other three are reported with BH p and don't change the verdict.
- **Reported with every test:**
  - the estimate with a 95 % interval (a bootstrap over events, B = 10,000);
  - the same on raw returns r, with Spearman in place of OLS (a check, outside BH);
  - the secondary surprise's slope (outside BH).

## 5. Power (rough, before any data)

There are 64 events (96 with 2012–2015, §2a). For a one-sided correlation test at α = 0.05, 80 % power needs about
|ρ| ≥ 0.31. At BH's strictest rank of 4, α = 0.0125, it needs about 0.38. With 2018–2023 only
(48 events) the figures are 0.35 and 0.43.

**OPEN:** no prior effect size for gold at a 60-minute horizon has been checked. If the true
correlation is around 0.2, the study is likely to come back null, and a null would say little.
§1's "could come back null" applies with force.

## 6. Checks (reported, outside the verdict)

- **2018–2023 only:** no look-ahead in σ's parameters (B5).
- **The secondary surprise** (USMPD target).
- **Without 2020** (COVID; Study A's weakest fold).
- **Influence:** the slope without each single event (the largest change is reported).

## 7. Machinery check (before the run, from the merged commit)

As Study A's §7, on synthetic bars:
- the real event times and real bar times (timestamps only);
- synthetic surprises s ~ N(0, 1);
- a reaction r = γ·s·σ in the first 30 minutes after e.

The check passes if:
- **with γ < 0:** H1 is detected (p < 0.05), and b recovers γ within ×0.5–×2;
- **with γ = 0:** p > 0.05.

The generator's parameters are fixed in the PREREG.

## 8. Rules

- **The run order is Study A's:**
  1. PREREG merged to main;
  2. machinery check from the merged commit;
  3. ATTEMPTS bumped;
  4. one run.
- **No reruns.** A change after the run is an amendment, reported as post hoc.
- **No raw price or surprise rows in git** (the FRBSF terms are OPEN); only aggregates.

## 9. Part 2 (data releases): what has to happen first

- **The pilot (feasibility §4):**
  - a sample of about 120 CPI m/m and NFP releases, 2019–2023;
  - Forex Factory's forecast compared with an independent consensus.
  - **The comparison source is OPEN.** The candidates, all OPEN on access and terms: archived
    press coverage quoting the poll median, a library Bloomberg terminal, a paid calendar API
    with history.
- **The pilot's rule (feasibility §4, a proposal):** Forex Factory surprises enter only if the
  two surprise series correlate ≥ 0.9 and ≥ 80 % of forecasts match to the published precision.
- **Scaling, fixed by the owner (OD5):** z = surprise / the expanding SD of the series' past
  surprises, used once n_prior ≥ 24; a rolling 60-surprise SD as a sensitivity check only.
  Two details of OD5 remain open: the SD estimator, and when the rolling check starts.

## 10. OPEN before this becomes a PREREG

1. **Done 2026-11-08:** the FRBSF files are downloaded and registered (`docs/FOMC_SURPRISES.md`). The PREREG
   must name their manifest keys. **No surprise value is looked at before the PREREG is merged.**
1a. **The owner's call on 2012–2015 (§2a):** a period for gold 2012–2015, a rule for 2012–2014's statement
   times, and a σ for those years.
2. B1–B13 approved or changed.
3. The primary surprise column (B3), fixed from the column definitions alone.
4. The machinery check's generator parameters.
5. The pilot's comparison source (Part 2 only; it doesn't block Part 1).
