"""
Regime Feature Engine

Computes all features required for the Jump Model regime classifier.
Fully vectorized, no forward-looking bias, handles NaN warmup.

Feature categories:
- Volatility (ATR, BB width, Parkinson, realized vol)
- Trend strength (ADX, DI+/DI-, EMA slope)
- Long-memory / persistence (Hurst via DFA, autocorrelation, variance ratio)
- Session (intraday seasonality dummies)

References:
- Nystrup et al. (2021), "Feature selection in jump models",
  Expert Systems with Applications 184:115558
- Barunik & Kristoufek (2012), "On Hurst exponent estimation under
  heavy-tailed distributions", arXiv:1201.4786
"""

import numpy as np
import pandas as pd
from typing import Optional
from zoneinfo import ZoneInfo

from .regime_config import RegimeConfig

_LONDON_TZ = ZoneInfo("Europe/London")


class RegimeFeatureEngine:
    """
    Computes regime classification features from OHLCV data.

    All rolling calculations use only past data (causal).
    Output DataFrame has NaN rows for warmup period — caller must handle.
    """

    def __init__(self, config: Optional[RegimeConfig] = None):
        self.config = config or RegimeConfig()

    def compute(self, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all regime features from OHLCV data.

        Args:
            ohlcv_df: DataFrame with columns [open, high, low, close, volume]
                      and a DatetimeIndex. Column names are case-insensitive.

        Returns:
            DataFrame with all computed features, same index as input.
        """
        df = self._normalize_columns(ohlcv_df)
        features = pd.DataFrame(index=df.index)

        # Returns (needed for multiple features)
        returns = df["close"].pct_change()
        log_returns = np.log(df["close"] / df["close"].shift(1))

        # --- Volatility features ---
        features["atr"] = self._atr(df)
        features["atr_ratio"] = self._atr_ratio(df)
        features["atr_percentile"] = self._rolling_percentile(
            features["atr"], self.config.atr_percentile_window
        )
        features["bb_width"] = self._bb_width(df)
        features["bb_width_percentile"] = self._rolling_percentile(
            features["bb_width"], self.config.bb_percentile_window
        )
        features["parkinson_vol"] = self._parkinson_volatility(df)

        # Realized volatility at multiple horizons
        for window in self.config.rv_windows:
            features[f"rv_{window}"] = self._realized_volatility(log_returns, window)

        # Periodicity-adjusted RV (intraday seasonality normalization)
        features["rv_adjusted"] = self._periodicity_adjusted_rv(log_returns, df.index)

        # Volatility term structure ratios — shape of vol curve across horizons
        # High short/long ratio = microstructure stress, regime transition imminent
        # Low ratio = current regime stable relative to long-run baseline
        rv_short = features["rv_15"]
        rv_mid   = features["rv_60"]
        rv_long  = features["rv_240"]
        features["rv_ratio_short_long"] = rv_short / rv_long.replace(0, np.nan)
        features["rv_ratio_mid_long"]   = rv_mid   / rv_long.replace(0, np.nan)
        features["rv_ratio_short_mid"]  = rv_short / rv_mid.replace(0, np.nan)

        # --- Trend strength features ---
        features["adx"] = self._adx(df)
        features["adx_percentile"] = self._rolling_percentile(
            features["adx"], self.config.adx_percentile_window
        )
        features["di_bias"] = self._di_bias(df, features["atr"])
        features["ema_slope"] = self._ema_slope_normalized(df, features["atr"])
        features["price_vwap_dist"] = self._price_vwap_distance(df, features["atr"])

        # --- Long-memory / persistence features ---
        # DFA Hurst exponent — Barunik & Kristoufek (2012)
        features["hurst_dfa"] = self._hurst_dfa(log_returns)
        features["autocorr_lag1"] = self._rolling_autocorrelation(
            returns, self.config.autocorr_window
        )
        features["variance_ratio_2"] = self._variance_ratio(
            log_returns, lag=2, window=self.config.variance_ratio_window
        )
        features["variance_ratio_5"] = self._variance_ratio(
            log_returns, lag=5, window=self.config.variance_ratio_window
        )

        # --- Session features ---
        session_features = self._session_features(df.index)
        features = pd.concat([features, session_features], axis=1)

        # --- Asian range features ---
        asian_features = self._asian_range_features(df, features["atr"])
        features = pd.concat([features, asian_features], axis=1)

        return features

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to lowercase."""
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        required = ["open", "high", "low", "close"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        return df

    # =========================================================================
    # Volatility Features
    # =========================================================================

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        """Average True Range (Wilder, 1978)."""
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)

        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        return tr.ewm(span=self.config.atr_period, adjust=False).mean()

    def _atr_ratio(self, df: pd.DataFrame) -> pd.Series:
        """ATR / SMA(ATR, 20) — scale-free volatility expansion/contraction."""
        atr = self._atr(df)
        sma_atr = atr.rolling(window=20, min_periods=10).mean()
        return atr / sma_atr

    def _bb_width(self, df: pd.DataFrame) -> pd.Series:
        """Bollinger Band Width = (upper - lower) / middle."""
        close = df["close"]
        sma = close.rolling(window=self.config.bb_period, min_periods=10).mean()
        std = close.rolling(window=self.config.bb_period, min_periods=10).std()
        upper = sma + self.config.bb_std * std
        lower = sma - self.config.bb_std * std
        return (upper - lower) / sma

    def _parkinson_volatility(self, df: pd.DataFrame) -> pd.Series:
        """
        Parkinson (1980) range-based volatility estimator.
        More efficient than close-to-close on intraday data.
        sigma^2 = (1/4*ln2) * E[(ln(H/L))^2]
        """
        log_hl = np.log(df["high"] / df["low"])
        log_hl_sq = log_hl ** 2
        factor = 1.0 / (4.0 * np.log(2.0))
        return np.sqrt(
            factor * log_hl_sq.rolling(
                window=self.config.parkinson_window, min_periods=10
            ).mean()
        )

    def _realized_volatility(self, log_returns: pd.Series, window: int) -> pd.Series:
        """Realized volatility = sqrt(sum of squared log returns) over window."""
        return np.sqrt(
            (log_returns ** 2).rolling(window=window, min_periods=max(5, window // 2)).sum()
        )

    def _periodicity_adjusted_rv(
        self, log_returns: pd.Series, index: pd.DatetimeIndex
    ) -> pd.Series:
        """
        Intraday periodicity-adjusted realized volatility.
        Divides returns by session-average volatility per time-of-day bucket
        to account for the U-shaped intraday vol pattern.
        """
        # Create hour-of-day buckets
        hours = index.hour
        # Compute per-hour average absolute return (expanding, causal)
        abs_ret = log_returns.abs()
        hourly_avg = pd.Series(index=index, dtype=float)

        # Use expanding mean per hour bucket (causal — only past data)
        for h in range(24):
            mask = hours == h
            if mask.any():
                # Expanding mean of abs returns for this hour
                expanding = abs_ret[mask].expanding(min_periods=5).mean()
                hourly_avg.loc[mask] = expanding.values

        # Fill any missing with global expanding mean
        global_avg = abs_ret.expanding(min_periods=5).mean()
        hourly_avg = hourly_avg.fillna(global_avg)

        # Adjusted returns = returns / periodicity factor
        hourly_avg = hourly_avg.replace(0, np.nan).ffill()
        adjusted_returns = log_returns / hourly_avg

        # RV of adjusted returns
        return np.sqrt(
            (adjusted_returns ** 2).rolling(window=60, min_periods=20).sum()
        )

    # =========================================================================
    # Trend Strength Features
    # =========================================================================

    def _adx(self, df: pd.DataFrame) -> pd.Series:
        """Average Directional Index (Wilder, 1978)."""
        high = df["high"]
        low = df["low"]
        prev_high = high.shift(1)
        prev_low = low.shift(1)

        # Directional movement
        plus_dm = np.where(
            (high - prev_high) > (prev_low - low),
            np.maximum(high - prev_high, 0),
            0.0
        )
        minus_dm = np.where(
            (prev_low - low) > (high - prev_high),
            np.maximum(prev_low - low, 0),
            0.0
        )

        plus_dm = pd.Series(plus_dm, index=df.index)
        minus_dm = pd.Series(minus_dm, index=df.index)

        atr = self._atr(df)
        period = self.config.adx_period

        # Smoothed DI
        plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr

        # DX and ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(span=period, adjust=False).mean()

        # Store DI values for later use
        self._last_plus_di = plus_di
        self._last_minus_di = minus_di

        return adx

    def _di_bias(self, df: pd.DataFrame, atr: pd.Series) -> pd.Series:
        """
        Directional bias: (DI+ - DI-) normalized by ATR.
        Positive = bullish trend, negative = bearish trend.
        """
        # Ensure ADX was computed first (it stores DI values)
        if not hasattr(self, "_last_plus_di"):
            self._adx(df)
        return (self._last_plus_di - self._last_minus_di) / 100.0

    def _ema_slope_normalized(self, df: pd.DataFrame, atr: pd.Series) -> pd.Series:
        """EMA(20) slope normalized by ATR — scale-free trend direction."""
        ema = df["close"].ewm(span=self.config.ema_period, adjust=False).mean()
        slope = ema.diff()
        # Normalize by ATR to make it scale-free
        return slope / atr.replace(0, np.nan)

    def _price_vwap_distance(self, df: pd.DataFrame, atr: pd.Series) -> pd.Series:
        """
        Distance of price from session VWAP, normalized by ATR.
        Uses typical price * volume proxy (since volume may be unavailable,
        falls back to typical price accumulation).
        """
        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0

        # If volume available and valid, use it; otherwise use equal-weight
        if "volume" in df.columns:
            vol = df["volume"].replace(-1, np.nan).fillna(1.0).clip(lower=1.0)
        else:
            vol = pd.Series(1.0, index=df.index)

        # Rolling VWAP (50-bar window as intraday approximation)
        cum_vol = vol.rolling(window=50, min_periods=10).sum()
        cum_pv = (typical_price * vol).rolling(window=50, min_periods=10).sum()
        vwap = cum_pv / cum_vol

        return (df["close"] - vwap) / atr.replace(0, np.nan)

    # =========================================================================
    # Long-Memory / Persistence Features
    # =========================================================================

    def _hurst_dfa(self, log_returns: pd.Series) -> pd.Series:
        """
        Hurst exponent via Detrended Fluctuation Analysis (DFA).
        More robust on heavy-tailed intraday data than R/S method.
        Reference: Barunik & Kristoufek (2012), arXiv:1201.4786

        Computed on rolling window (causal).
        """
        window = self.config.hurst_window
        result = pd.Series(np.nan, index=log_returns.index)

        values = log_returns.values
        for i in range(window, len(values)):
            segment = values[i - window:i]
            if np.isnan(segment).any():
                continue
            h = self._dfa_single(segment)
            result.iloc[i] = h

        return result

    @staticmethod
    def _dfa_single(x: np.ndarray) -> float:
        """
        Compute DFA Hurst exponent for a single time series segment.
        H > 0.5: persistent (trending), H < 0.5: anti-persistent (mean-reverting),
        H ≈ 0.5: random walk.
        """
        n = len(x)
        if n < 16:
            return np.nan

        # Cumulative sum (profile)
        y = np.cumsum(x - np.mean(x))

        # Scale sizes (powers of 2 that fit)
        min_scale = 4
        max_scale = n // 4
        if max_scale < min_scale:
            return np.nan

        scales = np.unique(np.logspace(
            np.log2(min_scale), np.log2(max_scale), num=8, base=2
        ).astype(int))
        scales = scales[scales >= min_scale]

        if len(scales) < 3:
            return np.nan

        fluctuations = np.zeros(len(scales))

        for idx, scale in enumerate(scales):
            n_segments = n // scale
            if n_segments == 0:
                fluctuations[idx] = np.nan
                continue

            rms_values = []
            for seg in range(n_segments):
                segment = y[seg * scale:(seg + 1) * scale]
                # Linear detrend
                x_axis = np.arange(scale)
                coeffs = np.polyfit(x_axis, segment, 1)
                trend = np.polyval(coeffs, x_axis)
                residual = segment - trend
                rms_values.append(np.sqrt(np.mean(residual ** 2)))

            fluctuations[idx] = np.mean(rms_values) if rms_values else np.nan

        # Log-log regression
        valid = ~np.isnan(fluctuations) & (fluctuations > 0)
        if valid.sum() < 3:
            return np.nan

        log_scales = np.log(scales[valid].astype(float))
        log_fluct = np.log(fluctuations[valid])

        coeffs = np.polyfit(log_scales, log_fluct, 1)
        return coeffs[0]  # Slope = Hurst exponent

    def _rolling_autocorrelation(
        self, returns: pd.Series, window: int
    ) -> pd.Series:
        """Lag-1 autocorrelation of returns (rolling window)."""
        return returns.rolling(window=window, min_periods=window // 2).apply(
            lambda x: pd.Series(x).autocorr(lag=1) if len(x) > 1 else np.nan,
            raw=False
        )

    def _variance_ratio(
        self, log_returns: pd.Series, lag: int, window: int
    ) -> pd.Series:
        """
        Lo-MacKinlay (1988) Variance Ratio.
        VR(q) = Var(q-period return) / (q * Var(1-period return))
        VR > 1: positive serial correlation (trending)
        VR < 1: mean reversion
        VR = 1: random walk
        """
        result = pd.Series(np.nan, index=log_returns.index)
        values = log_returns.values

        for i in range(window, len(values)):
            segment = values[i - window:i]
            if np.isnan(segment).any():
                continue

            # 1-period variance
            var_1 = np.var(segment, ddof=1)
            if var_1 == 0:
                continue

            # q-period returns
            q_returns = np.array([
                np.sum(segment[j:j + lag])
                for j in range(len(segment) - lag)
            ])

            if len(q_returns) < 2:
                continue

            var_q = np.var(q_returns, ddof=1)
            result.iloc[i] = var_q / (lag * var_1)

        return result

    # =========================================================================
    # Session Features
    # =========================================================================

    def _get_london_open_utc_hour(self, index: pd.DatetimeIndex) -> pd.Series:
        """UTC hour of London 08:00 per bar — 7 during BST, 8 during GMT."""
        import datetime
        unique_dates = sorted(set(index.date))
        date_to_hour = {}
        for d in unique_dates:
            dt_london = datetime.datetime(d.year, d.month, d.day, 8, 0, tzinfo=_LONDON_TZ)
            date_to_hour[d] = dt_london.astimezone(datetime.timezone.utc).hour
        return pd.Series([date_to_hour[d] for d in index.date], index=index)

    def _session_features(self, index: pd.DatetimeIndex) -> pd.DataFrame:
        """
        Binary session dummies, normalized time-in-session,
        and DST-aware London open timing features.
        """
        hours = index.hour
        cfg = self.config

        features = pd.DataFrame(index=index)
        features["session_asian"] = (
            (hours >= cfg.session_asian[0]) & (hours < cfg.session_asian[1])
        ).astype(float)
        features["session_london"] = (
            (hours >= cfg.session_london[0]) & (hours < cfg.session_london[1])
        ).astype(float)
        features["session_overlap"] = (
            (hours >= cfg.session_overlap[0]) & (hours < cfg.session_overlap[1])
        ).astype(float)
        features["session_ny"] = (
            (hours >= cfg.session_ny[0]) & (hours < cfg.session_ny[1])
        ).astype(float)
        features["session_offhours"] = (
            (hours >= cfg.session_ny[1]) | (hours < cfg.session_asian[0])
        ).astype(float)

        # Time since session open (normalized 0-1)
        session_starts = np.where(
            features["session_asian"] == 1, cfg.session_asian[0],
            np.where(
                features["session_london"] == 1, cfg.session_london[0],
                np.where(
                    features["session_overlap"] == 1, cfg.session_overlap[0],
                    np.where(
                        features["session_ny"] == 1, cfg.session_ny[0],
                        cfg.session_ny[1]  # off-hours
                    )
                )
            )
        )
        session_durations = np.where(
            features["session_asian"] == 1, cfg.session_asian[1] - cfg.session_asian[0],
            np.where(
                features["session_london"] == 1, cfg.session_london[1] - cfg.session_london[0],
                np.where(
                    features["session_overlap"] == 1, cfg.session_overlap[1] - cfg.session_overlap[0],
                    np.where(
                        features["session_ny"] == 1, cfg.session_ny[1] - cfg.session_ny[0],
                        3  # off-hours duration placeholder
                    )
                )
            )
        )

        hours_float = hours + index.minute / 60.0
        time_in_session = (hours_float - session_starts) / session_durations
        features["time_in_session"] = np.clip(time_in_session, 0, 1)

        # --- DST-aware London open proximity ---
        london_open_hour = self._get_london_open_utc_hour(index)
        bar_minutes = (index.hour * 60 + index.minute).astype(float)
        london_open_minutes = (london_open_hour * 60).astype(float)
        # Signed minutes from London open; negative = pre-open, positive = post-open
        # Normalized by 480 min (8 hours) → roughly [-1, 1] over a trading day
        signed_minutes = bar_minutes - london_open_minutes
        features["minutes_since_london_open"] = np.clip(signed_minutes, -480, 480) / 480.0

        # Binary flag: London DST active (UTC+1 → open at 07:00 UTC)
        features["london_dst_active"] = (london_open_hour == 7).astype(float)

        return features

    def _asian_range_features(self, df: pd.DataFrame, atr: pd.Series) -> pd.DataFrame:
        """
        Causal Asian session range features.

        During Asian session: running high/low so far this session.
        After Asian session: completed Asian high/low for that calendar day.
        Gives the classifier context on where price stands relative to Asian liquidity.
        """
        features = pd.DataFrame(index=df.index)

        hours = df.index.hour
        asian_end = self.config.session_asian[1]  # 8 UTC
        is_asian = hours < asian_end

        # NaN out non-Asian bars so cummax/cummin only tracks Asian session
        asian_high_only = df["high"].where(is_asian, np.nan)
        asian_low_only = df["low"].where(is_asian, np.nan)

        date_groups = pd.Series(df.index.date, index=df.index)

        # Cumulative high/low within Asian session per day (causal within session)
        asian_high_running = asian_high_only.groupby(date_groups).cummax()
        asian_low_running = asian_low_only.groupby(date_groups).cummin()

        # Forward-fill within each day: post-Asian bars get the final Asian value
        asian_high_final = asian_high_running.groupby(date_groups).ffill()
        asian_low_final = asian_low_running.groupby(date_groups).ffill()

        asian_range = asian_high_final - asian_low_final
        safe_atr = atr.replace(0, np.nan)

        # Distance of current close from Asian extremes (ATR-normalized)
        features["asian_high_dist"] = (df["close"] - asian_high_final) / safe_atr
        features["asian_low_dist"] = (df["close"] - asian_low_final) / safe_atr
        # Asian range size relative to current volatility
        features["asian_range_norm"] = asian_range / safe_atr
        # Rolling percentile of Asian range (how wide was today's Asian vs history)
        features["asian_range_percentile"] = self._rolling_percentile(asian_range, window=20)

        return features

    # =========================================================================
    # Utility
    # =========================================================================

    @staticmethod
    def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
        """Rolling percentile rank (0-1) — scale-free."""
        return series.rolling(window=window, min_periods=window // 2).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1],
            raw=False
        )
