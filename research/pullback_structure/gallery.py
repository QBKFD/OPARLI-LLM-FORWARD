#!/usr/bin/env python3
"""PREREG P37 / §10: the inspection gallery, dev only, for finding bugs. Writes
one HTML page and PNGs to research/pullback_structure/gallery/ (git-ignored:
the charts show real prices). Refuses to run before the dev outcome analysis
(the charts show outcomes) and never reads the holdout.

    python research/pullback_structure/gallery.py
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import params as P  # noqa: E402
import pullback as pb  # noqa: E402

OUT_DIR = HERE / "gallery"


def _cand(e: pb.Event, s: pb.Series) -> int:
    """The candle of the event's end."""
    return int(min(len(s.c) - 1, np.searchsorted(s.cend, e.term_ns, side="left")))


def sample(events: list[pb.Event], series: dict, seed: int, per_label: int, audit_each: int) -> list[tuple[str, pb.Event]]:
    """§10 sampling: per direction, `per_label` random events per label A, B, C (all
    of them if fewer); then `audit_each` events with the largest clock gap between
    the swing candle's start and the confirming candle's close, and `audit_each`
    drawn from those whose B/C label changes when W moves by one (W_ALT)."""
    rng = np.random.default_rng(seed)
    ok = [e for e in events if e.kind != "truncated"]
    out: list[tuple[str, pb.Event]] = []
    for sign, name in ((1, "bear"), (-1, "bull")):
        for lab in ("A", "B", "C"):
            pool = [e for e in ok if e.sign == sign and e.label == lab]
            take = rng.choice(len(pool), size=min(per_label, len(pool)), replace=False) if pool else []
            out += [(f"{name} {lab}", pool[j]) for j in sorted(take)]
    gap = sorted(ok, key=lambda e: -(series[e.sign].cend[e.conf] - series[e.sign].cstart[e.i]))
    out += [("audit: confirmation gap", e) for e in gap[:audit_each]]
    w0 = P.W
    flip = [e for e in ok if e.bc and any(e.bc_w.get(w) not in (None, e.bc_w.get(w0)) for w in e.bc_w if w != w0)]
    take = rng.choice(len(flip), size=min(audit_each, len(flip)), replace=False) if flip else []
    out += [("audit: label changes with W ± 1", flip[j]) for j in sorted(take)]
    return out


def chart(e: pb.Event, s: pb.Series, path: Path, title: str) -> dict:
    """One event: candles (1h under Amendment 2) from GALLERY_BEFORE before the swing low to
    GALLERY_AFTER after the end; the marks of §10. Prices are shown as traded
    (a bull event's negated view is turned back)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sg = s.sign
    end = _cand(e, s)
    a, b = max(0, e.i - P.GALLERY_BEFORE), min(len(s.c), end + P.GALLERY_AFTER + 1)
    x = np.arange(a, b)
    o, c = sg * s.o[a:b], sg * s.c[a:b]
    hi, lo = (sg * s.h[a:b], sg * s.lo[a:b]) if sg == 1 else (sg * s.lo[a:b], sg * s.h[a:b])
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.vlines(x, lo, hi, color="#555", lw=0.8)
    up = c >= o
    ax.bar(x[up], (c - o)[up], bottom=o[up], color="#2a9d8f", width=0.6)
    ax.bar(x[~up], (c - o)[~up], bottom=o[~up], color="#e76f51", width=0.6)
    ax.scatter([e.conf], [sg * e.sl], marker="^" if sg == 1 else "v", s=80, color="k", zorder=5,
               label="swing low (at its confirmation)" if sg == 1 else "swing high (at its confirmation)")
    ax.hlines(sg * e.sl, e.i, b - 1, color="k", ls=":", lw=0.8)
    ax.scatter([e.lh_conf], [sg * e.lh], marker="v" if sg == 1 else "^", s=80, color="#6a4c93", zorder=5,
               label="last lower high (at its confirmation)" if sg == 1 else "last higher low (at its confirmation)")
    ax.hlines(sg * e.lh, e.lh_i, b - 1, color="#6a4c93", ls=":", lw=0.8)
    info = {"label": e.label or "-", "kind": e.kind}
    if e.turn >= 0:
        ax.scatter([e.ph_i], [sg * e.ph], marker="*", s=140, color="#f4a261", zorder=6,
                   label=f"pullback peak: depth {e.depth:.2f} σ")
        ax.axvspan(e.turn - 0.45, e.turn + 0.45, color="#ffd166", alpha=0.4, label="turn candle")
        st = s.sig[e.turn]
        if np.isfinite(st):
            cl = sg * s.c[e.turn]
            ax.fill_between([e.turn, min(b - 1, e.turn + P.HORIZON)], cl - st, cl + st, color="#8ecae6", alpha=0.25,
                            label="turn close ± 1σ (HAR 240)")
        info["P_at_turn"] = round(float(e.p), 3)
    if e.brk >= 0:
        ax.axvline(e.brk, color="#6a4c93", lw=0.8, ls="--", label=f"close beyond LH: {e.bc or '-'}")
    ax.axvline(end, color="#999", lw=0.8, ls="-.", label="event end")
    p_txt = f"P at turn {e.p:.3f}" if e.turn >= 0 else "no turn (broke LH in the pullback)"
    ax.set_title(f"{title} | label {info['label']} ({e.kind}) | {p_txt}", fontsize=10)
    ax.set_xlabel(f"{P.TIMEFRAME} candle index (dev)")
    ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return info


def render(picks: list[tuple[str, pb.Event]], series: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for j, (group, e) in enumerate(picks):
        f = out_dir / f"event_{j:03d}.png"
        info = chart(e, series[e.sign], f, group)
        rows.append(f'<figure><img src="{f.name}" loading="lazy"><figcaption>{html.escape(group)} · '
                    f'{html.escape(json.dumps(info))}</figcaption></figure>')
    page = out_dir / "index.html"
    page.write_text("<!doctype html><meta charset='utf-8'><title>pullback gallery (dev)</title>"
                    "<style>body{font-family:sans-serif;margin:16px}img{max-width:100%}figure{margin:0 0 24px}</style>"
                    f"<h1>pullback_structure: inspection gallery (dev 2016–2023 only)</h1><p>{len(rows)} charts. "
                    "Local only: real prices, git-ignored.</p>" + "".join(rows))
    return page


def main() -> int:
    import common
    att = json.loads((HERE / "ATTEMPTS.json").read_text())
    if att["outcome_analyses"] < 1:
        raise SystemExit("run the dev outcome analysis first (the gallery shows outcomes)")
    d = common.prepare(verify_cache=False)
    _, evs, _ = common.run(d["cd"], d["mt"], d["mh"], d["ml"], d["sig_end"])
    series = {1: pb.series(d["cd"], d["mt"], d["mh"], d["ml"], d["sig_end"], False),
              -1: pb.series(d["cd"], d["mt"], d["mh"], d["ml"], d["sig_end"], True)}
    picks = sample(evs, series, P.GALLERY_SEED, P.GALLERY_PER_LABEL, P.GALLERY_AUDIT_EACH)
    page = render(picks, series, OUT_DIR)
    print(f"{len(picks)} charts: {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
