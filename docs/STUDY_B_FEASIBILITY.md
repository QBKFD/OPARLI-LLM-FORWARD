# Study B: feasibility (desk research only)

**What this is:** desk research for the 2026-11-01 brief, step 3. Nothing was downloaded,
bought or connected: no IBKR, no vendor account, no data file.

**Labels:**
- **SOURCED** cites a page that was read on 2026-11-01 (links at the end).
- **KNOWN** is general knowledge, not re-checked tonight.
- **OPEN** has to be checked before any plan relies on it.

**The question for Study B:** does the *size* of a release surprise matter for gold, and not
only the release itself (Study A, #64)? That needs two surprise measures:
- **FOMC:** the change in short-rate futures in a tight window around the announcement;
- **data releases:** actual − consensus. #60 already has actual − Forex Factory forecast.

## 1. Summary

| Item | Finding | Verdict |
|---|---|---|
| FOMC surprise, **ready-made** | The San Francisco Fed publishes the Bauer–Swanson surprise series and the newer US Monetary Policy Event-Study Database (USMPD): 30-minute windows around statements, 70-minute windows around press conferences, and minutes. The source is LSEG Tick History. Free download; the USMPD page says "updated 9/17/2026" (SOURCED) | **Feasible, cheapest.** OPEN: exact coverage of 2016–2023 and the terms of use; check both on download |
| FOMC surprise, **own computation** from 1-min ZQ / SR3 | **SR3 doesn't exist before 2018-05-07** (SOURCED). For 2016–2018 the standard instrument is Eurodollar (GE), which ended in 2023 (SOURCED). ZQ (30-day fed funds, CBOT) covers the whole period (KNOWN) | Feasible only through a paid historical vendor (§3) |
| **IBKR** as the source | IBKR serves historical data for expired futures **only up to 2 years after expiry**, and only through the TWS API (SOURCED). Every 2016–2023 contract expired more than 2 years ago | **Not feasible for 2016–2023.** Usable live, forward, for the sealed period, with a CME/CBOT data subscription (cost OPEN) |
| 1-min history from 2016, **vendors** | Databento GLBX.MDP3 (CME Globex): "since 2010", pay-as-you-go from $0.50/GB, with OHLCV-1m among the schemas (SOURCED). CME DataMine: time & sales back to 1982 or contract inception, priced by quote (SOURCED) | Feasible. Cost OPEN: probably small for OHLCV-1m on a few contract roots, but not quoted |
| **Forex Factory forecast** vs Bloomberg / Reuters consensus | No primary source explains how Forex Factory builds its forecast. Secondary sources disagree: "standard data providers" against "economists polled by Reuters or Bloomberg" (SOURCED, low quality). Bloomberg (ECO survey median) and LSEG/Reuters polls are the standard consensus; both are licensed products (KNOWN) | **Unknown provenance.** A pilot comparison (§4) is needed before Forex Factory surprises carry a study |

**Recommendation (conservative):**
- **FOMC surprises from the FRBSF series**, if the download covers 2016–2023. Our own
  ZQ/SR3 computation is only a cross-check, and only if the owner buys vendor data.
- **Treat Forex Factory surprises as unvalidated** until a pilot compares them with an
  independent consensus for CPI and NFP.

## 2. FOMC surprise from short-rate futures

**Method (KNOWN):**
- **Kuttner (2001):** the target surprise comes from the change in the current-month fed funds
  future (ZQ) around the announcement, scaled by D/(D − d) for the days left in the month.
- **Gürkaynak, Sack & Swanson (2005):** the "target" and "path" factors come from several
  money-market futures in a 30-minute window (−10 to +20 minutes).
- **Bauer & Swanson (2023):** they use the first principal component of the four quarterly
  money-market futures, and also publish a version orthogonalized to public information
  available before the meeting (SOURCED: the FRBSF page describes both versions).

**Instruments:**
- **ZQ, 30-Day Federal Funds futures (CBOT):** covers 2016–2023 (KNOWN). The front contracts
  carry the target surprise.
- **GE, Eurodollar (CME):** the quarterly path contracts for 2016–2018 and the standard input
  until 2022. **It ended:** open interest converted to SOFR after 2023-04-14, and Apr/May/Jun 2023
  expired normally (SOURCED).
- **SR3, Three-Month SOFR (CME):** **launched 2018-05-07** (SOURCED). Liquidity in its first
  years was much thinner than Eurodollar (KNOWN, not quantified). A series over 2016–2023 has to
  switch from GE to SR3, and that switch date is an OPEN design choice.

**What our own computation needs:**
- 1-min (better: tick) prices for the front ZQ months and the first four GE/SR3 quarterlies,
  around about 64 meetings in 2016–2023 plus their press conferences;
- the exact announcement times. #60's calendar has them, anchor-checked;
- a roll rule;
- care with the 2020-03-03 and 2020-03-15 unscheduled actions. The latter came on a Sunday,
  with futures closed.

## 3. Data sources and cost

| Source | History | Granularity | Cost | Licence / notes |
|---|---|---|---|---|
| FRBSF Bauer–Swanson surprises | OPEN: coverage not stated on the page; the chart covers "the past 12 years" | per FOMC event | free | OPEN: the page links only to general site policies |
| FRBSF USMPD | OPEN: start not stated; updated 2026-09-17 | per event: statement, press conference, minutes; many instruments | free | built from LSEG Tick History; code for the surprises included (SOURCED) |
| Databento GLBX.MDP3 | "since 2010" (SOURCED; check per symbol in the metadata) | tick → OHLCV-1s/1m/1h/1d | historical from $0.50/GB, pay as you go (SOURCED); OHLCV-1m for a few roots is likely small. OPEN: a quote from their cost estimator | licence terms for redistribution OPEN; our rule stays: no raw rows in git |
| CME DataMine | time & sales from 1982 or contract inception (SOURCED) | tick / top of book | by quote; "all sales are final" (SOURCED) | direct from the exchange |
| IBKR TWS API | expired futures ≤ 2 years after expiry (SOURCED) | 1-min bars | needs a CME/CBOT market data subscription (price OPEN) | only for forward or live collection; **no 2016–2023 history** |

## 4. Forex Factory forecast against Bloomberg / Reuters consensus

- **What we have (#60):**
  - actual − forecast for CPI, NFP, PCE and claims, standardised by a trailing SD (N
    proposed 24, the owner to decide);
  - the forecast column is as scraped: one value per release, with no time stamp of when it
    was set.
- **Not known:**
  - who makes the Forex Factory forecast (no primary source found; the help page refused an
    automated fetch);
  - whether it equals the Bloomberg or Reuters median, and whether it was revised before the
    release.
- **Why it matters:** the surprise literature uses the Bloomberg ECO median or the
  Reuters/MMS survey. A forecast that is stale, or isn't a median, adds noise. Noise attenuates
  a surprise effect, and it could bias it if the deviation correlates with the outcome.
- **Proposed pilot (needs owner approval, no data bought yet):**
  - take a sample of CPI m/m and NFP releases, e.g. 2019–2023, about 120 releases;
  - compare Forex Factory's forecast with an independent consensus;
  - report the share equal to within the rounding unit, the mean |difference|, and the
    correlation of the two surprise series.
  - Free comparison sources are thin. Options, all OPEN on access and terms:
    - archived pre-release press coverage (e.g. Reuters or Bloomberg articles that quote the
      poll median);
    - a library Bloomberg terminal;
    - a paid calendar API with history.
- **Decision rule for the pilot (proposal):** use Forex Factory surprises in Study B only if
  the two surprise series correlate ≥ 0.9 and ≥ 80 % of the forecasts match to the published
  precision. Otherwise Study B uses FOMC surprises only.

## 5. What Study B could look like (not a plan; for the owner)

1. **FOMC first:**
   - gold's 1-min returns and realized variance in the H = 60/240 windows around FOMC;
   - regressed on the FRBSF surprise (signed and |·|);
   - dev 2016–2023, XAUUSD.
   - It replicates on XAGUSD and EURUSD later, like Study A.
2. **Data releases:** only after the §4 pilot.
3. **Study A's caveat (N1) carries over:** the windows must contain the reaction bar.

## 6. OPEN items, in order

1. Download the FRBSF files. Check their coverage of 2016–2023, the column definitions and the
   terms of use. They are free; it's a data step, so it needs the owner's go-ahead.
2. If our own computation is wanted, get a Databento cost estimate for ZQ, GE and SR3 OHLCV-1m
   (2016–2023) and a DataMine quote.
3. Approve, or not, the §4 pilot and its comparison source.
4. The IBKR CME/CBOT subscription price, if forward collection of ZQ and SR3 is wanted.

## Sources (read 2026-11-01)

- CME, SOFR futures launch date: [press release 2018-03-01](https://www.cmegroup.com/media-room/press-releases/2018/3/01/cme_group_announcesnewsofrfutureslaunchdateandcontractspecificat.html), [clearing advisory 18-088](https://www.cmegroup.com/notices/clearing/2018/03/Chadv18-088.pdf), [contract specifications](https://www.cmegroup.com/content/dam/cmegroup/education/files/sofr-futures-contract-specifications.pdf)
- Eurodollar conversion: [CME press release 2022-09-06](https://www.cmegroup.com/media-room/press-releases/2022/9/06/cme_group_proposesapril142023forfallbacksconversionofeurodollarf.html), [CME press release 2023-04-24](https://cmegroup.com/media-room/press-releases/2023/4/24/cme_group_completeskeymilestonesinconversionofeurodollarfutureso.html), [Clarus: "The Eurodollar is no more"](https://www.clarusft.com/the-eurodollar-is-no-more/)
- IBKR expired futures: [market data limitations](https://www.interactivebrokers.com/docs/tws-api/doc/md/market-data-limitations), [expired futures](https://www.interactivebrokers.com/docs/general/contracts/futures/expired-futures)
- Databento: [GLBX.MDP3 dataset](https://databento.com/docs/venues-and-datasets/glbx-mdp3), [pricing](https://databento.com/pricing), [SR3 catalog](https://databento.com/catalog/cme/GLBX.MDP3/futures/SR3)
- CME DataMine: [DataMine](https://cmegroup.com/datamine.html), [pre-purchase information](https://cmegroupclientsite.atlassian.net/wiki/x/0Iq-Ag)
- FRBSF: [Monetary Policy Surprises](https://www.frbsf.org/research-and-insights/data-and-indicators/monetary-policy-surprises/), [U.S. Monetary Policy Event-Study Database](https://www.frbsf.org/research-and-insights/data-and-indicators/us-monetary-policy-event-study-database/), [working paper 2025-30](https://www.frbsf.org/wp-content/uploads/wp2025-30.pdf)
- Forex Factory forecast (secondary, low quality): [Forex Peace Army thread](https://www.forexpeacearmy.com/community/threads/how-is-made-an-economic-calendar.44342/post-234342), [Trading Writers](https://tradingwriters.com/news-trading-using-the-economic-calendar-with-forex-factory-example/), [work-club calendar comparison](https://work-club.com/best-economic-calendars-traders-2026/)
