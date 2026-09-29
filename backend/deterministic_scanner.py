#!/usr/bin/env python3
"""
Deterministic level scanner + decision log.

A MEASUREMENT INSTRUMENT for a human's discretionary judgment. There is no
agent, no LLM, no filtering intelligence anywhere in this loop. It watches price
arrive at pre-defined levels and records that arrival; the human decides.

An alert means "price has ARRIVED at a level, start watching" — NOT "trade now".
Alerts stay open and are referenced by id; a decision may come 40+ minutes later.

Level engine: reused verbatim from notebooks/backtest.ipynb (calibrated on
2023-2025, do not retune). Each level is a row (type, value, active_from,
active_to); active_from is the moment the source period CLOSES, so a level
cannot be touched before it exists — no look-ahead by construction. Building
levels from the full history is therefore safe (proven by the corrupt-future
unit test in --selftest), which keeps the replay and live paths identical.

Replay:   python backend/deterministic_scanner.py --date 2025-06-16
Self-test: python backend/deterministic_scanner.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

# ── Calibration (2023-2025, do not retune) ─────────────────────────────────
PARQUET = Path(__file__).resolve().parent.parent / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
# SCANNER_DB lets the container point the append-only journal at a mounted
# volume so decisions survive restarts / redeploys.
DB_PATH = Path(os.environ.get(
    "SCANNER_DB", Path(__file__).resolve().parent.parent / "database/scanner_decisions.sqlite"))
SESSIONS = {"ASIA": (0, 7), "LONDON": (7, 12), "NY": (12, 21)}  # UTC hours [start,end)
MIN_BARS_FULL_DAY = 300          # a full trading day needs >= this many bars
SESSION_CARRY_H = 24             # session H/L stay active +24h after close
TOL = 1.00                       # touch tolerance ($)
WINDOW_MIN = 15                  # collapse alerts within the same 15-min bucket
CF_HORIZONS = {"1h": 60, "4h": 240}   # counterfactual MFE/MAE horizons (minutes)
MISSED_AFTER_H = 4               # auto-assign MISSED to alerts unanswered this long
RECONNECT_BACKOFF_S = 20         # wait before exiting on a dropped gateway, so the
                                 # container doesn't hammer-restart while the gateway
                                 # is down for minutes (weekly 2FA window)
SILENCE_WARN_S = 1800            # connected but no bars this long (market hours) -> Telegram warn


# ── Level engine (from notebooks/backtest.ipynb, active_from construction) ──
def session_of(ts) -> str:
    for s, (a, b) in SESSIONS.items():
        if a <= ts.hour < b:
            return s
    return "ROLLOVER"


def build_levels(df: pd.DataFrame) -> list[dict]:
    """Precompute daily/weekly/session aggregates once, emit level instances.

    Each instance carries active_from (source-period close) and active_to, so it
    is invisible outside that window — the no-look-ahead property. Types map to
    the 12 the spec asks for: PDH PDL PWH PWL DOPEN WOPEN {ASIA,LONDON,NY}_{H,L}.
    """
    date = df.index.normalize()
    daily = df.groupby(date).agg(high=("high", "max"), low=("low", "min"),
                                 open=("open", "first"), n=("open", "size"))
    full_days = daily.index[daily["n"] >= MIN_BARS_FULL_DAY]
    hour = df.index.hour
    sess_hl = {
        s: df[(hour >= h0) & (hour < h1)]
           .groupby(df[(hour >= h0) & (hour < h1)].index.normalize())
           .agg(h=("high", "max"), l=("low", "min"))
        for s, (h0, h1) in SESSIONS.items()
    }
    ic = df.index.isocalendar()
    wkkey = pd.Series(ic["year"].astype(str) + "-" + ic["week"].astype(str).str.zfill(2), index=df.index)
    wk = df.groupby(wkkey).agg(high=("high", "max"), low=("low", "min"), open=("open", "first"))

    def wkkey_of(day):
        c = (day - pd.Timedelta(days=day.weekday())).isocalendar()
        return f"{c[0]}-{c[1]:02d}"

    levels: list[dict] = []
    for day in daily.index:
        de = day + pd.Timedelta(days=1)
        today = []
        i = full_days.searchsorted(day)                      # prior FULL trading day
        if i > 0:
            p = full_days[i - 1]
            today += [dict(type="PDH", value=daily.at[p, "high"], active_from=day, active_to=de, arm="static"),
                      dict(type="PDL", value=daily.at[p, "low"],  active_from=day, active_to=de, arm="static")]
        pwk = wkkey_of(day - pd.Timedelta(days=7))
        if pwk in wk.index:
            today += [dict(type="PWH", value=wk.at[pwk, "high"], active_from=day, active_to=de, arm="static"),
                      dict(type="PWL", value=wk.at[pwk, "low"],  active_from=day, active_to=de, arm="static")]
        today.append(dict(type="DOPEN", value=daily.at[day, "open"], active_from=day, active_to=de, arm="open"))
        cwk = wkkey_of(day)
        if cwk in wk.index:
            today.append(dict(type="WOPEN", value=wk.at[cwk, "open"], active_from=day, active_to=de, arm="open"))
        for s, (h0, h1) in SESSIONS.items():
            g = sess_hl[s]
            if day in g.index:
                cl = day + pd.Timedelta(hours=h1)            # active_from = session CLOSE
                ex = cl + pd.Timedelta(hours=SESSION_CARRY_H)
                today += [dict(type=f"{s}_H", value=g.at[day, "h"], active_from=cl, active_to=ex, arm="static"),
                          dict(type=f"{s}_L", value=g.at[day, "l"], active_from=cl, active_to=ex, arm="static")]
        for L in today:
            L["created_day"] = day
        levels += today
    for i, L in enumerate(levels):
        L["id"] = i
    return levels


# ── Streaming scanner (identical code for replay and live) ─────────────────
class Scanner:
    """Feeds one bar at a time. First-touch-only per level instance: once a
    level is touched it is dead forever (no cooldown re-arm — that floods to
    ~15/day; first-touch-only holds ~4.5/day). Raw touches landing in the same
    15-min bucket and at the same price (within TOL) collapse into one alert
    carrying a confluence_count and every contributing level type."""

    def __init__(self, levels: list[dict], tol: float = TOL, window_min: int = WINDOW_MIN):
        self.tol = tol
        self.window = pd.Timedelta(minutes=window_min)
        self.by_day = defaultdict(list)
        for L in levels:
            d, d1 = L["active_from"].normalize(), L["active_to"].normalize()
            while d <= d1:
                self.by_day[d].append(L)
                d += pd.Timedelta(days=1)
        # per-level runtime state: 'open' levels start UNARMED (price sits at the
        # open at creation); static levels are armed from birth.
        self.state = {L["id"]: {"armed": L["arm"] != "open", "alerted": False} for L in levels}
        self._cur_day = None
        self._active = []
        self._bucket_start = None
        self._bucket_touches: list[dict] = []
        # per-day counter, so an alert's id depends only on that day's alerts, not
        # on how far back the history (and a live warm-up) starts
        self._seq = defaultdict(int)

    def on_bar(self, ts, o, h, l, c) -> list[dict]:
        finalized = []
        # flush the previous 15-min bucket once we cross into a new one
        if self._bucket_start is not None and ts - self._bucket_start >= self.window:
            finalized += self._flush()
        if self._bucket_start is None or ts - self._bucket_start >= self.window:
            self._bucket_start = ts.floor(f"{int(self.window.total_seconds() // 60)}min")

        d = ts.normalize()
        if d != self._cur_day:
            self._cur_day = d
            self._active = self.by_day.get(d, [])

        for L in self._active:
            if not (L["active_from"] <= ts < L["active_to"]):
                continue
            s = self.state[L["id"]]
            if s["alerted"]:
                continue
            v = L["value"]
            in_zone = (l - self.tol) <= v <= (h + self.tol)
            left = (h < v - 2 * self.tol) or (l > v + 2 * self.tol)
            if not s["armed"]:
                if left:
                    s["armed"] = True
                continue
            if in_zone:
                self._bucket_touches.append(dict(ts=ts, type=L["type"], value=v, price=c))
                s["alerted"] = True     # first-touch-only: dead forever
        return finalized

    def close(self) -> list[dict]:
        return self._flush()

    def _flush(self) -> list[dict]:
        touches, self._bucket_touches = self._bucket_touches, []
        if not touches:
            return []
        touches.sort(key=lambda t: t["value"])
        clusters, cur = [], [touches[0]]
        for t in touches[1:]:
            if abs(t["value"] - cur[-1]["value"]) <= self.tol:
                cur.append(t)
            else:
                clusters.append(cur); cur = [t]
        clusters.append(cur)
        alerts = []
        for cl in clusters:
            ts0 = min(t["ts"] for t in cl)
            day = ts0.strftime("%Y%m%d")
            self._seq[day] += 1
            alerts.append(dict(
                alert_id=f"A{day}-{self._seq[day]:03d}",
                alert_ts_utc=ts0.strftime("%Y-%m-%d %H:%M:%S"),
                level_types=",".join(sorted({t["type"] for t in cl})),
                level_price=round(sum(t["value"] for t in cl) / len(cl), 3),
                price_at_alert=round(cl[0]["price"], 3),
                session=session_of(ts0),
                confluence_count=len({t["type"] for t in cl}),
            ))
        return alerts


class LiveLevels:
    """Keeps a live Scanner equal to a continuous replay over the same bars.

    build_levels() only knows a level once its source period has bars, so a level
    list built at one moment lacks — or holds partial values for — the session H/L
    that close later. So rebuild at every point where new levels become active
    (UTC midnight and each session close), then warm the fresh Scanner on ALL
    history before the current bar WITHOUT emitting. That skips alerts for touches
    that happened before we came online, and keeps first-touch state for levels
    carried over from yesterday, exactly as a continuous replay would hold it."""

    def __init__(self, hist: pd.DataFrame, now):
        self.hist = hist
        self._rebuild(now)

    @staticmethod
    def _epoch(ts):
        # changes exactly when new levels can activate: midnight, session closes
        return ts.normalize(), sum(ts.hour >= h1 for _, h1 in SESSIONS.values())

    def _rebuild(self, now):
        self.epoch = self._epoch(now)
        self.scanner = Scanner(build_levels(self.hist))
        for ts, o, h, l, c in self.hist[self.hist.index < now].itertuples(name=None):
            self.scanner.on_bar(ts, o, h, l, c)

    def on_bar(self, ts, o, h, l, c) -> list[dict]:
        self.hist.loc[ts] = [o, h, l, c]
        if self._epoch(ts) != self.epoch:
            self._rebuild(ts)
        return self.scanner.on_bar(ts, o, h, l, c)


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


# ── Append-only SQLite log ─────────────────────────────────────────────────
def open_log(path=DB_PATH, check_same_thread=True) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS alerts(
        alert_id TEXT, alert_ts_utc TEXT, level_types TEXT, level_price REAL,
        price_at_alert REAL, session TEXT, confluence_count INTEGER, inserted_ts_utc TEXT);
      CREATE TABLE IF NOT EXISTS decisions(
        alert_id TEXT, decision TEXT, decision_ts_utc TEXT, minutes_from_alert REAL,
        direction TEXT, entry REAL, stop REAL, target REAL, reasoning TEXT,
        reasoning_ts_utc TEXT, source TEXT);
      CREATE TABLE IF NOT EXISTS outcomes(
        alert_id TEXT, exit_price REAL, r_multiple REAL, exit_type TEXT, filled_ts_utc TEXT);
      CREATE TABLE IF NOT EXISTS counterfactuals(
        alert_id TEXT, fwd_high_1h REAL, fwd_low_1h REAL, mfe_up_1h REAL, mfe_dn_1h REAL,
        fwd_high_4h REAL, fwd_low_4h REAL, mfe_up_4h REAL, mfe_dn_4h REAL, computed_ts_utc REAL);
    """)
    return con


