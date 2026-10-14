# backend/agents/meta_agent_new.py
"""
Meta-Agent - Strategic Coordinator

Four operational modes:
1. Pre-Trade Coordination: Weighted voting + agreement calculation
2. Mid-Trade Management: Respond to Trade Manager triggers
3. Post-Trade Weight Adjustment: EMA-smoothed tactical adaptation (Layer 1)
4. Monthly Self-Evolution: Symbolic policy changes with human approval (Layer 2, Paper A)

Agency Score: 7.5/10 (High Agency - heavy governance required, Paper B)
"""

import logging
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

from collections import deque

from agents.base_agent import BaseAgent, AgentType, MessageType, Message
from services.weight_manager import get_weight_manager
from services.analysis.meta_decision import MetaDecisionService, DEFAULT_META_PROMPT, create_meta_decision_service
from services.governance import (
    GovernanceConfig, check_circuit_breakers, enforce_weight_bounds,
    ACTION_CLOSE_ALL, ACTION_PAUSE,
)
from config.llm_provider import get_llm_client
from config.database import get_database

logger = logging.getLogger(__name__)


# Assumed XAUUSD spread when the request is built. The Risk Manager rejects
# above $0.50; a live quote source should replace this.
DEFAULT_SPREAD = 0.30


class MetaAgentMode(Enum):
    """Meta-Agent operational modes"""
    PRE_TRADE = "pre_trade"
    MID_TRADE = "mid_trade"
    POST_TRADE = "post_trade"
    EVOLUTION = "evolution"


class MidTradeDecision(Enum):
    """Mid-trade management decisions"""
    HOLD = "hold"
    PARTIAL_EXIT = "partial_exit"
    FULL_EXIT = "full_exit"
    ADJUST_TP = "adjust_tp"
    MOVE_SL = "move_sl"


