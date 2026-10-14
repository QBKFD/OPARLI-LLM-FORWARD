# backend/services/confluence_checker.py
"""
Confluence Checker Service

Checks for agreement between multiple technical indicators
Minimum 3 indicators must agree for a valid signal
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ConfluenceChecker:
    """
    Checks confluence (agreement) between technical indicators

    Minimum Requirement: 3+ indicators must agree for valid signal

    Confidence Scoring:
    - 3 indicators agree: 60% confidence
    - 4 indicators agree: 75% confidence
    - 5+ indicators agree: 90% confidence

    VOTERS is the set of indicators that can express a direction, and it is the
    denominator confluence is measured against. Only directional indicators
    belong here: an indicator that can never return LONG or SHORT does not
    "abstain", it silently lowers every score by occupying a slot. Volume was
    such a slot — see _volume_context.
    """

    VOTERS = ('RSI', 'Bollinger', 'EMA', 'VWAP', 'Support/Resistance')

    @staticmethod
    def check_confluence(
        indicators: Dict,
        regime: Dict,
        thresholds: Dict
    ) -> Dict:
        """
        Check indicator confluence for LONG/SHORT signals

        Args:
            indicators: Dict from TechnicalIndicators.calculate_all_indicators()
            regime: Dict from RegimeDetector.detect_regime()
            thresholds: Dict from RegimeDetector.get_adaptive_thresholds()

        Returns:
            Dict with confluence analysis:
            {
                'signal': 'LONG|SHORT|PASS',
                'confidence': 0.6-0.9,
                'confluence_score': 3-6,
                'indicators_agreeing': ['RSI', 'Bollinger', 'EMA', ...],
                'indicators_neutral': ['VWAP'],
                'indicators_opposing': [],
                'details': {
                    'rsi_signal': 'LONG|SHORT|NEUTRAL',
                    'bollinger_signal': 'LONG|SHORT|NEUTRAL',
                    ...
                }
            }
        """

        # Evaluate each indicator
        rsi_signal = ConfluenceChecker._check_rsi(
            indicators['rsi'],
            thresholds['rsi_oversold'],
            thresholds['rsi_overbought']
        )

        bollinger_signal = ConfluenceChecker._check_bollinger(
            indicators['close'],
            indicators['bb_upper'],
            indicators['bb_middle'],
            indicators['bb_lower'],
            thresholds['bb_lower_pct'],
            thresholds['bb_upper_pct']
        )

        ema_signal = ConfluenceChecker._check_ema(
            indicators['ema_signal'],
            indicators['ema_fast'],
            indicators['ema_slow'],
            thresholds['ema_spread_min']
        )

        vwap_signal = ConfluenceChecker._check_vwap(
            indicators['close'],
            indicators['vwap']
        )

        support_resistance_signal = ConfluenceChecker._check_support_resistance(
            indicators['close'],
            indicators['support'],
            indicators['resistance']
        )

        # Aggregate signals. Volume is deliberately NOT here — it is context,
        # not a vote (see VOTERS and _volume_context).
        signal_votes = {
            'RSI': rsi_signal,
            'Bollinger': bollinger_signal,
            'EMA': ema_signal,
            'VWAP': vwap_signal,
            'Support/Resistance': support_resistance_signal
        }

        # Count votes
        long_votes = [name for name, sig in signal_votes.items() if sig == 'LONG']
        short_votes = [name for name, sig in signal_votes.items() if sig == 'SHORT']
        neutral_votes = [name for name, sig in signal_votes.items() if sig == 'NEUTRAL']

        # Determine final signal
        final_signal, confidence, confluence_score = ConfluenceChecker._determine_signal(
            long_votes, short_votes, neutral_votes
        )

        # Check for strong opposition
        if final_signal == 'LONG' and len(short_votes) >= 2:
            # Strong opposition, reduce confidence
            confidence = max(0.5, confidence - 0.15)
        elif final_signal == 'SHORT' and len(long_votes) >= 2:
            confidence = max(0.5, confidence - 0.15)

        return {
            'signal': final_signal,
            'confidence': round(confidence, 2),
            'confluence_score': confluence_score,
            'max_confluence': len(ConfluenceChecker.VOTERS),
            'indicators_agreeing': long_votes if final_signal == 'LONG' else short_votes,
            'indicators_neutral': neutral_votes,
            'indicators_opposing': short_votes if final_signal == 'LONG' else long_votes,
            'details': signal_votes,
            # Volume is reported as context so it stays visible to downstream
            # consumers and to the scanner's volume-spike trigger, without
            # diluting the directional vote.
            'volume_context': ConfluenceChecker._volume_context(
                indicators.get('volume_ratio', 1.0),
                thresholds.get('volume_threshold', 1.5),
            ),
        }

    @staticmethod
    def _check_rsi(rsi: float, oversold: float, overbought: float) -> str:
        """
        Check RSI signal

        Args:
            rsi: Current RSI value (0-100)
            oversold: Oversold threshold (e.g., 30)
            overbought: Overbought threshold (e.g., 70)

        Returns:
            'LONG', 'SHORT', or 'NEUTRAL'
        """
        if rsi < oversold:
            return 'LONG'  # Oversold, potential bounce
        elif rsi > overbought:
            return 'SHORT'  # Overbought, potential reversal
        else:
            return 'NEUTRAL'

    @staticmethod
    def _check_bollinger(
        close: float,
        bb_upper: float,
        bb_middle: float,
        bb_lower: float,
        lower_pct: float,
        upper_pct: float
    ) -> str:
        """
        Check Bollinger Bands signal

        The thresholds are expressed as a fraction of BAND WIDTH, not of price.

        This previously read `bb_lower * (1 - lower_pct)`, i.e. lower_pct as a
        fraction of price. In the RANGING regime lower_pct is 0.02, which
        demanded price sit 2% of price below the lower band — roughly $40 on
        gold, several band widths away. The indicator never voted.

        Band-relative also makes the regime settings in
        RegimeDetector.get_adaptive_thresholds read consistently, which the
        price-relative reading did not:
          VOLATILE 0.01 -> must be essentially at/through the band
                           ("only extreme extremes")
          RANGING  0.02 -> tight zone at the extreme ("tight entry at extremes")
          TRENDING 0.10 -> fires while still 10% of a band width short of the
                           band ("allow deeper pullbacks")
        so a larger value is a more permissive zone in every regime.

        Args:
            close: Current price
            bb_upper, bb_middle, bb_lower: Bollinger Band levels
            lower_pct: tolerance above the lower band, as a fraction of band
                       width, that still counts as "at the lower band" (LONG)
            upper_pct: tolerance below the upper band, as a fraction of band
                       width, that still counts as "at the upper band" (SHORT)

        Returns:
            'LONG', 'SHORT', or 'NEUTRAL'
        """
        if bb_lower is None or bb_upper is None:
            return 'NEUTRAL'

        band_width = bb_upper - bb_lower
        if band_width <= 0:
            return 'NEUTRAL'

        # Tolerance zone extends INTO the band from each edge, so price at or
        # beyond the band always qualifies and near-misses qualify by degree.
        lower_threshold = bb_lower + (lower_pct * band_width)
        upper_threshold = bb_upper - (upper_pct * band_width)

        if close <= lower_threshold:
            return 'LONG'  # Price at/below lower band
        elif close >= upper_threshold:
            return 'SHORT'  # Price at/above upper band
        else:
            return 'NEUTRAL'

    @staticmethod
    def _check_ema(
        ema_signal: str,
        ema_fast: float,
        ema_slow: float,
        min_spread: float
    ) -> str:
        """
        Check EMA crossover signal

        Args:
            ema_signal: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            ema_fast: Fast EMA value
            ema_slow: Slow EMA value
            min_spread: Minimum spread required (e.g., 0.001 = 0.1%)

        Returns:
            'LONG', 'SHORT', or 'NEUTRAL'
        """
        if ema_fast is None or ema_slow is None:
            return 'NEUTRAL'

        # Check if spread is significant enough
        spread_pct = abs(ema_fast - ema_slow) / ema_slow

        if spread_pct < min_spread:
            return 'NEUTRAL'  # EMAs too close, no clear trend

        if ema_signal == 'BULLISH':
            return 'LONG'
        elif ema_signal == 'BEARISH':
            return 'SHORT'
        else:
            return 'NEUTRAL'

    @staticmethod
    def _check_vwap(close: float, vwap: float) -> str:
        """
        Check VWAP signal

        Args:
            close: Current price
            vwap: Volume Weighted Average Price

        Returns:
            'LONG', 'SHORT', or 'NEUTRAL'
        """
        if vwap is None:
            return 'NEUTRAL'

        # Price above VWAP = bullish, below = bearish
        if close > vwap * 1.001:  # 0.1% above VWAP
            return 'LONG'
        elif close < vwap * 0.999:  # 0.1% below VWAP
            return 'SHORT'
        else:
            return 'NEUTRAL'

    @staticmethod
    def _volume_context(volume_ratio: float, threshold: float) -> Dict:
        """
        Volume as CONTEXT, not as a vote.

        Volume is non-directional: a 3x volume bar says the move is being
        participated in, not which way it resolves. The previous _check_volume
        acknowledged this in its own comment and returned NEUTRAL on both
        branches — but it was still listed as a voter, so it silently subtracted
        one from the achievable confluence on every single bar.

        Turning it into a directional vote (e.g. "high volume + up candle =
        LONG") would be an untested trading claim. This repo already has the
        machinery to test such a claim properly — matched-random controls and
        pre-registered conditions, see notebooks/backtest.ipynb — so the honest
        move is to stop counting volume until it has been through that, rather
        than to invent a rule and wire it straight into live decisions.

        Args:
            volume_ratio: Current volume / Average volume
            threshold: Ratio above which volume is considered significant

        Returns:
            {'volume_ratio': float, 'significant': bool, 'threshold': float}
        """
        return {
            'volume_ratio': round(float(volume_ratio), 2),
            'threshold': float(threshold),
            'significant': float(volume_ratio) >= float(threshold),
        }

    @staticmethod
    def _check_support_resistance(
        close: float,
        support: float,
        resistance: float
    ) -> str:
        """
        Check support/resistance signal

        Args:
            close: Current price
            support: Support level
            resistance: Resistance level

        Returns:
            'LONG', 'SHORT', or 'NEUTRAL'

        Balanced by construction. The previous version checked support first and
        returned LONG whenever price was within 0.5% of support, so on tight
        ranges (common on low timeframes, where 20 bars span well under 1%) price
        was within 0.5% of BOTH levels and resistance never got a look — the
        voter returned LONG on every such bar. Measured on 2023 XAUUSD across the
        five-timeframe cascade that was 32,163 LONG vs 7,036 SHORT (82% LONG),
        even though price was nearer support only 50.7% of the time: a pure
        artifact of check order, not of the levels.

        The fix removes the asymmetry rather than tuning it out: distances use a
        common denominator (close) so equal dollar gaps compare equally, and the
        NEARER level decides. Mirror price about the mid-range and swap
        support<->resistance and the vote flips. A genuine tie, or price near
        neither level, is NEUTRAL.
        """
        if close <= 0:
            return 'NEUTRAL'

        dist_support = abs(close - support) / close
        dist_resistance = abs(close - resistance) / close

        near_support = dist_support < 0.005     # within 0.5% of support
        near_resistance = dist_resistance < 0.005  # within 0.5% of resistance

        if near_support and near_resistance:
            # Range tighter than ~1%: let the nearer level decide; a dead tie
            # carries no directional information, so abstain.
            if dist_support < dist_resistance:
                return 'LONG'
            if dist_resistance < dist_support:
                return 'SHORT'
            return 'NEUTRAL'

        if near_support:
            return 'LONG'   # price at support = potential bounce
        if near_resistance:
            return 'SHORT'  # price at resistance = potential rejection

        return 'NEUTRAL'

    @staticmethod
    def _determine_signal(
        long_votes: List[str],
        short_votes: List[str],
        neutral_votes: List[str]
    ) -> Tuple[str, float, int]:
        """
        Determine final signal from votes

        Args:
            long_votes: List of indicators voting LONG
            short_votes: List of indicators voting SHORT
            neutral_votes: List of indicators voting NEUTRAL

        Returns:
            Tuple of (signal, confidence, confluence_score)
        """
        long_count = len(long_votes)
        short_count = len(short_votes)

        # Minimum 3 indicators must agree
        if long_count >= 3 and long_count > short_count:
            # Confidence based on confluence
            if long_count >= 5:
                confidence = 0.90
            elif long_count == 4:
                confidence = 0.75
            else:  # long_count == 3
                confidence = 0.60

            return 'LONG', confidence, long_count

        elif short_count >= 3 and short_count > long_count:
            if short_count >= 5:
                confidence = 0.90
            elif short_count == 4:
                confidence = 0.75
            else:  # short_count == 3
                confidence = 0.60

            return 'SHORT', confidence, short_count

        else:
            # No confluence, pass
            return 'PASS', 0.0, max(long_count, short_count)

    @staticmethod
    def check_strong_opposition(
        signal: str,
        opposing_timeframes: List[Dict]
    ) -> bool:
        """
        Check if other timeframes strongly oppose this signal

        Strong opposition = 2+ higher timeframes with opposite signal and confidence >70%

        Args:
            signal: 'LONG' or 'SHORT'
            opposing_timeframes: List of timeframe analysis dicts

        Returns:
            True if strong opposition exists
        """
        if signal not in ['LONG', 'SHORT']:
            return False

        opposite_signal = 'SHORT' if signal == 'LONG' else 'LONG'

        strong_opposition_count = 0

        for tf_data in opposing_timeframes:
            if (tf_data.get('signal') == opposite_signal and
                tf_data.get('confidence', 0) > 0.70):
                strong_opposition_count += 1

        return strong_opposition_count >= 2


# Singleton instance
_confluence_checker: Optional[ConfluenceChecker] = None


def get_confluence_checker() -> ConfluenceChecker:
    """
    Get singleton instance of ConfluenceChecker

    Returns:
        ConfluenceChecker instance
    """
    global _confluence_checker
    if _confluence_checker is None:
        _confluence_checker = ConfluenceChecker()
    return _confluence_checker