def insert_alert(con, a: dict):
    con.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?,?,datetime('now'))",
                (a["alert_id"], a["alert_ts_utc"], a["level_types"], a["level_price"],
                 a["price_at_alert"], a["session"], a["confluence_count"]))


def insert_counterfactual(con, alert_id: str, cf: dict):
    con.execute("INSERT INTO counterfactuals VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
                (alert_id, cf["fwd_high_1h"], cf["fwd_low_1h"], cf["mfe_up_1h"], cf["mfe_dn_1h"],
                 cf["fwd_high_4h"], cf["fwd_low_4h"], cf["mfe_up_4h"], cf["mfe_dn_4h"]))


def _utc(ts) -> pd.Timestamp:
    """Parse a stored/naive timestamp as UTC (stored alert timestamps are naive)."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def record_decision(con, alert_id, decision, decision_ts, alert_ts, direction=None,
                    entry=None, stop=None, target=None, reasoning="", source="telegram"):
    """Append a decision. reasoning_ts == decision_ts, which is the proof that the
    reasoning was recorded at decision time, not reconstructed with hindsight."""
    mins = (_utc(decision_ts) - _utc(alert_ts)).total_seconds() / 60
    con.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (alert_id, decision, str(decision_ts), round(mins, 2), direction,
                 entry, stop, target, reasoning, str(decision_ts), source))
    con.commit()


def record_outcome(con, alert_id, exit_price, r_multiple, exit_type):
    con.execute("INSERT INTO outcomes VALUES(?,?,?,?,datetime('now'))",
                (alert_id, exit_price, r_multiple, exit_type))
    con.commit()


def undecided_alerts(con):
    """alert rows that have no decision row yet, with their alert timestamp."""
    return con.execute(
        "SELECT a.alert_id, a.alert_ts_utc FROM alerts a "
        "LEFT JOIN decisions d ON a.alert_id = d.alert_id WHERE d.alert_id IS NULL"
    ).fetchall()


def sweep_auto_missed(con, now_utc) -> int:
    """MISSED = never saw it. Auto-assign to any alert with no reply after 4h, so
    it is excluded from the TAKE-vs-SKIP comparison rather than polluting it."""
    now = _utc(now_utc)
    n = 0
    for alert_id, alert_ts in undecided_alerts(con):
        if (now - _utc(alert_ts)).total_seconds() >= MISSED_AFTER_H * 3600:
            record_decision(con, alert_id, "MISSED", now, alert_ts,
                            reasoning=f"auto: no reply within {MISSED_AFTER_H}h", source="auto")
            n += 1
    return n


def alerts_needing_counterfactual(con, now_utc):
    """Alerts old enough that a full 4h forward window exists but with no
    counterfactual row yet — the SKIP/MISSED 'what would have happened' data
    can't be computed at alert time, only once the future bars arrive."""
    now = _utc(now_utc)
    rows = con.execute(
        "SELECT a.alert_id, a.alert_ts_utc, a.price_at_alert FROM alerts a "
        "LEFT JOIN counterfactuals cf ON a.alert_id = cf.alert_id WHERE cf.alert_id IS NULL"
    ).fetchall()
    horizon = max(CF_HORIZONS.values())
    return [r for r in rows if (now - _utc(r[1])).total_seconds() >= horizon * 60]


