#!/usr/bin/env python3
"""The owner's exhaustive audit of the dev events (2026-10-27), in code. Dev
2016-2023 only; the holdout is never read. Six checks, each with the count, the
share and up to 5 example candle indices (the gallery's x axis). No price is
written: only indices, counts, shares and sigma ratios.

    python research/pullback_structure/audit.py [--near IDX ...]
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import params as P  # noqa: E402
import pullback as pb  # noqa: E402

OUT = HERE / "results" / "audit_dev.json"
NEAR_TIE = 0.05            # the owner's threshold, in sigma


def ex(xs, n=5):
    return [int(x) for x in xs[:n]]


def main() -> int:
    near = [int(x) for x in sys.argv[sys.argv.index("--near") + 1:]] if "--near" in sys.argv else [41324, 15381]
    d = common.prepare(verify_cache=False)
    cd, sig = d["cd"], d["sig_end"]
    out, evs_all = {}, []
    for bull in (False, True):
        s = pb.series(cd, d["mt"], d["mh"], d["ml"], sig, bull)
        ev, _ = pb.run_events(s, events_from_ns=P.EVENTS_FROM.value, **{k: v for k, v in common.RUN.items()})
        sl_idx, sh_idx = pb.swings(s.lo, P.K, True), pb.swings(s.h, P.K, False)
        evs_all += [(e, s, sl_idx, sh_idx) for e in ev if e.kind != "truncated"]
    n = len(evs_all)
    q2 = [(e, s) for e, s, *_ in evs_all if e.bc in ("B", "C")]
    q1 = [(e, s) for e, s, *_ in evs_all if e.turn >= 0]

    # 1. a close beyond LH before the swing low is confirmed (the break candle <= conf)
    c1 = [e for e, _ in q2 if e.brk <= e.conf]
    out["1_q2_break_before_confirmation"] = {"count": len(c1), "of": len(q2), "share": len(c1) / len(q2),
                                             "examples_break_candle": ex([e.brk for e in c1]),
                                             "by_label": {lab: sum(e.bc == lab for e in c1) for lab in ("B", "C")}}
    # 2. structure violations, recomputed from the swings independently of run_events
    bad, outside = [], []
    for e, s, sl_idx, sh_idx in evs_all:
        lows = sl_idx[sl_idx <= e.i][-P.N_SWINGS:]
        highs = sh_idx[sh_idx + P.K <= e.conf][-P.N_SWINGS:]         # every high confirmed by the event's start (P6)
        before = sh_idx[sh_idx < e.i]                                 # LH: the newest before the swing low (P7)
        if not (len(lows) == P.N_SWINGS and lows[-1] == e.i and (np.diff(s.lo[lows]) < 0).all()
                and len(highs) == P.N_SWINGS and (np.diff(s.h[highs]) < 0).all() and before[-1] == e.lh_i):
            bad.append(e)
        if e.i in sh_idx:
            outside.append(e)
    out["2_structure_violations"] = {"count": len(bad), "of": n, "share": len(bad) / n, "examples_swing_candle": ex([e.i for e in bad]),
                                     "info_outside_candles_kept": {"count": len(outside), "examples_swing_candle": ex([e.i for e in outside])}}
    # 3. near-ties: the last two swing highs or lows within 0.05 sigma (definition, not a bug)
    ties_h, ties_l = [], []
    for e, s, sl_idx, sh_idx in evs_all:
        lows = sl_idx[sl_idx <= e.i][-2:]
        highs = sh_idx[sh_idx < e.i][-2:]
        if abs(s.lo[lows[0]] - s.lo[lows[1]]) < NEAR_TIE * e.sigma_conf:
            ties_l.append(e)
        if abs(s.h[highs[0]] - s.h[highs[1]]) < NEAR_TIE * e.sigma_conf:
            ties_h.append(e)
    either = {id(e) for e in ties_h} | {id(e) for e in ties_l}
    out["3_near_ties_below_0.05_sigma"] = {"count_either": len(either), "of": n, "share": len(either) / n,
                                           "swing_highs": len(ties_h), "swing_lows": len(ties_l),
                                           "examples_swing_candle": ex([e.i for e in ties_h + ties_l])}
    # 4. Q1 events with a close beyond LH during the confirmation candles
    c4 = [e for e, s in q1 if (s.c[e.i + 1:e.conf + 1] > e.lh).any()]
    out["4_q1_with_close_beyond_lh_during_confirmation"] = {"count": len(c4), "of": len(q1), "share": len(c4) / len(q1),
                                                           "examples_swing_candle": ex([e.i for e in c4])}
    # 5. same-candle A: the candle that decides A also closes beyond the pullback high (definition)
    c5, a_n = [], 0
    for e, s in q1:
        if e.a != 1:
            continue
        a_n += 1
        row = int(np.searchsorted(s.mt, e.term_ns))
        cand = int(np.searchsorted(s.i1, row, side="right"))
        if s.c[cand] > e.ph:
            c5.append((e, cand))
    out["5_same_candle_A_close_beyond_pullback_high"] = {"count": len(c5), "of": a_n, "share": len(c5) / max(a_n, 1),
                                                        "examples_candle": ex([c for _, c in c5])}
    # 6. which sigma: P9 = sigma_240 as of the confirmation candle's close; the gallery band uses the turn's
    rows = []
    for e, s in q1:
        rows.append((e, abs(sig[e.turn] / e.sigma_conf - 1) if np.isfinite(sig[e.turn]) else np.nan))
    ratio = np.array([r for _, r in rows], float)
    near_ev = {}
    for x in near:
        cands = sorted(evs_all, key=lambda t: min(abs(t[0].i - x), abs(max(t[0].turn, t[0].brk) - x)))[:1]
        for e, s, *_ in cands:
            near_ev[str(x)] = {"dir": "bear" if e.sign == 1 else "bull", "swing_candle": e.i, "conf_candle": e.conf,
                               "turn_candle": e.turn, "break_candle": e.brk, "label": e.label, "kind": e.kind,
                               "sigma_at_conf_close_px": round(e.sigma_conf, 3),
                               "sigma_at_turn_close_px": round(float(sig[e.turn]), 3) if e.turn >= 0 else None,
                               "depth_sigma_conf (P9, used)": round(e.depth, 3) if e.turn >= 0 else None,
                               "depth_if_sigma_turn": round((e.ph - e.sl) / sig[e.turn], 3) if e.turn >= 0 else None,
                               "lh_break_during_confirmation": bool((s.c[e.i + 1:e.conf + 1] > e.lh).any())}
    out["6_depth_sigma"] = {"used": "sigma_px_240 at the label equal to the confirmation candle's close (P9, D10); "
                                    "the gallery's band uses the turn candle's close",
                            "median_abs_rel_diff_conf_vs_turn": float(np.nanmedian(ratio)),
                            "p90_abs_rel_diff": float(np.nanquantile(ratio, 0.9)), "events_near": near_ev}
    out["events_audited"] = n
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
