# backend/services/market_data_provider.py
"""
Market Data Provider

The abstraction that lets the SAME agent logic run against either:
  - LiveDataProvider       → real-time PostgreSQL   (as_of defaults to now)
  - HistoricalDataProvider → a historical DataFrame (as_of required)

Every consumer (agents, backtests) depends on this interface instead of
calling get_database() directly. Swapping live ↔ backtest = swapping the
provider object. Nothing else changes.

The `as_of` parameter is the key: a provider physically cannot return a bar
later than `as_of`, so lookahead bias becomes structurally impossible in
backtests.

Both providers return the identical format the analysis services expect:
    { '1m': df, '5m': df, '15m': df, '1h': df, '4h': df }
where each df has columns Open, High, Low, Close, Volume (oldest first) plus a
Timestamp column.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Timeframe → minutes to aggregate from 1-minute base bars
TIMEFRAME_MINUTES = {
    '1m': 1,
    '5m': 5,
    '15m': 15,
    '1h': 60,
    '4h': 240,
}

DEFAULT_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h']
DEFAULT_LOOKBACK = 100  # final candles wanted per timeframe


def _aggregate_1min(df_1m: pd.DataFrame, minutes: int, lookback: int) -> pd.DataFrame:
    """
    Aggregate 1-minute OHLCV to a higher timeframe and keep the last `lookback`
    candles. Input df_1m must be indexed by a (tz-aware) DatetimeIndex with
    columns Open/High/Low/Close/Volume. Returns oldest-first with a Timestamp
    column — the exact shape the cascade / indicator services expect.
    """
    if df_1m.empty:
        return df_1m

    if minutes == 1:
        out = df_1m.tail(lookback).copy()
    else:
        freq = f'{minutes}min'
        out = (
            df_1m.resample(freq)
            .agg({'Open': 'first', 'High': 'max', 'Low': 'min',
                  'Close': 'last', 'Volume': 'sum'})
            .dropna()
            .tail(lookback)
        )

    out = out.reset_index()
    out = out.rename(columns={out.columns[0]: 'Timestamp'})
    return out


class MarketDataProvider(ABC):
    """Interface every data source implements."""

    @abstractmethod
    def get_timeframe_data(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        as_of: Optional[datetime] = None,
        lookback: int = DEFAULT_LOOKBACK,
    ) -> Dict[str, pd.DataFrame]:
        """Return {timeframe: OHLCV DataFrame} using only data at/before as_of."""
        ...

    @abstractmethod
    def get_price(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[float]:
        """Latest close at/before as_of."""
        ...


class HistoricalDataProvider(MarketDataProvider):
    """
    Serves data from an in-memory 1-minute DataFrame (e.g. loaded from CSV).
    Used by backtests. `as_of` is REQUIRED for every call — a missing as_of
    would mean "give me everything", which is the lookahead trap we're avoiding.
    """

    def __init__(self, df_1min: pd.DataFrame, symbol: str = 'XAUUSD'):
        """
        Args:
            df_1min: 1-minute bars, DatetimeIndex (tz-aware), columns
                     Open/High/Low/Close/Volume.
            symbol:  symbol this frame represents.
        """
        if not isinstance(df_1min.index, pd.DatetimeIndex):
            raise ValueError("HistoricalDataProvider requires a DatetimeIndex")
        self.df = df_1min.sort_index()
        self.symbol = symbol
        logger.info(f"✓ HistoricalDataProvider: {len(self.df):,} bars "
                    f"({self.df.index[0]} → {self.df.index[-1]})")

    def get_timeframe_data(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        as_of: Optional[datetime] = None,
        lookback: int = DEFAULT_LOOKBACK,
    ) -> Dict[str, pd.DataFrame]:
        if as_of is None:
            raise ValueError("HistoricalDataProvider requires as_of (no lookahead)")
        timeframes = timeframes or DEFAULT_TIMEFRAMES

        # Strict causality: only bars strictly before as_of.
        # searchsorted (binary search on the sorted index) finds the cutoff in
        # O(log n); we then slice only the recent bars each timeframe needs,
        # instead of masking + resampling the entire multi-million-row history.
        cutoff = self.df.index.searchsorted(as_of, side='left')  # bars [0:cutoff) are < as_of
        if cutoff == 0:
            return {}

        out: Dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            minutes = TIMEFRAME_MINUTES.get(tf, 1)
            # Enough 1-min bars to build `lookback` candles, + buffer for gaps
            # (weekends/holidays) so the tail(lookback) is never short.
            need = int(lookback * minutes * 1.5) + minutes
            start = max(0, cutoff - need)
            window = self.df.iloc[start:cutoff]
            agg = _aggregate_1min(window, minutes, lookback)
            if not agg.empty:
                out[tf] = agg
        return out

    def get_price(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[float]:
        if as_of is None:
            return float(self.df['Close'].iloc[-1]) if len(self.df) else None
        cutoff = self.df.index.searchsorted(as_of, side='left')
        if cutoff == 0:
            return None
        return float(self.df['Close'].iloc[cutoff - 1])


class LiveDataProvider(MarketDataProvider):
    """
    Serves data from the production PostgreSQL store. Used in live trading.
    `as_of` defaults to now(); when passed, bounds the query with
    `timestamp <= as_of` (useful for replay/debugging).

    Reuses the exact query/aggregation the TechnicalAnalystAgent used to do
    inline — that logic now lives here so every consumer shares it.
    """

    TABLE = 'ohlcv_1min'

    def __init__(self, db=None):
        # Imported lazily so backtests never need a DB connection.
        if db is None:
            from config.database import get_database
            db = get_database()
        self.db = db
        logger.info("✓ LiveDataProvider initialized (PostgreSQL)")

    def get_timeframe_data(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        as_of: Optional[datetime] = None,
        lookback: int = DEFAULT_LOOKBACK,
    ) -> Dict[str, pd.DataFrame]:
        timeframes = timeframes or DEFAULT_TIMEFRAMES
        out: Dict[str, pd.DataFrame] = {}

        try:
            with self.db.get_cursor() as cur:
                for tf in timeframes:
                    minutes = TIMEFRAME_MINUTES.get(tf, 1)
                    bars_needed = lookback * minutes

                    if as_of is None:
                        cur.execute(
                            f"""SELECT timestamp, open, high, low, close, volume
                                FROM {self.TABLE}
                                WHERE symbol = %s
                                ORDER BY timestamp DESC
                                LIMIT %s""",
                            (symbol, bars_needed),
                        )
                    else:
                        cur.execute(
                            f"""SELECT timestamp, open, high, low, close, volume
                                FROM {self.TABLE}
                                WHERE symbol = %s AND timestamp <= %s
                                ORDER BY timestamp DESC
                                LIMIT %s""",
                            (symbol, as_of, bars_needed),
                        )

                    rows = cur.fetchall()
                    if not rows:
                        continue

                    df = pd.DataFrame([dict(r) for r in rows])
                    df = df.rename(columns={
                        'timestamp': 'Timestamp', 'open': 'Open', 'high': 'High',
                        'low': 'Low', 'close': 'Close', 'volume': 'Volume',
                    })
                    for c in ['Open', 'High', 'Low', 'Close']:
                        df[c] = df[c].astype(float)
                    df['Volume'] = df['Volume'].astype(float)

                    # Oldest-first, DatetimeIndex for aggregation
                    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                    df = df.iloc[::-1].set_index('Timestamp')

                    agg = _aggregate_1min(
                        df[['Open', 'High', 'Low', 'Close', 'Volume']], minutes, lookback
                    )
                    if not agg.empty:
                        out[tf] = agg
        except Exception as e:
            logger.error(f"LiveDataProvider query error for {symbol}: {e}")

        return out

    def get_price(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[float]:
        try:
            with self.db.get_cursor() as cur:
                if as_of is None:
                    cur.execute(
                        f"SELECT close FROM {self.TABLE} WHERE symbol = %s "
                        f"ORDER BY timestamp DESC LIMIT 1",
                        (symbol,),
                    )
                else:
                    cur.execute(
                        f"SELECT close FROM {self.TABLE} WHERE symbol = %s "
                        f"AND timestamp <= %s ORDER BY timestamp DESC LIMIT 1",
                        (symbol, as_of),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                return float(row['close'] if isinstance(row, dict) else row[0])
        except Exception as e:
            logger.error(f"LiveDataProvider price error for {symbol}: {e}")
            return None