def sweep_counterfactuals(con, df, now_utc) -> int:
    """Fill forward MFE/MAE for alerts whose 4h window has elapsed, from the
    accumulated live bar frame. Runs live so the experiment data is captured."""
    n = 0
    for alert_id, alert_ts, price in alerts_needing_counterfactual(con, now_utc):
        cf = counterfactual(df, alert_ts, price)
        insert_counterfactual(con, alert_id, cf)
        n += 1
    if n:
        con.commit()
    return n


# ── Telegram (stdlib only — message carries facts, never a recommendation) ──
def format_alert(a: dict) -> str:
    """EXACTLY the fields the spec allows: id, ts UTC, level type(s), level
    price, current price, session, confluence. No bias, no commentary."""
    return (f"{a['alert_id']}\n"
            f"{a['alert_ts_utc']} UTC\n"
            f"{a['session']}\n"
            f"{a['level_types']} @ {a['level_price']}\n"
            f"price {a['price_at_alert']}\n"
            f"confluence {a['confluence_count']}")


def parse_reply(text: str) -> dict | None:
    """Parse a human reply. Deterministic, no interpretation.
       '<id> TAKE long 4128 sl 4122 tp 4140 <reason>'  or  '<id> SKIP <reason>'."""
    tok = text.strip().split()
    if len(tok) < 2:
        return None
    alert_id, verb = tok[0], tok[1].upper()
    if verb == "SKIP":
        return dict(alert_id=alert_id, decision="SKIP", reasoning=" ".join(tok[2:]))
    if verb == "TAKE":
        d = dict(alert_id=alert_id, decision="TAKE", direction=None,
                 entry=None, stop=None, target=None, reasoning="")
        rest = tok[2:]
        if rest and rest[0].lower() in ("long", "short"):
            d["direction"] = rest[0].lower(); rest = rest[1:]
        if rest and _isnum(rest[0]):
            d["entry"] = float(rest[0]); rest = rest[1:]
        i = 0
        while i < len(rest):
            w = rest[i].lower()
            if w == "sl" and i + 1 < len(rest) and _isnum(rest[i + 1]):
                d["stop"] = float(rest[i + 1]); i += 2; continue
            if w == "tp" and i + 1 < len(rest) and _isnum(rest[i + 1]):
                d["target"] = float(rest[i + 1]); i += 2; continue
            break
        d["reasoning"] = " ".join(rest[i:])
        return d
    return None


