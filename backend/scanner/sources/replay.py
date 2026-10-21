"""The parquet replay (--date): one UTC day through a fresh Scanner, with
counterfactuals, optionally into a journal (bar_source 'parquet')."""
from __future__ import annotations

import pandas as pd

from backend.levels.build import build_levels
from backend.levels.touch import Scanner
from backend.scanner.config import PARQUET
from backend.scanner.journal.outcomes import counterfactual
from backend.scanner.journal.schema import start_epoch
from backend.scanner.journal.writes import insert_alert, insert_counterfactual

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
    epoch = start_epoch(con, "parquet") if con is not None else None
    for a in alerts:
        cf = counterfactual(df, a["alert_ts_utc"], a["price_at_alert"])
        if con is not None:
            insert_alert(con, a, epoch=epoch, bar_source="parquet")
            insert_counterfactual(con, a["alert_id"], cf, epoch=epoch)
        rows.append({**a, **{k: v for k, v in cf.items() if k != "alert_id"}})
    if con is not None:
        con.commit()
    return pd.DataFrame(rows)
