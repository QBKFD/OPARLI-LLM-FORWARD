# backend/agents/technical_analyst_agent.py
"""
Technical Analyst Agent

Pure rule-based technical analysis across multiple timeframes
NO LLM calls - deterministic, fast, backtestable

Architecture:
1. Load OHLCV data for 5 timeframes (1m, 5m, 15m, 1h, 4h)
2. Calculate indicators for each timeframe
3. Detect regime and confluence per timeframe
4. Apply multi-timeframe cascade logic
5. Optimize entry timing on 1m
6. Return structured signal to Meta-Agent
"""

import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from services.technical_indicators import get_technical_indicators
from services.regime_detector import get_regime_detector
from services.confluence_checker import get_confluence_checker
from services.timeframe_cascade import get_timeframe_cascade
from services.entry_timing import get_entry_timing_optimizer
from services.market_data_provider import LiveDataProvider, MarketDataProvider
from services.technical_service import run_technical_analysis
from config.database import get_database

logger = logging.getLogger(__name__)


class TechnicalAnalystAgent(BaseAgent):
    """
    Technical Analyst Agent - Pure rule-based multi-timeframe analysis

    Key Features:
    - No LLM calls (fully deterministic)
    - Multi-timeframe confluence (1m, 5m, 15m, 1h, 4h)
    - Regime-adaptive thresholds
    - Hierarchical cascade logic
    - 1m entry timing optimization
    - Fast execution (<200ms target)
    """

    TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h']

    def __init__(self, config: Optional[Dict] = None, provider: Optional[MarketDataProvider] = None):
        super().__init__(AgentType.TECHNICAL_ANALYST, config)

        # Initialize services
        self.indicators = get_technical_indicators()
        self.regime_detector = get_regime_detector()
        self.confluence_checker = get_confluence_checker()
        self.cascade_analyzer = get_timeframe_cascade()
        self.entry_optimizer = get_entry_timing_optimizer()

        # Live data source behind the shared MarketDataProvider interface.
        # Backtests/tests inject a HistoricalDataProvider into the same code
        # path (services/technical_service.run_technical_analysis); when they
        # do, this agent never touches the database (db stays None).
        if provider is None:
            self.db = get_database()
            provider = LiveDataProvider(db=self.db)
        else:
            self.db = None
        self.provider = provider

        logger.info("✓ Technical Analyst Agent initialized (rule-based, no LLM)")

    def run(self) -> list:
        """
        Technical Analyst doesn't run on schedule - responds to analysis requests
        """
        return []

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process analysis requests from Scanner Agent

        Args:
            message: Message with analysis request

        Returns:
            Analysis result message to Meta-Agent
        """
        if message.type != MessageType.ANALYSIS_REQUEST:
            return None

        if not self.is_active:
            logger.debug("Technical Analyst is inactive, skipping analysis")
            return None

        try:
            logger.info("📊 Technical Analyst: Running multi-timeframe analysis...")
            start_time = datetime.now()

            # Extract data from message
            symbol = message.data.get('symbol')
            market_data = message.data.get('market_data', {})
            # Evaluation timestamp carried by the request. None = "latest",
            # which is what the live scanner sends; a replay/test sends the bar
            # being evaluated so the historical provider can bound its slice.
            as_of = message.data.get('as_of')

            # Run multi-timeframe analysis via the shared service.
            # SAME call the backtest uses — only the injected provider differs
            # (LiveDataProvider here, HistoricalDataProvider in backtests).
            analysis = run_technical_analysis(self.provider, symbol, as_of=as_of)

            if not analysis.get('timeframe_analysis'):
                logger.warning(f"No timeframe data available for {symbol}")
                return self._create_neutral_analysis(
                    symbol, analysis.get('reasoning', 'No data available')
                )

            # Optimize entry timing if signal is not PASS
            if analysis['signal'] != 'PASS':
                # Get 1m indicators for entry timing
                tf_1m = analysis['timeframe_analysis'].get('1m', {})
                if tf_1m:
                    indicators_1m = tf_1m.get('indicators', {})

                    # Get current bid/ask from market_data if available
                    bid_price = market_data.get('bid')
                    ask_price = market_data.get('ask')

                    entry_timing = self.entry_optimizer.should_enter_now(
                        signal=analysis['signal'],
                        indicators_1m=indicators_1m,
                        current_time=datetime.utcnow(),
                        bid_price=bid_price,
                        ask_price=ask_price
                    )

                    # Update analysis with refined entry timing
                    analysis['entry_timing'] = entry_timing

            # Calculate execution time
            execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(f"✓ Technical analysis complete: {analysis['signal']} "
                       f"(confidence: {analysis['confidence']:.0%}, "
                       f"execution: {execution_time_ms:.0f}ms)")

            # Send analysis result to Meta-Agent
            return self._create_analysis_message(symbol, analysis)

        except Exception as e:
            logger.error(f"Error in Technical Analyst: {e}", exc_info=True)
            return None

    def _load_timeframe_data(self, symbol: str) -> Dict:
        """
        Load OHLCV data for all timeframes from database

        Args:
            symbol: Trading symbol

        Returns:
            Dict mapping timeframe to DataFrame:
            {
                '1m': df_1m,
                '5m': df_5m,
                '15m': df_15m,
                '1h': df_1h,
                '4h': df_4h
            }
        """
        import pandas as pd

        timeframe_data = {}

        # Map timeframes to table names and aggregation periods
        # Currently only 1min data exists, so we aggregate on-the-fly
        table_map = {
            '1m': ('ohlcv_1min', 1),
            '5m': ('ohlcv_1min', 5),
            '15m': ('ohlcv_1min', 15),
            '1h': ('ohlcv_1min', 60),
            '4h': ('ohlcv_1min', 240)
        }

        # Lookback in terms of final candles we want
        lookback_periods = {
            '1m': 100,   # ~1.5 hours
            '5m': 100,   # ~8 hours
            '15m': 100,  # ~1 day
            '1h': 100,   # ~4 days
            '4h': 100    # ~16 days
        }

        try:
            with self.db.get_cursor() as cur:
                for tf in self.TIMEFRAMES:
                    table_name, period = table_map.get(tf, ('ohlcv_1min', 1))
                    lookback = lookback_periods[tf]

                    # Calculate how many 1min bars we need
                    bars_needed = lookback * period

                    # Fetch 1-minute OHLCV data
                    cur.execute(f"""
                        SELECT timestamp, open, high, low, close, volume
                        FROM {table_name}
                        WHERE symbol = %s
                        ORDER BY timestamp DESC
                        LIMIT %s
                    """, (symbol, bars_needed))

                    rows = cur.fetchall()

                    if rows:
                        # Convert RealDictRow to DataFrame
                        # RealDictRow has lowercase keys, so we create from dicts
                        df = pd.DataFrame([dict(row) for row in rows])

                        # Rename columns to expected capitalized format
                        df = df.rename(columns={
                            'timestamp': 'Timestamp',
                            'open': 'Open',
                            'high': 'High',
                            'low': 'Low',
                            'close': 'Close',
                            'volume': 'Volume'
                        })

                        # Convert Decimal to float
                        for col in ['Open', 'High', 'Low', 'Close']:
                            df[col] = df[col].astype(float)
                        df['Volume'] = df['Volume'].astype(int)

                        # Reverse order (oldest first)
                        df = df.iloc[::-1].reset_index(drop=True)

                        # Aggregate if needed (for 5m, 15m, 1h, 4h)
                        if period > 1:
                            df = self._aggregate_ohlcv(df, period)

                        if not df.empty:
                            timeframe_data[tf] = df
                            logger.debug(f"✓ Loaded {len(df)} candles for {symbol} {tf}")
                    else:
                        logger.warning(f"No data for {symbol} {tf}")

        except Exception as e:
            logger.error(f"Error loading timeframe data for {symbol}: {e}")

        return timeframe_data

    def _aggregate_ohlcv(self, df: 'pd.DataFrame', period: int) -> 'pd.DataFrame':
        """
        Aggregate 1-minute OHLCV data to higher timeframe

        Args:
            df: DataFrame with 1-minute data
            period: Number of minutes to aggregate (5, 15, 60, 240)

        Returns:
            Aggregated DataFrame
        """
        import pandas as pd

        # Make a copy to avoid modifying original
        df = df.copy()

        # Ensure Timestamp is datetime
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])

        # Drop any rows with NaT timestamps
        df = df.dropna(subset=['Timestamp'])

        if df.empty:
            return df

        df = df.set_index('Timestamp')

        # Resample to target period (use 'epoch' origin to avoid NaT issues)
        freq = f'{period}min'
        try:
            resampled = df.resample(freq).agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()
        except Exception as e:
            logger.warning(f"Resample failed for {period}min: {e}")
            return pd.DataFrame()

        # Reset index
        resampled = resampled.reset_index()
        return resampled

    def _create_analysis_message(self, symbol: str, analysis: Dict) -> Message:
        """
        Create analysis result message to send to Meta-Agent

        Args:
            symbol: Trading symbol
            analysis: Analysis results from cascade analyzer

        Returns:
            Message for Meta-Agent
        """
        return self.send_message(
            msg_type=MessageType.ANALYSIS_RESULT,
            recipient=AgentType.META_AGENT,
            data={
                'symbol': symbol,
                'analyst': 'technical',
                'signal': analysis['signal'],
                'confidence': analysis['confidence'],
                'analysis': {
                    'lead_timeframe': analysis['lead_timeframe'],
                    'decision_case': analysis['decision_case'],
                    'reasoning': analysis['reasoning'],
                    'entry_timing': analysis['entry_timing'],
                    'timeframe_summary': self._summarize_timeframes(
                        analysis['timeframe_analysis']
                    )
                },
                'full_analysis': analysis  # Full details for debugging
            },
            priority=8  # High priority (technical analysis is fast)
        )

    def _create_neutral_analysis(self, symbol: str, reason: str) -> Message:
        """
        Create neutral analysis when data is unavailable

        Args:
            symbol: Trading symbol
            reason: Reason for neutral signal

        Returns:
            Message for Meta-Agent
        """
        return self.send_message(
            msg_type=MessageType.ANALYSIS_RESULT,
            recipient=AgentType.META_AGENT,
            data={
                'symbol': symbol,
                'analyst': 'technical',
                'signal': 'NEUTRAL',
                'confidence': 0.0,
                'analysis': {
                    'reasoning': reason,
                    'lead_timeframe': None,
                    'decision_case': 'D',
                    'entry_timing': {'enter_now': False, 'reason': 'no_data'}
                }
            },
            priority=8
        )

    def _summarize_timeframes(self, timeframe_analysis: Dict) -> Dict:
        """
        Create summary of timeframe signals for Meta-Agent

        Args:
            timeframe_analysis: Full timeframe analysis dict

        Returns:
            Summarized dict with key info per timeframe
        """
        summary = {}

        for tf, data in timeframe_analysis.items():
            summary[tf] = {
                'signal': data.get('signal'),
                'confidence': data.get('confidence'),
                'regime': data.get('regime'),
                'trend': data.get('trend_direction'),
                'confluence_score': data.get('confluence_score')
            }

        return summary

    def get_latest_analysis(self, symbol: str) -> Optional[Dict]:
        """
        Get latest technical analysis for symbol (for debugging/monitoring)

        Args:
            symbol: Trading symbol

        Returns:
            Latest analysis dict or None
        """
        try:
            timeframe_data = self._load_timeframe_data(symbol)
            if not timeframe_data:
                return None

            analysis = self.cascade_analyzer.analyze_all_timeframes(timeframe_data)
            return analysis

        except Exception as e:
            logger.error(f"Error getting latest analysis for {symbol}: {e}")
            return None