def _isnum(s: str) -> bool:
    try:
        float(s); return True
    except ValueError:
        return False


class Telegram:
    """Thin sendMessage / getUpdates wrapper. Token + chat id from env."""
    def __init__(self):
        import os
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment")
        self._base = f"https://api.telegram.org/bot{self.token}"

    def _get(self, method, params):
        import json
        import urllib.parse
        import urllib.request
        url = f"{self._base}/{method}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=70) as r:
            return json.load(r)

    def send(self, text: str):
        return self._get("sendMessage", {"chat_id": self.chat_id, "text": text})

    def poll(self, offset: int):
        """Long-poll replies. Returns (messages, next_offset)."""
        res = self._get("getUpdates", {"offset": offset, "timeout": 60})
        msgs, nxt = [], offset
        for u in res.get("result", []):
            nxt = u["update_id"] + 1
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) == str(self.chat_id) and "text" in m:
                msgs.append(m["text"])
        return msgs, nxt


# ── Replay ──────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET).set_index("timestamp")[["open", "high", "low", "close"]].astype(float).sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def replay_day(date: str, con=None) -> pd.DataFrame:
    df = load_data()
    levels = build_levels(df)
    day = pd.Timestamp(date, tz="UTC")
    day_bars = df.loc[date:date]
    if day_bars.empty:
        print(f"No data for {date} (range {df.index[0].date()}..{df.index[-1].date()})")
        return pd.DataFrame()

    scanner = Scanner(levels)
    alerts = []
    for ts, o, h, l, c in day_bars.itertuples(name=None):
        alerts += scanner.on_bar(ts, o, h, l, c)
    alerts += scanner.close()

    rows = []
    for a in alerts:
        cf = counterfactual(df, a["alert_ts_utc"], a["price_at_alert"])
        if con is not None:
            insert_alert(con, a)
            insert_counterfactual(con, a["alert_id"], cf)
        rows.append({**a, **{k: v for k, v in cf.items() if k != "alert_id"}})
    if con is not None:
        con.commit()
    return pd.DataFrame(rows)