# META-AGENT PROMPT TEMPLATE (Section 7 for evolved instructions)
# Using base template from shared service, extended with evolved instructions
META_AGENT_PROMPT_TEMPLATE = """You are the Meta-Agent coordinator for OPARLI's gold scalping trading system.

## ROLE & CONTEXT
You coordinate multiple analyst agents (Visual, Technical, Sentiment) to make strategic trading decisions.
You operate in 4 modes: Pre-Trade, Mid-Trade, Post-Trade, and Monthly Evolution.

## MODE 1: PRE-TRADE COORDINATION

**Inputs:**
- Visual Analyst: signal, confidence, analysis
- Technical Analyst: signal, confidence, analysis
- Sentiment Analyst: signal, confidence, analysis
- Current weights: visual={visual_weight:.4f}, technical={technical_weight:.4f}, sentiment={sentiment_weight:.4f}
- Market context: price, volume, regime

**Decision Process:**

1. **Calculate Weighted Score:**
   ```
   weighted_score = (visual_conf × visual_weight) + (technical_conf × tech_weight) + (sentiment_conf × sent_weight)
   ```

2. **Calculate Agreement (IMPORTANT: PASS ≠ Disagreement):**
   - PASS/NEUTRAL = neutral, not opposing
   - Only LONG vs SHORT = conflict
   - Examples:
     - 2 LONG, 1 PASS → agreement = 1.0 (no opposition)
     - 2 LONG, 1 SHORT → agreement = 0.67 (1 opposes)
     - 1 LONG, 1 SHORT, 1 PASS → agreement = 0.5 (evenly split)

3. **Decision Thresholds:**
   - weighted_score ≥ 0.7 AND agreement ≥ 0.5 → TRADE
   - weighted_score ≥ 0.6 AND agreement ≥ 0.67 → TRADE (reduced confidence)
   - Otherwise → PASS

**Output JSON:**
```json
{{
  "decision": "LONG|SHORT|PASS",
  "confidence": 0.75,
  "weighted_score": 0.72,
  "agreement": 0.67,
  "position_size_modifier": 1.0,
  "tp_suggestion": 2680.0,
  "sl_suggestion": 2650.0,
  "reasoning": "Detailed reasoning...",
  "analyst_breakdown": {{
    "visual": {{"signal": "LONG", "confidence": 0.85, "weight_contribution": 0.30}},
    "technical": {{"signal": "LONG", "confidence": 0.70, "weight_contribution": 0.28}},
    "sentiment": {{"signal": "PASS", "confidence": 0.60, "weight_contribution": 0}}
  }}
}}
```

## MODE 2: MID-TRADE MANAGEMENT

**Inputs:**
- Trigger type: profit_reversal | profit_milestone | pattern_invalidated | etc
- Trade details: entry_price, current_price, pnl, time_in_trade, max_profit
- Agent updates: Can request fresh analysis from any agent
- Market context: current indicators, regime, volatility

**Decision Options:**
- HOLD: Keep position unchanged
- PARTIAL_EXIT: Close X%, adjust SL on remaining
- FULL_EXIT: Close entire position
- ADJUST_TP: Extend or reduce target
- MOVE_SL: Tighten or move to breakeven

**Consider:**
- Momentum: Is price still moving in our direction?
- Pattern: Is original setup still valid?
- Profit: Have we captured enough vs risk more?
- Time: Approaching max hold time (40min)?

**Output JSON:**
```json
{{
  "decision": "HOLD|PARTIAL_EXIT|FULL_EXIT|ADJUST_TP|MOVE_SL",
  "exit_percentage": 50,
  "new_sl": 2655.0,
  "new_tp": 2685.0,
  "reasoning": "Detailed reasoning...",
  "urgency": "high|medium|low"
}}
```

## MODE 3: POST-TRADE WEIGHT ADJUSTMENT

**This mode is AUTOMATED (no LLM needed), handled by weight_manager service.**

Formula (with EMA smoothing):
```python
# Step 1: Raw update
if WIN:
    agents_correct × 1.05
    agents_wrong × 0.95
if LOSS:
    agents_correct × 0.95
    agents_wrong × 1.05

# Step 2: EMA smoothing (alpha=0.3)
smoothed = (0.3 × raw) + (0.7 × current)

# Step 3: Bounds (5% floor, 60% cap)
bounded = max(0.05, min(0.60, smoothed))

# Step 4: Normalize to sum=1.0
```

## MODE 4: MONTHLY SELF-EVOLUTION 

**Triggered:** Monthly, after 100+ trades

**Analyzes:**
- Performance by confidence level, agreement level, market regime
- Agent combination effectiveness
- Systematic failure patterns

**Proposes Changes:**

**A) Own coordination logic (Section 7 instructions):**
- ADD: "In volatile regime, require agreement ≥0.67 (not 0.5)"
- MODIFY: "Weighted score threshold 0.7 → 0.72"
- REMOVE: "Don't require Visual+Technical, Technical alone sufficient if confluence ≥5"

**B) Other agents' prompts/parameters:**
- Visual: "Reduce confidence 20% in first 30min of trading day"
- Technical: "Set 1m timeframe weight to 0% in volatile regime"
- Sentiment: "Reduce geopolitical weight from 0.8 to 0.5"

**Requirements:**
- Minimum 100 trades
- Statistical significance (p<0.05)
- Backtest improvement >10%
- **Human approval required**

**Output JSON:**
```json
{{
  "proposals": [
    {{
      "type": "ADD|MODIFY|REMOVE",
      "target": "meta_agent|visual_agent|technical_agent|sentiment_agent",
      "instruction": "Specific change...",
      "rationale": "Why this will improve...",
      "evidence": {{
        "sample_size": 150,
        "p_value": 0.02,
        "win_rate_before": 0.52,
        "win_rate_after_backtest": 0.58,
        "improvement_pct": 11.5
      }}
    }}
  ]
}}
```

## SECTION 7: EVOLVED INSTRUCTIONS (Dynamic)
{evolved_instructions}

## CONSTRAINTS (Paper B Governance)
- CANNOT override Risk Manager hard limits
- CANNOT exceed max 3 trades/hour
- CANNOT use position size >1.5× base
- IF win rate <45% for 15 trades → MUST return PAUSE decision
- IF daily loss >6% → MUST return CLOSE_ALL decision

## OUTPUT FORMAT
- Always return valid JSON only
- Include detailed reasoning
- Show all calculations
- No markdown code blocks, just raw JSON

---

**Current Mode:** {mode}
**Timestamp:** {timestamp}
**Context:** {context}
"""


