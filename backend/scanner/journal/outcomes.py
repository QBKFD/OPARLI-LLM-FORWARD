"""Counterfactuals (forward MFE/MAE at 1h and 4h for every alert) and the
realised outcomes of TAKEs (scored with backend.scoring, imported lazily)."""
from __future__ import annotations

import pandas as pd

from backend.scanner.journal.writes import insert_counterfactual
from backend.scanner.market import _utc

# ── Calibration (2023-2025, do not retune) ─────────────────────────────────
CF_HORIZONS = {"1h": 60, "4h": 240}   # counterfactual MFE/MAE horizons (minutes)
OUTCOME_MAX_HOLD_MIN = 240       # a TAKE is scored over at most this many minutes (PREREG H = 240)
OUTCOME_SETTLE_MIN = 5           # a timeout is final this long after the holding period ends


# ── Counterfactual: forward MFE/MAE at 1h/4h (direction-agnostic) ──────────
def counterfactual(df: pd.DataFrame, ts_str: str, price: float) -> dict:
    ts = pd.Timestamp(ts_str, tz="UTC")
    out = {"alert_id": None}
    for name, mins in CF_HORIZONS.items():
        w = df.loc[ts: ts + pd.Timedelta(minutes=mins)]
        hi, lo = (w["high"].max(), w["low"].min()) if len(w) else (price, price)
        out[f"fwd_high_{name}"] = round(hi, 3)
        out[f"fwd_low_{name}"] = round(lo, 3)
        out[f"mfe_up_{name}"] = round(hi - price, 2)     # what a long would have gained
        out[f"mfe_dn_{name}"] = round(price - lo, 2)     # what a short would have gained
    return out


def alerts_needing_counterfactual(con, now_utc):
    """Alerts old enough that a full 4h forward window exists but with no
    counterfactual row yet — the SKIP/MISSED 'what would have happened' data
    can't be computed at alert time, only once the future bars arrive.
    Rows are (alert_id, alert_ts, price_at_alert, epoch)."""
    now = _utc(now_utc)
    rows = con.execute(
        "SELECT a.alert_id, a.alert_ts_utc, a.price_at_alert, a.epoch FROM alerts a "
        "LEFT JOIN counterfactuals cf ON a.alert_id = cf.alert_id AND a.epoch = cf.epoch "
        "WHERE cf.alert_id IS NULL"
    ).fetchall()
    horizon = max(CF_HORIZONS.values())
    return [r for r in rows if (now - _utc(r[1])).total_seconds() >= horizon * 60]


def sweep_counterfactuals(con, df, now_utc) -> int:
    """Fill forward MFE/MAE for alerts whose 4h window has elapsed, from the
    accumulated live bar frame. Runs live so the experiment data is captured."""
    n = 0
    for alert_id, alert_ts, price, epoch in alerts_needing_counterfactual(con, now_utc):
        cf = counterfactual(df, alert_ts, price)
        insert_counterfactual(con, alert_id, cf, epoch=epoch)
        n += 1
    if n:
        con.commit()
    return n


# ── Realised outcomes of TAKEs ──────────────────────────────────────────────
# Each TAKE is walked forward on the 1-min bars with the reply's own stop and
# target, by scoring.score (stop before target in one bar, gaps through the
# stop fill at the open, timeout at the last close). Only the FIRST decision
# per alert and epoch counts, as for the TAKE-vs-SKIP comparison; a later
# reply is logged but not scored. scoring is imported lazily, so replay and
# selftest don't need it.
OUTCOME_EXIT_TYPES = {"target": "tp", "stop": "sl", "timeout": "timeout"}


def take_problem(direction, entry, stop, target) -> str | None:
    """Why a TAKE can't be scored, or None. entry may be missing (then the
    first bar's open after the reply is used, so only stop vs target is
    checked here); stop and target may not."""
    if direction not in ("long", "short"):
        return "no direction (long/short)"
    if stop is None:
        return "no stop (sl)"
    if target is None:
        return "no target (tp)"
    for name, x in (("entry", entry), ("stop", stop), ("target", target)):
        if x is not None and not (x == x and x > 0):
            return f"{name} must be > 0"
    lo, hi = (stop, target) if direction == "long" else (target, stop)
    if entry is None:
        return None if lo < hi else f"a {direction} needs {'stop < target' if direction == 'long' else 'target < stop'}"
    if not lo < entry < hi:
        want = "stop < entry < target" if direction == "long" else "target < entry < stop"
        return f"a {direction} needs {want}"
    return None


