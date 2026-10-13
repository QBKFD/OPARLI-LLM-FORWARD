# backend/agents/sentiment_analyst_agent.py
"""
Sentiment Analyst Agent

Uses Claude Text API to analyze market sentiment from news, context, and market conditions
Provides bullish/bearish/neutral sentiment with confidence level
"""

import logging
import os
from typing import Dict, Optional
import anthropic

from agents.base_agent import BaseAgent, AgentType, MessageType, Message

logger = logging.getLogger(__name__)


SENTIMENT_ANALYST_PROMPT = """You are an expert gold market sentiment analyst with deep understanding of macroeconomic factors, geopolitical events, and market psychology.

Your task is to analyze current market sentiment for XAUUSD (spot gold) based on:
1. Current market conditions and price action
2. Recent news and events (if provided)
3. Technical context (trend, momentum, volume)
4. Broader market environment (risk-on vs risk-off)

**Key Factors to Consider for Gold:**
- USD strength/weakness (inverse correlation)
- Inflation expectations
- Interest rate outlook (Fed policy)
- Geopolitical tensions and safe-haven demand
- Stock market performance (risk appetite)
- Central bank gold purchases
- Economic data releases

**Your Analysis Should Include:**

1. **Sentiment Direction**: Determine if current sentiment is:
   - BULLISH: Positive sentiment favoring higher gold prices
   - BEARISH: Negative sentiment favoring lower gold prices
   - NEUTRAL: Mixed or unclear sentiment

2. **Confidence Level**: 0-100% (how certain are you?)
   - 80-100%: Very strong sentiment signals
   - 60-79%: Moderate sentiment signals
   - 40-59%: Weak or mixed signals
   - <40%: Very uncertain, conflicting information

3. **Sentiment Drivers**: What are the main factors driving sentiment?
   - List 2-4 key drivers
   - Be specific (e.g., "Fed signals rate cuts", "Dollar weakness")

4. **Market Psychology**: Assess fear vs greed
   - Is market overly fearful (bullish for gold)?
   - Is market complacent (bearish for gold)?
   - Is there panic buying or selling?

5. **Contrarian Signals**: Any signs of market extremes?
   - Excessive bullishness might indicate a top
   - Excessive bearishness might indicate a bottom

**IMPORTANT RULES:**
- Only recommend strong BULLISH/BEARISH if you have HIGH confidence (>70%)
- If sentiment is mixed or unclear, choose NEUTRAL
- Consider both short-term sentiment and medium-term trends
- Gold is a safe-haven asset - fear is bullish, complacency is bearish
- Be objective - don't force a signal if sentiment is unclear

**Return your analysis in this EXACT JSON format:**
```json
{
  "sentiment": "BULLISH|BEARISH|NEUTRAL",
  "confidence": 75,
  "drivers": [
    "Fed signaling rate cuts in 2024",
    "Dollar weakness on inflation data",
    "Geopolitical tensions rising"
  ],
  "market_psychology": "Fear increasing - safe-haven demand strong",
  "contrarian_signals": ["Gold ETF inflows at 6-month high - watch for exhaustion"],
  "time_horizon": "short-term|medium-term",
  "reasoning": "Clear bullish sentiment driven by weakening dollar and Fed pivot expectations. Safe-haven demand rising on geopolitical concerns. Market psychology shows increasing fear which is positive for gold. However, watch for overbought conditions."
}
```

Provide only the JSON output, no additional text.
"""