class MetaAgentNew(BaseAgent):
    """
    Meta-Agent - Strategic Coordinator (Complete Rewrite)

    Implements:
    - Paper A: Symbolic policy optimization (MODE 4)
    - Paper B: Governance and circuit breakers
    - Two-layer evolution: Tactical weights (continuous) + Strategic policies (monthly)

    Agency Score: 7.5/10 (High - requires heavy governance)
    """

    def __init__(
        self,
        config: Optional[Dict] = None,
        weight_manager=None,
        llm_client=None,
        account_state_fn=None,
    ):
        super().__init__(AgentType.META_AGENT, config)

        # Database & services. weight_manager/llm_client are injectable so the
        # pipeline can be exercised deterministically without a DB or an API
        # key; llm_client=None makes the decision service take its deterministic
        # threshold path (see MetaDecisionService.make_decision).
        self.db = get_database()
        self.weight_manager = weight_manager or get_weight_manager()
        if llm_client is None:
            try:
                llm_client = get_llm_client()
            except Exception as e:
                logger.warning(f"⚠️ LLM client unavailable ({e}); Meta-Agent will "
                               f"use deterministic threshold logic")
        self.llm_client = llm_client
        self.account_state_fn = account_state_fn or self._live_account_state

        # Load current weights
        self.weights = self.weight_manager.get_current_weights()

        # Load evolved instructions from database
        self.evolved_instructions = self._load_evolved_instructions()

        # Initialize shared decision service (uses same logic as backtest notebook)
        self.decision_service = create_meta_decision_service(
            llm_client=self.llm_client,
            weights=self.weights,
            prompt_template=META_AGENT_PROMPT_TEMPLATE
        )

        # Governance settings (single source of truth in services/governance.py)
        self.governance = GovernanceConfig()

        # Rolling record of recent trade outcomes ('WIN'/'LOSS'), fed by MODE 3.
        # In-memory so circuit breakers work without a DB round-trip; survives
        # for the life of the process and is what check_circuit_breakers reads.
        self.recent_outcomes: deque = deque(maxlen=self.governance.circuit_breaker_lookback)
        self.trades_this_hour = 0

        # Track pending analyses for pre-trade
        self.pending_analyses: Dict[str, Dict] = {}

        logger.info("✓ Meta-Agent initialized (4-mode coordinator)")
        logger.info(f"  Weights: visual={self.weights['visual']:.4f}, "
                   f"technical={self.weights['technical']:.4f}, "
                   f"sentiment={self.weights['sentiment']:.4f}")
        logger.info(f"  Evolved instructions: {len(self.evolved_instructions)} active")

    def run(self) -> list:
        """Meta-Agent responds to messages, doesn't run on schedule"""
        return []

    def process_message(self, message: Message) -> Optional[Message]:
        """
        Route messages to appropriate mode

        Args:
            message: Incoming message

        Returns:
            Response message or None
        """
        if message.type == MessageType.ANALYSIS_RESULT:
            return self._handle_pre_trade(message)
        elif message.type == MessageType.SOFT_TRIGGER:
            return self._handle_mid_trade(message)
        # MODE 3 (post-trade) is handled by direct method call, not messages
        # MODE 4 (evolution) is triggered manually monthly

        return None

    # ============================================================
    # MODE 1: PRE-TRADE COORDINATION
    # ============================================================

    def _handle_pre_trade(self, message: Message) -> Optional[Message]:
        """
        MODE 1: Pre-Trade Coordination

        Collect analyst signals, apply weighted voting + agreement logic
        """
        symbol = message.data.get('symbol')
        analyst = message.data.get('analyst')
        signal = message.data.get('signal')
        confidence = message.data.get('confidence')
        analysis = message.data.get('analysis')

        logger.info(f"📊 Meta-Agent MODE 1: Received {analyst} analysis for {symbol}: {signal} ({confidence:.0%})")

        # Store analysis
        if symbol not in self.pending_analyses:
            self.pending_analyses[symbol] = {
                'visual': None,
                'technical': None,
                'sentiment': None,
                'timestamp': datetime.now()
            }

        self.pending_analyses[symbol][analyst] = {
            'signal': signal,
            'confidence': confidence,
            'analysis': analysis,
            # Technical carries the full per-timeframe indicator block here; it
            # is what the Risk Manager's market_context is built from.
            'full_analysis': message.data.get('full_analysis'),
        }

        # Check if all analyses received
        analyses = self.pending_analyses[symbol]
        if not self._all_analyses_received(analyses):
            return None

        logger.info(f"✓ All analyses received for {symbol}, making decision...")

        # GOVERNANCE GATE (Paper B): halt before any LLM cost if a breaker trips.
        breaker = check_circuit_breakers(
            recent_outcomes=list(self.recent_outcomes),
            account_state=self._get_account_state(),
            trades_this_hour=self.trades_this_hour,
            config=self.governance,
        )
        if breaker['tripped']:
            logger.warning(f"⛔ Circuit breaker [{breaker['action']}]: {breaker['reason']} — forcing PASS")
            del self.pending_analyses[symbol]
            return None

        # Refresh weights (bounded to governance floor/cap)
        self.weights = enforce_weight_bounds(
            self.weight_manager.get_current_weights(), self.governance
        )

        # Make decision using LLM
        decision = self._make_pre_trade_decision(symbol, analyses)

        # Clear pending
        del self.pending_analyses[symbol]

        # Send to Risk Manager if not PASS
        if decision['decision'] != 'PASS':
            return self._create_trade_signal(symbol, decision, analyses)

        return None

    def _make_pre_trade_decision(self, symbol: str, analyses: Dict) -> Dict:
        """
        Use shared MetaDecisionService for pre-trade decision.

        This ensures identical logic between production and backtest.

        Args:
            symbol: Trading symbol
            analyses: All analyst analyses

        Returns:
            Decision dict
        """
        # Update weights in decision service (in case they changed)
        self.decision_service.set_weights(self.weights)

        # Prepare analyst inputs for shared service
        visual_analysis = {
            'signal': analyses['visual']['signal'],
            'confidence': analyses['visual']['confidence']
        }
        technical_analysis = {
            'signal': analyses['technical']['signal'],
            'confidence': analyses['technical']['confidence']
        }
        sentiment_analysis = {
            'signal': analyses['sentiment']['signal'],
            'confidence': analyses['sentiment']['confidence']
        }

        # Use shared decision service (same logic as backtest notebook)
        decision = self.decision_service.make_decision(
            visual_analysis=visual_analysis,
            technical_analysis=technical_analysis,
            sentiment_analysis=sentiment_analysis,
            market_context={'symbol': symbol},
            use_llm=True
        )

        logger.info(f"🎯 Meta-Agent DECISION: {decision['decision']} "
                   f"(confidence: {decision['confidence']:.0%}, "
                   f"weighted_score: {decision['weighted_score']:.2f}, "
                   f"agreement: {decision['agreement']:.2f})")

        return decision

    def _all_analyses_received(self, analyses: Dict) -> bool:
        """Check if all analysts reported"""
        return all(analyses[a] is not None for a in ['visual', 'technical', 'sentiment'])

    @staticmethod
    def _market_context(analyses: Dict) -> Tuple[Optional[float], Dict]:
        """
        Pull entry price and the risk inputs out of the technical analysis.

        The Risk Manager needs ATR and the nearest structural levels to size a
        trade at all; without them run_risk_validation rejects everything as
        'invalid_data'. The technical analyst already computed all of it per
        timeframe, so read it from there rather than re-deriving it (or, worse,
        going back to the database at decision time).

        Returns:
            (entry_price, {'atr', 'support_level', 'resistance_level', 'spread'})
        """
        technical = analyses.get('technical') or {}
        full = technical.get('full_analysis') or technical.get('analysis') or {}
        timeframes = full.get('timeframe_analysis') or {}

        # Prefer the timeframe that led the decision, then progressively
        # shorter ones; any of them carries the same indicator block.
        lead = full.get('lead_timeframe')
        for tf in [lead, '15m', '5m', '1m', '1h', '4h']:
            indicators = (timeframes.get(tf) or {}).get('indicators') if tf else None
            if indicators and indicators.get('atr'):
                return indicators.get('close'), {
                    'atr': indicators.get('atr'),
                    'support_level': indicators.get('support'),
                    'resistance_level': indicators.get('resistance'),
                    'spread': DEFAULT_SPREAD,
                }

        return None, {}

    def _create_trade_signal(self, symbol: str, decision: Dict, analyses: Dict) -> Optional[Message]:
        """
        Create the TRADE_REQUEST the Risk Manager validates.

        This used to send MessageType.TRADE_SIGNAL, which the Risk Manager does
        not handle (it handles TRADE_REQUEST) — so every decision the Meta-Agent
        ever made was dropped by the bus one hop before validation.
        """
        entry_price, market_context = self._market_context(analyses)

        if entry_price is None or not market_context.get('atr'):
            logger.error(f"Cannot build trade request for {symbol}: technical "
                         f"analysis carried no usable price/ATR — dropping decision")
            return None

        return self.send_message(
            msg_type=MessageType.TRADE_REQUEST,
            recipient=AgentType.RISK_MANAGER,
            data={
                'symbol': symbol,
                # Risk Manager's contract: direction/entry_price/confidence/context
                'direction': decision['decision'],
                'entry_price': entry_price,
                'confidence': decision['confidence'],
                'market_context': market_context,
                # Provenance, carried through for logging and weight attribution
                'reasoning': decision.get('reasoning', ''),
                'decision': decision,
                'analyst_signals': {
                    'visual': analyses['visual']['signal'],
                    'technical': analyses['technical']['signal'],
                    'sentiment': analyses['sentiment']['signal']
                },
                'timestamp': datetime.now().isoformat()
            },
            priority=8
        )

    # ============================================================
    # MODE 2: MID-TRADE MANAGEMENT
    # ============================================================

    def _handle_mid_trade(self, message: Message) -> Optional[Message]:
        """
        MODE 2: Mid-Trade Management

        Respond to soft triggers from Trade Manager
        """
        trigger_data = message.data
        trade_id = trigger_data['trade_id']
        reason = trigger_data['reason']
        details = trigger_data['details']

        logger.info(f"⚠️ Meta-Agent MODE 2: Soft trigger for trade #{trade_id}: {reason}")

        # Use LLM to decide action
        decision = self._make_mid_trade_decision(trigger_data)

        # Send to Execution Agent
        return self._create_mid_trade_action(trade_id, decision)

    def _make_mid_trade_decision(self, trigger_data: Dict) -> Dict:
        """
        Use LLM to make mid-trade decision

        Args:
            trigger_data: Trigger details from Trade Manager

        Returns:
            Decision dict
        """
        # Build context
        context = {
            'trigger_reason': trigger_data['reason'],
            'details': trigger_data['details'],
            'trade_info': {
                'symbol': trigger_data['symbol'],
                'action': trigger_data['action'],
                'entry_price': trigger_data['entry_price'],
                'current_pnl': trigger_data['current_pnl'],
                'max_profit': trigger_data['max_profit'],
                'time_in_trade_minutes': trigger_data['time_in_trade_minutes']
            }
        }

        # Build prompt
        prompt = META_AGENT_PROMPT_TEMPLATE.format(
            visual_weight=self.weights['visual'],
            technical_weight=self.weights['technical'],
            sentiment_weight=self.weights['sentiment'],
            evolved_instructions=self.evolved_instructions or "None yet.",
            mode="MID_TRADE",
            timestamp=datetime.now().isoformat(),
            context=json.dumps(context, indent=2)
        )

        try:
            response = self.llm_client.generate(
                prompt=prompt,
                temperature=0.3,
                max_tokens=500
            )

            decision = json.loads(response)

            logger.info(f"🎯 Mid-trade decision: {decision['decision']}")

            return decision

        except Exception as e:
            logger.error(f"Error in mid-trade decision: {e}")
            # Fallback: HOLD
            return {
                'decision': 'HOLD',
                'reasoning': 'Fallback - insufficient data',
                'urgency': 'low'
            }

    def _create_mid_trade_action(self, trade_id: int, decision: Dict) -> Message:
        """Create mid-trade action message for Execution Agent"""
        return self.send_message(
            msg_type=MessageType.MID_TRADE_ACTION,
            recipient=AgentType.EXECUTION,
            data={
                'trade_id': trade_id,
                'action': decision['decision'],
                'exit_percentage': decision.get('exit_percentage', 0),
                'new_sl': decision.get('new_sl'),
                'new_tp': decision.get('new_tp'),
                'reasoning': decision.get('reasoning', ''),
                'urgency': decision.get('urgency', 'medium')
            },
            priority=9
        )

    # ============================================================
    # MODE 3: POST-TRADE WEIGHT ADJUSTMENT (Delegated to weight_manager)
    # ============================================================

    def update_weights_after_trade(
        self,
        analyst_signals: Dict[str, str],
        trade_outcome: str,
        trade_id: Optional[int] = None
    ):
        """
        MODE 3: Post-Trade Weight Adjustment (Layer 1: Tactical)

        Delegated to weight_manager service (no LLM needed)

        Args:
            analyst_signals: Analyst signals that led to trade
            trade_outcome: 'WIN' or 'LOSS'
            trade_id: Trade ID
        """
        logger.info(f"📊 Meta-Agent MODE 3: Updating weights after {trade_outcome}")

        # Feed the governance window so circuit breakers reflect recent reality.
        self.recent_outcomes.append(str(trade_outcome).upper())

        updated_weights = self.weight_manager.update_all_weights_after_trade(
            analyst_signals,
            trade_outcome,
            trade_id
        )

        # Keep weights within governance floor/cap on every update.
        self.weights = enforce_weight_bounds(updated_weights, self.governance)

        logger.info(f"  New weights: {self.weights}")

    def _get_account_state(self) -> Dict:
        """Account state for governance checks (injected source)."""
        return self.account_state_fn()

    def _live_account_state(self) -> Dict:
        """
        Default account-state source: the AccountStateManager. Returns a safe
        default if unavailable so a missing account row never blocks a decision
        by throwing.
        """
        try:
            from services.account_state_manager import get_account_state_manager
            return get_account_state_manager().get_current_state() or {}
        except Exception as e:
            logger.debug(f"Account state unavailable ({e}); defaulting to flat")
            return {'daily_pnl_pct': 0.0}

    # ============================================================
    # MODE 4: MONTHLY SELF-EVOLUTION (Paper A)
    # ============================================================

    def run_monthly_evolution(self) -> List[Dict]:
        """
        MODE 4: Monthly Self-Evolution (Layer 2: Strategic, Paper A)

        Analyze performance, propose symbolic policy changes

        Returns:
            List of evolution proposals for human approval
        """
        logger.info("🧬 Meta-Agent MODE 4: Running monthly self-evolution analysis...")

        # Get performance summary
        performance_summary = self._get_evolution_performance_summary()

        if performance_summary['trade_count'] < 100:
            logger.warning("Insufficient trades for evolution (<100)")
            return []

        # Use LLM to analyze and propose changes
        proposals = self._generate_evolution_proposals(performance_summary)

        # Store proposals in database
        for proposal in proposals:
            self._store_evolution_proposal(proposal)

        logger.info(f"✓ Generated {len(proposals)} evolution proposals")

        return proposals

    def _get_evolution_performance_summary(self) -> Dict:
        """Get comprehensive performance summary for evolution analysis"""
        # Implementation would query database for performance metrics
        # Simplified for now
        return {
            'trade_count': 150,
            'overall_win_rate': 0.52,
            'win_rate_by_confidence': {
                'high': 0.58,
                'medium': 0.51,
                'low': 0.42
            },
            'win_rate_by_agreement': {
                'high': 0.56,
                'medium': 0.49,
                'low': 0.38
            },
            'systematic_failures': [
                {
                    'pattern': 'Low agreement trades (<0.5) have 38% win rate',
                    'sample_size': 45,
                    'p_value': 0.01
                }
            ]
        }

    def _generate_evolution_proposals(self, performance_summary: Dict) -> List[Dict]:
        """
        Use LLM to generate symbolic policy change proposals

        Args:
            performance_summary: Performance data

        Returns:
            List of proposals
        """
        context = {
            'performance': performance_summary,
            'current_thresholds': {
                'weighted_score_high': self.weighted_score_threshold_high,
                'weighted_score_low': self.weighted_score_threshold_low,
                'agreement_high': self.agreement_threshold_high,
                'agreement_low': self.agreement_threshold_low
            }
        }

        prompt = META_AGENT_PROMPT_TEMPLATE.format(
            visual_weight=self.weights['visual'],
            technical_weight=self.weights['technical'],
            sentiment_weight=self.weights['sentiment'],
            evolved_instructions=self.evolved_instructions or "None yet.",
            mode="EVOLUTION",
            timestamp=datetime.now().isoformat(),
            context=json.dumps(context, indent=2)
        )

        try:
            response = self.llm_client.generate(
                prompt=prompt,
                temperature=0.4,
                max_tokens=2000
            )

            result = json.loads(response)
            proposals = result.get('proposals', [])

            return proposals

        except Exception as e:
            logger.error(f"Error generating evolution proposals: {e}")
            return []

    def _store_evolution_proposal(self, proposal: Dict):
        """Store proposal in database for human approval"""
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO meta_agent_evolution_proposals (
                        type, target, instruction, rationale, evidence, status
                    ) VALUES (%s, %s, %s, %s, %s, 'PENDING')
                """, (
                    proposal['type'],
                    proposal['target'],
                    proposal['instruction'],
                    proposal['rationale'],
                    json.dumps(proposal.get('evidence', {}))
                ))

        except Exception as e:
            logger.error(f"Error storing evolution proposal: {e}")

    def _load_evolved_instructions(self) -> str:
        """Load evolved instructions from database (Section 7)"""
        try:
            with self.db.get_cursor() as cur:
                cur.execute("""
                    SELECT instruction
                    FROM meta_agent_evolution_proposals
                    WHERE status = 'DEPLOYED'
                      AND target = 'meta_agent'
                    ORDER BY deployed_at DESC
                """)

                rows = cur.fetchall()
                instructions = [row[0] for row in rows]

                return "\n".join(instructions) if instructions else ""

        except Exception as e:
            logger.error(f"Error loading evolved instructions: {e}")
            return ""