def score_take(direction, entry, stop, target, decision_ts, bars: pd.DataFrame, now,
               max_hold_min: int = OUTCOME_MAX_HOLD_MIN) -> dict | None:
    """The outcome row for one TAKE, or None while it isn't final yet.

    Entry: the reply's entry price at the reply's time (entry_source 'reply'),
    or, without one, the open of the first bar at or after the reply
    ('market_open'). entry_ref_close is the close of the last bar before the
    reply, so a stated entry far from the market is visible. A stop or target
    hit is final at once; a timeout only OUTCOME_SETTLE_MIN after the holding
    period. A TAKE that can't be scored is final with exit_type 'unscorable'
    and the reason."""
    from backend.scoring.outcome import NoBarsError, bars_from_frame, score

    t0, now = _utc(decision_ts), _utc(now)
    end = t0 + pd.Timedelta(minutes=max_hold_min)
    final = now >= end + pd.Timedelta(minutes=OUTCOME_SETTLE_MIN)
    before = bars[bars.index < t0]
    row = dict(entry_ts_utc=None, entry_price=entry, entry_source="reply",
               entry_ref_close=float(before["close"].iloc[-1]) if len(before) else None,
               max_hold_min=max_hold_min, exit_price=None, r_multiple=None, mae_r=None,
               mfe_r=None, bars_used=None, gap=None)

    def unscorable(reason):
        return dict(row, exit_type="unscorable", reason=reason)

    problem = take_problem(direction, entry, stop, target)
    if problem:
        return unscorable(problem)
    window = bars[(bars.index >= t0) & (bars.index < end)]
    entry_time = t0
    if entry is None:
        if window.empty:
            return unscorable("no bar after the reply for a market entry") if final else None
        entry_time, entry = window.index[0], float(window["open"].iloc[0])
        row.update(entry_price=entry, entry_source="market_open")
        problem = take_problem(direction, entry, stop, target)
        if problem:
            return unscorable(f"{problem} (market entry {entry})")
    row["entry_ts_utc"] = str(_utc(entry_time))
    try:
        out = score(entry_time=entry_time.to_pydatetime(), direction=direction, entry_price=entry,
                    stop=stop, target=target, max_hold_minutes=max_hold_min,
                    bars=bars_from_frame(window))
    except NoBarsError:
        return unscorable("no bar inside the holding period") if final else None
    except ValueError as e:
        return unscorable(str(e))
    if out.reason == "timeout" and not final:
        return None
    return dict(row, exit_type=OUTCOME_EXIT_TYPES[out.reason], reason=out.reason,
                exit_price=out.exit_price, r_multiple=round(out.r_multiple, 4),
                mae_r=round(out.mae_r, 4), mfe_r=round(out.mfe_r, 4), bars_used=out.bars_used,
                gap=int(out.gap))


def takes_needing_outcome(con):
    """First decisions per (alert, epoch) that are TAKEs and have no outcome row
    yet: (rowid, alert_id, epoch, direction, entry, stop, target, decision_ts)."""
    return con.execute(
        "SELECT d.rowid, d.alert_id, d.epoch, d.direction, d.entry, d.stop, d.target, "
        "d.decision_ts_utc FROM decisions d "
        "JOIN (SELECT MIN(rowid) AS r FROM decisions GROUP BY alert_id, epoch) f ON d.rowid = f.r "
        "LEFT JOIN outcomes o ON o.alert_id = d.alert_id AND o.epoch = d.epoch "
        "WHERE d.decision = 'TAKE' AND o.alert_id IS NULL ORDER BY d.rowid").fetchall()


def insert_outcome(con, alert_id: str, epoch: int, decision_rowid: int, o: dict) -> None:
    con.execute("INSERT INTO outcomes (alert_id, exit_price, r_multiple, exit_type, filled_ts_utc, "
                "epoch, decision_rowid, entry_ts_utc, entry_price, entry_source, entry_ref_close, "
                "max_hold_min, reason, mae_r, mfe_r, bars_used, gap) "
                "VALUES(?,?,?,?,datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?)",
                (alert_id, o["exit_price"], o["r_multiple"], o["exit_type"], epoch, decision_rowid,
                 o["entry_ts_utc"], o["entry_price"], o["entry_source"], o["entry_ref_close"],
                 o["max_hold_min"], o["reason"], o["mae_r"], o["mfe_r"], o["bars_used"], o["gap"]))


def sweep_outcomes(con, df, now_utc, log=print) -> int:
    """Score every TAKE whose outcome is final, from the accumulated bar frame.
    Runs with the other 5-min sweeps. Returns the number of outcomes written."""
    n = 0
    for rowid, alert_id, epoch, direction, entry, stop, target, ts in takes_needing_outcome(con):
        o = score_take(direction, entry, stop, target, ts, df, now_utc)
        if o is None:
            continue
        insert_outcome(con, alert_id, epoch, rowid, o)
        n += 1
        log(f"outcome {alert_id} (epoch {epoch}): {o['exit_type']} "
            f"R={o['r_multiple']} ({o['reason']})", flush=True)
    if n:
        con.commit()
    return n
