"""The scanner's self-test (--selftest): no look-ahead, first-touch-only,
open-level arming, sessions and market hours, the reply parser and alert
text, the sweeps, journal epochs, and live == continuous replay. Needs the
historical parquet."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from backend.levels.build import MIN_BARS_FULL_DAY, build_levels, session_of
from backend.levels.touch import Scanner
from backend.scanner.engine import LiveLevels
from backend.scanner.journal.outcomes import sweep_counterfactuals
from backend.scanner.journal.schema import JOURNAL_VERSION, open_log, start_epoch
from backend.scanner.journal.writes import _journal_new_alert, insert_alert, sweep_auto_missed
from backend.scanner.market import _market_open, _valid_bar
from backend.scanner.sources.replay import load_data
from backend.scanner.telegram import format_alert, parse_reply

# ── Self-test: no look-ahead + first-touch dedup (from the notebook) ────────
def selftest():
    df = load_data()
    fails = []
    # 1) no look-ahead: corrupt FUTURE bars, prior-derived levels must not change
    days = df.groupby(df.index.normalize()).size()
    full = days[days >= MIN_BARS_FULL_DAY].index
    d0 = [d for d in full if d >= pd.Timestamp("2023-06-15", tz="UTC")][0]
    before = {L["type"]: L["value"] for L in build_levels(df) if L["created_day"] == d0}
    df2 = df.copy()
    df2.loc[df2.index >= d0, "high"] += 10000
    after = {L["type"]: L["value"] for L in build_levels(df2) if L["created_day"] == d0}
    for t in ("PDH", "PDL", "PWH", "PWL"):
        if t in before and abs(before[t] - after.get(t, -1)) > 1e-6:
            fails.append(f"look-ahead:{t}")
    # 2) first-touch-only: oscillate at a level -> exactly ONE alert (not two)
    idx = pd.date_range("2023-06-15", periods=10, freq="1min", tz="UTC")
    pr = [100, 100, 100, 100, 100, 105, 105, 100, 100, 100]
    p = pd.DataFrame({"open": pr, "high": [x + .1 for x in pr],
                      "low": [x - .1 for x in pr], "close": pr}, index=idx)
    lvl = [dict(type="X", value=100.0, active_from=idx[0], active_to=idx[-1] + pd.Timedelta(minutes=1),
                arm="static", created_day=idx[0].normalize(), id=0)]
    sc = Scanner(lvl, window_min=1)
    got = []
    for ts, o, h, l, c in p.itertuples(name=None):
        got += sc.on_bar(ts, o, h, l, c)
    got += sc.close()
    if len(got) != 1:
        fails.append(f"first-touch-only: got {len(got)} alerts, expected 1")
    # 3) session assignment is pure UTC hour (no DST)
    if not (session_of(pd.Timestamp("2023-03-12 07:30", tz="UTC")) == "LONDON"
            and session_of(pd.Timestamp("2023-06-15 07:30", tz="UTC")) == "LONDON"
            and session_of(pd.Timestamp("2023-03-12 21:30", tz="UTC")) == "ROLLOVER"):
        fails.append("session/DST")
    # 4) open-level arming: price starts AT the open (v=100). It must NOT alert
    #    at creation ("midnight happened"); only after leaving by 2xtol ($2) and
    #    returning does a touch mean "price came back to the open" — real info.
    idx = pd.date_range("2023-06-15", periods=6, freq="1min", tz="UTC")
    pr = [100, 100, 100, 103, 103, 100]   # sit on open, leave by $3, return
    p = pd.DataFrame({"open": pr, "high": [x + .1 for x in pr],
                      "low": [x - .1 for x in pr], "close": pr}, index=idx)
    ol = [dict(type="DOPEN", value=100.0, active_from=idx[0], active_to=idx[-1] + pd.Timedelta(minutes=1),
               arm="open", created_day=idx[0].normalize(), id=0)]
    sc = Scanner(ol, window_min=1)
    got = []
    for ts, o, h, l, c in p.itertuples(name=None):
        got += sc.on_bar(ts, o, h, l, c)
    got += sc.close()
    if len(got) != 1 or got[0]["alert_ts_utc"][-8:] != "00:05:00":
        fails.append(f"open-arming: {[g['alert_ts_utc'] for g in got]} (want one, at 00:05 the return)")
    # 5) reply parser (deterministic, no interpretation)
    t = parse_reply("A20250616-005 TAKE long 4128 sl 4122 tp 4140 london low reclaim")
    if not (t and t["decision"] == "TAKE" and t["direction"] == "long" and t["entry"] == 4128.0
            and t["stop"] == 4122.0 and t["target"] == 4140.0 and t["reasoning"] == "london low reclaim"):
        fails.append(f"parse TAKE: {t}")
    s = parse_reply("A20250616-002 SKIP no structure, into news")
    if not (s and s["decision"] == "SKIP" and s["reasoning"] == "no structure, into news"):
        fails.append(f"parse SKIP: {s}")
    if parse_reply("garbage") is not None:
        fails.append("parse garbage should be None")
    # 6) alert message carries ONLY the allowed fields (no recommendation)
    msg = format_alert(dict(alert_id="A1", alert_ts_utc="2025-06-16 00:06:00", session="ASIA",
                            level_types="DOPEN,WOPEN", level_price=3443.998,
                            price_at_alert=3442.985, confluence_count=2))
    banned = ("buy", "sell", "long", "short", "take", "skip", "recommend", "signal", "trade")
    if any(b in msg.lower() for b in banned):
        fails.append("alert message contains a recommendation word")
    # 7) auto-MISSED after 4h, and not before
    import tempfile
    con = open_log(Path(tempfile.mkdtemp()) / "t.sqlite")
    insert_alert(con, dict(alert_id="A_old", alert_ts_utc="2025-06-16 00:00:00", level_types="PDH",
                           level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1),
                 epoch=1, bar_source="ibkr")
    insert_alert(con, dict(alert_id="A_new", alert_ts_utc="2025-06-16 03:30:00", level_types="PDL",
                           level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1),
                 epoch=1, bar_source="ibkr")
    con.commit()
    n = sweep_auto_missed(con, pd.Timestamp("2025-06-16 04:30:00", tz="UTC"))
    decided = dict(con.execute("SELECT alert_id, decision FROM decisions").fetchall())
    if not (n == 1 and decided.get("A_old") == "MISSED" and "A_new" not in decided):
        fails.append(f"auto-MISSED: n={n} decided={decided}")
    # 8) counterfactual sweep: only alerts with a full 4h forward window get filled
    insert_alert(con, dict(alert_id="A_cf", alert_ts_utc="2025-06-16 00:00:00", level_types="PDH",
                           level_price=100.0, price_at_alert=100.0, session="ASIA", confluence_count=1),
                 epoch=1, bar_source="ibkr")
    con.commit()
    fdf = pd.DataFrame({"open": 100.0, "high": 110.0, "low": 95.0, "close": 100.0},
                       index=pd.date_range("2025-06-16 00:00", periods=300, freq="1min", tz="UTC"))
    # A_cf (00:00) has >4h of bars; A_new (03:30) does not, at now=04:30
    sweep_counterfactuals(con, fdf, pd.Timestamp("2025-06-16 04:30:00", tz="UTC"))
    cfs = dict((r[0], (r[1], r[2])) for r in
               con.execute("SELECT alert_id, mfe_up_4h, mfe_dn_4h FROM counterfactuals").fetchall())
    if not (cfs.get("A_cf") == (10.0, 5.0) and "A_new" not in cfs):
        fails.append(f"counterfactual sweep: cfs={cfs}")

    # 9) heartbeat market hours follow New York time, so the break is right both
    #    sides of US DST (21-22 UTC in summer, 22-23 UTC in winter)
    want = {"2026-07-14 21:30": False, "2026-07-14 22:30": True,    # summer Tue: break, reopened
            "2026-01-13 21:30": True, "2026-01-13 22:30": False,    # winter Tue: open, break
            "2026-01-11 22:30": False, "2026-01-11 23:30": True}    # winter Sun: before/after open
    got = {t: _market_open(pd.Timestamp(t, tz="UTC")) for t in want}
    if got != want:
        fails.append(f"market hours/DST: {[t for t in want if got[t] != want[t]]}")
    # 10) live == continuous replay: from the moment it comes online, LiveLevels
    #     must emit exactly what one Scanner over the same bars emits — across
    #     session closes, a UTC midnight and a weekend reopen
    win = df[(df.index >= pd.Timestamp("2024-03-07", tz="UTC")) & (df.index < pd.Timestamp("2024-03-13", tz="UTC"))]
    t0 = pd.Timestamp("2024-03-08 10:37", tz="UTC")
    sc, rep = Scanner(build_levels(win)), []
    for ts, o, h, l, c in win.itertuples(name=None):
        out = sc.on_bar(ts, o, h, l, c)
        if ts >= t0:
            rep += out
    live, got = LiveLevels(win[win.index < t0].copy(), t0), []
    for ts, o, h, l, c in win[win.index >= t0].itertuples(name=None):
        got += live.on_bar(ts, o, h, l, c)
    if not rep or got != rep:
        fails.append(f"live vs replay: {len(got)} live vs {len(rep)} replay alerts, "
                     f"{sum(a != b for a, b in zip(got, rep))} differ")
    # 11) live bar filter: IBKR's -1 "no quote", NaN, and frozen quotes outside
    #     trading hours never reach the level engine; a normal bar does
    nan = float("nan")
    bars = {"2026-09-28 10:00": (4280, 4281, 4279, 4280.5),     # Mon, London: real
            "2026-09-28 10:01": (4280, 4281, -1.0, -1.0),       # -1 "no quote"
            "2026-09-28 10:02": (4280, nan, 4279, 4280),        # NaN
            "2026-09-27 18:07": (4285, 4285, 4285, 4285),       # Sun before the reopen
            "2026-07-14 21:30": (4280, 4281, 4279, 4280)}       # summer daily break
    ok = {t: _valid_bar(pd.Timestamp(t, tz="UTC"), *v) for t, v in bars.items()}
    if ok != {"2026-09-28 10:00": True, "2026-09-28 10:01": False, "2026-09-28 10:02": False,
              "2026-09-27 18:07": False, "2026-07-14 21:30": False}:
        fails.append(f"live bar filter: {ok}")

    # 12) journal epochs: a pre-migration journal (old DDL, old positional
    #     INSERTs) keeps its rows as epoch 1 / bar_source ibkr; every bar-source
    #     change starts a new epoch and is logged in source_switches (ibkr ->
    #     stream = 2, rollback to ibkr = 3, never 1 again); an alert id reused
    #     by two epochs stays two alerts
    jp = Path(tempfile.mkdtemp()) / "old.sqlite"
    old = sqlite3.connect(str(jp))
    old.executescript("""
      CREATE TABLE alerts(alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
        price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT);
      CREATE TABLE decisions(alert_id TEXT, decision TEXT, decision_ts_utc TEXT,
        minutes_from_alert REAL, direction TEXT, entry REAL, stop REAL, target REAL,
        reasoning TEXT, reasoning_ts_utc TEXT, source TEXT);
      CREATE TABLE outcomes(alert_id TEXT, exit_price REAL, r_multiple REAL, exit_type TEXT,
        filled_ts_utc TEXT);
      CREATE TABLE counterfactuals(alert_id TEXT, fwd_high_1h REAL, fwd_low_1h REAL,
        mfe_up_1h REAL, mfe_dn_1h REAL, fwd_high_4h REAL, fwd_low_4h REAL, mfe_up_4h REAL,
        mfe_dn_4h REAL, computed_ts_utc REAL);
      INSERT INTO alerts VALUES('A20251014-001','2025-10-14 01:00:00','PDH',1,1,'ASIA',1,'x');
      INSERT INTO decisions VALUES('A20251014-001','SKIP','2025-10-14 01:05:00',5,NULL,NULL,
        NULL,NULL,'r','2025-10-14 01:05:00','telegram');
      INSERT INTO counterfactuals VALUES('A20251014-001',1,1,0,0,1,1,0,0,'x');
    """)
    old.close()
    con = open_log(jp)
    open_log(jp).close()                                   # idempotent
    rows = con.execute("SELECT alert_id, epoch, bar_source FROM alerts").fetchall()
    kids = [con.execute(f"SELECT epoch FROM {t}").fetchall() for t in ("decisions", "counterfactuals")]
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    epochs = [start_epoch(con, src) for src in ("ibkr", "stream", "stream", "ibkr", "stream")]
    switches = con.execute("SELECT from_source, to_source, epoch FROM source_switches").fetchall()
    _journal_new_alert(con, dict(alert_id="A20251014-001", alert_ts_utc="2025-10-14 09:00:00",
                                 level_types="NY_L", level_price=2.0, price_at_alert=2.0,
                                 session="LONDON", confluence_count=1), epoch=4, bar_source="stream")
    n_missed = sweep_auto_missed(con, pd.Timestamp("2025-10-14 14:00:00", tz="UTC"))
    n_cf = sweep_counterfactuals(con, fdf, pd.Timestamp("2025-10-14 14:00:00", tz="UTC"))
    dec = con.execute("SELECT epoch, decision FROM decisions ORDER BY rowid").fetchall()
    cfe = con.execute("SELECT epoch FROM counterfactuals ORDER BY rowid").fetchall()
    if not (rows == [("A20251014-001", 1, "ibkr")] and kids == [[(1,)], [(1,)]]
            and ver == JOURNAL_VERSION == 4 and epochs == [1, 2, 2, 3, 4]
            and switches == [(None, "ibkr", 1), ("ibkr", "stream", 2), ("stream", "ibkr", 3),
                             ("ibkr", "stream", 4)]
            and n_missed == 1 and n_cf == 1
            and dec == [(1, "SKIP"), (4, "MISSED")] and cfe == [(1,), (4,)]):
        fails.append(f"journal epoch: rows={rows} kids={kids} ver={ver} epochs={epochs} "
                     f"switches={switches} missed={n_missed} cf={n_cf} dec={dec} cfe={cfe}")

    # 13) live started between UTC midnight and the day's first bar still builds
    #     the day's levels on that first bar (it used to wait for 07:00)
    d = pd.Timestamp("2025-06-16", tz="UTC")
    win = df[(df.index >= d - pd.Timedelta(days=14)) & (df.index < d + pd.Timedelta(hours=8))]
    sc, rep = Scanner(build_levels(win)), []
    for ts, o, h, l, c in win.itertuples(name=None):
        out = sc.on_bar(ts, o, h, l, c)
        if ts >= d:
            rep += out
    for start in (d, d + pd.Timedelta(seconds=30)):
        live, got = LiveLevels(win[win.index < d].copy(), start), []
        for ts, o, h, l, c in win[win.index >= d].itertuples(name=None):
            got += live.on_bar(ts, o, h, l, c)
        if not rep or got != rep:
            fails.append(f"live start at {start.time()}: {len(got)} live vs {len(rep)} replay alerts")

    print("SELFTEST:", "ALL PASSED" if not fails else f"FAILED {fails}")
    return not fails
