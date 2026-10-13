# backend/services/meta_agent_strategist.py
"""
Meta-Agent Strategist Mode

Offline analysis mode for policy evolution (Layer 2: Strategic)
Analyzes Visual Agent performance and proposes policy improvements
"""

import logging
import json
from typing import Dict, Optional

from config.llm_provider import get_llm_client
from services.performance_tracker import get_performance_tracker
from config.database import get_database

logger = logging.getLogger(__name__)


STRATEGIST_PROMPT_TEMPLATE = """You are the Strategist for the Visual Agent in a multi-agent trading system.

Your role: Analyze Visual Agent performance data and propose policy improvements.

## Current Visual Agent Policy (Version {current_version}):
{current_policy}

## Performance Summary (last {analyses_count} analyses):

**Overall Statistics:**
- Total Analyses: {total_analyses}
- Win Rate: {win_rate}%
- Average Confidence: {avg_confidence}%
- Calibration Score: {calibration_score}/100 (higher = better calibrated)

**Pattern Performance:**
{pattern_performance}

**Market Regime Performance:**
{regime_performance}

**Identified Failure Patterns:**
{failure_patterns}

## Top 5 Best Analyses (High Confidence + Win):
{best_analyses}

## Top 5 Worst Analyses (High Confidence + Loss):
{worst_analyses}

---

## Your Task:

Identify **systematic failures** (NOT temporary regime shifts) and propose ONE policy change.

**Important Guidelines:**
1. Only propose changes for systematic biases visible across multiple regimes
2. Do NOT propose changes for regime-specific performance (weights handle that)
3. Be specific: "Add rule X" not "Improve pattern recognition"
4. Consider trade-offs: will this reduce false positives? Will it reduce signal frequency?

**Output Format (JSON only):**
```json
{{
  "operation": "ADD|MODIFY|REMOVE",
  "instruction": "Specific rule to add/modify/remove in the prompt",
  "rationale": "Why this will improve systematic performance (2-3 sentences)",
  "expected_impact": "Expected improvement and trade-offs (1-2 sentences)"
}}
```

**Example Operations:**
- ADD: "For bull flag patterns: require volume >1.5x average AND trending regime. In ranging markets, reduce confidence by 50%."
- MODIFY: "Change head & shoulders confidence threshold from >70% to >80% due to high false positive rate."
- REMOVE: "Remove IFVG pattern recognition - showing 35% win rate across all regimes."

Provide ONLY the JSON output, no additional text.
"""


