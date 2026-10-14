# backend/agents/scanner_agent_new.py
"""
Scanner Agent (Two-Stage Event-Driven Architecture)

Hybrid event-driven system that balances cost and opportunity detection

ARCHITECTURE:
============
Stage 1: Technical Filter (Cheap, Fast, Rule-Based)
- Cost: $0 (pure Python, no LLM)
- Speed: <100ms
- Purpose: Quick quality check using Technical Analyst
- Filters: confidence ≥0.5, confluence ≥2, price at key level, volume spike, lead TF

Stage 2: Full Multi-Agent Analysis (Expensive, High-Quality)
- Cost: ~$0.035 per analysis
- Speed: ~3-5 seconds
- Purpose: Full analysis with Visual, Sentiment, Meta-Agent
- Only triggered if Stage 1 passes quality check

EVENT-DRIVEN TRIGGERS:
======================
1. Candle close (on 1m, 5m, 15m, 1h, 4h)
2. Price move (±0.3% from last scan)
3. Volume spike (>2× 20-bar average)
4. Volatility change (ATR change >20%)
5. Sentiment event (future: news alerts)
6. Heartbeat (fallback: every 5 minutes)

THROTTLING:
===========
- Minimum 3 minutes between scans (same symbol)
- Maximum 500 scans per day
- Maximum 15 full analyses per hour (Stage 2)

COST OPTIMIZATION:
==================
- Without filtering: ~$300/month (20 scans/hour × ~$0.035)
- With two-stage: ~$100-120/month (8-10 full analyses/hour)
- Savings: ~60% reduction
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
import asyncio

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from agents.technicalanalyst_agent import TechnicalAnalystAgent
from services.trigger_monitor import get_trigger_monitor
from services.key_level_detector import get_key_level_detector
from services.throttle_manager import get_throttle_manager
from services.chart_generator import ChartGenerator, ChartConfig
from config.database import get_database

logger = logging.getLogger(__name__)


class ScannerAgentNew(BaseAgent):
    """
    Scanner Agent - Two-stage event-driven market monitoring

    Stage 1: Technical Filter
    - Fast rule-based quality check
    - Uses Technical Analyst (no LLM, $0 cost)
    - Checks: confidence, confluence, key levels, volume, lead timeframe

    Stage 2: Full Analysis
    - Generate charts for Visual Analyst
    - Orchestrate Visual + Sentiment + Meta-Agent
    - Only runs if Stage 1 passes (~8-10 times/hour vs 20 scans/hour)

    Cost Optimization:
    - Stage 1: $0 × 20 scans/hour = $0
    - Stage 2: $0.035 × 8-10 scans/hour = ~$100-120/month
    """

    # Stage 1 quality criteria
    MIN_CONFIDENCE = 0.5
    MIN_CONFLUENCE = 2
    REQUIRED_VOLUME_SPIKE = 2.0  # 2× average
    ALLOWED_LEAD_TIMEFRAMES = ['15m', '5m']  # Prefer higher timeframes

    # Stage 2 recipients. The scan request goes to the analysts; the Meta-Agent
    # receives their ANALYSIS_RESULTs and aggregates.
    ANALYSTS = (
        AgentType.TECHNICAL_ANALYST,
        AgentType.VISUAL_ANALYST,
        AgentType.SENTIMENT_ANALYST,
    )

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(AgentType.SCANNER, config)

        self.symbols = config.get('symbols', ['XAUUSD']) if config else ['XAUUSD']
        self.db = get_database()

        # Initialize services
        self.trigger_monitor = get_trigger_monitor()
        self.key_level_detector = get_key_level_detector()
        self.throttle_manager = get_throttle_manager()

        # Initialize Technical Analyst (for Stage 1)
        self.technical_analyst = TechnicalAnalystAgent(config)

        # Initialize chart generator (for Stage 2)
        chart_config = self._build_chart_config(config)
        self.chart_generator = ChartGenerator(chart_config)

        # Performance tracking
        self.stats = {
            'total_triggers': 0,
            'stage1_passed': 0,
            'stage2_executed': 0,
            'throttled_scans': 0,
            'throttled_analyses': 0
        }

        logger.info("✓ Scanner Agent (Two-Stage Event-Driven) initialized")
        logger.info(f"  Symbols: {self.symbols}")
        logger.info(f"  Stage 1 Quality: confidence ≥{self.MIN_CONFIDENCE}, confluence ≥{self.MIN_CONFLUENCE}")
        logger.info(f"  Stage 2 Limit: {self.throttle_manager.MAX_FULL_ANALYSES_PER_HOUR} analyses/hour")

    def _build_chart_config(self, config: Optional[Dict]) -> ChartConfig:
        """Build ChartConfig from agent configuration"""
        chart_settings = config.get('chart_settings', {}) if config else {}

        return ChartConfig(
            timeframes=chart_settings.get('timeframes', ['1min', '5min', '15min', '1H']),
            lookback_bars=chart_settings.get('lookback_bars', 200),
            width=chart_settings.get('width', 1920),
            height=chart_settings.get('height', 1080),
            indicators=chart_settings.get('indicators', [
                {'name': 'ema', 'params': {'length': 20}, 'plot': True, 'color': 'blue'},
                {'name': 'ema', 'params': {'length': 50}, 'plot': True, 'color': 'orange'},
                {'name': 'bbands', 'params': {'length': 20, 'std': 2}, 'plot': True, 'color': 'gray'},
                {'name': 'rsi', 'params': {'length': 14}, 'plot': False}
            ]),
            chart_style=chart_settings.get('style', 'charles'),
            volume=chart_settings.get('volume', True),
            save_to_file=chart_settings.get('save_to_file', False),
            output_dir=chart_settings.get('output_dir', '/tmp')
        )

    def run(self) -> List[Message]:
        """
        Event-driven monitoring loop

        Called every second to check for triggers

        Returns:
            List of messages (analysis requests if Stage 2 triggered)
        """
        if not self.is_active:
            return []

        messages = []

        for symbol in self.symbols:
            try:
                # Check if any triggers occurred
                trigger_result = self.trigger_monitor.check_triggers(symbol)

                if trigger_result['triggered']:
                    self.stats['total_triggers'] += 1

                    logger.info(f"🔔 Trigger detected for {symbol}: {trigger_result['reason']}")
                    logger.debug(f"  Triggers: {trigger_result['triggers']}")
                    logger.debug(f"  Priority: {trigger_result['priority']}")

                    # Check throttle before scanning
                    throttle_check = self.throttle_manager.can_scan(symbol)

                    if not throttle_check['allowed']:
                        self.stats['throttled_scans'] += 1
                        logger.debug(f"⏸️  Scan throttled for {symbol}: {throttle_check['reason']} "
                                   f"(wait {throttle_check['wait_seconds']}s)")
                        continue

                    # Run two-stage analysis (one request per analyst)
                    messages.extend(
                        self._run_two_stage_analysis(symbol, trigger_result) or []
                    )

                    # Record scan
                    self.throttle_manager.record_scan(symbol)

            except Exception as e:
                logger.error(f"Error in Scanner Agent run loop for {symbol}: {e}", exc_info=True)

        return messages

    def _run_two_stage_analysis(
        self,
        symbol: str,
        trigger_result: Dict
    ) -> List[Message]:
        """
        Run two-stage analysis

        Stage 1: Technical filter (cheap, fast)
        Stage 2: Full analysis (expensive, high-quality)

        Args:
            symbol: Trading symbol
            trigger_result: Trigger detection result

        Returns:
            One ANALYSIS_REQUEST per analyst if Stage 2 executed, else []
        """
        try:
            # ===================================================
            # STAGE 1: TECHNICAL FILTER (Cheap, Fast, $0 cost)
            # ===================================================
            logger.info(f"📊 Stage 1: Running technical filter for {symbol}...")
            start_time = datetime.now()

            # Get latest technical analysis (no LLM, pure rule-based)
            technical_analysis = self.technical_analyst.get_latest_analysis(symbol)

            if not technical_analysis:
                logger.warning(f"⚠️ Stage 1: No technical analysis available for {symbol}")
                return []

            stage1_time = (datetime.now() - start_time).total_seconds() * 1000

            # Extract Stage 1 quality criteria
            signal = technical_analysis['signal']
            confidence = technical_analysis['confidence']
            lead_timeframe = technical_analysis['lead_timeframe']
            timeframe_analysis = technical_analysis['timeframe_analysis']

            # Get confluence score from lead timeframe
            lead_tf_data = timeframe_analysis.get(lead_timeframe, {}) if lead_timeframe else {}
            confluence_score = lead_tf_data.get('confluence_score', 0)

            # Get 1m indicators for key level check
            tf_1m = timeframe_analysis.get('1m', {})
            indicators_1m = tf_1m.get('indicators', {})
            current_price = indicators_1m.get('close')

            logger.info(f"  Signal: {signal}, Confidence: {confidence:.0%}, "
                       f"Confluence: {confluence_score}, Lead TF: {lead_timeframe}")

            # Check quality criteria
            quality_result = self._check_stage1_quality(
                signal=signal,
                confidence=confidence,
                confluence_score=confluence_score,
                lead_timeframe=lead_timeframe,
                symbol=symbol,
                current_price=current_price,
                indicators_1m=indicators_1m,
                trigger_result=trigger_result
            )

            logger.info(f"✓ Stage 1 complete ({stage1_time:.0f}ms): "
                       f"Quality={'PASS' if quality_result['passed'] else 'FAIL'}")

            if not quality_result['passed']:
                logger.info(f"  ❌ Stage 1 reject: {quality_result['reject_reason']}")
                return []

            # Stage 1 passed!
            self.stats['stage1_passed'] += 1
            logger.info(f"  ✅ Stage 1 passed: {quality_result['pass_reason']}")

            # ===================================================
            # STAGE 2: FULL ANALYSIS (Expensive, $0.035 cost)
            # ===================================================

            # Check throttle for Stage 2
            analysis_throttle = self.throttle_manager.can_run_full_analysis()

            if not analysis_throttle['allowed']:
                self.stats['throttled_analyses'] += 1
                logger.warning(f"⏸️  Stage 2 throttled: {analysis_throttle['reason']} "
                             f"(wait {analysis_throttle['wait_seconds']}s)")
                return []

            logger.info(f"🎨 Stage 2: Running full analysis for {symbol}...")
            stage2_start = datetime.now()

            # Generate charts for Visual Analyst
            charts = self._generate_charts(symbol)

            if not charts:
                logger.warning(f"⚠️ Stage 2: Failed to generate charts for {symbol}")
                return []

            # Record full analysis
            self.throttle_manager.record_full_analysis()
            self.stats['stage2_executed'] += 1

            stage2_time = (datetime.now() - stage2_start).total_seconds() * 1000

            logger.info(f"✓ Stage 2 complete ({stage2_time:.0f}ms): Charts generated for {list(charts.keys())}")

            # Fan the request out to the ANALYSTS, one message each.
            #
            # This previously addressed a single message to the Meta-Agent,
            # which does not handle ANALYSIS_REQUEST (it handles ANALYSIS_RESULT)
            # — so the request was dropped by the bus and no analyst ever ran.
            # The Meta-Agent is the aggregator of the results, not the router of
            # the requests.
            payload = {
                'symbol': symbol,
                'charts': charts,
                'technical_analysis': technical_analysis,
                'stage1_quality': quality_result,
                'trigger_info': trigger_result,
                'scan_time': datetime.now().isoformat(),
                'timeframes': list(charts.keys()),
            }

            return [
                self.send_message(
                    msg_type=MessageType.ANALYSIS_REQUEST,
                    recipient=analyst,
                    data={**payload, 'screenshot': self._screenshot_for(analyst, charts)},
                    priority=trigger_result['priority'],
                )
                for analyst in self.ANALYSTS
            ]

        except Exception as e:
            logger.error(f"Error in two-stage analysis for {symbol}: {e}", exc_info=True)
            return []

    @staticmethod
    def _screenshot_for(analyst: AgentType, charts: Dict) -> Optional[str]:
        """Only the Visual Analyst needs a rendered chart; others ignore it."""
        if analyst != AgentType.VISUAL_ANALYST or not charts:
            return None
        first = next(iter(charts.values()), {})
        return first.get('image_base64')

    def _check_stage1_quality(
        self,
        signal: str,
        confidence: float,
        confluence_score: int,
        lead_timeframe: Optional[str],
        symbol: str,
        current_price: Optional[float],
        indicators_1m: Dict,
        trigger_result: Dict
    ) -> Dict:
        """
        Check Stage 1 quality criteria

        Criteria:
        1. Signal is not PASS (must be LONG or SHORT)
        2. Confidence ≥ 0.5
        3. Confluence ≥ 2
        4. Price at key level OR volume spike >2×
        5. Lead timeframe is 15m or 5m (prefer higher timeframes)

        Returns:
            Dict with quality check result:
            {
                'passed': True/False,
                'pass_reason': 'High confidence, strong confluence, price at support',
                'reject_reason': 'Low confidence (0.45)',
                'criteria_met': ['confidence', 'confluence', 'key_level'],
                'criteria_failed': ['lead_timeframe']
            }
        """
        criteria_met = []
        criteria_failed = []
        reject_reasons = []

        # 1. Signal check
        if signal == 'PASS' or signal == 'NEUTRAL':
            criteria_failed.append('signal')
            reject_reasons.append(f'Signal is {signal} (need LONG or SHORT)')
        else:
            criteria_met.append('signal')

        # 2. Confidence check
        if confidence >= self.MIN_CONFIDENCE:
            criteria_met.append('confidence')
        else:
            criteria_failed.append('confidence')
            reject_reasons.append(f'Low confidence ({confidence:.0%})')

        # 3. Confluence check
        if confluence_score >= self.MIN_CONFLUENCE:
            criteria_met.append('confluence')
        else:
            criteria_failed.append('confluence')
            reject_reasons.append(f'Low confluence ({confluence_score})')

        # 4. Key level OR volume spike check
        key_level_met = False
        volume_spike_met = False

        # Check key level
        if current_price and indicators_1m:
            key_level_result = self.key_level_detector.is_at_key_level(
                symbol, current_price, indicators_1m
            )

            if key_level_result['at_key_level']:
                key_level_met = True
                criteria_met.append('key_level')

        # Check volume spike
        if 'volume_spike' in trigger_result.get('triggers', []):
            volume_spike_met = True
            criteria_met.append('volume_spike')

        if not key_level_met and not volume_spike_met:
            criteria_failed.append('key_level_or_volume')
            reject_reasons.append('Price not at key level and no volume spike')

        # 5. Lead timeframe check (optional, but preferred)
        if lead_timeframe in self.ALLOWED_LEAD_TIMEFRAMES:
            criteria_met.append('lead_timeframe')
        else:
            # Not a hard reject, just note it
            criteria_failed.append('lead_timeframe')

        # Determine if passed (need at least: signal, confidence, confluence, and key_level OR volume)
        required_criteria = {'signal', 'confidence', 'confluence'}
        optional_criteria = {'key_level', 'volume_spike'}

        required_met = required_criteria.issubset(set(criteria_met))
        optional_met = len(optional_criteria.intersection(set(criteria_met))) > 0

        passed = required_met and optional_met

        # Build pass/reject reason
        if passed:
            pass_reason_parts = []
            if confidence >= 0.7:
                pass_reason_parts.append(f'High confidence ({confidence:.0%})')
            else:
                pass_reason_parts.append(f'Confidence {confidence:.0%}')

            if confluence_score >= 3:
                pass_reason_parts.append(f'Strong confluence ({confluence_score})')
            else:
                pass_reason_parts.append(f'Confluence {confluence_score}')

            if 'key_level' in criteria_met:
                pass_reason_parts.append('Price at key level')
            elif 'volume_spike' in criteria_met:
                pass_reason_parts.append('Volume spike')

            if 'lead_timeframe' in criteria_met:
                pass_reason_parts.append(f'Lead TF {lead_timeframe}')

            pass_reason = ', '.join(pass_reason_parts)
        else:
            pass_reason = None

        reject_reason = ', '.join(reject_reasons) if reject_reasons else None

        return {
            'passed': passed,
            'pass_reason': pass_reason,
            'reject_reason': reject_reason,
            'criteria_met': criteria_met,
            'criteria_failed': criteria_failed
        }

    def _generate_charts(self, symbol: str) -> Dict[str, Dict]:
        """
        Generate charts for all configured timeframes

        Args:
            symbol: Trading symbol

        Returns:
            Dict mapping timeframe to chart data
        """
        try:
            charts = self.chart_generator.generate_charts(symbol)

            if charts:
                logger.debug(f"  Charts generated: {list(charts.keys())}")

            return charts

        except Exception as e:
            logger.error(f"Error generating charts for {symbol}: {e}")
            return {}

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process incoming messages

        Scanner mainly sends messages, rarely receives them
        """
        if message.type == MessageType.STATUS_UPDATE:
            logger.debug(f"Scanner received status update: {message.data}")
            return None

        return None

    def get_stats(self) -> Dict:
        """
        Get performance statistics

        Returns:
            Dict with stats
        """
        throttle_status = self.throttle_manager.get_status()

        return {
            **self.stats,
            'throttle_status': throttle_status,
            'stage1_pass_rate': (
                self.stats['stage1_passed'] / max(self.stats['total_triggers'], 1) * 100
            ),
            'stage2_rate': (
                self.stats['stage2_executed'] / max(self.stats['stage1_passed'], 1) * 100
            )
        }
