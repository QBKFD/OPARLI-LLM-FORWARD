# Pre-registration: is listed gold options positioning big enough for a GEX level effect?

Committed **before any data** (no probe has run, and no code exists yet).
Changes after the owner's review are committed before the probe first runs;
after that, this file is never edited. Deviations go to "Amendments" in
REPORT.md.

## Question

A GEX ("gamma exposure") level effect needs option dealers' delta hedging to
be a noticeable part of what trades in gold. The listed gold options market is
small next to the underlying, and much gold optionality is London OTC and
invisible. Before any GEX level test is built: **is the hedging flow implied by
listed COMEX gold options (OG, on GC futures) big enough, relative to GC
futures volume, for such an effect to be plausible at all?**

This is a feasibility check, not a test of any effect. Nothing here looks at
prices around strikes, returns or alerts.

## Seal (read first)

`research/SEALING.md` reserves all data from 2026-10-01 onward for the final
strategy test. This study reads a **live snapshot** taken after that date: one
options chain, plus GC daily bars for the 20 trade dates before it. It runs by
the owner's explicit request (2026-10-22). To keep the sealed period unused
for any price question:
- **It reads only magnitudes:** option open interest, implied volatility,
  gamma, quotes (whether one exists, and the underlying futures mid), GC daily
  volume, and the GC daily close used only to turn contracts into dollars.
- **It reads no XAUUSD bars, computes no returns, and looks at no price
  behaviour** at, near or around any strike or level.
- A GEX level test that follows from this study would have its own PREREG and
  its own data plan, which must respect the seal.

## Data: one snapshot

Taken by `tools/options_probe.py` (read-only) on the production IBKR paper
gateway, inside the scanner image, by the owner. It runs only while COMEX is
open on a regular trading day: it refuses weekends, the daily break and CME
holidays. The intended time is about 05:00 UTC on a weekday.

- **Options:** IBKR trading class `OG` (monthly options on GC futures, COMEX),
  every listed strike and both rights, for the **next 3 expiries** whose expiry
  is at least 2 calendar days after the snapshot (an expiry closer than that
  has a near-singular gamma). Contracts come from IBKR contract details per
  expiry ("listed").
- **Per option contract:** open interest (generic tick 101), IBKR's model
  implied volatility and model gamma (`modelGreeks`), bid and ask.
- **Underlying:** each option's GC futures contract, by the `underConId` in its
  contract details, never by expiry month (OG options are written on the next
  active GC month). Per future: bid, ask, last, close, futures open interest
  (generic tick 588).
- **Freshness:** market data type 1 (live) is requested first. A contract that
  gets no tick within the probe's timeout is requested again as type 2
  (frozen). Per contract, the probe records which type its values came from,
  and the time each value was received.
- **GC daily bars:** `TRADES`, 1 day, `useRTH=False`, for the nearest 8 listed
  GC futures months: date, close, volume (contracts).

**One snapshot decides.** The first probe run that exits successfully and
passes the gates below is the one analysed. A rerun is allowed only after a
probe failure (for example error 10197, a competing live session) or a failed
gate. Every attempt, with its time and the reason it was not used, is listed in
REPORT.md. Among successful runs that pass the gates, the first one is used,
never another one chosen afterwards.

**Raw data isn't committed.** The probe's JSON contains exchange market data
(IBKR/CME) and the repo is public. It stays under `research/gex_feasibility/data/`
(git-ignored). REPORT.md holds only aggregates, and the JSON's SHA-256.

## The metric R

For each option contract i in the 3 selected expiries:

- `F_i`: the price of its underlying GC future (by `underConId`). This is the
  mid of bid and ask when both are positive, else the last price, else the
  close. The source is recorded.
- `T_i`: years from the snapshot time to the option's expiry. The expiry is
  the contract's `realExpirationDate` at its `lastTradeTime` in its
  `timeZoneId`, or 12:30 America/Chicago if IBKR gives no time. 1 year =
  365 days.
- `σ_i`: IBKR's model implied volatility for that contract.
- `Γ_i`: **Black-76 gamma** with `r = 0`:
  `Γ = φ(d1) / (F σ √T)`, `d1 = (ln(F/K) + σ²T/2) / (σ √T)`.
  Calls and puts have the same gamma. `r = 0` gives the largest gamma
  (`e^(−rT) ≤ 1`); at these maturities the difference is under 1 %.
- `OI_i`: the contract's open interest, in contracts.

**|GEX|** in dollars of gold traded per 1 % move in the underlying, unsigned:

    |GEX| = Σ_i  Γ_i · OI_i · 100 · F_i² · 0.01

- 100 oz is the OG/GC contract multiplier.
- `Γ · 100 · OI` is the hedge change in ounces per $1 move, `· 0.01 F` makes
  it per 1 % move, and `· F` makes it dollars.
- **Unsigned:** every contract counts positively, whatever side dealers are
  on. So |GEX| is an **upper bound** on the net dealer hedging flow, which no
  sign assumption can exceed.

