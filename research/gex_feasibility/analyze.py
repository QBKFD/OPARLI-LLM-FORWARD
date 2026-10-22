"""The probe's JSON -> REPORT.md, by the rules in PREREG.md.

    python research/gex_feasibility/analyze.py research/gex_feasibility/data/<probe>.json \\
        [--report research/gex_feasibility/REPORT.md] [--attempt "<UTC time>: <why it was not used>" ...]

The attempts in ATTEMPTS.md (every probe run, used or not) go into the report,
followed by any --attempt given.

Stdlib only; gex.py holds the formulas. Every number in the report comes from
`analyze(doc)`, which the tests call directly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gex import MULTIPLIER, black76_gamma, dollar_gamma_1pct  # noqa: E402

LOW, HIGH = 0.01, 0.05                  # R thresholds (PREREG "Decision rule")
N_DAYS = 20                             # D: trade dates averaged
G3_MIN_OI_COVERAGE = 0.90
G4_MAX_FILLED_OI_SHARE = 0.25
G5_MAX_MEDIAN_REL_DIFF = 0.05
DEFAULT_EXPIRY_TIME = time(12, 30)      # OG last trade, Chicago, when IBKR gives no time
DEFAULT_TZ = "America/Chicago"
YEAR_S = 365 * 86400


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def val(rec: dict, name: str):
    f = rec.get("fields", {}).get(name)
    return None if f is None else f.get("value")


def underlying_price(u: dict) -> tuple[float | None, str]:
    bid, ask = val(u, "bid"), val(u, "ask")
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2, "mid"
    for name in ("last", "close"):
        v = val(u, name)
        if v and v > 0:
            return v, name
    return None, "none"


def expiry_utc(o: dict) -> tuple[datetime, str]:
    """The option's expiry instant: realExpirationDate at lastTradeTime in timeZoneId."""
    d = datetime.strptime((o.get("real_expiration_date") or o["expiry"])[:8], "%Y%m%d").date()
    t, how = DEFAULT_EXPIRY_TIME, "default 12:30"
    raw = (o.get("last_trade_time") or "").strip()
    if raw:
        part = raw.split(":", 1)[1] if len(raw.split(":")[0]) == 8 else raw     # "20261124:1230" form
        for fmt in ("%H:%M:%S", "%H:%M", "%H%M"):
            try:
                t, how = datetime.strptime(part, fmt).time(), "ibkr"
                break
            except ValueError:
                pass
    try:
        tz = ZoneInfo(o.get("time_zone_id") or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        tz, how = ZoneInfo(DEFAULT_TZ), how + ", default zone"
    return datetime.combine(d, t, tz).astimezone(timezone.utc), how


def fill_iv(options: list[dict]) -> None:
    """PREREG "IV fill rule": OI > 0 and no IV -> the nearest strike's IV in the same
    expiry (either right; on a tie the lower strike)."""
    by_exp = defaultdict(list)
    for o in options:
        if o["iv"] is not None:
            by_exp[o["expiry"]].append((o["strike"], o["iv"]))
    for o in options:
        o["iv_filled"] = False
        if o["iv"] is None and o["oi"]:
            cands = by_exp.get(o["expiry"])
            if cands:
                k, iv = min(cands, key=lambda c: (abs(c[0] - o["strike"]), c[0]))
                o["iv"], o["iv_filled"], o["iv_from_strike"] = iv, True, k


def daily_dollar_volume(bars: list[dict], before: date) -> tuple[list[tuple[str, float]], dict[str, float]]:
    """Per complete trade date (< before): Σ over GC months of volume × 100 × close.
    Also the 20-date volume per month (for the front-month sensitivity)."""
    per_date: dict[str, float] = defaultdict(float)
    for m in bars:
        for b in m["bars"]:
            if b["volume"] is None or b["close"] is None or date.fromisoformat(b["date"][:10]) >= before:
                continue
            per_date[b["date"][:10]] += b["volume"] * MULTIPLIER * b["close"]
    last = sorted(per_date.items())[-N_DAYS:]
    days = {d for d, _ in last}
    per_month = {}
    for m in bars:
        per_month[m["local_symbol"]] = sum(b["volume"] * MULTIPLIER * b["close"] for b in m["bars"]
                                           if b["date"][:10] in days and b["volume"] is not None
                                           and b["close"] is not None)
    return last, per_month


def verdict_of(r: float) -> str:
    if r < LOW:
        return "GEX DROPPED: R < 1 %, no GEX level test"
    if r > HIGH:
        return "BUILD the pre-registered GEX level test: R > 5 %"
    return "IN BETWEEN (1 % ≤ R ≤ 5 %): a GEX level test may be built later, low priority"


def analyze(doc: dict) -> dict:
    snap = parse_utc(doc["snapshot_start_utc"])
    trade_date = date.fromisoformat(doc["trade_date"])
    und = {u["con_id"]: u for u in doc["underlyings"]}
    und_price = {c: underlying_price(u) for c, u in und.items()}

    opts = []
    for o in doc["options"]:
        exp_dt, exp_how = expiry_utc(o)
        f, f_src = und_price.get(o["under_con_id"], (None, "none"))
        opts.append({
            "con_id": o["con_id"], "expiry": o["expiry"], "strike": float(o["strike"]), "right": o["right"],
            "data_type": o["data_type"], "oi": val(o, "open_interest"), "iv": val(o, "model_iv"),
            "ib_iv": val(o, "model_iv"),
            "ib_gamma": val(o, "model_gamma"), "ib_und": val(o, "model_und_price"),
            "quoted": any((val(o, n) or 0) > 0 for n in ("bid", "ask")),
            "f": f, "f_src": f_src, "under": o["under_con_id"],
            "t": (exp_dt - snap).total_seconds() / YEAR_S, "expiry_time_source": exp_how,
        })
    fill_iv(opts)

    expiries = sorted(doc["selected_expiries"])
    for o in opts:
        o["gamma"] = o["dg"] = None
        if o["oi"] and o["iv"] and o["f"] and o["t"] > 0:
            o["gamma"] = black76_gamma(o["f"], o["strike"], o["t"], o["iv"])
            o["dg"] = dollar_gamma_1pct(o["gamma"], o["f"], o["oi"])

    def gex(subset):
        return sum(o["dg"] for o in subset if o["dg"] is not None)

    # gates
    gates = {}
    gates["G1"] = (_inside_hours(snap), f"snapshot {doc['snapshot_start_utc']}, trade date {trade_date}")
    days, per_month = daily_dollar_volume(doc["gc_daily_bars"], trade_date)
    gates["G2"] = (len(days) >= N_DAYS, f"{len(days)} complete GC trade dates")
    n_oi = sum(o["oi"] is not None for o in opts)
    cov = n_oi / len(opts) if opts else 0.0
    gates["G3"] = (cov >= G3_MIN_OI_COVERAGE, f"{n_oi} of {len(opts)} listed contracts have open interest ({cov:.1%})")
    total_oi = sum(o["oi"] or 0 for o in opts)
    filled_oi = sum(o["oi"] or 0 for o in opts if o["iv_filled"])
    share = filled_oi / total_oi if total_oi else 0.0
    gates["G4"] = (share <= G4_MAX_FILLED_OI_SHARE,
                   f"{sum(o['iv_filled'] for o in opts)} contracts with a filled-in IV hold {share:.1%} of open interest")
    rel = [abs(black76_gamma(o["ib_und"], o["strike"], o["t"], o["ib_iv"]) / o["ib_gamma"] - 1)
           for o in opts
           if o["ib_gamma"] and o["ib_gamma"] > 0 and o["ib_und"] and o["ib_und"] > 0 and o["ib_iv"] and o["t"] > 0]
    med = statistics.median(rel) if rel else math.inf
    gates["G5"] = (med <= G5_MAX_MEDIAN_REL_DIFF,
                   f"median |Γ_ours/Γ_IBKR − 1| = {med:.2%} over {len(rel)} contracts" if rel
                   else "no contract has an IBKR model gamma")
    missing_f = [e for e in expiries if not any(o["f"] for o in opts if o["expiry"] == e)]
    gates["G6"] = (not missing_f, "every selected expiry has an underlying price" if not missing_f
                   else f"no underlying price for {missing_f}")
    gates_ok = all(ok for ok, _ in gates.values())

    d_total = sum(v for _, v in days) / len(days) if days else 0.0
    abs_gex = gex(opts)
    r = abs_gex / d_total if d_total else math.inf
    verdict = verdict_of(r) if gates_ok else "INCONCLUSIVE: a gate failed, no decision (rerun per PREREG)"

    # sensitivities (cannot change the verdict)
    front_month = max(per_month, key=per_month.get) if per_month else None
    d_front = per_month[front_month] / len(days) if front_month and days else 0.0
    gex_ib = sum(dollar_gamma_1pct(o["ib_gamma"], o["f"], o["oi"]) if (o["ib_gamma"] and o["oi"] and o["f"])
                 else (o["dg"] or 0.0) for o in opts)
    sens = {
        "R, next 2 expiries only": gex([o for o in opts if o["expiry"] in expiries[:2]]) / d_total if d_total else math.inf,
        f"R, D = front GC month only ({front_month})": abs_gex / d_front if d_front else math.inf,
        "R, IBKR's model gamma instead of ours": gex_ib / d_total if d_total else math.inf,
    }

    # descriptives
    per_exp = []
    for e in expiries:
        sub = [o for o in opts if o["expiry"] == e]
        listed = sorted({o["strike"] for o in sub})
        quoted = sorted({o["strike"] for o in sub if o["quoted"]})
        per_exp.append({
            "expiry": e, "underlying": und.get(sub[0]["under"], {}).get("local_symbol") if sub else None,
            "f": sub[0]["f"] if sub else None, "f_src": sub[0]["f_src"] if sub else None,
            "days": sub[0]["t"] * 365 if sub else None,
            "oi_calls": sum(o["oi"] or 0 for o in sub if o["right"] == "C"),
            "oi_puts": sum(o["oi"] or 0 for o in sub if o["right"] == "P"),
            "gex": gex(sub), "n_contracts": len(sub),
            "listed": (len(listed), listed[0] if listed else None, listed[-1] if listed else None),
            "quoted": (len(quoted), quoted[0] if quoted else None, quoted[-1] if quoted else None),
        })
    by_strike = defaultdict(float)
    for o in opts:
        by_strike[o["strike"]] += o["oi"] or 0
    top10 = sorted(by_strike.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    front = expiries[0] if expiries else None
    kinds = [o["data_type"] for o in opts]
    other_classes = defaultdict(set)
    for ch in doc.get("chains", []):
        if ch["trading_class"] != "OG":
            other_classes[ch["trading_class"]].update(ch["expirations"])
    err_codes = defaultdict(int)
    for e in doc.get("errors", []):
        err_codes[e["code"]] += 1

    return {
        "snapshot": doc["snapshot_start_utc"], "snapshot_end": doc["snapshot_end_utc"],
        "trade_date": doc["trade_date"], "oi_date": doc["oi_settlement_date_inferred"],
        "oi_date_method": doc.get("oi_date_method", ""),
        "expiries": expiries, "n_options": len(opts),
        "data_types": {k: kinds.count(k) for k in ("live", "frozen", "none")},
        "und_types": {u["local_symbol"]: u["data_type"] for u in doc["underlyings"]},
        "gates": gates, "gates_ok": gates_ok,
        "abs_gex": abs_gex, "d": d_total, "days": days, "r": r, "verdict": verdict,
        "sensitivities": sens, "per_expiry": per_exp,
        "total_oi": total_oi, "top10": top10,
        "top10_share": sum(v for _, v in top10) / total_oi if total_oi else 0.0,
        "front": front,
        "front_oi_share": sum(o["oi"] or 0 for o in opts if o["expiry"] == front) / total_oi if total_oi else 0.0,
        "front_gex_share": gex([o for o in opts if o["expiry"] == front]) / abs_gex if abs_gex else 0.0,
        "futures_oi": {u["local_symbol"]: val(u, "open_interest") for u in doc["underlyings"]},
        "other_classes": {k: len(v) for k, v in sorted(other_classes.items())},
        "errors": dict(sorted(err_codes.items())),
        "expiry_time_sources": dict(_count(o["expiry_time_source"] for o in opts)),
        "max_concurrent_lines": doc.get("max_concurrent_lines"),
        "n_expiries_used": doc.get("n_expiries_used", len(expiries)),
        "n_expiries_target": doc.get("n_expiries_target", 3),
        "expiry_fallback": doc.get("expiry_fallback"),
        "chain_futures_queried": doc.get("chain_futures_queried", []),
        "expiry_sources": [(x["expiry"], [_sym(doc, c) for c in x["chain_underlying_con_ids"]],
                            [_sym(doc, c) for c in x["details_under_con_ids"]])
                           for x in doc.get("expiry_sources", [])],
    }


def _sym(doc: dict, con_id: int) -> str:
    for f in doc.get("futures", []):
        if f["con_id"] == con_id:
            return f["local_symbol"]
    return str(con_id)


def _count(items):
    out = defaultdict(int)
    for x in items:
        out[x] += 1
    return out


def probe_module():
    """tools/options_probe.py, loaded by path: one market-hours and holiday rule for both."""
    path = Path(__file__).resolve().parents[2] / "tools" / "options_probe.py"
    spec = importlib.util.spec_from_file_location("options_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inside_hours(t: datetime) -> bool:
    return probe_module().closed_reason(t) is None


def money(x: float) -> str:
    if not math.isfinite(x):
        return "n/a"
    for unit, div in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:,.2f}{unit}"
    return f"${x:,.0f}"


def pct(x: float) -> str:
    return "n/a" if not math.isfinite(x) else f"{x:.2%}"


def report(a: dict, *, source: str, sha256: str, attempts: list[str]) -> str:
    L = ["# GEX feasibility: report", ""]
    L += ["## Verdict", "", f"**{a['verdict']}**", ""]
    L += [f"- **R = {pct(a['r'])}** = |GEX| {money(a['abs_gex'])} per 1 % move "
          f"/ D {money(a['d'])} average daily GC futures dollar volume ({len(a['days'])} trade dates).",
          "- Rule (PREREG): R < 1 % drop; R > 5 % build the test; in between, later at low priority.",
          f"- Gates: {'all passed' if a['gates_ok'] else 'FAILED, see below'}.", ""]
    L += ["## Snapshot", "",
          f"- Snapshot: {a['snapshot']} to {a['snapshot_end']} (UTC); CME trade date {a['trade_date']}.",
          f"- Open-interest settlement date: **{a['oi_date']}** (inferred: {a['oi_date_method']}).",
          f"- Expiries: {a['n_expiries_used']} used, {a['n_expiries_target']} targeted"
          + (f"; **fallback**: {a['expiry_fallback']}" if a["expiry_fallback"] else "") + ".",
          "- Expiry → underlying (from its chain / from its contracts' details): "
          + ("; ".join(f"{e} {'/'.join(ch) or '?'} / {'/'.join(dt) or '?'}" for e, ch, dt in a["expiry_sources"])
             or "not recorded") + ".",
          "- GC futures asked for their option chains: " + (", ".join(a["chain_futures_queried"]) or "not recorded")
          + ".",
          f"- Option contracts: {a['n_options']} in expiries {', '.join(a['expiries'])}; values "
          f"live {a['data_types']['live']} ({_share(a['data_types']['live'], a['n_options'])}), "
          f"frozen {a['data_types']['frozen']} ({_share(a['data_types']['frozen'], a['n_options'])}), "
          f"none {a['data_types']['none']} ({_share(a['data_types']['none'], a['n_options'])}).",
          "- Underlyings: " + ", ".join(f"{k} {v}" for k, v in a["und_types"].items()) + ".",
          f"- GC futures open interest (check against CME's figure for {a['oi_date']}): "
          + ", ".join(f"{k} {_int(v)}" for k, v in a["futures_oi"].items()) + ".",
          f"- Probe file: `{source}`, SHA-256 `{sha256}` (not committed: exchange data, public repo).",
          "- Expiry times: " + ", ".join(f"{k}: {v}" for k, v in a["expiry_time_sources"].items())
          + f". Most market data lines at once: {a['max_concurrent_lines']}.",
          f"- IBKR messages by code: {a['errors'] or 'none'}.", ""]
    L += ["## Attempts", ""]
    L += [f"- {x}" for x in attempts] if attempts else ["- This was the first attempt."]
    L += [""]
    L += ["## Gates", "", "| Gate | Result | Detail |", "|---|---|---|"]
    L += [f"| {g} | {'pass' if ok else '**FAIL**'} | {detail} |" for g, (ok, detail) in a["gates"].items()]
    L += [""]
    L += ["## Per expiry", "",
          "| Expiry | Underlying | F (source) | Days | OI calls | OI puts | OI total | \\|GEX\\| per 1 % | "
          "Strikes listed (range) | Strikes quoted (range) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for e in a["per_expiry"]:
        L.append(f"| {e['expiry']} | {e['underlying']} | {_num(e['f'])} ({e['f_src']}) | {_num(e['days'], 1)} | "
                 f"{_int(e['oi_calls'])} | {_int(e['oi_puts'])} | {_int(e['oi_calls'] + e['oi_puts'])} | "
                 f"{money(e['gex'])} | {_rng(e['listed'])} | {_rng(e['quoted'])} |")
    L += ["", f"- Front month ({a['front']}): {pct(a['front_oi_share'])} of open interest, "
              f"{pct(a['front_gex_share'])} of |GEX|; back months the rest.", ""]
    L += ["## Top 10 strikes by open interest (calls + puts, all selected expiries)", "",
          "| Strike | Open interest | Share |", "|---|---|---|"]
    L += [f"| {_num(k)} | {_int(v)} | {_share(v, a['total_oi'])} |" for k, v in a["top10"]]
    L += ["", f"- Together: {pct(a['top10_share'])} of {_int(a['total_oi'])} contracts of open interest.", ""]
    L += ["## Sensitivities (cannot change the verdict)", ""]
    L += [f"- {k}: {pct(v)}" for k, v in a["sensitivities"].items()]
    L += ["- Other GC option trading classes listed (not in R), with their expiry counts: "
          + (", ".join(f"{k} {v}" for k, v in a["other_classes"].items()) or "none") + ".", ""]
    L += ["## D, by trade date", "", "| Trade date | GC dollar volume |", "|---|---|"]
    L += [f"| {d} | {money(v)} |" for d, v in a["days"]]
    L += ["", "## Known limits", "",
          "- One day; listed OG monthlies only (no weeklies, GLD or OTC options).",
          "- |GEX| is unsigned, an upper bound on net dealer hedging; D is futures volume only.",
          "- Post-seal data, read for magnitudes only (PREREG, \"Seal\"): no prices around strikes, "
          "no returns, no XAUUSD bars.", ""]
    return "\n".join(L)


def _share(x, total) -> str:
    return f"{x / total:.1%}" if total else "n/a"


def _int(x) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def _num(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def _rng(t) -> str:
    n, lo, hi = t
    return f"{n} ({_num(lo)}–{_num(hi)})" if n else "0"


def read_attempts(path: Path) -> list[str]:
    """The "- " lines of ATTEMPTS.md, without the dash."""
    if not path.is_file():
        return []
    return [line[2:].strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="probe JSON -> REPORT.md (research/gex_feasibility)")
    ap.add_argument("probe_json")
    ap.add_argument("--report", default=str(Path(__file__).resolve().parent / "REPORT.md"))
    ap.add_argument("--attempt", action="append", default=[],
                    help="an attempt not in ATTEMPTS.md, and why it was not used; repeat per attempt")
    ap.add_argument("--attempts-file", default=str(Path(__file__).resolve().parent / "ATTEMPTS.md"))
    args = ap.parse_args(argv)
    attempts = read_attempts(Path(args.attempts_file)) + args.attempt
    raw = Path(args.probe_json).read_bytes()
    doc = json.loads(raw)
    if doc.get("schema") != "options_probe/1":
        ap.error(f"not an options_probe/1 file: schema {doc.get('schema')!r}")
    a = analyze(doc)
    Path(args.report).write_text(report(a, source=Path(args.probe_json).name,
                                        sha256=hashlib.sha256(raw).hexdigest(), attempts=attempts),
                                 encoding="utf-8")
    print(f"R = {pct(a['r'])}: {a['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
