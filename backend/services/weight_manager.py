# backend/services/weight_manager.py
"""
Weight Manager Service

Manages dynamic agent weights for weighted voting (Layer 1: Tactical Adaptation)
Implements weight adjustment after trade outcomes (Paper A + B integration)
"""

import logging
from typing import Dict, Tuple, Optional
from datetime import datetime

from config.database import get_database

logger = logging.getLogger(__name__)


class WeightManager:
    """
    Manages dynamic agent weights

    Weights adjust after each trade outcome:
    - Win: weight *= 1.05
    - Loss: weight *= 0.95
    - Weights normalized to sum to 1.0

    This provides fast, tactical adaptation to regime changes.
    When regime shifts back, weights naturally recover.
    """

    def __init__(self):
        self.db = get_database()
        self.current_epoch = self._get_current_epoch()
        logger.info(f"✓ WeightManager initialized (epoch: {self.current_epoch})")

    def get_current_weights(self) -> Dict[str, float]:
        """
        Get current agent weights for Meta-Agent weighted voting

        Returns:
            Dict mapping analyst name to weight:
            {
                'visual': 0.35,
                'technical': 0.40,
                'sentiment': 0.25
            }
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    SELECT visual_weight, technical_weight, sentiment_weight, epoch
                    FROM agent_weights
                    WHERE is_active = TRUE
                    ORDER BY created_at DESC
                    LIMIT 1
                """)

                row = cur.fetchone()
        except Exception as e:
            # Same outcome as "no row": fall back to defaults rather than
            # taking the whole decision path down with a DB error.
            logger.warning(f"Could not read weights from database ({e}), using defaults")
            return self._get_default_weights()

        if not row:
            logger.warning("No weights found in database, using defaults")
            return self._get_default_weights()

        # Update cached epoch
        self.current_epoch = row['epoch']

        return {
            'visual': float(row['visual_weight']),
            'technical': float(row['technical_weight']),
            'sentiment': float(row['sentiment_weight'])
        }

    def update_weight_after_trade(
        self,
        analyst: str,
        is_win: bool,
        trade_id: Optional[int] = None
    ) -> Dict[str, float]:
        """
        Update agent weight after trade outcome

        Args:
            analyst: 'visual', 'technical', or 'sentiment'
            is_win: True if trade was profitable, False if loss
            trade_id: Optional reference to executed_trades.id

        Returns:
            Updated weights dict
        """
        if analyst not in ['visual', 'technical', 'sentiment']:
            logger.error(f"Invalid analyst: {analyst}")
            return self.get_current_weights()

        try:
            with self.db.get_cursor() as cur:
                # Call database function to update weight
                cur.execute("""
                    SELECT update_weight_after_trade(%s, %s, %s, %s)
                """, (analyst, is_win, trade_id, self.current_epoch))

            # Get updated weights
            new_weights = self.get_current_weights()

            action = "WIN" if is_win else "LOSS"
            logger.info(f"✓ Updated {analyst} weight after {action}: {new_weights[analyst]:.4f}")

            return new_weights

        except Exception as e:
            logger.error(f"Error updating weight for {analyst}: {e}")
            return self.get_current_weights()

    def update_all_weights_after_trade(
        self,
        analyst_signals: Dict[str, str],
        trade_outcome: str,
        trade_id: Optional[int] = None
    ) -> Dict[str, float]:
        """
        Update all analyst weights based on trade outcome

        Args:
            analyst_signals: Dict of analyst signals that led to trade
                {'visual': 'BUY', 'technical': 'BUY', 'sentiment': 'NEUTRAL'}
            trade_outcome: 'WIN' or 'LOSS'
            trade_id: Optional reference to executed_trades.id

        Returns:
            Updated weights dict
        """
        if trade_outcome not in ['WIN', 'LOSS']:
            logger.error(f"Invalid trade outcome: {trade_outcome}")
            return self.get_current_weights()

        is_win = (trade_outcome == 'WIN')

        # Determine final signal from analyst_signals
        # Assuming trade was BUY or SELL based on majority
        buy_count = sum(1 for sig in analyst_signals.values() if sig == 'BUY')
        sell_count = sum(1 for sig in analyst_signals.values() if sig == 'SELL')

        final_signal = 'BUY' if buy_count > sell_count else 'SELL'

        # Update weights for analysts who agreed with winning/losing signal
        for analyst, signal in analyst_signals.items():
            # Analyst was correct if their signal matched final signal and trade won
            # OR their signal was NEUTRAL/opposite and trade lost
            analyst_correct = (
                (signal == final_signal and is_win) or
                (signal != final_signal and not is_win and signal != 'NEUTRAL')
            )

            # Skip NEUTRAL analysts (they didn't contribute to decision)
            if signal == 'NEUTRAL':
                continue

            self.update_weight_after_trade(analyst, analyst_correct, trade_id)

        return self.get_current_weights()

    def get_weight_history(
        self,
        analyst: Optional[str] = None,
        limit: int = 100
    ) -> list:
        """
        Get weight change history

        Args:
            analyst: Optional filter by analyst name
            limit: Maximum number of records to return

        Returns:
            List of weight change records
        """
        with self.db.get_cursor() as cur:
            if analyst:
                cur.execute("""
                    SELECT
                        timestamp,
                        analyst,
                        weight_before,
                        weight_after,
                        weight_delta,
                        trigger_type,
                        trade_id,
                        epoch
                    FROM weight_history
                    WHERE analyst = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (analyst, limit))
            else:
                cur.execute("""
                    SELECT
                        timestamp,
                        analyst,
                        weight_before,
                        weight_after,
                        weight_delta,
                        trigger_type,
                        trade_id,
                        epoch
                    FROM weight_history
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (limit,))

            rows = cur.fetchall()

            return [
                {
                    'timestamp': row[0].isoformat() if row[0] else None,
                    'analyst': row[1],
                    'weight_before': float(row[2]),
                    'weight_after': float(row[3]),
                    'weight_delta': float(row[4]),
                    'trigger_type': row[5],
                    'trade_id': row[6],
                    'epoch': row[7]
                }
                for row in rows
            ]

    def reset_weights_for_new_epoch(
        self,
        epoch: int,
        weights: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """
        Reset weights for new evolution epoch

        Called when policy evolution occurs.
        Can optionally set custom weights, otherwise keeps current weights.

        Args:
            epoch: New epoch number
            weights: Optional custom weights dict, or None to keep current

        Returns:
            New weights dict
        """
        if weights is None:
            # Keep current weights (recommended: new policy should keep weights)
            weights = self.get_current_weights()

        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO agent_weights (
                        visual_weight,
                        technical_weight,
                        sentiment_weight,
                        epoch,
                        notes
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (
                    weights['visual'],
                    weights['technical'],
                    weights['sentiment'],
                    epoch,
                    f'Epoch {epoch} started - policy evolution'
                ))

                # Record in history for all analysts
                for analyst in ['visual', 'technical', 'sentiment']:
                    cur.execute("""
                        INSERT INTO weight_history (
                            analyst,
                            weight_before,
                            weight_after,
                            weight_delta,
                            trigger_type,
                            epoch,
                            notes
                        ) VALUES (%s, %s, %s, 0, 'EPOCH_RESET', %s, %s)
                    """, (
                        analyst,
                        weights[analyst],
                        weights[analyst],
                        epoch,
                        f'New epoch started: {epoch}'
                    ))

            self.current_epoch = epoch
            logger.info(f"✓ Weights reset for epoch {epoch}: {weights}")
            return weights

        except Exception as e:
            logger.error(f"Error resetting weights for epoch {epoch}: {e}")
            return self.get_current_weights()

    def _get_current_epoch(self) -> int:
        """Get current evolution epoch from database (defaults to 1)"""
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    SELECT epoch
                    FROM evolution_epochs
                    ORDER BY started_at DESC
                    LIMIT 1
                """)

                row = cur.fetchone()
                return row['epoch'] if row else 1
        except Exception as e:
            logger.warning(f"Could not read evolution epoch ({e}), defaulting to 1")
            return 1

    def _get_default_weights(self) -> Dict[str, float]:
        """Get default weights if database is empty"""
        return {
            'visual': 0.35,
            'technical': 0.40,
            'sentiment': 0.25
        }

    def get_weight_stats(self, analyst: str, window: int = 50) -> Dict:
        """
        Get weight statistics for governance monitoring

        Args:
            analyst: Analyst name
            window: Number of recent changes to analyze

        Returns:
            Statistics dict with min, max, avg, current, velocity
        """
        history = self.get_weight_history(analyst=analyst, limit=window)

        if not history:
            current_weights = self.get_current_weights()
            return {
                'analyst': analyst,
                'current': current_weights.get(analyst, 0),
                'min': current_weights.get(analyst, 0),
                'max': current_weights.get(analyst, 0),
                'avg': current_weights.get(analyst, 0),
                'velocity': 0.0,
                'sample_size': 0
            }

        weights = [h['weight_after'] for h in history]
        deltas = [h['weight_delta'] for h in history]

        return {
            'analyst': analyst,
            'current': weights[0] if weights else 0,
            'min': min(weights),
            'max': max(weights),
            'avg': sum(weights) / len(weights),
            'velocity': sum(deltas) / len(deltas),  # Avg change per trade
            'sample_size': len(history)
        }


# Singleton instance
_weight_manager: Optional[WeightManager] = None


def get_weight_manager() -> WeightManager:
    """
    Get singleton instance of WeightManager

    Returns:
        WeightManager instance
    """
    global _weight_manager
    if _weight_manager is None:
        _weight_manager = WeightManager()
    return _weight_manager
