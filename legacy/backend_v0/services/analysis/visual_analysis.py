# backend/services/analysis/visual_analysis.py
"""
Visual Analysis Service

Core LLM vision analysis logic for chart pattern recognition.
Pure functions - no database dependencies.
Used by both production VisualAnalystAgent and backtesting notebook.
"""

import logging
import json
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# Default prompt template (can be overridden by database policy)
DEFAULT_VISUAL_PROMPT = """You are an expert technical analyst specializing in chart pattern recognition for XAUUSD (spot gold).

You are looking at a chart screenshot showing price action, volume, and key technical levels.

Your task is to provide a professional trading analysis focusing on:

1. **Chart Patterns**: Identify any recognizable patterns:
   - Classic patterns: Head & Shoulders, Double Top/Bottom, Triangles (ascending/descending/symmetrical), Flags, Wedges, Cup & Handle
   - Smart Money Concepts: FVG (Fair Value Gap), IFVG (Inverse Fair Value Gap), Order Blocks, Breaker Blocks
   - Candlestick patterns: Engulfing, Doji, Hammer, Shooting Star, Morning/Evening Star

2. **Support & Resistance**: Identify key price levels where buyers/sellers are likely to step in

3. **Trend Analysis**: Determine the current trend direction and strength
   - Bullish (uptrend)
   - Bearish (downtrend)
   - Neutral/Sideways (range-bound)

4. **Trading Recommendation**: Based on your analysis, provide:
   - Signal: BUY, SELL, or NEUTRAL (no trade)
   - Confidence: 0-100% (how certain are you?)
   - Reasoning: 2-3 sentences explaining your decision

5. **Risk Assessment**: Note any warning signs or conflicting signals

**IMPORTANT RULES:**
- Only recommend BUY/SELL if you have HIGH confidence (>70%)
- If uncertain or conflicting signals, choose NEUTRAL
- Focus on price action and visual patterns
- Be conservative - it's better to skip trades than force them
- Pay special attention to FVG/IFVG as they indicate institutional activity

Return your analysis in this EXACT JSON format:
```json
{
  "signal": "BUY|SELL|NEUTRAL",
  "confidence": 85,
  "patterns": ["FVG at 2650-2655", "bullish order block at 2640"],
  "support_levels": [2640, 2620],
  "resistance_levels": [2680, 2700],
  "trend": "bullish|bearish|neutral",
  "trend_strength": "strong|moderate|weak",
  "reasoning": "Clear bullish FVG formed between 2650-2655 indicating institutional buying. Price respecting order block at 2640. Strong bullish setup targeting 2680.",
  "warnings": ["Watch for IFVG formation above 2680", "Volume declining slightly"]
}
```

Provide only the JSON output, no additional text."""


