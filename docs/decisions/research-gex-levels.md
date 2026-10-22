# research/gex-levels

The draft PREREG for the GEX level test, `research/gex_levels/PREREG.md`,
written after the owner's post-hoc decision in `research/gex_feasibility/`
(2026-10-22). **A draft:** nothing is pre-registered until the owner settles
D1–D6 in it. No data was read. Choices made without the owner, each with its
alternative:

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| GL1 | **Levels only within ±2 SD of the money** (σ_ATM √T, T at least 1 day). In the feasibility snapshot, the raw top open-interest strikes were far out-of-the-money calls (20,000 and 15,000, gold near 4,080) that price can't reach. | A level that can't be touched can't be tested. | Raw top open interest (kept as a sensitivity). |
| GL2 | **Placebos are strikes:** equally round, same side, similar distance, lightly held (below the band's median open interest). | It separates "heavily held" from "round number". Without it, a round-number effect would read as GEX. | Random prices at matched distance (used only for zero-gamma, which isn't a strike). |
| GL3 | **The unit is the expiry cycle,** and the near-expiry window is a pre-declared condition. H2, near against far, is the mechanism check. | Events within a cycle are dependent; the mechanism's own prediction is part of the test. | Pooled events with clustered errors. |
| GL4 | **Outcome:** first passage ±1 σ_60 within 60 minutes from the touch (the scanner's $1 tolerance), with "neither" excluded and counted. | The levels studies' style: σ-scaled, symmetric, no look-ahead. | A fixed-dollar band, or pinning at the close on expiry day (one observation per cycle). |
| GL5 | **Zero-gamma uses one stated sign convention** (dealers long calls and short puts). It is secondary (H3), with no verdict of its own. | The convention is an assumption that public data can't check. | Several conventions (forking paths). |
| GL6 | **Gates restricted to the band**, the feasibility study's G5 lesson. | A gate measures the data the result depends on. | Gates over the whole chain. |
| GL7 | **Sample sizes come from stated assumptions** (§9), with the touch rate q as the main unknown, to be counted on seen data (XAUUSD 2007–2023, round-number levels, no outcomes) before the PREREG is final. **Draft defaults:** W = 10 trading days and Δ = 15 pp, which gives about 16 monthly cycles (12–23 for q between 0.4 and 0.2). | The owner sees how the duration depends on each choice. | Pick N without a power calculation. |
| GL8 | **The seal and data plan B are open decisions,** not settled here. Plan A reads post-2026-10-01 prices, which the seal reserves. Plan B (historical CME OG open interest, 2007–2023) would be seal-compatible and fast, but its availability and cost aren't verified. | Neither is mine to decide. | none |
