#!/usr/bin/env python3
"""XAUUSD sigma right now: price, HAR sigma over 60 and 240 min, the ±1 and ±2
sigma bands, the vol-block state and its L tercile, and a high-impact event
warning. Read-only.

    python tools/sigma_now.py [--host H] [--port P] [--client-id 64] [--days 6]
                              [--model parquet2021_2023] [--events PATH]

1. Loads the pinned HAR model (backend/datafeed/features/models, SHA-256
   checked against vol_block.SIGMA_MODELS) and its L tercile cut-offs (SHA-256
   checked, l_tercile.CUTOFFS) before connecting.
2. Connects read-only to the IBKR gateway (client id 64), and fetches the
   last 6 trading days (--days) of XAUUSD CMDTY 1-min MIDPOINT bars (useRTH=False,
   formatDate=2: UTC), as the datafeed's IBKR source asks for them.
   Disconnects.
3. vol_block.state_at(model.params, bars, now): L, S_hd, T at the latest
   hourly label, and the close of the last finished bar as the price.
   sigma_over(model, state, H) for H = 60 and 240: sigma over the next H
   market-open minutes (closed minutes don't count; "crosses a close" means
   gap risk is not in sigma).
4. Events: high-impact (Forex Factory "High") events in the next 4 hours from
   database/historical_news/events_utc.parquet, for the currencies in
   --currencies (default USD). Only if the file covers now .. now + 4 h;
   otherwise "calendar unavailable".

Exit status: 0 printed; 1 no usable state (no bars, too little history);
2 bad arguments; 4 IBKR error 10197 (a competing live session: log out
everywhere, run again); 3 any other IBKR or connection failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.datafeed.features import l_tercile  # noqa: E402
from backend.datafeed.features import vol_block as vb  # noqa: E402
from backend.datafeed.ingest.xauusd_bars import _market_open  # noqa: E402

CLIENT_ID = 64                 # 61 options probe, 62 options snapshot, 63 backfill, 11/21 scanner, 31 feed
DAYS, BAR_SIZE, WHAT = 6, "1 min", "MIDPOINT"
HORIZONS = (60, 240)
EVENTS = ROOT / "database" / "historical_news" / "events_utc.parquet"
EVENT_HOURS = 4


class IbkrStop(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def fetch_bars(ib, contract_cls, host: str, port: int, client_id: int, days: int = DAYS):
    """The last `days` trading days of 1-min MIDPOINT bars, as an OHLC frame on a UTC
    index of bar opens."""
    import pandas as pd
    errors = []
    ib.errorEvent += lambda reqId, code, msg, contract=None: errors.append((code, msg))
    try:
        ib.connect(host, port, clientId=client_id, readonly=True, timeout=20)
        c = contract_cls(symbol="XAUUSD", secType="CMDTY", exchange="SMART", currency="USD")
        got = ib.qualifyContracts(c)
        if not got:
            raise IbkrStop("IBKR could not qualify XAUUSD CMDTY SMART USD", 3)
        bars = ib.reqHistoricalData(got[0], endDateTime="", durationStr=f"{days} D", barSizeSetting=BAR_SIZE,
                                    whatToShow=WHAT, useRTH=False, formatDate=2, keepUpToDate=False, timeout=60)
    except IbkrStop:
        raise
    except (OSError, TimeoutError, ConnectionError) as e:
        raise IbkrStop(f"no connection to the gateway at {host}:{port}: {e!r}", 3) from e
    finally:
        ib.disconnect()
    if any(code == 10197 for code, _ in errors):
        raise IbkrStop("IBKR error 10197: a competing live session. Log the live account out everywhere "
                       "(TWS, mobile, web) and run again.", 4)
    rows = []
    for b in bars or []:
        t = b.date
        if not isinstance(t, datetime) or t.tzinfo is None:
            raise IbkrStop(f"unexpected bar time {t!r} (formatDate=2 should give UTC)", 3)
        rows.append((t.astimezone(timezone.utc), float(b.open), float(b.high), float(b.low), float(b.close)))
    idx = pd.DatetimeIndex([r[0] for r in rows], name="bar_ts")
    if len(idx) == 0:
        idx = pd.DatetimeIndex([], tz="UTC", name="bar_ts")
    df = pd.DataFrame({c: [r[i + 1] for r in rows] for i, c in enumerate(("open", "high", "low", "close"))},
                      index=idx).sort_index()
    return df[~df.index.duplicated(keep="last")], errors


def event_lines(path: Path, now: datetime, hours: int, currencies: tuple[str, ...]) -> list[str]:
    import pandas as pd
    if not Path(path).is_file():
        return [f"events     calendar unavailable (no events file at {path})"]
    ev = pd.read_parquet(path, columns=["ts_utc", "currency", "event_normalized", "impact"])
    ts = pd.DatetimeIndex(ev["ts_utc"]).tz_convert("UTC")
    first, last = ts.min().normalize(), ts.max().normalize() + pd.Timedelta(days=1)
    t0, t1 = pd.Timestamp(now), pd.Timestamp(now) + pd.Timedelta(hours=hours)
    if not (first <= t0 and t1 <= last):
        return [f"events     calendar unavailable (the events file covers {first.date()} .. "
                f"{(last - pd.Timedelta(days=1)).date()}, not now .. +{hours} h)"]
    sel = ev[(ts >= t0) & (ts < t1) & (ev["impact"] == "High") & ev["currency"].isin(currencies)]
    if sel.empty:
        return [f"events     no high-impact {'/'.join(currencies)} event in the next {hours} h"]
    out = []
    for t, cur, name in zip(pd.DatetimeIndex(sel["ts_utc"]).tz_convert("UTC"), sel["currency"],
                            sel["event_normalized"]):
        mins = int((t - t0).total_seconds() // 60)
        out.append(f"WARNING    high-impact event within {hours} h: {t:%H:%M} UTC {cur} {name} "
                   f"(in {mins // 60}h{mins % 60:02d})")
    return out


def report(model, cut, df, now: datetime, events: Path, currencies, days: int = DAYS) -> tuple[list[str], int]:
    st = vb.state_at(model.params, df, now)
    head = (f"XAUUSD sigma now: {now:%Y-%m-%d %H:%M} UTC  (HAR model {model.name}, SHA-256 "
            f"{model.sha256[:12]}… checked; L cut-offs {cut.sha256[:12]}… checked)")
    if st.reason is not None:
        hint = (f"; T needs 120 hourly rows plus one: right after the Sunday open {days} trading days "
                f"can fall short, run again with --days {days + 1}") if st.reason == "insufficient_history" else ""
        return [head, f"no usable vol state: {st.reason} ({len(df)} bars"
                f"{', last ' + df.index[-1].isoformat() if len(df) else ''}){hint}"], 1
    lines = [head]
    when = st.last_bar_ts.strftime("%Y-%m-%d %H:%M")
    closed = "" if _market_open(now.replace(second=0, microsecond=0)) else "  [market closed: last close]"
    lines.append(f"price      {st.close_ref:,.2f}  (close of the {when} UTC 1-min {WHAT} bar){closed}")
    lines.append(f"vol state  L {st.L:+.3f} = {l_tercile.tercile(st.L, cut)} "
                 f"(calm < {cut.q1:+.3f} <= normal < {cut.q2:+.3f} <= stressed)   "
                 f"S_hd {st.S_hd:+.3f}   T {st.T:+.3f}   (hourly label {st.label:%H:%M} UTC)")
    for H in HORIZONS:
        s = vb.sigma_over(model, st, H)
        p, d = st.close_ref, s.sigma_px
        lines.append(f"sigma {H:>3}m ${d:,.2f} ({s.sigma_ret * 100:.2f} %)   "
                     f"±1σ {p - d:,.2f} .. {p + d:,.2f}   ±2σ {p - 2 * d:,.2f} .. {p + 2 * d:,.2f}"
                     + ("   [crosses a close: gap risk not in sigma]" if s.crosses_close else ""))
    lines += event_lines(events, now, EVENT_HOURS, currencies)
    return lines, 0


def main(argv=None, *, ib=None, contract_cls=None, now=None, out=None) -> int:
    ap = argparse.ArgumentParser(prog="sigma_now.py", description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("IB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IB_PORT", "4004")))
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--days", type=int, default=DAYS, help="trading days of bars to fetch (default 6)")
    ap.add_argument("--model", default=None, help=f"HAR model name (default {vb.DEFAULT_SIGMA_MODEL})")
    ap.add_argument("--events", default=str(EVENTS))
    ap.add_argument("--currencies", default="USD", help="comma-separated, for the event warning")
    a = ap.parse_args(argv)
    out = out or sys.stdout
    model = vb.load_sigma_model(a.model)          # SHA-256 checked; raises on a mismatch
    cut = l_tercile.load(model)                   # SHA-256 checked, and computed for this model
    if ib is None:
        from ib_insync import IB, Contract
        ib, contract_cls = IB(), Contract
    try:
        df, _ = fetch_bars(ib, contract_cls, a.host, a.port, a.client_id, a.days)
    except IbkrStop as e:
        print(f"sigma_now: {e}", file=sys.stderr)
        return e.status
    t = (now() if now else datetime.now(timezone.utc)).astimezone(timezone.utc)
    lines, status = report(model, cut, df, t, Path(a.events), tuple(c.strip() for c in a.currencies.split(",")), a.days)
    print("\n".join(lines), file=out)
    return status


if __name__ == "__main__":
    sys.exit(main())