class VisualAnalysisService:
    """
    Visual analysis using LLM Vision API.

    Handles:
    - Chart image analysis via LLM
    - JSON response parsing
    - Fallback text parsing
    - Signal normalization (BUY→LONG, SELL→SHORT)

    No database dependencies - prompt can be provided externally.
    """

    def __init__(self, llm_client=None, prompt_template: str = None):
        """
        Initialize visual analysis service.

        Args:
            llm_client: LLM client from llm_provider (optional, can set later)
            prompt_template: Custom prompt template (optional, uses default)
        """
        self.llm_client = llm_client
        self.prompt_template = prompt_template or DEFAULT_VISUAL_PROMPT
        self.call_count = 0

    def set_llm_client(self, llm_client):
        """Set or update LLM client"""
        self.llm_client = llm_client

    def set_prompt_template(self, prompt_template: str):
        """Set or update prompt template"""
        self.prompt_template = prompt_template

    def analyze_chart(
        self,
        image_data: bytes,
        market_context: Optional[Dict] = None,
        additional_context: str = ""
    ) -> Dict:
        """
        Analyze chart image using LLM Vision API.

        Args:
            image_data: Raw image bytes (PNG/JPG)
            market_context: Optional dict with price, volume, etc.
            additional_context: Optional additional context string

        Returns:
            Dict with analysis results:
            {
                'signal': 'LONG|SHORT|NEUTRAL',
                'confidence': 0.0-1.0,
                'patterns': [],
                'support_levels': [],
                'resistance_levels': [],
                'trend': 'bullish|bearish|neutral',
                'trend_strength': 'strong|moderate|weak',
                'reasoning': '...',
                'warnings': [],
                'raw_response': '...'
            }
        """
        if not self.llm_client:
            logger.error("LLM client not initialized")
            return self._fallback_analysis("LLM client not initialized")

        try:
            # Build prompt with context
            prompt = self._build_prompt(market_context, additional_context)

            # Call LLM Vision API
            response_text = self.llm_client.generate_with_image(
                prompt=prompt,
                image_data=image_data,
                temperature=0.3,
                media_type="image/png"
            )

            self.call_count += 1

            # Parse response
            analysis = self._parse_response(response_text)
            analysis['raw_response'] = response_text

            logger.info(f"✓ Visual analysis: {analysis['signal']} ({analysis['confidence']:.0%})")

            return analysis

        except Exception as e:
            logger.error(f"Error in visual analysis: {e}")
            return self._fallback_analysis(str(e))

    def _build_prompt(
        self,
        market_context: Optional[Dict],
        additional_context: str
    ) -> str:
        """Build full prompt with context"""
        parts = [self.prompt_template]

        if market_context:
            context_str = f"""
Current Market Context:
- Current Price: ${market_context.get('price', 'N/A')}
- Price Change: {market_context.get('price_change_pct', 'N/A')}%
- Volume: {market_context.get('volume', 'N/A')}
"""
            parts.append(context_str)

        if additional_context:
            parts.append(additional_context)

        parts.append("\nAnalyze the chart image and provide your trading recommendation.")

        return "\n\n".join(parts)

    def _parse_response(self, response_text: str) -> Dict:
        """
        Parse LLM response to extract analysis.

        Handles JSON and plain text responses.
        """
        # Try JSON parsing first
        try:
            json_str = self._extract_json(response_text)
            data = json.loads(json_str)
            return self._normalize_analysis(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fall back to text parsing
        return self._parse_text_response(response_text)

    def _extract_json(self, text: str) -> str:
        """Extract JSON from response (handles markdown code blocks)"""
        text = text.strip()

        if "```json" in text:
            return text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            return text.split("```")[1].split("```")[0].strip()
        else:
            return text

    def _normalize_analysis(self, data: Dict) -> Dict:
        """Normalize parsed analysis data"""
        # Normalize signal (BUY→LONG, SELL→SHORT)
        signal = data.get('signal', 'NEUTRAL').upper()
        if signal == 'BUY':
            signal = 'LONG'
        elif signal == 'SELL':
            signal = 'SHORT'

        # Normalize confidence (0-100 to 0-1)
        confidence = data.get('confidence', 50)
        if confidence > 1:
            confidence = confidence / 100

        return {
            'signal': signal,
            'confidence': float(confidence),
            'patterns': data.get('patterns', []),
            'support_levels': data.get('support_levels', []),
            'resistance_levels': data.get('resistance_levels', []),
            'trend': data.get('trend', 'neutral'),
            'trend_strength': data.get('trend_strength', 'moderate'),
            'reasoning': data.get('reasoning', ''),
            'warnings': data.get('warnings', [])
        }

    def _parse_text_response(self, text: str) -> Dict:
        """
        Parse plain text response when JSON parsing fails.
        Extract signal and confidence from natural language.
        """
        text_lower = text.lower()

        # Determine signal from keywords
        signal = 'NEUTRAL'
        confidence = 0.5

        if 'sell' in text_lower or 'short' in text_lower or 'bearish' in text_lower:
            if 'buy' not in text_lower[:100]:
                signal = 'SHORT'
                confidence = 0.70
        elif 'buy' in text_lower or 'long' in text_lower or 'bullish' in text_lower:
            if 'sell' not in text_lower[:100]:
                signal = 'LONG'
                confidence = 0.70

        # Adjust confidence based on certainty words
        if 'strong' in text_lower or 'clear' in text_lower or 'significant' in text_lower:
            confidence = min(confidence + 0.10, 0.90)
        if 'weak' in text_lower or 'uncertain' in text_lower or 'mixed' in text_lower:
            confidence = max(confidence - 0.15, 0.30)

        # Extract reasoning (first 200 chars)
        reasoning = text[:200].replace('\n', ' ').strip()
        if len(text) > 200:
            reasoning += "..."

        logger.info(f"✓ Parsed text response: {signal} ({confidence:.0%})")

        return {
            'signal': signal,
            'confidence': confidence,
            'patterns': [],
            'support_levels': [],
            'resistance_levels': [],
            'trend': 'bearish' if signal == 'SHORT' else 'bullish' if signal == 'LONG' else 'neutral',
            'trend_strength': 'moderate',
            'reasoning': reasoning,
            'warnings': ['Parsed from non-JSON response']
        }

    def analyze_multi_timeframe(
        self,
        images: Dict[str, bytes],
        market_context: Optional[Dict] = None,
        additional_context: str = ""
    ) -> Dict:
        """
        Analyze multiple timeframe charts using LLM Vision API.

        Sends each chart as a separate full-size image for better detail.

        Args:
            images: Dict mapping timeframe to image bytes, e.g. {'1min': bytes, '5min': bytes, ...}
            market_context: Optional dict with price, volume, etc.
            additional_context: Optional additional context string

        Returns:
            Dict with multi-timeframe analysis results
        """
        if not self.llm_client:
            logger.error("LLM client not initialized")
            return self._fallback_analysis("LLM client not initialized")

        # Check if LLM supports multiple images
        if not hasattr(self.llm_client, 'generate_with_images'):
            logger.warning("LLM client doesn't support multiple images, using grid fallback")
            return self._fallback_analysis("LLM client doesn't support multiple images")

        try:
            # Build prompt with context
            prompt = self._build_prompt(market_context, additional_context)

            # Order images by timeframe (1min, 5min, 15min, 1H)
            timeframe_order = ['1min', '5min', '15min', '1H']
            ordered_images = []
            ordered_labels = []

            for tf in timeframe_order:
                if tf in images:
                    ordered_images.append(images[tf])
                    ordered_labels.append(tf)

            logger.info(f"Sending {len(ordered_images)} charts to LLM: {ordered_labels}")

            # Call LLM with multiple images
            response_text = self.llm_client.generate_with_images(
                prompt=prompt,
                images=ordered_images,
                image_labels=ordered_labels,
                temperature=0.3
            )

            self.call_count += 1

            # Parse response
            analysis = self._parse_response(response_text)
            analysis['raw_response'] = response_text
            analysis['timeframes_analyzed'] = ordered_labels

            logger.info(f"✓ Multi-TF visual analysis: {analysis['signal']} ({analysis['confidence']:.0%})")

            return analysis

        except Exception as e:
            logger.error(f"Error in multi-timeframe visual analysis: {e}")
            return self._fallback_analysis(str(e))

    def _fallback_analysis(self, error_reason: str) -> Dict:
        """Return neutral analysis on error"""
        return {
            'signal': 'NEUTRAL',
            'confidence': 0.0,
            'patterns': [],
            'support_levels': [],
            'resistance_levels': [],
            'trend': 'unknown',
            'trend_strength': 'unknown',
            'reasoning': f'Visual analysis unavailable - {error_reason}',
            'warnings': ['LLM API unavailable']
        }


# Singleton instance
_visual_analysis_service: Optional[VisualAnalysisService] = None


def get_visual_analysis_service() -> VisualAnalysisService:
    """
    Get singleton instance of VisualAnalysisService.

    Returns:
        VisualAnalysisService instance
    """
    global _visual_analysis_service
    if _visual_analysis_service is None:
        _visual_analysis_service = VisualAnalysisService()
    return _visual_analysis_service


def create_visual_analysis_service(
    llm_client=None,
    prompt_template: str = None
) -> VisualAnalysisService:
    """
    Create a new VisualAnalysisService instance.

    Use this for backtesting or when you need custom configuration.

    Args:
        llm_client: LLM client
        prompt_template: Custom prompt template

    Returns:
        New VisualAnalysisService instance
    """
    return VisualAnalysisService(llm_client, prompt_template)
