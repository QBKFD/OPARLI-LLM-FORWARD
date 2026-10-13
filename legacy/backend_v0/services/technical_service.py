# backend/services/technical_service.py
"""
Technical Analysis Service

The single source of truth for the Technical Analyst's decision.

Both consumers call the SAME function:
  - Production TechnicalAnalystAgent (via LiveDataProvider)
  - Backtests / backtest_agents.py (via HistoricalDataProvider)

This guarantees live and backtest evaluate identical logic — no parallel
re-implementation that can silently drift.

Signal path (unchanged from before, just relocated out of the bus actor):
    provider.get_timeframe_data() → TimeframeCascade.analyze_all_timeframes()
"""

import logging
from datetime import datetime
from typing import Dict, Optional

from services.market_data_provider import MarketDataProvider, DEFAULT_TIMEFRAMES
from services.timeframe_cascade import get_timeframe_cascade

logger = logging.getLogger(__name__)


def _neutral(reason: str) -> Dict:
    return {
        'signal': 'NEUTRAL',
        'confidence': 0.0,
        'lead_timeframe': None,
        'decision_case': 'D',
        'reasoning': reason,
        'entry_timing': {'immediate': False, 'wait_for': None},
        'timeframe_analysis': {},
    }


def run_technical_analysis(
    provider: MarketDataProvider,
    symbol: str,
    as_of: Optional[datetime] = None,
    timeframes=None,
) -> Dict:
    """
    Run the full multi-timeframe technical decision.

    Args:
        provider:   any MarketDataProvider (Live or Historical)
        symbol:     e.g. 'XAUUSD'
        as_of:      evaluation timestamp. None = "latest" (live only).
                    HistoricalDataProvider requires it.
        timeframes: override the default 5 timeframes if desired.

    Returns:
        The cascade analysis dict: {signal, confidence, lead_timeframe,
        decision_case, reasoning, timeframe_analysis, ...}
    """
    timeframes = timeframes or DEFAULT_TIMEFRAMES

    tf_data = provider.get_timeframe_data(symbol, timeframes=timeframes, as_of=as_of)
    if not tf_data:
        return _neutral("No timeframe data available")

    cascade = get_timeframe_cascade()
    analysis = cascade.analyze_all_timeframes(tf_data)
    return analysis