class SentimentAnalystAgent(BaseAgent):
    """
    Sentiment Analyst Agent - Market sentiment analysis via Claude Text API

    Analyzes market sentiment from news, macroeconomic factors, and market psychology
    """

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(AgentType.SENTIMENT_ANALYST, config)

        # Get API key from environment
        self.api_key = os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ ANTHROPIC_API_KEY not set - Sentiment Analyst will not work")

        self.client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else None
        self.model = config.get('model', 'claude-sonnet-4-20250514') if config else 'claude-sonnet-4-20250514'

        logger.info(f"✓ Sentiment Analyst initialized with model: {self.model}")

    def run(self) -> list:
        """
        Sentiment Analyst doesn't run on schedule - it responds to analysis requests
        """
        return []

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process analysis requests from Scanner Agent

        Args:
            message: Message with symbol and market data

        Returns:
            Analysis result message to Meta-Agent
        """
        if message.type != MessageType.ANALYSIS_REQUEST:
            return None

        if not self.is_active:
            logger.debug("Sentiment Analyst is inactive, skipping analysis")
            return None

        try:
            logger.info("📰 Sentiment Analyst: Analyzing market sentiment...")

            # Extract data from message
            symbol = message.data.get('symbol')
            market_data = message.data.get('market_data', {})

            # Analyze sentiment
            sentiment = self._analyze_sentiment(symbol, market_data)

            # Send analysis result to Meta-Agent
            return self._create_analysis_message(symbol, sentiment)

        except Exception as e:
            logger.error(f"Error in Sentiment Analyst: {e}")
            return None

    def _analyze_sentiment(self, symbol: str, market_data: Dict) -> Dict:
        """
        Analyze market sentiment using Claude Text API

        Args:
            symbol: Trading symbol
            market_data: Current market conditions

        Returns:
            Sentiment analysis dict
        """
        if not self.client:
            logger.error("Claude API client not initialized")
            return self._fallback_sentiment()

        try:
            # Build context from market data
            context = self._build_market_context(symbol, market_data)

            # Call Claude Text API
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": context + "\n\n" + SENTIMENT_ANALYST_PROMPT
                    }
                ]
            )

            # Parse response
            response_text = response.content[0].text

            # Extract JSON from response (handle markdown code blocks)
            import json
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text.strip()

            sentiment = json.loads(json_str)

            logger.info(f"✓ Sentiment analysis complete: {sentiment['sentiment']} (confidence: {sentiment['confidence']}%)")
            return sentiment

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.error(f"Raw response: {response_text[:500]}")
            return self._fallback_sentiment()

        except Exception as e:
            logger.error(f"Error calling Claude API: {e}")
            return self._fallback_sentiment()

    def _build_market_context(self, symbol: str, market_data: Dict) -> str:
        """
        Build context string from market data

        Args:
            symbol: Trading symbol
            market_data: Market conditions

        Returns:
            Context string for sentiment analysis
        """
        price = market_data.get('price', 'N/A')
        price_change = market_data.get('price_change_pct', 'N/A')
        volume = market_data.get('volume', 'N/A')
        sma_20 = market_data.get('sma_20', 'N/A')
        sma_50 = market_data.get('sma_50', 'N/A')

        # Determine trend
        trend = "unknown"
        if price != 'N/A' and sma_20 != 'N/A' and sma_50 != 'N/A':
            if price > sma_20 > sma_50:
                trend = "bullish (price above both MAs)"
            elif price < sma_20 < sma_50:
                trend = "bearish (price below both MAs)"
            else:
                trend = "mixed (no clear trend)"

        # Build context
        context = f"""
Current Market Context for {symbol}:

**Price Action:**
- Current Price: ${price}
- Price Change: {price_change}%
- Trend: {trend}

**Technical Context:**
- SMA 20: ${sma_20}
- SMA 50: ${sma_50}
- Volume: {volume}

**Additional Context:**
- Market is currently {'rising' if isinstance(price_change, (int, float)) and price_change > 0 else 'falling' if isinstance(price_change, (int, float)) and price_change < 0 else 'mixed'}
- Volume {'spike detected' if market_data.get('volume_spike') else 'normal'}

Based on this market context, analyze the current sentiment for gold trading.
"""

        return context

    def _fallback_sentiment(self) -> Dict:
        """Return neutral sentiment if API fails"""
        return {
            'sentiment': 'NEUTRAL',
            'confidence': 0,
            'drivers': [],
            'market_psychology': 'Unable to assess - API unavailable',
            'contrarian_signals': [],
            'time_horizon': 'unknown',
            'reasoning': 'Sentiment analysis unavailable - Claude API error'
        }

    # Sentiment reasons in BULLISH/BEARISH/NEUTRAL on a 0-100 scale because
    # that is the natural language for the prompt. The Meta-Agent scores
    # LONG/SHORT on 0-1 (see MetaDecisionService.calculate_weighted_score).
    # Translating at this boundary is what makes the sentiment weight actually
    # count: unmapped signals fell through the scorer and contributed 0.0 while
    # still holding their share of the weight budget.
    _SIGNAL_MAP = {
        'BULLISH': 'LONG',
        'BEARISH': 'SHORT',
        'NEUTRAL': 'PASS',
    }

    @staticmethod
    def _normalize(sentiment: Dict) -> Dict:
        """
        Map a sentiment result onto the Meta-Agent's contract.

        Returns:
            {'signal': 'LONG'|'SHORT'|'PASS', 'confidence': 0.0-1.0}
        """
        raw_signal = str(sentiment.get('sentiment', 'NEUTRAL')).strip().upper()
        signal = SentimentAnalystAgent._SIGNAL_MAP.get(raw_signal)
        if signal is None:
            logger.warning(f"Unrecognised sentiment '{raw_signal}', treating as PASS")
            signal = 'PASS'

        try:
            confidence = float(sentiment.get('confidence', 0.0))
        except (TypeError, ValueError):
            logger.warning(f"Non-numeric sentiment confidence "
                           f"{sentiment.get('confidence')!r}, treating as 0")
            confidence = 0.0

        if confidence > 1.0:
            confidence = confidence / 100.0
        confidence = max(0.0, min(1.0, confidence))

        return {'signal': signal, 'confidence': confidence}

    def _create_analysis_message(self, symbol: str, sentiment: Dict) -> Message:
        """
        Create analysis result message to send to Meta-Agent

        Args:
            symbol: Trading symbol
            sentiment: Sentiment analysis results

        Returns:
            Message for Meta-Agent
        """
        normalized = self._normalize(sentiment)

        return self.send_message(
            msg_type=MessageType.ANALYSIS_RESULT,
            recipient=AgentType.META_AGENT,
            data={
                'symbol': symbol,
                'analyst': 'sentiment',
                'signal': normalized['signal'],
                'confidence': normalized['confidence'],
                # Full result kept for logging/debugging, with the original
                # BULLISH/BEARISH wording intact.
                'analysis': sentiment
            },
            priority=7
        )
