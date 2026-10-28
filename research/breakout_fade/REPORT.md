# breakout_fade: report

## 1. Verdict (B13, H1): **PASS on silver dev.** The gold finding replicates

**This was outcome analysis 1** (`ATTEMPTS.json`), run once on silver dev
2016–2023. It ran on the pre-registration with Amendment 1: `PREREG.md`
sha256 `5bf18647…`, committed and pushed at b1d5adb before the run (PR #59),
with the code from #57 plus Amendment 1.

All OBSERVED: `results/dev.json` and `results/rw.json`.

| | silver | random walk (200 paths, same timestamps) | difference | 95 % CI | p (one-sided) | BH p |
|---|---:|---:|---:|---|---:|---:|
| **H1** share of breaks that become B | **28.3 %** (143 of 505) | 34.3 % | **−5.9 pp** | [−9.9, −2.0] | 0.0029 | **0.0058** |
| **H2** fade trade, stratified (Amendment 1) | — | — | **−3.5 pp** | [−7.1, +0.2] | 0.97 | 0.97 |

**H1 passes:** the difference is below 0, the CI excludes 0 and BH p is
under 0.05. After a close beyond the last swing high (bear) or low (bull),
the +1 σ follow-through within 5 candles came less often on silver than on a
driftless random walk with the same σ. That is the same direction and size
as gold dev (−5.4 pp, `research/pullback_structure`).

**H2 is not significant, and its point estimate is negative.** Fading the
break (target the swing low, stop at entry + 1 σ) did *not* reach the target
more often than the random walk.
- **By quartile** (silver minus random walk): +3.0, −3.0, −11.5, −2.6 pp; n
  122 / 107 / 100 / 81 resolved trades.
- **Unresolved:** 95 of 505 trades, none truncated.
- **Break-even spread: −0.22 σ.** The fade loses before any spread.

**Counts:** 505 breaks, 0 excluded for short days, 143 B, 362 C.

**Descriptive only** (no test):
- **By direction:** B share bear 27.0 %, bull 29.7 %. H2 analytic excess bear
  −6.5 pp, bull −3.2 pp.
- **W sensitivity, silver against the random walk:** W = 4: 25.0 % vs 29.3 %
  (−4.3 pp); W = 5: 28.3 % vs 34.3 % (−6.0 pp); W = 6: 33.9 % vs 38.3 %
  (−4.4 pp).
- **Mean move after a break, in σ, in the break's direction:** after 5 candles
  −0.03 (random walk −0.005); after 30 candles −0.15 (random walk −0.01).
- **H2 unstratified analytic excess:** −4.9 pp. The hit rate was 25.6 %
  against a mean P of 30.5 %.

INFERRED:
- **Breaks follow through less than chance**, both at +1 σ in 5 candles (H1)
  and on average after 30 candles (−0.15 σ).
- **The reversal is too small to pay as traded in H2.** It doesn't reach the
  swing low before the 1 σ stop more often than chance. A different exit
  might, but that would be a new question with its own PREREG.
- **The replications listed in B20** (gold holdout, silver holdout, EURUSD
  dev) are the next confirmations, each under its own PREREG.

## 2. The first run: STOPPED at the random-walk check (B15), before any outcome

The pre-registration is `PREREG.md` at 3b2a56c (sha256 `7e86afc4…`), and the
code is #57 (229fa28). Steps ran in PREREG order. **No silver outcome has been
read:** `ATTEMPTS.json` `outcome_analyses` is 0, and the dev step (H1, H2) did
not run.

**Step B18, counts (no outcome): PASS.** `results/counts.json`:
- **Breaks:** 505 on silver dev 2016–2023 (256 bear, 249 bull).
- **Short days:** 0 trade dates under 1,000 rows, so no exclusions. Silver's
  shortest dev day is 2020-02-17, with 1,005 rows.
- **Candles:** 47,219 1h candles kept, 14 dropped.
- **Power:** at n = 505, the MDE at 80 % is 5.9 pp for H1 and at most 6.2 pp
  for H2. Gold's Q2 effect (−5.4 pp) sits just under it.

**Step B15, the random-walk check: FAIL** (200 paths on the silver dev
timestamps; `results/rw.json`):

| check | result | required | |
|---|---:|---|---|
| H2 pooled mean(hit − P) | **−1.86 pp** (n 128,131 resolved simulated trades) | \|·\| ≤ 1 pp | fail |
| H2 95 % CI covers 0 | **81.5 %** of 200 paths | 93–97 % | fail |
| H1 95 % CI covers 0 | 96.5 % of 200 paths | 93–97 % | pass |

The random walk's B share, which would have been H1's null, is 34.3 %. By B15
the study stops here, and dev is not run.

**Why H2 fails** (diagnosis on the random walk only, 20 paths, 14,097 breaks;
no real outcome):
- **The resolution filter.** The baseline P = stop / (stop + target) is the
  driftless chance of the target first *without a time limit*. H2 counts only
  trades resolved within 30 candles. The target (the swing low, typically
  2–3.5 σ away) is far, and the stop (1 σ) is near. So the unresolved trades,
  9.6 % in all, are mostly would-be hits, and dropping them biases the
  measured hit rate below P.
- **The bias grows with target distance:**

  | target distance (σ) | unresolved | excess among resolved |
  |---|---:|---:|
  | 0.55–1.87 | 5.7 % | −1.3 pp |
  | 1.87–2.60 | 8.4 % | −0.8 pp |
  | 2.60–3.50 | 10.2 % | −1.7 pp |
  | 3.50–10.3 | 14.0 % | −4.5 pp |

- **Counting unresolved trades as misses** makes it worse: −4.3 pp.
- **A residual of about −1 pp even for near targets** comes from the 1-min
  discreteness and the tie rule (a bar holding both counts as the stop, B8).

**The fix needs the owner.** It can be done before any outcome, as an
amendment. INFERRED options:
1. **H2 against the random walk, as H1 already is.** Test silver's
   mean(hit − P) minus the random walk's mean(hit − P) on the same timestamps.
   The bias then cancels: the walk has the same horizon, discreteness and tie
   rule.
2. **A random-walk baseline per event,** P_rw(target distance in σ), instead of
   the analytic P.
3. **Drop H2 and keep H1 only.** H1 passed its machinery check.