# ── Live loop (reuses tws_connector for the feed, Postgres for level history) ─
def _load_recent_history(symbol: str, days: int, host: str, port: int, client_id: int) -> pd.DataFrame:
    """Recent 1-min bars straight from IBKR (MIDPOINT, same pricing the live feed
    builds from) so the level engine has yesterday's / last week's / today's
    session data to build from. Deliberately NOT the Postgres ohlcv_1min view —
    that stops at the last backfill and would leave the scanner with no levels."""
    from ib_insync import IB, Contract
    ib = IB()
    ib.connect(host, port, clientId=client_id, timeout=10, readonly=True)
    try:
        c = ib.qualifyContracts(Contract(symbol=symbol, secType="CMDTY", exchange="SMART", currency="USD"))[0]
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr=f"{days} D",
                                    barSizeSetting="1 min", whatToShow="MIDPOINT",
                                    useRTH=False, formatDate=2)
    finally:
        ib.disconnect()
    df = pd.DataFrame([(b.date, b.open, b.high, b.low, b.close) for b in bars],
                      columns=["timestamp", "open", "high", "low", "close"]).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def _market_open(now) -> bool:
    """Approximate XAUUSD (spot gold) trading window, used only to suppress false
    'no data' heartbeat warnings. Sun 18:00 -> Fri 17:00 New York time, minus the
    daily 17:00-18:00 break. Evaluated in New York time because the break moves in
    UTC with US DST (21-22 UTC in summer, 22-23 UTC in winter). Holidays aren't
    modelled — a rare spurious warning is acceptable for a safety-net notification."""
    et = now.tz_convert("America/New_York")
    wd, h = et.weekday(), et.hour       # Mon=0 .. Sun=6
    if wd == 5:                          # Saturday
        return False
    if wd == 6:                          # Sunday: reopens 18:00 ET
        return h >= 18
    if wd == 4 and h >= 17:              # Friday close
        return False
    if h == 17:                          # daily maintenance break
        return False
    return True