class MetaAgentStrategist:
    """
    Meta-Agent Strategist - Policy Evolution Analyzer

    Runs offline to analyze performance and propose improvements
    """

    def __init__(self):
        self.db = get_database()
        self.performance_tracker = get_performance_tracker()
        self.llm_client = None

        try:
            self.llm_client = get_llm_client()
            logger.info("✓ Meta-Agent Strategist initialized")
        except Exception as e:
            logger.warning(f"⚠️ LLM client not available for Strategist: {e}")

    def analyze_and_propose(
        self,
        min_analyses: int = 100
    ) -> Optional[Dict]:
        """
        Analyze Visual Agent performance and propose policy evolution

        Args:
            min_analyses: Minimum analyses before proposing evolution

        Returns:
            Evolution proposal dict or None if not ready
        """
        # Check if evolution should be triggered
        if not self.performance_tracker.check_evolution_trigger(min_analyses):
            logger.info("Evolution not triggered yet (insufficient data)")
            return None

        logger.info(f"🧠 Meta-Agent Strategist: Analyzing performance for evolution...")

        # Get performance summary
        summary = self.performance_tracker.get_evolution_summary(min_analyses)

        # Get current policy
        current_policy = self._get_current_policy()

        if not current_policy:
            logger.error("No current policy found, cannot propose evolution")
            return None

        # Generate proposal using LLM
        proposal = self._generate_proposal(current_policy, summary)

        if not proposal:
            logger.warning("Failed to generate evolution proposal")
            return None

        # Store proposal in database
        proposal_id = self._store_proposal(current_policy['version'], proposal, summary)

        proposal['id'] = proposal_id
        proposal['current_version'] = current_policy['version']

        logger.info(f"✓ Evolution proposal generated: {proposal['operation']} - {proposal['instruction'][:80]}")

        return proposal

    def _get_current_policy(self) -> Optional[Dict]:
        """Get current active Visual Agent policy"""
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT version, prompt, deployed_at
                FROM visual_agent_policies
                WHERE is_active = TRUE
                LIMIT 1
            """)

            row = cur.fetchone()

            if not row:
                return None

            return {
                'version': row[0],
                'prompt': row[1],
                'deployed_at': row[2]
            }

    def _generate_proposal(
        self,
        current_policy: Dict,
        summary: Dict
    ) -> Optional[Dict]:
        """
        Generate evolution proposal using LLM

        Args:
            current_policy: Current policy dict
            summary: Performance summary dict

        Returns:
            Proposal dict or None
        """
        if not self.llm_client:
            logger.error("LLM client not available")
            return None

        # Format prompt
        prompt = self._format_strategist_prompt(current_policy, summary)

        try:
            # Call LLM
            response = self.llm_client.generate(
                prompt=prompt,
                temperature=0.3,
                max_tokens=1000
            )

            # Parse JSON response
            proposal = self._parse_proposal_response(response)

            return proposal

        except Exception as e:
            logger.error(f"Error generating proposal: {e}")
            return None

    def _format_strategist_prompt(
        self,
        current_policy: Dict,
        summary: Dict
    ) -> str:
        """Format strategist prompt with performance data"""

        # Format pattern performance
        pattern_lines = []
        for pattern, stats in summary['pattern_performance'].items():
            pattern_lines.append(
                f"  - {pattern}: {stats['attempts']} attempts, "
                f"{stats['win_rate']:.1f}% win rate"
            )
        pattern_text = "\n".join(pattern_lines) if pattern_lines else "  No pattern data available"

        # Format regime performance
        regime_lines = []
        for regime, stats in summary['regime_performance'].items():
            regime_lines.append(
                f"  - {regime}: {stats['analyses']} analyses, "
                f"{stats['win_rate']:.1f}% win rate"
            )
        regime_text = "\n".join(regime_lines) if regime_lines else "  No regime data available"

        # Format failure patterns
        failure_lines = []
        for failure in summary['failure_patterns']:
            failure_lines.append(
                f"  - {failure['type']}: {failure.get('pattern', failure.get('regime', 'N/A'))} "
                f"({failure['win_rate']:.1f}% win rate, {failure['sample_size']} samples)"
            )
        failure_text = "\n".join(failure_lines) if failure_lines else "  No systematic failures identified"

        # Format best analyses
        best_lines = []
        for i, analysis in enumerate(summary['best_analyses'][:5], 1):
            best_lines.append(
                f"{i}. {analysis['signal']} @ {analysis['confidence']:.0f}% confidence: "
                f"{analysis['reasoning'][:100]}"
            )
        best_text = "\n".join(best_lines) if best_lines else "No data available"

        # Format worst analyses
        worst_lines = []
        for i, analysis in enumerate(summary['worst_analyses'][:5], 1):
            worst_lines.append(
                f"{i}. {analysis['signal']} @ {analysis['confidence']:.0f}% confidence: "
                f"{analysis['reasoning'][:100]}"
            )
        worst_text = "\n".join(worst_lines) if worst_lines else "No data available"

        return STRATEGIST_PROMPT_TEMPLATE.format(
            current_version=current_policy['version'],
            current_policy=current_policy['prompt'],
            analyses_count=summary['analyses_count'],
            total_analyses=summary['overall']['total_analyses'],
            win_rate=summary['overall']['win_rate'],
            avg_confidence=summary['overall']['avg_confidence'],
            calibration_score=summary['overall']['calibration_score'],
            pattern_performance=pattern_text,
            regime_performance=regime_text,
            failure_patterns=failure_text,
            best_analyses=best_text,
            worst_analyses=worst_text
        )

    def _parse_proposal_response(self, response: str) -> Optional[Dict]:
        """Parse LLM response to extract proposal JSON"""
        try:
            # Extract JSON from response (handle markdown code blocks)
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                json_str = response.strip()

            proposal = json.loads(json_str)

            # Validate required fields
            required_fields = ['operation', 'instruction', 'rationale', 'expected_impact']
            for field in required_fields:
                if field not in proposal:
                    logger.error(f"Missing required field in proposal: {field}")
                    return None

            # Validate operation type
            if proposal['operation'] not in ['ADD', 'MODIFY', 'REMOVE']:
                logger.error(f"Invalid operation: {proposal['operation']}")
                return None

            return proposal

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse proposal JSON: {e}")
            logger.error(f"Response: {response[:500]}")
            return None

    def _store_proposal(
        self,
        current_version: str,
        proposal: Dict,
        summary: Dict
    ) -> int:
        """Store evolution proposal in database"""
        try:
            # Generate new version number
            major, minor = current_version[1:].split('.')
            new_version = f"v{major}.{int(minor) + 1}"

            with self.db.get_cursor() as cur:
                cur.execute("""
                    INSERT INTO evolution_proposals (
                        operation,
                        instruction,
                        rationale,
                        expected_impact,
                        current_version,
                        proposed_version,
                        trigger_analysis,
                        analyses_count,
                        status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING')
                    RETURNING id
                """, (
                    proposal['operation'],
                    proposal['instruction'],
                    proposal['rationale'],
                    proposal['expected_impact'],
                    current_version,
                    new_version,
                    json.dumps(summary),
                    summary['analyses_count']
                ))

                proposal_id = cur.fetchone()[0]

                logger.info(f"✓ Stored evolution proposal #{proposal_id}: {current_version} -> {new_version}")

                return proposal_id

        except Exception as e:
            logger.error(f"Error storing proposal: {e}")
            return -1

    def get_pending_proposals(self) -> List[Dict]:
        """Get all pending evolution proposals awaiting approval"""
        with self.db.get_cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    proposed_at,
                    operation,
                    instruction,
                    rationale,
                    expected_impact,
                    current_version,
                    proposed_version,
                    analyses_count
                FROM evolution_proposals
                WHERE status = 'PENDING'
                ORDER BY proposed_at DESC
            """)

            rows = cur.fetchall()

            return [
                {
                    'id': row[0],
                    'proposed_at': row[1].isoformat() if row[1] else None,
                    'operation': row[2],
                    'instruction': row[3],
                    'rationale': row[4],
                    'expected_impact': row[5],
                    'current_version': row[6],
                    'proposed_version': row[7],
                    'analyses_count': row[8]
                }
                for row in rows
            ]


# Singleton instance
_strategist: Optional[MetaAgentStrategist] = None


def get_strategist() -> MetaAgentStrategist:
    """
    Get singleton instance of MetaAgentStrategist

    Returns:
        MetaAgentStrategist instance
    """
    global _strategist
    if _strategist is None:
        _strategist = MetaAgentStrategist()
    return _strategist
