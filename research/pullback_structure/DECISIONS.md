# pullback_structure: implementation decisions

Choices made while coding PREREG.md (merged in 42675bb) where the PREREG
doesn't spell out the mechanics. None changes an approved value. Each is
tested in `test_pullback.py` where marked.

| id | decision | why |
|---|---|---|
| D1 | After the turn, Q1's first passage (SL vs pullback_high, P12) and the search for a close above LH (Q2) run independently over the 30-candle horizon. A close above LH counts for Q2 only if no 1-min low < SL came before its close. The gallery label is the first in time: B or C if such a break came, else A if Q1 is A. | §3.8: "B and C: on every close above LH before a new lower low". When pullback_high > LH, a break can come before Q1 is decided; stopping at Q1 would lose it. |
| D2 | When the turn led to no lower low and no break within the horizon, the event ends at the close of candle turn + 30 ("no_break" or "unresolved"). | P17's horizon, applied to the whole event. |
| D3 | A close above LH counts from the candle after the swing low, including the k confirmation candles; a lower low and the turn count from the candle after the confirmation (tested: `test_break_during_confirmation_counts`). | §3.4 says "before the turn"; no lower low can come during confirmation (P5). |
| D4 | P18: an event ends at its decisive 1-min bar or at the close of its last tracked candle. A new event in that direction needs a confirmation close strictly after that. | One live event per direction. |
| D5 | P24: an event not finished inside the data is dropped from every question (kind `truncated`), even when one of its questions was already decided. | "an event … is dropped". |
| D6 | B vs C: a 1-min bar reaching the B target and the same or an earlier bar going below SL makes C; a lower low inside the W window makes C and the event leaves Q3 (`ll_during_w`, counted). | The tie rule of P13, applied to B. |
| D7 | Bootstrap p-values: one-sided p = (#{resamples ≤ 0} + 1) / (B + 1); Q2's two-sided p = 2 × min of the two tail shares, each +1-corrected, capped at 1. Each test seeds its own generator from BOOT_SEED (20261027) plus a fixed offset (Q1 bucket b: +b, Q2: +10, Q3: +20). | P27, P28; reproducible per test. |
| D8 | A candle's scheduled minutes are its real length in New York time, so a candle across a DST change has 180 or 300 (tested: `test_candles_ny_anchor_dst_and_drops`). | P3, "scheduled open minutes". |
| D9 | The swing highs used for the structure and for LH are those that occurred before the swing-low candle. | P6, P7: confirmed by the swing low's confirmation, so they occurred at or before it. |
| D10 | σ (P9) is `stage1.state_table`'s `sigma_px_240` at the label equal to the candle's end (the state as of that close). A start without a finite σ is refused and counted (`no_sigma`). | squeeze_oco's test checks that table against `vol_block.state_at` + `sigma_over`. |
| D11 | Step 2 checks P30 on **turns** per bucket, not on resolved events. Resolution is an outcome, and step 2 reports none. | §6.2 counts before any outcome; turns ≥ resolved, so a bucket short on turns is short on resolved events too. |
