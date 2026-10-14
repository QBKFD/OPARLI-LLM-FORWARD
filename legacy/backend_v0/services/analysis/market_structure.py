# backend/services/analysis/market_structure.py
"""
Market Structure Detection

Detects swing points (HH/HL/LH/LL), order blocks, and fair value gaps.
Pure functions - no database dependencies.
Used by both production agents and backtesting notebook.
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SwingPoint:
    """Swing high or low point"""
    idx: int
    price: float
    timestamp: datetime
    swing_type: str  # 'HH', 'HL', 'LH', 'LL'


@dataclass
class OrderBlock:
    """Order block (last opposing candle before impulsive move)"""
    idx: int
    high: float
    low: float
    timestamp: datetime
    ob_type: str  # 'bullish' or 'bearish'
    mitigated: bool = False


@dataclass
class FairValueGap:
    """Fair Value Gap (3-candle imbalance)"""
    idx: int
    high: float  # Top of gap
    low: float   # Bottom of gap
    timestamp: datetime
    fvg_type: str  # 'bullish' or 'bearish'
    filled: bool = False


class MarketStructureAnalyzer:
    """
    Detects market structure: swing points, order blocks, and FVGs.

    Maintains state across bar-by-bar processing for backtesting,
    or can analyze a full DataFrame at once for production.
    """

    def __init__(self, lookback: int = 5):
        """
        Initialize market structure analyzer.

        Args:
            lookback: Number of bars on each side for swing point detection
        """
        self.lookback = lookback
        self.swing_points: List[SwingPoint] = []
        self.order_blocks: List[OrderBlock] = []
        self.fvgs: List[FairValueGap] = []
        self.trend = 'neutral'  # 'bullish', 'bearish', 'neutral'

    def reset(self):
        """Reset all detected structures"""
        self.swing_points = []
        self.order_blocks = []
        self.fvgs = []
        self.trend = 'neutral'

    def analyze_full(self, df: pd.DataFrame) -> Dict:
        """
        Analyze entire DataFrame at once.

        Args:
            df: DataFrame with Open, High, Low, Close, Timestamp columns

        Returns:
            Dict with all detected structures
        """
        self.reset()

        for idx in range(len(df)):
            self.process_bar(df, idx)

        return self.get_current_state()

    def process_bar(self, df: pd.DataFrame, idx: int):
        """
        Process a single bar, updating detected structures.
        Used for bar-by-bar backtesting (no look-ahead bias).

        Args:
            df: DataFrame with OHLC data
            idx: Current bar index (only data up to idx is used)
        """
        # Detect swing points (with lag to avoid look-ahead)
        if idx >= self.lookback * 2:
            swing = self._detect_swing_point(df, idx - self.lookback)
            if swing:
                self.swing_points.append(swing)
                self._update_trend()

        # Detect order blocks
        ob = self._detect_order_block(df, idx)
        if ob:
            self.order_blocks.append(ob)

        # Detect FVGs
        fvg = self._detect_fvg(df, idx)
        if fvg:
            self.fvgs.append(fvg)

        # Check for mitigated OBs and filled FVGs
        self._update_mitigation_status(df, idx)

    def _detect_swing_point(self, df: pd.DataFrame, idx: int) -> Optional[SwingPoint]:
        """
        Detect swing high or low at given index.

        Swing High: High is higher than N bars before and after
        Swing Low: Low is lower than N bars before and after
        """
        if idx < self.lookback or idx >= len(df) - self.lookback:
            return None

        current_high = df.iloc[idx]['High']
        current_low = df.iloc[idx]['Low']
        timestamp = df.iloc[idx]['Timestamp'] if 'Timestamp' in df.columns else datetime.now()

        # Check swing high
        is_swing_high = all(
            df.iloc[idx - i]['High'] < current_high and df.iloc[idx + i]['High'] < current_high
            for i in range(1, self.lookback + 1)
        )

        # Check swing low
        is_swing_low = all(
            df.iloc[idx - i]['Low'] > current_low and df.iloc[idx + i]['Low'] > current_low
            for i in range(1, self.lookback + 1)
        )

        if is_swing_high:
            # Determine if HH or LH
            prev_highs = [sp for sp in self.swing_points if sp.swing_type in ['HH', 'LH']]
            if not prev_highs or current_high > prev_highs[-1].price:
                swing_type = 'HH'
            else:
                swing_type = 'LH'
            return SwingPoint(idx, current_high, timestamp, swing_type)

        if is_swing_low:
            # Determine if HL or LL
            prev_lows = [sp for sp in self.swing_points if sp.swing_type in ['HL', 'LL']]
            if not prev_lows or current_low > prev_lows[-1].price:
                swing_type = 'HL'
            else:
                swing_type = 'LL'
            return SwingPoint(idx, current_low, timestamp, swing_type)

        return None

    def _detect_order_block(self, df: pd.DataFrame, idx: int) -> Optional[OrderBlock]:
        """
        Detect order block at given index.

        Bullish OB: Bearish candle followed by 2+ bullish impulsive candles
        Bearish OB: Bullish candle followed by 2+ bearish impulsive candles
        """
        if idx < 3:
            return None

        ob_candle = df.iloc[idx - 2]
        impulse1 = df.iloc[idx - 1]
        impulse2 = df.iloc[idx]
        timestamp = ob_candle['Timestamp'] if 'Timestamp' in df.columns else datetime.now()

        # Bullish OB: bearish candle followed by 2 bullish impulsive candles
        ob_is_bearish = ob_candle['Close'] < ob_candle['Open']
        impulse1_bullish = impulse1['Close'] > impulse1['Open']
        impulse2_bullish = impulse2['Close'] > impulse2['Open']

        if ob_is_bearish and impulse1_bullish and impulse2_bullish:
            ob_range = ob_candle['High'] - ob_candle['Low']
            move = impulse2['Close'] - ob_candle['Low']
            if move > 2 * ob_range:  # Impulsive move requirement
                return OrderBlock(
                    idx - 2, ob_candle['High'], ob_candle['Low'],
                    timestamp, 'bullish'
                )

        # Bearish OB: bullish candle followed by 2 bearish impulsive candles
        ob_is_bullish = ob_candle['Close'] > ob_candle['Open']
        impulse1_bearish = impulse1['Close'] < impulse1['Open']
        impulse2_bearish = impulse2['Close'] < impulse2['Open']

        if ob_is_bullish and impulse1_bearish and impulse2_bearish:
            ob_range = ob_candle['High'] - ob_candle['Low']
            move = ob_candle['High'] - impulse2['Close']
            if move > 2 * ob_range:
                return OrderBlock(
                    idx - 2, ob_candle['High'], ob_candle['Low'],
                    timestamp, 'bearish'
                )

        return None

    def _detect_fvg(self, df: pd.DataFrame, idx: int) -> Optional[FairValueGap]:
        """
        Detect Fair Value Gap at given index.

        Bullish FVG: Gap between candle 1 high and candle 3 low
        Bearish FVG: Gap between candle 1 low and candle 3 high
        """
        if idx < 2:
            return None

        candle1 = df.iloc[idx - 2]
        candle2 = df.iloc[idx - 1]
        candle3 = df.iloc[idx]
        timestamp = candle2['Timestamp'] if 'Timestamp' in df.columns else datetime.now()

        # Bullish FVG: candle3.low > candle1.high
        if candle3['Low'] > candle1['High']:
            return FairValueGap(
                idx - 1, candle3['Low'], candle1['High'],
                timestamp, 'bullish'
            )

        # Bearish FVG: candle3.high < candle1.low
        if candle3['High'] < candle1['Low']:
            return FairValueGap(
                idx - 1, candle1['Low'], candle3['High'],
                timestamp, 'bearish'
            )

        return None

    def _update_mitigation_status(self, df: pd.DataFrame, idx: int):
        """Check if any OBs have been mitigated or FVGs filled"""
        current_low = df.iloc[idx]['Low']
        current_high = df.iloc[idx]['High']

        # Check OB mitigation
        for ob in self.order_blocks:
            if not ob.mitigated:
                if ob.ob_type == 'bullish' and current_low <= ob.high:
                    ob.mitigated = True
                elif ob.ob_type == 'bearish' and current_high >= ob.low:
                    ob.mitigated = True

        # Check FVG fill
        for fvg in self.fvgs:
            if not fvg.filled:
                if fvg.fvg_type == 'bullish' and current_low <= fvg.low:
                    fvg.filled = True
                elif fvg.fvg_type == 'bearish' and current_high >= fvg.high:
                    fvg.filled = True

    def _update_trend(self):
        """Update trend based on recent swing points"""
        if len(self.swing_points) < 4:
            self.trend = 'neutral'
            return

        recent = self.swing_points[-4:]
        highs = [sp for sp in recent if sp.swing_type in ['HH', 'LH']]
        lows = [sp for sp in recent if sp.swing_type in ['HL', 'LL']]

        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
                self.trend = 'bullish'
            elif highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
                self.trend = 'bearish'
            else:
                self.trend = 'neutral'

    def get_current_state(self) -> Dict:
        """Get current market structure state"""
        return {
            'trend': self.trend,
            'swing_points': self.swing_points,
            'order_blocks': self.order_blocks,
            'fvgs': self.fvgs,
            'active_order_blocks': [ob for ob in self.order_blocks if not ob.mitigated],
            'unfilled_fvgs': [fvg for fvg in self.fvgs if not fvg.filled]
        }

    def get_context_string(self) -> str:
        """Get human-readable market structure context"""
        lines = [f"Trend: {self.trend.upper()}"]

        if self.swing_points:
            swings = self.swing_points[-4:]
            lines.append(f"Recent Swing Points: {', '.join(f'{sp.swing_type}@{sp.price:.2f}' for sp in swings)}")

        active_obs = [ob for ob in self.order_blocks if not ob.mitigated][-3:]
        if active_obs:
            lines.append(f"Active Order Blocks: {', '.join(f'{ob.ob_type}({ob.low:.2f}-{ob.high:.2f})' for ob in active_obs)}")

        active_fvgs = [fvg for fvg in self.fvgs if not fvg.filled][-3:]
        if active_fvgs:
            lines.append(f"Unfilled FVGs: {', '.join(f'{fvg.fvg_type}({fvg.low:.2f}-{fvg.high:.2f})' for fvg in active_fvgs)}")

        return '\n'.join(lines)

    def get_support_resistance(self, current_price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        Get nearest support and resistance levels from swing points.

        Args:
            current_price: Current price

        Returns:
            Tuple of (support, resistance) prices
        """
        support = None
        resistance = None

        # Support from swing lows
        for sp in reversed(self.swing_points):
            if sp.swing_type in ['HL', 'LL'] and sp.price < current_price:
                support = sp.price
                break

        # Resistance from swing highs
        for sp in reversed(self.swing_points):
            if sp.swing_type in ['HH', 'LH'] and sp.price > current_price:
                resistance = sp.price
                break

        return support, resistance

    def get_atr(self, df: pd.DataFrame, idx: int, period: int = 14) -> float:
        """
        Calculate ATR (Average True Range).

        Args:
            df: DataFrame with OHLC data
            idx: Current bar index
            period: ATR period

        Returns:
            ATR value
        """
        if idx < period:
            # Not enough data, use simple range
            data = df.iloc[:idx+1]
            return (data['High'] - data['Low']).mean() if len(data) > 0 else 5.0

        data = df.iloc[idx-period:idx+1]
        tr = data['High'] - data['Low']
        return tr.mean()


# Singleton instance
_market_structure_analyzer: Optional[MarketStructureAnalyzer] = None


def get_market_structure_analyzer(lookback: int = 5) -> MarketStructureAnalyzer:
    """
    Get singleton instance of MarketStructureAnalyzer.

    Args:
        lookback: Swing point detection lookback

    Returns:
        MarketStructureAnalyzer instance
    """
    global _market_structure_analyzer
    if _market_structure_analyzer is None:
        _market_structure_analyzer = MarketStructureAnalyzer(lookback)
    return _market_structure_analyzer


def create_market_structure_analyzer(lookback: int = 5) -> MarketStructureAnalyzer:
    """
    Create a new MarketStructureAnalyzer instance (for backtesting).

    Use this instead of get_market_structure_analyzer() when you need
    a fresh instance (e.g., for each backtest run).

    Args:
        lookback: Swing point detection lookback

    Returns:
        New MarketStructureAnalyzer instance
    """
    return MarketStructureAnalyzer(lookback)