def _send_quietly(tg, text: str):
    """Confirmation messages are best-effort: the decision is already logged."""
    try:
        tg.send(text)
    except Exception as e:
        print(f"telegram send failed ({e!r})", flush=True)


def _valid_bar(ts, o, h, l, c) -> bool:
    """Only real prices inside trading hours may reach the level engine. IBKR sends
    -1 for "no quote" (e.g. while a data farm reconnects) and pushes frozen quotes
    while the market is closed; either becomes a fake touch or a fake session.
    The backtest data has neither: all its bars fall inside _market_open() and
    every price is positive."""
    return _market_open(ts) and all(x == x and x > 0 for x in (o, h, l, c)) and l <= h


def run_live(symbol: str = "XAUUSD", dry: bool = False):
    """Live path: identical scanner code, fed by real IBKR bars.

    dry=False: alerts go to Telegram, TAKE/SKIP replies are logged, MISSED swept.
    dry=True : alerts print to the terminal, no Telegram at all — for watching the
               live feed + scanner without spending messages or needing creds."""
    import os
    import threading
    import time as _time
    from services.tws_connector import TWSConnector

    host = os.environ.get("IB_HOST", "127.0.0.1")
    port = int(os.environ.get("IB_PORT", "7497"))
    client_id = int(os.environ.get("IB_CLIENT_ID", "11"))

    tg = None if dry else Telegram()
    con = open_log(check_same_thread=False)
    lock = threading.Lock()

    # history first (separate short-lived connection, distinct clientId). A failure
    # here takes the same crash-only path as a failed tws.connect(): back off, exit,
    # let Docker restart us clean.
    try:
        hist = _load_recent_history(symbol, days=10, host=host, port=port, client_id=client_id + 10)
    except Exception as e:
        print(f"history load failed ({e!r}); backing off then exiting for restart", flush=True)
        _time.sleep(RECONNECT_BACKOFF_S)
        raise SystemExit(1)
    hist = hist[[_valid_bar(*r) for r in hist.itertuples(name=None)]]
    live = LiveLevels(hist, pd.Timestamp.now(tz="UTC"))

    hb = {"last": _time.time(), "warned": False}   # last final bar (wall clock)

    def on_bar(_sym, bar):
        if not bar.get("is_final"):
            return
        ts = pd.Timestamp(bar["timestamp"]); ts = ts.tz_convert("UTC") if ts.tz else ts.tz_localize("UTC")
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        if not _valid_bar(ts, o, h, l, c):
            return
        hb["last"] = _time.time()
        with lock:
            for a in live.on_bar(ts, o, h, l, c):
                insert_alert(con, a); con.commit()
                if dry:
                    print("ALERT  " + format_alert(a).replace("\n", " | "), flush=True)
                else:
                    try:
                        tg.send(format_alert(a))
                    except Exception as e:  # a send failure must not kill the feed
                        print(f"telegram send failed for {a['alert_id']}: {e}")

    tws = TWSConnector(host=host, port=port, client_id=client_id, readonly=True)
    # Crash-only design: surface disconnects instead of relying on the connector's
    # in-place reconnect, whose market-data resubscribe is a stub (a silent
    # reconnect would leave us connected with NO bars). On any drop we back off
    # and exit; Docker's restart policy + the entrypoint port-wait do a clean full
    # reconnect + resubscribe via run_live's cold start. One code path to get
    # right (cold start), not two.
    tws.max_retries = 0
    tws.on_bar_update(on_bar)
    if not tws.connect():
        print("could not connect to gateway; backing off then exiting for restart", flush=True)
        _time.sleep(RECONNECT_BACKOFF_S)
        raise SystemExit(1)
    tws.subscribe_bars(symbol, exchange="SMART", sec_type="CMDTY", currency="USD")
    active = sum(1 for L in build_levels(live.hist)
                 if L["active_from"] <= pd.Timestamp.now(tz="UTC") < L["active_to"])
    mode = "DRY (terminal)" if dry else "LIVE (Telegram)"
    print(f"{mode}: scanning {symbol}, {active} levels active. Ctrl-C to stop.", flush=True)

    offset = 0
    last_sweep = 0.0
    try:
        while True:
            # Crash-only: a dropped gateway (daily restart / weekly 2FA) surfaces
            # as not-connected. Back off, then exit so Docker restarts us into a
            # clean full reconnect. The backoff stops a fast restart-loop while the
            # gateway is down for minutes.
            if not tws.connected:
                print("gateway connection lost — backing off then exiting so Docker reconnects clean", flush=True)
                try: tws.disconnect()
                except Exception: pass
                _time.sleep(RECONNECT_BACKOFF_S)
                raise SystemExit(1)

            # Heartbeat: connected but no bars for a while during market hours is a
            # silent stall the self-heal won't catch -> warn once via Telegram.
            now = pd.Timestamp.now(tz="UTC")
            if not _market_open(now):
                # silence only counts while the market is open, so the first
                # minutes after a reopen don't read as a 30-min stall
                hb["last"] = _time.time()
            if not dry and _market_open(now) and _time.time() - hb["last"] > SILENCE_WARN_S:
                if not hb["warned"]:
                    try:
                        tg.send(f"scanner: no XAUUSD bars for >{SILENCE_WARN_S // 60} min "
                                f"during market hours — check the feed")
                    except Exception:
                        pass
                    hb["warned"] = True
            else:
                hb["warned"] = False

            if dry:
                _time.sleep(2)
                continue
            try:
                msgs, offset = tg.poll(offset)
            except Exception as e:
                # a Telegram/network hiccup must not kill the feed (it used to:
                # "connection reset by peer" -> crash -> restart gap). offset is
                # unchanged, so unread replies are fetched again next round.
                print(f"telegram poll failed ({e!r}); retrying", flush=True)
                _time.sleep(5)
                msgs = []
            for text in msgs:
                r = parse_reply(text)
                if not r:
                    continue
                with lock:
                    row = con.execute("SELECT alert_ts_utc FROM alerts WHERE alert_id=?",
                                      (r["alert_id"],)).fetchone()
                    if not row:
                        _send_quietly(tg, f"unknown alert_id {r['alert_id']}"); continue
                    record_decision(con, r["alert_id"], r["decision"], pd.Timestamp.now(tz="UTC"),
                                    row[0], r.get("direction"), r.get("entry"), r.get("stop"),
                                    r.get("target"), r.get("reasoning"))
                    _send_quietly(tg, f"logged {r['decision']} for {r['alert_id']}")
            if _time.time() - last_sweep > 300:      # every 5 min
                with lock:
                    now = pd.Timestamp.now(tz="UTC")
                    sweep_auto_missed(con, now)
                    # counterfactuals need the future bars, so fill from the
                    # accumulated live frame once each alert's 4h window elapses
                    sweep_counterfactuals(con, live.hist.sort_index(), now)
                last_sweep = _time.time()
    except KeyboardInterrupt:
        print("\nstopping."); tws.disconnect()


