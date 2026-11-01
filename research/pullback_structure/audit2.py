#!/usr/bin/env python3
"""The owner's audit, items 7, 10 and 11 (2026-10-28). Dev 2016-2023 only, after
the D18-D19 fixes; descriptive, no test and no new outcome analysis. Counts,
shares and candle indices only, no price.

     7. Q2 breaks whose W window (the W candles after the break) contains a
        data gap: consecutive 1h candles more than 1 h apart (the weekend, a
        holiday, a dropped candle); how many B labels have such a gap, and how
        many of those reached the B target after the gap.
    10. For C events, what "event end" (term_ns, the gallery's dash-dot line)
        marks: the close of the W window, or the Q3 resolution.
    11. The B share for W = 4, 5, 6 (W_ALT, recorded on every break with the
        same target) on gold and on the random walk (the same 200 paths as
        run_rw.py).

    python research/pullback_structure/audit2.py [--near IDX ...]
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import data  # noqa: E402
import params as P  # noqa: E402
import pullback as pb  # noqa: E402
import run_rw  # noqa: E402

OUT = HERE / "results" / "audit2_dev.json"
HOUR_NS = 3600 * 10**9


def window_gaps(s: pb.Series, b: int, w: int) -> list[int]:
    """Candles j in b+1 .. b+w that start more than an hour after the previous one ends."""
    out = []
    for j in range(b + 1, min(b + w, len(s.c) - 1) + 1):
        if s.cstart[j] - s.cend[j - 1] > 0 or s.cend[j] - s.cstart[j] > HOUR_NS:
            out.append(j)
    return out


def b_hit_candle(s: pb.Series, e: pb.Event) -> int | None:
    """The candle where the B target was reached (the first 1-min high >= target)."""
    if e.bc != "B":
        return None
    target = s.c[e.brk] + P.X_SIGMA * e.sigma_brk
    r0, r1 = s.i0[e.brk + 1], s.i1[min(e.brk + P.W, len(s.c) - 1)]
    k = int(np.argmax(s.mh[r0:r1] >= target))
    row = r0 + k
    return int(np.searchsorted(s.i1, row, side="right"))


def main() -> int:
    near = [int(x) for x in sys.argv[sys.argv.index("--near") + 1:]] if "--near" in sys.argv else [4565, 4763, 7620]
    d = common.prepare(verify_cache=False)
    cd, sig = d["cd"], d["sig_end"]
    out: dict = {}
    evs = []
    for bull in (False, True):
        s = pb.series(cd, d["mt"], d["mh"], d["ml"], sig, bull)
        ev, _ = pb.run_events(s, events_from_ns=P.EVENTS_FROM.value, **common.RUN)
        evs += [(e, s) for e in ev if e.kind != "truncated"]
    q2 = [(e, s) for e, s in evs if e.bc in ("B", "C")]

    # 7. gaps inside the W window
    gap = [(e, s, window_gaps(s, e.brk, P.W)) for e, s in q2]
    with_gap = [(e, s, g) for e, s, g in gap if g]
    b_gap = [(e, s, g) for e, s, g in with_gap if e.bc == "B"]
    b_after = [e for e, s, g in b_gap if (b_hit_candle(s, e) or 0) >= g[0]]
    out["7_window_gaps"] = {
        "breaks": len(q2), "with_a_gap_in_window": len(with_gap), "share": len(with_gap) / len(q2),
        "B_with_a_gap": len(b_gap), "B_of_all": sum(e.bc == "B" for e, _ in q2),
        "B_reached_after_the_gap": len(b_after),
        "share_B_with_gap": (sum(e.bc == "B" for e, _, _ in with_gap) / len(with_gap)) if with_gap else None,
        "share_B_without_gap": (sum(e.bc == "B" for e, _, g in gap if not g) / max(1, sum(1 for *_, g in gap if not g))),
        "examples_break_candle": [int(e.brk) for e, _, _ in with_gap[:5]]}

    # 10. what event end marks for C events
    c = [(e, s) for e, s in evs if e.bc == "C"]
    rows, at_w, at_q3 = [], 0, 0
    for e, s in c:
        end_c = int(min(len(s.c) - 1, np.searchsorted(s.cend, e.term_ns, side="left")))
        if e.term_ns == int(s.cend[e.c_fix]):
            at_w += 1
        else:
            at_q3 += 1
        rows.append((e, end_c))
    near_ev = {}
    for x in near:
        e, end_c = min(rows, key=lambda r: min(abs(r[0].brk - x), abs(r[0].i - x), abs(r[1] - x)))
        near_ev[str(x)] = {"dir": "bear" if e.sign == 1 else "bull", "swing": e.i, "break": e.brk,
                           "c_fixed_at_close_of": e.c_fix, "event_end_candle": end_c,
                           "q3": {1: "A (lower low first)", 0: "the high H first", None: "unresolved"}[e.a3]
                           if not e.q3_excluded else f"excluded: {e.q3_excluded}",
                           "event_end_marks": "end of W" if e.term_ns == int(s.cend[e.c_fix]) else "Q3 resolution"}
    out["10_c_event_end"] = {"c_events": len(c), "end_is_close_of_W": at_w, "end_is_Q3_resolution_or_horizon": at_q3,
                             "rule": "a C event ends at its Q3 resolution (the decisive 1-min bar, or the close of "
                                     "c_fix + 30 when unresolved); only a C that left Q3 (a lower low inside W) ends at the close of W",
                             "events_near": near_ev}

    # 11. W sensitivity, gold and the random walk
    def shares(pairs):
        res = {}
        for w in P.W_ALT:
            labs = [e.bc_w.get(w) for e, _ in pairs if e.bc_w.get(w) in ("B", "C")]
            res[str(w)] = {"n": len(labs), "share_B": sum(x == "B" for x in labs) / len(labs) if labs else None}
        return res
    gold = shares(q2)
    smin = data.per_minute_sigma(d["tbl"], d["m"].index)
    close0 = float(d["m"]["close"].iloc[0])
    tot = {str(w): [0, 0] for w in P.W_ALT}
    for r in range(P.RW_REPS):
        o, h, lo, cc = pb.simulate_minutes(close0, smin, np.random.default_rng((P.BOOT_SEED, r)))
        cs = run_rw.sim_candles(cd, o, h, lo, cc)
        ssig = cs["close"].to_numpy() * d["ret_end"]
        for bull in (False, True):
            s = pb.series(cs, d["mt"], h, lo, ssig, bull)
            ev, _ = pb.run_events(s, events_from_ns=P.EVENTS_FROM.value, **common.RUN)
            for e in ev:
                if e.kind == "truncated" or e.bc not in ("B", "C"):
                    continue
                for w in P.W_ALT:
                    lab = e.bc_w.get(w)
                    if lab in ("B", "C"):
                        tot[str(w)][0] += lab == "B"
                        tot[str(w)][1] += 1
    rw = {w: {"n": n, "share_B": b / n if n else None} for w, (b, n) in tot.items()}
    out["11_w_sensitivity"] = {w: {"gold": gold[w], "random_walk": rw[w],
                                   "gap_pp": round(100 * (gold[w]["share_B"] - rw[w]["share_B"]), 2)}
                               for w in gold}
    out["note"] = ("descriptive; the event population is the W = 5 run's (P18 ends each event by its W = 5 path), "
                   "and W = 4 / 6 relabel the same breaks with the same target")
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
