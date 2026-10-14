# backend/services/performance_tracker.py
"""
Performance Tracker Service

Tracks Visual Agent performance for evolution analysis (Layer 2: Strategic)
Aggregates outcomes, identifies systematic patterns, prepares data for Meta-Agent Strategist
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict

from config.database import get_database

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """
    Tracks Visual Agent analysis performance

    Responsibilities:
    - Record analysis outcomes (linked to trades)
    - Aggregate performance by pattern, regime, timeframe
    - Identify systematic failures vs random noise
    - Prepare evolution summaries for Meta-Agent Strategist
    """

    def __init__(self):
        self.db = get_database()
        logger.info("✓ PerformanceTracker initialized")

    def record_visual_analysis(
        self,
        symbol: str,
        analysis: Dict,
        policy_version: str
    ) -> int:
        """
        Record Visual Agent analysis in database

        Args:
            symbol: Trading symbol
            analysis: Full analysis dict from Visual Agent
            policy_version: Current policy version (e.g., 'v1.0')

        Returns:
            Analysis ID
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO agent_analyses (
                        symbol,
                        analyst,
                        signal,
                        confidence,
                        reasoning,
                        analysis_data
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    symbol,
                    'visual',
                    analysis.get('signal', 'NEUTRAL'),
                    analysis.get('confidence', 0),
                    analysis.get('reasoning', ''),
                    str(analysis)  # Store full analysis as JSONB
                ))

                analysis_id = cur.fetchone()[0]
                logger.debug(f"✓ Recorded visual analysis #{analysis_id} for {symbol}")
                return analysis_id

        except Exception as e:
            logger.error(f"Error recording visual analysis: {e}")
            return -1

    def link_analysis_to_trade(
        self,
        analysis_id: int,
        trade_id: int,
        outcome: str
    ):
        """
        Link analysis to trade outcome

        Args:
            analysis_id: agent_analyses.id
            trade_id: executed_trades.id
            outcome: 'WIN', 'LOSS', or 'PENDING'
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    UPDATE agent_analyses
                    SET outcome = %s
                    WHERE id = %s
                """, (outcome, analysis_id))

                logger.debug(f"✓ Linked analysis #{analysis_id} to trade #{trade_id}: {outcome}")

        except Exception as e:
            logger.error(f"Error linking analysis to trade: {e}")

    def check_evolution_trigger(self, min_analyses: int = 100) -> bool:
        """
        Check if evolution should be triggered

        Criteria:
        - At least min_analyses completed since last epoch
        - Systematic pattern detected (NOT random noise)

        Args:
            min_analyses: Minimum analyses before evolution

        Returns:
            True if evolution should be triggered
        """
        current_epoch = self._get_current_epoch()

        # Count analyses in current epoch with outcomes
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM agent_analyses aa
                WHERE analyst = 'visual'
                  AND outcome IN ('WIN', 'LOSS')
                  AND timestamp >= (
                      SELECT started_at
                      FROM evolution_epochs
                      WHERE epoch = %s
                  )
            """, (current_epoch,))

            count = cur.fetchone()[0]

        if count < min_analyses:
            logger.debug(f"Evolution trigger: Only {count}/{min_analyses} analyses completed")
            return False

        # Check for systematic failures (placeholder - will be refined)
        # For now, just check if we have enough data
        logger.info(f"✓ Evolution trigger met: {count} analyses completed")
        return True

    def get_evolution_summary(
        self,
        min_analyses: int = 100,
        current_epoch: Optional[int] = None
    ) -> Dict:
        """
        Get performance summary for Meta-Agent Strategist

        Aggregates:
        - Overall win rate, accuracy, confidence calibration
        - Performance by pattern type
        - Performance by market regime
        - Performance by timeframe
        - Top 5 best analyses (high confidence + win)
        - Top 5 worst analyses (high confidence + loss)

        Args:
            min_analyses: Minimum analyses to include
            current_epoch: Optional epoch filter (None = current)

        Returns:
            Summary dict for Strategist prompt
        """
        if current_epoch is None:
            current_epoch = self._get_current_epoch()

        # Get epoch start time
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT started_at
                FROM evolution_epochs
                WHERE epoch = %s
            """, (current_epoch,))

            row = cur.fetchone()
            epoch_start = row[0] if row else datetime.now() - timedelta(days=30)

        # Overall statistics
        overall_stats = self._get_overall_stats(epoch_start)

        # Pattern-specific performance
        pattern_performance = self._get_pattern_performance(epoch_start)

        # Regime-specific performance
        regime_performance = self._get_regime_performance(epoch_start)

        # Timeframe-specific performance
        timeframe_performance = self._get_timeframe_performance(epoch_start)

        # Best and worst analyses
        best_analyses = self._get_best_analyses(epoch_start, limit=5)
        worst_analyses = self._get_worst_analyses(epoch_start, limit=5)

        # Systematic failure analysis
        failure_patterns = self._identify_failure_patterns(pattern_performance, regime_performance)

        return {
            'epoch': current_epoch,
            'analyses_count': overall_stats['total_analyses'],
            'overall': overall_stats,
            'pattern_performance': pattern_performance,
            'regime_performance': regime_performance,
            'timeframe_performance': timeframe_performance,
            'best_analyses': best_analyses,
            'worst_analyses': worst_analyses,
            'failure_patterns': failure_patterns,
            'timestamp': datetime.now().isoformat()
        }

    def _get_overall_stats(self, since: datetime) -> Dict:
        """Get overall performance statistics"""
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                    AVG(confidence) as avg_confidence,
                    AVG(CASE
                        WHEN outcome = 'WIN' THEN confidence
                        WHEN outcome = 'LOSS' THEN 100 - confidence
                        ELSE 50
                    END) as calibration_score
                FROM agent_analyses
                WHERE analyst = 'visual'
                  AND outcome IN ('WIN', 'LOSS')
                  AND timestamp >= %s
            """, (since,))

            row = cur.fetchone()

            if not row or row[0] == 0:
                return {
                    'total_analyses': 0,
                    'win_rate': 0.0,
                    'avg_confidence': 0.0,
                    'calibration_score': 0.0
                }

            total, wins, losses, avg_conf, calib = row

            return {
                'total_analyses': int(total),
                'wins': int(wins or 0),
                'losses': int(losses or 0),
                'win_rate': round((wins or 0) / total * 100, 2),
                'avg_confidence': round(float(avg_conf or 0), 2),
                'calibration_score': round(float(calib or 0), 2)
            }

    def _get_pattern_performance(self, since: datetime) -> Dict:
        """Get performance grouped by pattern type"""
        # This is simplified - in production, parse analysis_data JSONB
        # For now, use placeholder logic
        return {
            'bull_flag': {'attempts': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0},
            'head_shoulders': {'attempts': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0},
            'fvg': {'attempts': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0}
        }

    def _get_regime_performance(self, since: datetime) -> Dict:
        """Get performance grouped by market regime"""
        # Placeholder - would need regime classification in analysis_data
        return {
            'TRENDING_BULL': {'analyses': 0, 'win_rate': 0.0},
            'TRENDING_BEAR': {'analyses': 0, 'win_rate': 0.0},
            'RANGING': {'analyses': 0, 'win_rate': 0.0}
        }

    def _get_timeframe_performance(self, since: datetime) -> Dict:
        """Get performance grouped by timeframe"""
        # Placeholder - would parse from analysis_data
        return {
            '1m': {'analyses': 0, 'win_rate': 0.0},
            '5m': {'analyses': 0, 'win_rate': 0.0},
            '15m': {'analyses': 0, 'win_rate': 0.0},
            '1h': {'analyses': 0, 'win_rate': 0.0},
            '4h': {'analyses': 0, 'win_rate': 0.0}
        }

    def _get_best_analyses(self, since: datetime, limit: int = 5) -> List[Dict]:
        """Get best performing analyses (high confidence + win)"""
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    symbol,
                    signal,
                    confidence,
                    reasoning,
                    timestamp
                FROM agent_analyses
                WHERE analyst = 'visual'
                  AND outcome = 'WIN'
                  AND timestamp >= %s
                ORDER BY confidence DESC
                LIMIT %s
            """, (since, limit))

            rows = cur.fetchall()

            return [
                {
                    'id': row[0],
                    'symbol': row[1],
                    'signal': row[2],
                    'confidence': float(row[3]),
                    'reasoning': row[4],
                    'timestamp': row[5].isoformat() if row[5] else None
                }
                for row in rows
            ]

    def _get_worst_analyses(self, since: datetime, limit: int = 5) -> List[Dict]:
        """Get worst performing analyses (high confidence + loss)"""
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    symbol,
                    signal,
                    confidence,
                    reasoning,
                    timestamp
                FROM agent_analyses
                WHERE analyst = 'visual'
                  AND outcome = 'LOSS'
                  AND timestamp >= %s
                ORDER BY confidence DESC
                LIMIT %s
            """, (since, limit))

            rows = cur.fetchall()

            return [
                {
                    'id': row[0],
                    'symbol': row[1],
                    'signal': row[2],
                    'confidence': float(row[3]),
                    'reasoning': row[4],
                    'timestamp': row[5].isoformat() if row[5] else None
                }
                for row in rows
            ]

    def _identify_failure_patterns(
        self,
        pattern_perf: Dict,
        regime_perf: Dict
    ) -> List[Dict]:
        """
        Identify systematic failure patterns

        Args:
            pattern_perf: Performance by pattern type
            regime_perf: Performance by market regime

        Returns:
            List of identified failure patterns
        """
        failures = []

        # Check for patterns with <40% win rate and >10 attempts
        for pattern, stats in pattern_perf.items():
            if stats['attempts'] >= 10 and stats['win_rate'] < 40:
                failures.append({
                    'type': 'PATTERN_FAILURE',
                    'pattern': pattern,
                    'win_rate': stats['win_rate'],
                    'sample_size': stats['attempts'],
                    'severity': 'HIGH'
                })

        # Check for regime-specific failures
        for regime, stats in regime_perf.items():
            if stats['analyses'] >= 20 and stats['win_rate'] < 35:
                failures.append({
                    'type': 'REGIME_FAILURE',
                    'regime': regime,
                    'win_rate': stats['win_rate'],
                    'sample_size': stats['analyses'],
                    'severity': 'MEDIUM'
                })

        return failures

    def calculate_brier_score(self, since: Optional[datetime] = None) -> float:
        """
        Calculate Brier score for confidence calibration

        Lower is better (0 = perfect calibration)

        Args:
            since: Optional start time (None = last 50 analyses)

        Returns:
            Brier score (0.0 to 1.0)
        """
        with self.db.get_cursor() as cur:
            if since:
                cur.execute("""
                    SELECT confidence, outcome
                    FROM agent_analyses
                    WHERE analyst = 'visual'
                      AND outcome IN ('WIN', 'LOSS')
                      AND timestamp >= %s
                    ORDER BY timestamp DESC
                """, (since,))
            else:
                cur.execute("""
                    SELECT confidence, outcome
                    FROM agent_analyses
                    WHERE analyst = 'visual'
                      AND outcome IN ('WIN', 'LOSS')
                    ORDER BY timestamp DESC
                    LIMIT 50
                """)

            rows = cur.fetchall()

            if not rows:
                return 0.0

            # Calculate Brier score: mean((confidence - outcome)^2)
            scores = []
            for conf, outcome in rows:
                # Convert outcome to binary (1 = WIN, 0 = LOSS)
                actual = 1.0 if outcome == 'WIN' else 0.0
                # Convert confidence to probability (0-100 -> 0-1)
                predicted = float(conf) / 100.0
                # Brier score component
                scores.append((predicted - actual) ** 2)

            return sum(scores) / len(scores)

    def _get_current_epoch(self) -> int:
        """Get current evolution epoch"""
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT epoch
                FROM evolution_epochs
                WHERE ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
            """)

            row = cur.fetchone()
            return row[0] if row else 1


# Singleton instance
_performance_tracker: Optional[PerformanceTracker] = None


def get_performance_tracker() -> PerformanceTracker:
    """
    Get singleton instance of PerformanceTracker

    Returns:
        PerformanceTracker instance
    """
    global _performance_tracker
    if _performance_tracker is None:
        _performance_tracker = PerformanceTracker()
    return _performance_tracker