# ── Self-test: no look-ahead + first-touch dedup (from the notebook) ────────
def selftest():
    df = load_data()
    fails = []
    # 1) no look-ahead: corrupt FUTURE bars, prior-derived levels must not change
    days = df.groupby(df.index.normalize()).size()
    full = days[days >= MIN_BARS_FULL_DAY].index
    d0 = [d for d in full if d >= pd.Timestamp("2023-06-15", tz="UTC")][0]
    before = {L["type"]: L["value"] for L in build_levels(df) if L["created_day"] == d0}
    df2 = df.copy(); df2.loc[df2.index >= d0, "high"] += 10000
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
                           level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1))
    insert_alert(con, dict(alert_id="A_new", alert_ts_utc="2025-06-16 03:30:00", level_types="PDL",
                           level_price=1.0, price_at_alert=1.0, session="ASIA", confluence_count=1))
    con.commit()
    n = sweep_auto_missed(con, pd.Timestamp("2025-06-16 04:30:00", tz="UTC"))
    decided = dict(con.execute("SELECT alert_id, decision FROM decisions").fetchall())
    if not (n == 1 and decided.get("A_old") == "MISSED" and "A_new" not in decided):
        fails.append(f"auto-MISSED: n={n} decided={decided}")
    # 8) counterfactual sweep: only alerts with a full 4h forward window get filled
    insert_alert(con, dict(alert_id="A_cf", alert_ts_utc="2025-06-16 00:00:00", level_types="PDH",
                           level_price=100.0, price_at_alert=100.0, session="ASIA", confluence_count=1))
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

    print("SELFTEST:", "ALL PASSED" if not fails else f"FAILED {fails}")
    return not fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="UTC day to replay, e.g. 2025-06-16")
    ap.add_argument("--db", default=None, help="write alerts+counterfactuals to this sqlite file")
    ap.add_argument("--live", action="store_true", help="run live on IBKR + Telegram (needs TWS + env creds)")
    ap.add_argument("--dry-live", action="store_true", help="run live on IBKR but print alerts to terminal (no Telegram)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(0 if selftest() else 1)
    if args.live or args.dry_live:
        run_live(dry=args.dry_live)
        return
    if not args.date:
        ap.error("give --date YYYY-MM-DD, --live, --dry-live, or --selftest")

    con = open_log(args.db) if args.db else None
    out = replay_day(args.date, con)
    if out.empty:
        print(f"{args.date}: 0 alerts")
        return
    show = ["alert_id", "alert_ts_utc", "session", "level_types", "level_price",
            "price_at_alert", "confluence_count", "mfe_up_1h", "mfe_dn_1h", "mfe_up_4h", "mfe_dn_4h"]
    print(f"\n{args.date} — {len(out)} alerts (first-touch-only, {WINDOW_MIN}-min collapse, ${TOL:.2f} tol)\n")
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(out[show].to_string(index=False))


if __name__ == "__main__":
    main()
