# backend/agents/visual_analyst_agent.py
"""
Visual Analyst Agent

Uses LLM Vision API to analyze chart screenshots
Identifies patterns, support/resistance, and provides trading recommendations
Supports multiple LLM providers (Gemini, Claude) via llm_provider abstraction
"""

import logging
import os
import json
from typing import Dict, Optional

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from config.llm_provider import get_llm_client, get_provider_info
from config.database import get_database
from services.analysis.visual_analysis import VisualAnalysisService, DEFAULT_VISUAL_PROMPT

logger = logging.getLogger(__name__)


# NOTE: This is the fallback prompt if database is unavailable
# The active prompt is loaded from visual_agent_policies table
# Using shared prompt from services/analysis/visual_analysis.py
VISUAL_ANALYST_PROMPT_FALLBACK = DEFAULT_VISUAL_PROMPT


class VisualAnalystAgent(BaseAgent):
    """
    Visual Analyst Agent - Chart pattern recognition via AI vision

    Uses LLM Vision API to analyze chart screenshots
    Supports multiple providers (Gemini, Claude) via llm_provider abstraction
    """

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(AgentType.VISUAL_ANALYST, config)

        # Initialize database connection
        self.db = get_database()

        # Load current policy from database (Layer 2: Strategic)
        self.policy_version, self.policy_prompt = self._load_current_policy()

        # Initialize LLM client using provider abstraction
        try:
            self.llm_client = get_llm_client()
            provider_info = get_provider_info()

            # Initialize shared analysis service
            self.analysis_service = VisualAnalysisService(
                llm_client=self.llm_client,
                prompt_template=self.policy_prompt
            )

            logger.info(f"✓ Visual Analyst initialized with {provider_info['provider']} ({provider_info['model']})")
            logger.info(f"  Using policy: {self.policy_version}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize LLM client: {e}")
            self.llm_client = None
            self.analysis_service = None

    def run(self) -> list:
        """
        Visual Analyst doesn't run on schedule - it responds to analysis requests
        """
        return []

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process analysis requests from Scanner Agent

        Args:
            message: Message with screenshot and market data

        Returns:
            Analysis result message to Meta-Agent
        """
        if message.type != MessageType.ANALYSIS_REQUEST:
            return None

        if not self.is_active:
            logger.debug("Visual Analyst is inactive, skipping analysis")
            return None

        try:
            logger.info("🎨 Visual Analyst: Analyzing chart screenshot...")

            # Extract data from message
            symbol = message.data.get('symbol')
            screenshot = message.data.get('screenshot')
            market_data = message.data.get('market_data', {})

            if not screenshot:
                logger.warning("No screenshot provided, cannot analyze visually")
                # Return neutral analysis
                return self._create_analysis_message(symbol, {
                    'signal': 'NEUTRAL',
                    'confidence': 0,
                    'reasoning': 'No screenshot available for visual analysis',
                    'patterns': [],
                    'support_levels': [],
                    'resistance_levels': [],
                    'trend': 'unknown',
                    'warnings': ['Screenshot not available']
                })

            # Analyze with LLM Vision API
            analysis = self._analyze_chart(screenshot, symbol, market_data)

            # Send analysis result to Meta-Agent
            return self._create_analysis_message(symbol, analysis)

        except Exception as e:
            logger.error(f"Error in Visual Analyst: {e}")
            return None

    def _analyze_chart(self, screenshot: str, symbol: str, market_data: Dict) -> Dict:
        """
        Send screenshot to LLM Vision API for analysis.

        Uses shared VisualAnalysisService for core logic.

        Args:
            screenshot: Base64 encoded image
            symbol: Trading symbol
            market_data: Current price data

        Returns:
            Analysis dict
        """
        if not self.analysis_service:
            logger.error("Analysis service not initialized")
            return self._fallback_analysis()

        try:
            # Decode base64 screenshot to bytes
            import base64
            image_bytes = base64.b64decode(screenshot)

            # Build market context
            market_context = {
                'symbol': symbol,
                'price': market_data.get('price'),
                'price_change_pct': market_data.get('price_change_pct'),
                'volume': market_data.get('volume'),
                'sma_20': market_data.get('sma_20'),
                'sma_50': market_data.get('sma_50')
            }

            # Use shared analysis service
            analysis = self.analysis_service.analyze_chart(
                image_data=image_bytes,
                market_context=market_context
            )

            # Normalize signal format (service returns LONG/SHORT, agent expects BUY/SELL for some code paths)
            # Keep LONG/SHORT internally but log with percentage
            confidence_pct = analysis['confidence'] * 100 if analysis['confidence'] <= 1 else analysis['confidence']
            logger.info(f"✓ Visual analysis complete: {analysis['signal']} (confidence: {confidence_pct:.0f}%)")

            return analysis

        except Exception as e:
            logger.error(f"Error in visual analysis: {e}")
            return self._fallback_analysis()

    def _parse_text_response(self, text: str) -> Dict:
        """
        Parse plain text response when JSON parsing fails.
        Extract signal and confidence from natural language.
        """
        text_lower = text.lower()

        # Determine signal from keywords
        if 'sell' in text_lower or 'short' in text_lower or 'bearish' in text_lower:
            if 'buy' not in text_lower[:100]:  # Check it's not "don't buy"
                signal = 'SELL'
                confidence = 70
        elif 'buy' in text_lower or 'long' in text_lower or 'bullish' in text_lower:
            if 'sell' not in text_lower[:100]:
                signal = 'BUY'
                confidence = 70
        else:
            signal = 'NEUTRAL'
            confidence = 50

        # Adjust confidence based on certainty words
        if 'strong' in text_lower or 'clear' in text_lower or 'significant' in text_lower:
            confidence = min(confidence + 10, 90)
        if 'weak' in text_lower or 'uncertain' in text_lower or 'mixed' in text_lower:
            confidence = max(confidence - 15, 30)

        # Extract first 200 chars as reasoning
        reasoning = text[:200].replace('\n', ' ').strip()
        if len(text) > 200:
            reasoning += "..."

        logger.info(f"✓ Parsed text response: {signal} ({confidence}%)")

        return {
            'signal': signal,
            'confidence': confidence,
            'reasoning': reasoning,
            'patterns': [],
            'support_levels': [],
            'resistance_levels': [],
            'trend': 'bearish' if signal == 'SELL' else 'bullish' if signal == 'BUY' else 'neutral',
            'trend_strength': 'moderate',
            'warnings': ['Parsed from non-JSON response']
        }

    def _fallback_analysis(self) -> Dict:
        """Return neutral analysis if API fails"""
        return {
            'signal': 'NEUTRAL',
            'confidence': 0,
            'reasoning': 'Visual analysis unavailable - API error',
            'patterns': [],
            'support_levels': [],
            'resistance_levels': [],
            'trend': 'unknown',
            'trend_strength': 'unknown',
            'warnings': ['LLM API unavailable']
        }

    def _create_analysis_message(self, symbol: str, analysis: Dict) -> Message:
        """
        Create analysis result message to send to Meta-Agent

        Args:
            symbol: Trading symbol
            analysis: Analysis results

        Returns:
            Message for Meta-Agent
        """
        return self.send_message(
            msg_type=MessageType.ANALYSIS_RESULT,
            recipient=AgentType.META_AGENT,
            data={
                'symbol': symbol,
                'analyst': 'visual',
                'signal': analysis['signal'],
                'confidence': analysis['confidence'],
                'analysis': analysis,
                'policy_version': self.policy_version  # Track which policy was used
            },
            priority=7
        )

    def _load_current_policy(self) -> tuple:
        """
        Load current active policy from database

        Returns:
            Tuple of (version, prompt)
        """
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    SELECT version, prompt
                    FROM visual_agent_policies
                    WHERE is_active = TRUE
                    LIMIT 1
                """)

                row = cur.fetchone()

                if row:
                    version, prompt = row
                    logger.info(f"✓ Loaded policy {version} from database")
                    return version, prompt
                else:
                    logger.warning("⚠️ No active policy in database, using fallback")
                    return 'v1.0-fallback', VISUAL_ANALYST_PROMPT_FALLBACK

        except Exception as e:
            logger.error(f"Error loading policy from database: {e}")
            return 'v1.0-fallback', VISUAL_ANALYST_PROMPT_FALLBACK

    def reload_policy(self):
        """
        Reload policy from database (called after policy evolution)

        This allows hot-reloading of policy without restarting the agent
        """
        old_version = self.policy_version
        self.policy_version, self.policy_prompt = self._load_current_policy()

        if self.policy_version != old_version:
            logger.info(f"✓ Policy reloaded: {old_version} -> {self.policy_version}")
        else:
            logger.debug("Policy unchanged after reload")

    def get_policy_version(self) -> str:
        """Get current policy version"""
        return self.policy_version