**D**: the average daily GC futures dollar volume over the last 20 trade
dates. For each trade date, sum `volume × 100 × close` over the GC months
fetched. Then average over the 20 most recent trade dates that are complete
(before the snapshot's trade date).

**R = |GEX| / D.**

## Decision rule

| R | Decision |
|---|---|
| **R < 1 %** | **GEX dropped.** No GEX level test. |
| **1 % ≤ R ≤ 5 %** | A GEX level test may be built later, at low priority. |
| **R > 5 %** | **Build the pre-registered GEX level test** (its own PREREG). |

R is rounded only for display; the comparison uses the unrounded value.

## Gates (checked before the verdict)

If any gate fails, the verdict is **INCONCLUSIVE**: no decision, and a rerun
under the rules above.

| # | Gate |
|---|---|
| G1 | The snapshot is inside COMEX trading hours on a regular trading day (the probe's own check, rechecked). |
| G2 | At least 20 complete GC trade dates with bars. |
| G3 | At least 90 % of the listed contracts in the selected expiries have an open-interest value (live or frozen). |
| G4 | Contracts whose IV had to be filled in (below) hold at most 25 % of the total open interest. |
| G5 | **Our gamma agrees with IBKR's.** For every contract with an IBKR model gamma, Black-76 gamma is recomputed with IBKR's own `impliedVol` and `undPrice` and the same T. The median of `|Γ_ours / Γ_IBKR − 1|` is at most 5 %. |
| G6 | Every selected expiry has an underlying price F. |

**IV fill rule:** a contract with open interest > 0 but no IBKR model IV takes
the IV of the nearest strike with an IV in the same expiry (either right;
on a tie, the lower strike). Contracts with no open interest, or zero, add
nothing to |GEX| and need no IV.

## Reported, no decision

- The snapshot time (UTC), the trade date, and the **open-interest settlement
  date**. IBKR doesn't timestamp open interest, so the date is inferred: the
  last trade date before the snapshot's. The GC futures open interest is
  printed so it can be checked against CME's published figure for that date.
- The share of contracts with live, frozen or no values.
- The total open interest per expiry (calls, puts, total).
- The **top 10 strikes by open interest** (calls and puts summed over the
  selected expiries) and their share of the total.
- The front-month (nearest selected expiry) versus back-month shares of open
  interest and of |GEX|.
- The strike count and strike range per expiry, listed (from contract details)
  versus quoted (a bid or an ask in the snapshot).
- The other trading classes IBKR lists for GC options (weeklies and so on),
  with expiry counts. They are **not** in R.
- **Sensitivities**, which can't change the verdict:
  - R with the next 2 expiries only;
  - R with the front GC month's volume only as D;
  - R with IBKR's model gamma instead of ours.

## Known limits (repeated in the report)

- **One day.** Open interest moves slowly, so one day says how big the
  market is, not how it varies.
- **Listed OG monthlies only.** Weekly gold options, GLD options and OTC
  options are left out.
  - GLD and OTC can't be seen here.
  - The weeklies' expiries are reported, but they are not in R.
- **|GEX| overstates net hedging:** it is unsigned, by design. If even this
  upper bound is under 1 %, the conclusion is robust to any dealer-sign
  assumption. If R is high, the net could still be much smaller.
- **D counts the futures volume only,** not spot or OTC gold, so R overstates
  the share of all gold trading.

## Amendment 1 (2026-10-22, before any data was read)

By the owner's instruction after probe attempt 1. The text above is unchanged.
The header says this file isn't edited once the probe has run. Attempt 1 ran,
but read **no data**: it stopped after reference data (the GC futures list and
the option-chain definitions). It read no quotes, open interest, IV, gamma or
volume. This amendment is committed before the next run.

**Attempt 1** (2026-10-22 10:43 UTC, exit 1): "only 2 OG expiries at least
2 days away: ['20261027', '20261124']".
- The probe had asked for the OG chains of the first 4 GC futures in IBKR's
  list.
- The likely cause: IBKR lists GC months consecutively (Oct, Nov, Dec, Jan,
  ...), while OG options sit only on some of them. GCG7, which carries the
  Dec 28 and Jan 26 expiries, came after the first 4.
- This wasn't confirmed: that probe didn't log which futures it queried.

Changes:
1. **Expiry discovery.** The OG chain (`reqSecDefOptParams`) is asked for each
   GC future in expiry order, until 3 eligible expiries are found, at most 12
   futures.
   - Each expiry is recorded with the underlying future whose chain listed
     it, and with the underlyings its contracts' details name (`underConId`).
     The underlying used for F stays the contract's `underConId`, as above.
   - Every future queried is logged, with the OG expiries its chain returned.
2. **Fallback to 2 expiries.** If fewer than 3 eligible OG expiries exist after
   step 1, the next 2 are used.
   - The JSON and the report record that, and why.
   - With fewer than 2 the probe stops (exit 1).
   - In the fallback case, the "next 2 expiries only" sensitivity equals R.
3. **Nothing else changes:** the metric, the thresholds, the gates, the IV fill
   rule and D are as above.

Attempts are listed in `ATTEMPTS.md`; `analyze.py` copies them into the report.
