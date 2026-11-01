## Owner decision (post hoc), 2026-10-22

Taken by the owner after seeing this report's numbers. It is **not** part of
the pre-registered rule: **the pre-registered verdict stays INCONCLUSIVE**
(gate G5 failed), and G5 is **not** amended.

**Decision: proceed to build the GEX level test**, under its own PREREG
(`research/gex_levels/`). The reasons:
- **R with IBKR's own model gamma is 8.20 %.** 98.9 % of that |GEX| comes from
  IBKR's gamma (2,373 contracts with open interest). Our formula fills in only
  for the 20 contracts that have open interest but no IBKR gamma (1.1 %). G5
  exists to make sure the gamma behind R is right, and R computed essentially
  without our formula gives the same answer (8.19 % with it). So G5's purpose
  is met without our formula.
- **R > 5 % under every sensitivity:** the next 2 expiries only, 7.21 %; the
  front GC month only as D, 8.38 %; IBKR's gamma, 8.20 %.

**Caveats, unchanged:**
- **|GEX| is unsigned:** an upper bound on net dealer hedging. The net, under
  any dealer-positioning assumption, can be much smaller.
- **D is GC futures volume only:** spot, OTC and other gold trading isn't in
  it, so R overstates the share of all gold trading.
- **One day** (trade date 2026-10-22, open interest as of 2026-10-21, inferred).
  Open interest moves slowly, but one day shows the size, not how stable it
  is.
- R > 5 % says a GEX level effect is **plausible**, not that it exists. That is
  the level test's question.

**Lesson for future gates (the G5 design flaw).** G5 was an *unweighted* median
of the relative difference between our gamma and IBKR's, over all 3,797
contracts with an IBKR gamma.
- Most listed contracts are far out of the money, where gamma is close to 0,
  and there the relative difference is large and meaningless: a median of
  132 % for contracts 3+ SD out, against 0.56 % within 1 SD of the money.
- So the gate measured the deep tails, which hold almost none of |GEX| (all
  contracts 2+ SD out together hold 6.5 %).
- Weighted by |GEX| the median difference is 1.24 %.
- **A gate that protects a weighted sum must be weighted like that sum** (here
  by each contract's |GEX|), or restricted to the contracts that carry it.
  Otherwise the gate measures the part of the data that the result doesn't
  depend on.
