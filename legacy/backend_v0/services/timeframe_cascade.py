# backend/services/timeframe_cascade.py
"""
Multi-Timeframe Cascade Logic

Hierarchical analysis across 5 timeframes (1m, 5m, 15m, 1h, 4h)
Higher timeframes lead, lower timeframes confirm or veto
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from services.technical_indicators import get_technical_indicators
from services.regime_detector import get_regime_detector
from services.confluence_checker import get_confluence_checker

logger = logging.getLogger(__name__)


class TimeframeCascade:
    """
    Multi-Timeframe Cascade Analyzer

    Decision Framework:
    - Case A: 4h/1h lead LONG, no strong opposition → LONG
    - Case B: 4h/1h lead SHORT, no strong opposition → SHORT
    - Case C: 4h/1h neutral, but 15m/5m show strong confluence → Conditional signal
    - Case D: All timeframes neutral or conflicting → PASS

    Hierarchical Priority:
    4h > 1h > 15m > 5m > 1m
    """

    TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h']
    TIMEFRAME_PRIORITY = {
        '4h': 5,
        '1h': 4,
        '15m': 3,
        '5m': 2,
        '1m': 1
    }

    # Confidence gates. These are INCLUSIVE minimums ("at least this confident")
    # and must be compared with >=, not >.
    #
    # ConfluenceChecker emits confidence from a discrete ladder — 3 agreeing
    # indicators is exactly 0.60, 4 is 0.75, 5 is 0.90 — and these gate values
    # are drawn from that same ladder. Comparing with a strict > therefore did
    # not mean "more confident than 0.60", it meant "not a 3-indicator signal",
    # silently discarding the most common valid signal there is. On 2023 XAUUSD
    # that was the difference between 5 signals per 2000 evaluations and 127.
    #
    # Note this is a property of comparing against a value the ladder can land
    # on, not of the ladder being coarse: making confidence continuous does NOT
    # fix it (measured — a continuous ladder still puts a clean 3-indicator
    # signal at exactly 0.60 and reproduces the baseline 5/2000 exactly).
    LEAD_TF_MIN_CONFIDENCE = 0.60    # 4h/1h to lead a Case A/B signal
    OPPOSITION_MIN_CONFIDENCE = 0.70 # lower TF conviction needed to count as opposition
    MID_TF_MIN_CONFIDENCE = 0.75     # 15m/5m to carry a Case C signal alone

    def __init__(self):
        self.indicators = get_technical_indicators()
        self.regime_detector = get_regime_detector()
        self.confluence_checker = get_confluence_checker()

    def analyze_all_timeframes(
        self,
        timeframe_data: Dict[str, 'pd.DataFrame']
    ) -> Dict:
        """
        Analyze all timeframes and apply cascade logic

        Args:
            timeframe_data: Dict mapping timeframe to OHLCV DataFrame
                {
                    '1m': df_1m,
                    '5m': df_5m,
                    '15m': df_15m,
                    '1h': df_1h,
                    '4h': df_4h
                }

        Returns:
            Dict with final signal:
            {
                'signal': 'LONG|SHORT|PASS',
                'confidence': 0.6-0.9,
                'lead_timeframe': '4h',
                'timeframe_analysis': {
                    '1m': {...},
                    '5m': {...},
                    ...
                },
                'decision_case': 'A|B|C|D',
                'reasoning': 'Human-readable explanation',
                'entry_timing': {
                    'immediate': True/False,
                    'wait_for': 'pullback_to_support'
                }
            }
        """

        # Step 1: Analyze each timeframe independently
        timeframe_analysis = {}

        for tf in self.TIMEFRAMES:
            if tf not in timeframe_data or timeframe_data[tf] is None:
                logger.warning(f"Missing data for {tf} timeframe, skipping")
                continue

            analysis = self._analyze_single_timeframe(tf, timeframe_data[tf])
            timeframe_analysis[tf] = analysis

        # Step 2: Apply cascade logic
        final_signal = self._apply_cascade_logic(timeframe_analysis)

        return final_signal

    def _analyze_single_timeframe(
        self,
        timeframe: str,
        df: 'pd.DataFrame'
    ) -> Dict:
        """
        Analyze a single timeframe

        Args:
            timeframe: '1m', '5m', '15m', '1h', or '4h'
            df: OHLCV DataFrame

        Returns:
            Dict with timeframe analysis
        """

        # Calculate technical indicators
        indicators = self.indicators.calculate_all_indicators(df)

        # Detect market regime
        regime = self.regime_detector.detect_regime(indicators, df)

        # Get adaptive thresholds based on regime
        thresholds = self.regime_detector.get_adaptive_thresholds(
            regime['regime'],
            regime['volatility'],
            indicators['atr']
        )

        # Check indicator confluence
        confluence = self.confluence_checker.check_confluence(
            indicators,
            regime,
            thresholds
        )

        return {
            'timeframe': timeframe,
            'signal': confluence['signal'],
            'confidence': confluence['confidence'],
            'confluence_score': confluence['confluence_score'],
            'regime': regime['regime'],
            'trend_direction': regime['trend_direction'],
            'volatility': regime['volatility'],
            'indicators': indicators,
            'confluence_details': confluence,
            'timestamp': datetime.now().isoformat()
        }

    def _apply_cascade_logic(
        self,
        timeframe_analysis: Dict[str, Dict]
    ) -> Dict:
        """
        Apply hierarchical cascade logic to determine final signal

        Args:
            timeframe_analysis: Dict of timeframe analyses

        Returns:
            Final signal dict
        """

        # Extract signals for each timeframe
        tf_4h = timeframe_analysis.get('4h', {})
        tf_1h = timeframe_analysis.get('1h', {})
        tf_15m = timeframe_analysis.get('15m', {})
        tf_5m = timeframe_analysis.get('5m', {})
        tf_1m = timeframe_analysis.get('1m', {})

        # Case A: 4h or 1h lead LONG, no strong opposition
        if self._check_case_a(tf_4h, tf_1h, [tf_15m, tf_5m]):
            return self._build_signal_response(
                'LONG',
                tf_4h if tf_4h.get('signal') == 'LONG' else tf_1h,
                timeframe_analysis,
                'A',
                'Higher timeframe (4h/1h) shows LONG signal with no strong opposition'
            )

        # Case B: 4h or 1h lead SHORT, no strong opposition
        if self._check_case_b(tf_4h, tf_1h, [tf_15m, tf_5m]):
            return self._build_signal_response(
                'SHORT',
                tf_4h if tf_4h.get('signal') == 'SHORT' else tf_1h,
                timeframe_analysis,
                'B',
                'Higher timeframe (4h/1h) shows SHORT signal with no strong opposition'
            )

        # Case C: 4h/1h neutral, but 15m/5m show strong confluence
        case_c_result = self._check_case_c(tf_4h, tf_1h, tf_15m, tf_5m)
        if case_c_result:
            signal, lead_tf = case_c_result
            return self._build_signal_response(
                signal,
                lead_tf,
                timeframe_analysis,
                'C',
                f'Mid-timeframes ({lead_tf["timeframe"]}) show strong confluence, higher TFs neutral'
            )

        # Case D: No clear signal, pass
        return self._build_signal_response(
            'PASS',
            None,
            timeframe_analysis,
            'D',
            'No clear confluence across timeframes or conflicting signals'
        )

    def _check_case_a(
        self,
        tf_4h: Dict,
        tf_1h: Dict,
        lower_tfs: List[Dict]
    ) -> bool:
        """
        Check Case A: Higher TF leads LONG, no strong opposition

        Returns:
            True if Case A applies
        """
        # Check if 4h or 1h shows LONG with confidence of at least 60%
        has_long_signal = (
            (tf_4h.get('signal') == 'LONG' and tf_4h.get('confidence', 0) >= self.LEAD_TF_MIN_CONFIDENCE) or
            (tf_1h.get('signal') == 'LONG' and tf_1h.get('confidence', 0) >= self.LEAD_TF_MIN_CONFIDENCE)
        )

        if not has_long_signal:
            return False

        # Check for strong opposition (2+ lower TFs with SHORT at >=70%)
        opposition_count = sum(
            1 for tf in lower_tfs
            if tf.get('signal') == 'SHORT' and tf.get('confidence', 0) >= self.OPPOSITION_MIN_CONFIDENCE
        )

        return opposition_count < 2

    def _check_case_b(
        self,
        tf_4h: Dict,
        tf_1h: Dict,
        lower_tfs: List[Dict]
    ) -> bool:
        """
        Check Case B: Higher TF leads SHORT, no strong opposition

        Returns:
            True if Case B applies
        """
        # Check if 4h or 1h shows SHORT with confidence of at least 60%
        has_short_signal = (
            (tf_4h.get('signal') == 'SHORT' and tf_4h.get('confidence', 0) >= self.LEAD_TF_MIN_CONFIDENCE) or
            (tf_1h.get('signal') == 'SHORT' and tf_1h.get('confidence', 0) >= self.LEAD_TF_MIN_CONFIDENCE)
        )

        if not has_short_signal:
            return False

        # Check for strong opposition
        opposition_count = sum(
            1 for tf in lower_tfs
            if tf.get('signal') == 'LONG' and tf.get('confidence', 0) >= self.OPPOSITION_MIN_CONFIDENCE
        )

        return opposition_count < 2

    def _check_case_c(
        self,
        tf_4h: Dict,
        tf_1h: Dict,
        tf_15m: Dict,
        tf_5m: Dict
    ) -> Optional[Tuple[str, Dict]]:
        """
        Check Case C: Higher TFs neutral, mid TFs show strong confluence

        Returns:
            Tuple of (signal, lead_tf) if Case C applies, None otherwise
        """
        # Check if 4h and 1h are neutral or weak signals. Complement of the
        # Case A/B lead gate, so exactly one of the two branches can apply.
        higher_tf_neutral = (
            tf_4h.get('signal') in ['PASS', 'NEUTRAL'] or tf_4h.get('confidence', 0) < self.LEAD_TF_MIN_CONFIDENCE
        ) and (
            tf_1h.get('signal') in ['PASS', 'NEUTRAL'] or tf_1h.get('confidence', 0) < self.LEAD_TF_MIN_CONFIDENCE
        )

        if not higher_tf_neutral:
            return None

        # Check if 15m or 5m show strong confluence (at least 0.75 confidence)
        if tf_15m.get('signal') in ['LONG', 'SHORT'] and tf_15m.get('confidence', 0) >= self.MID_TF_MIN_CONFIDENCE:
            return tf_15m['signal'], tf_15m

        if tf_5m.get('signal') in ['LONG', 'SHORT'] and tf_5m.get('confidence', 0) >= self.MID_TF_MIN_CONFIDENCE:
            return tf_5m['signal'], tf_5m

        return None

    def _build_signal_response(
        self,
        signal: str,
        lead_tf: Optional[Dict],
        timeframe_analysis: Dict,
        decision_case: str,
        reasoning: str
    ) -> Dict:
        """
        Build final signal response

        Args:
            signal: 'LONG', 'SHORT', or 'PASS'
            lead_tf: Leading timeframe analysis dict
            timeframe_analysis: All timeframe analyses
            decision_case: 'A', 'B', 'C', or 'D'
            reasoning: Human-readable explanation

        Returns:
            Final signal dict
        """

        # Calculate final confidence
        if signal == 'PASS':
            final_confidence = 0.0
            lead_timeframe = None
        else:
            final_confidence = lead_tf.get('confidence', 0.6) if lead_tf else 0.6
            lead_timeframe = lead_tf.get('timeframe') if lead_tf else None

            # Adjust confidence based on supporting timeframes
            supporting_count = sum(
                1 for tf_data in timeframe_analysis.values()
                if tf_data.get('signal') == signal and tf_data.get('confidence', 0) > 0.60
            )

            if supporting_count >= 4:
                final_confidence = min(0.90, final_confidence + 0.10)
            elif supporting_count >= 3:
                final_confidence = min(0.85, final_confidence + 0.05)

        # Determine entry timing (will be used by entry timing service)
        entry_timing = self._determine_entry_timing(signal, timeframe_analysis)

        return {
            'signal': signal,
            'confidence': round(final_confidence, 2),
            'lead_timeframe': lead_timeframe,
            'timeframe_analysis': timeframe_analysis,
            'decision_case': decision_case,
            'reasoning': reasoning,
            'entry_timing': entry_timing,
            'timestamp': datetime.now().isoformat()
        }

    def _determine_entry_timing(
        self,
        signal: str,
        timeframe_analysis: Dict
    ) -> Dict:
        """
        Determine if entry should be immediate or wait for pullback

        Args:
            signal: 'LONG' or 'SHORT'
            timeframe_analysis: All timeframe analyses

        Returns:
            Dict with entry timing guidance
        """
        if signal == 'PASS':
            return {'immediate': False, 'wait_for': None}

        # Check 1m timeframe for entry timing
        tf_1m = timeframe_analysis.get('1m', {})

        if not tf_1m:
            return {'immediate': True, 'wait_for': None}

        # Check if 1m aligns with signal
        if tf_1m.get('signal') == signal:
            return {'immediate': True, 'wait_for': None}

        # 1m doesn't align, wait for pullback/bounce
        if signal == 'LONG':
            return {
                'immediate': False,
                'wait_for': 'pullback_to_support',
                'support_level': tf_1m.get('indicators', {}).get('support')
            }
        else:  # SHORT
            return {
                'immediate': False,
                'wait_for': 'bounce_to_resistance',
                'resistance_level': tf_1m.get('indicators', {}).get('resistance')
            }


# Singleton instance
_timeframe_cascade: Optional[TimeframeCascade] = None


def get_timeframe_cascade() -> TimeframeCascade:
    """
    Get singleton instance of TimeframeCascade

    Returns:
        TimeframeCascade instance
    """
    global _timeframe_cascade
    if _timeframe_cascade is None:
        _timeframe_cascade = TimeframeCascade()
    return _timeframe_cascade
