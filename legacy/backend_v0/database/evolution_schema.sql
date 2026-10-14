-- ============================================================
-- Agent Evolution System - Database Schema
-- ============================================================
-- Implements Paper A (Symbolic Policy Evolution) + Paper B (Governance)
-- Two-layer adaptation: Weights (tactical) + Policy Evolution (strategic)
-- ============================================================

-- ============================================================
-- Agent Weights Table
-- ============================================================
-- Tracks dynamic weights for weighted voting (Layer 1: Tactical)
CREATE TABLE IF NOT EXISTS agent_weights (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Current weights (normalized, sum to 1.0)
    visual_weight DECIMAL(5, 4) NOT NULL DEFAULT 0.35,
    technical_weight DECIMAL(5, 4) NOT NULL DEFAULT 0.40,
    sentiment_weight DECIMAL(5, 4) NOT NULL DEFAULT 0.25,

    -- Evolution epoch tracking
    epoch INT NOT NULL DEFAULT 1,

    -- Metadata
    notes TEXT,

    CONSTRAINT weights_sum_check CHECK (
        visual_weight + technical_weight + sentiment_weight BETWEEN 0.99 AND 1.01
    )
);

CREATE INDEX idx_agent_weights_timestamp ON agent_weights(timestamp DESC);
CREATE INDEX idx_agent_weights_epoch ON agent_weights(epoch);

-- ============================================================
-- Weight History Table
-- ============================================================
-- Audit trail of weight adjustments
CREATE TABLE IF NOT EXISTS weight_history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Which analyst's weight changed
    analyst VARCHAR(50) NOT NULL,  -- 'visual', 'technical', 'sentiment'

    -- Weight change details
    weight_before DECIMAL(5, 4) NOT NULL,
    weight_after DECIMAL(5, 4) NOT NULL,
    weight_delta DECIMAL(6, 4) NOT NULL,  -- After - Before (can be negative)

    -- Trigger information
    trigger_type VARCHAR(50) NOT NULL,  -- 'WIN', 'LOSS', 'EPOCH_RESET', 'MANUAL'
    trade_id INT REFERENCES executed_trades(id),

    -- Epoch tracking
    epoch INT NOT NULL,

    -- Metadata
    notes TEXT
);

CREATE INDEX idx_weight_history_analyst ON weight_history(analyst);
CREATE INDEX idx_weight_history_timestamp ON weight_history(timestamp DESC);
CREATE INDEX idx_weight_history_epoch ON weight_history(epoch);

-- ============================================================
-- Visual Agent Policies Table
-- ============================================================
-- Version control for Visual Agent prompts (Layer 2: Strategic)
CREATE TABLE IF NOT EXISTS visual_agent_policies (
    version VARCHAR(20) PRIMARY KEY,  -- e.g., 'v1.0', 'v1.1', 'v2.0'
    prompt TEXT NOT NULL,

    -- Deployment tracking
    deployed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deployed_by VARCHAR(100) DEFAULT 'system',
    is_active BOOLEAN DEFAULT FALSE,

    -- Evolution context
    evolution_rationale TEXT,  -- Why this policy was created
    parent_version VARCHAR(20),  -- Previous version (NULL for v1.0)

    -- Performance tracking
    performance_before JSONB,  -- Stats from parent version
    performance_after JSONB,   -- Stats after deployment (updated later)

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_policies_deployed_at ON visual_agent_policies(deployed_at DESC);
CREATE INDEX idx_policies_active ON visual_agent_policies(is_active) WHERE is_active = TRUE;

-- ============================================================
-- Evolution Proposals Table
-- ============================================================
-- Stores policy change proposals from Meta-Agent Strategist
CREATE TABLE IF NOT EXISTS evolution_proposals (
    id SERIAL PRIMARY KEY,

    -- Proposal details
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    proposed_by VARCHAR(100) DEFAULT 'meta_agent_strategist',

    -- Policy change details
    operation VARCHAR(20) NOT NULL,  -- 'ADD', 'MODIFY', 'REMOVE'
    instruction TEXT NOT NULL,  -- Specific rule to add/modify/remove
    rationale TEXT NOT NULL,  -- Why this will improve performance
    expected_impact TEXT,  -- Expected improvements and trade-offs

    -- Current policy context
    current_version VARCHAR(20) REFERENCES visual_agent_policies(version),
    proposed_version VARCHAR(20),  -- Version number for new policy
    proposed_prompt TEXT,  -- Full prompt with changes applied

    -- Performance analysis that triggered proposal
    trigger_analysis JSONB,  -- Performance summary, edge cases, etc.
    analyses_count INT NOT NULL,  -- Number of analyses evaluated

    -- Approval workflow
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- 'PENDING', 'APPROVED', 'REJECTED', 'DEPLOYED'
    reviewed_at TIMESTAMPTZ,
    reviewed_by VARCHAR(100),
    rejection_reason TEXT,

    -- Post-deployment tracking
    deployed_at TIMESTAMPTZ,
    performance_after_deployment JSONB
);

CREATE INDEX idx_proposals_status ON evolution_proposals(status);
CREATE INDEX idx_proposals_proposed_at ON evolution_proposals(proposed_at DESC);
CREATE INDEX idx_proposals_current_version ON evolution_proposals(current_version);

-- ============================================================
-- Evolution Epochs Table
-- ============================================================
-- Tracks evolution epochs (periods between policy changes)
CREATE TABLE IF NOT EXISTS evolution_epochs (
    epoch INT PRIMARY KEY,

    -- Time period
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,

    -- Policy version during this epoch
    policy_version VARCHAR(20) REFERENCES visual_agent_policies(version),

    -- Performance metrics for this epoch
    total_analyses INT DEFAULT 0,
    win_rate DECIMAL(5, 2),
    avg_confidence DECIMAL(5, 2),
    accuracy_rate DECIMAL(5, 2),
    brier_score DECIMAL(6, 4),  -- Confidence calibration

    -- Pattern-specific performance
    pattern_performance JSONB,  -- Win rates per pattern type
    regime_performance JSONB,   -- Win rates per market regime

    -- What triggered epoch end
    end_trigger VARCHAR(50),  -- 'POLICY_EVOLUTION', 'MANUAL_RESET', NULL (ongoing)

    -- Notes
    notes TEXT
);

CREATE INDEX idx_epochs_started_at ON evolution_epochs(started_at DESC);
CREATE INDEX idx_epochs_policy_version ON evolution_epochs(policy_version);

-- ============================================================
-- Governance Metrics Table
-- ============================================================
-- Tracks circuit breaker triggers and anomalies (Paper B)
CREATE TABLE IF NOT EXISTS governance_metrics (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Metric type
    metric_type VARCHAR(50) NOT NULL,  -- 'ACCURACY', 'CALIBRATION', 'VELOCITY', 'WEIGHT'
    metric_value DECIMAL(10, 4) NOT NULL,

    -- Agent being monitored
    analyst VARCHAR(50) NOT NULL,

    -- Threshold violations
    threshold_value DECIMAL(10, 4),
    threshold_violated BOOLEAN DEFAULT FALSE,

    -- Circuit breaker action
    action_taken VARCHAR(50),  -- 'ALERT', 'PAUSE_AGENT', 'REDUCE_CONFIDENCE', 'NONE'

    -- Context
    window_size INT,  -- Rolling window size (e.g., 50 analyses)
    epoch INT,

    -- Details
    details JSONB
);

CREATE INDEX idx_governance_timestamp ON governance_metrics(timestamp DESC);
CREATE INDEX idx_governance_analyst ON governance_metrics(analyst);
CREATE INDEX idx_governance_violations ON governance_metrics(threshold_violated) WHERE threshold_violated = TRUE;

-- ============================================================
-- Views
-- ============================================================

-- View: Current Active Policy
CREATE OR REPLACE VIEW v_active_policy AS
SELECT
    version,
    prompt,
    deployed_at,
    deployed_by,
    evolution_rationale,
    parent_version,
    performance_after
FROM visual_agent_policies
WHERE is_active = TRUE
LIMIT 1;

-- View: Current Weights
CREATE OR REPLACE VIEW v_current_weights AS
SELECT
    visual_weight,
    technical_weight,
    sentiment_weight,
    epoch,
    timestamp
FROM agent_weights
ORDER BY timestamp DESC
LIMIT 1;

-- View: Weight Change History (Last 100)
CREATE OR REPLACE VIEW v_weight_changes AS
SELECT
    wh.timestamp,
    wh.analyst,
    wh.weight_before,
    wh.weight_after,
    wh.weight_delta,
    wh.trigger_type,
    et.symbol,
    et.action,
    et.net_pnl,
    wh.epoch
FROM weight_history wh
LEFT JOIN executed_trades et ON wh.trade_id = et.id
ORDER BY wh.timestamp DESC
LIMIT 100;

-- View: Evolution History
CREATE OR REPLACE VIEW v_evolution_history AS
SELECT
    ep.id AS proposal_id,
    ep.proposed_at,
    ep.operation,
    ep.instruction,
    ep.rationale,
    ep.status,
    ep.reviewed_at,
    ep.reviewed_by,
    ep.current_version,
    ep.proposed_version,
    ep.analyses_count,
    vap.deployed_at,
    vap.performance_after
FROM evolution_proposals ep
LEFT JOIN visual_agent_policies vap ON ep.proposed_version = vap.version
ORDER BY ep.proposed_at DESC;

-- View: Epoch Performance Summary
CREATE OR REPLACE VIEW v_epoch_performance AS
SELECT
    ee.epoch,
    ee.started_at,
    ee.ended_at,
    ee.policy_version,
    ee.total_analyses,
    ee.win_rate,
    ee.accuracy_rate,
    ee.brier_score,
    ee.end_trigger,
    vap.evolution_rationale,
    EXTRACT(EPOCH FROM (COALESCE(ee.ended_at, NOW()) - ee.started_at)) / 86400 AS duration_days
FROM evolution_epochs ee
LEFT JOIN visual_agent_policies vap ON ee.policy_version = vap.version
ORDER BY ee.epoch DESC;

-- ============================================================
-- Functions
-- ============================================================

-- Function: Get Current Weights
CREATE OR REPLACE FUNCTION get_current_weights()
RETURNS TABLE (
    visual DECIMAL,
    technical DECIMAL,
    sentiment DECIMAL,
    epoch INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        visual_weight,
        technical_weight,
        sentiment_weight,
        agent_weights.epoch
    FROM agent_weights
    ORDER BY timestamp DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- Function: Update Weight After Trade
CREATE OR REPLACE FUNCTION update_weight_after_trade(
    p_analyst VARCHAR(50),
    p_is_win BOOLEAN,
    p_trade_id INT,
    p_current_epoch INT
) RETURNS VOID AS $$
DECLARE
    v_current_weight DECIMAL(5, 4);
    v_new_weight DECIMAL(5, 4);
    v_multiplier DECIMAL(5, 4);
    v_total DECIMAL(5, 4);
    v_visual DECIMAL(5, 4);
    v_technical DECIMAL(5, 4);
    v_sentiment DECIMAL(5, 4);
BEGIN
    -- Get current weights
    SELECT visual_weight, technical_weight, sentiment_weight
    INTO v_visual, v_technical, v_sentiment
    FROM agent_weights
    ORDER BY timestamp DESC
    LIMIT 1;

    -- Get analyst's current weight
    v_current_weight := CASE p_analyst
        WHEN 'visual' THEN v_visual
        WHEN 'technical' THEN v_technical
        WHEN 'sentiment' THEN v_sentiment
    END;

    -- Calculate new weight (win: *1.05, loss: *0.95)
    v_multiplier := CASE WHEN p_is_win THEN 1.05 ELSE 0.95 END;
    v_new_weight := v_current_weight * v_multiplier;

    -- Update analyst's weight
    IF p_analyst = 'visual' THEN
        v_visual := v_new_weight;
    ELSIF p_analyst = 'technical' THEN
        v_technical := v_new_weight;
    ELSIF p_analyst = 'sentiment' THEN
        v_sentiment := v_new_weight;
    END IF;

    -- Normalize weights to sum to 1.0
    v_total := v_visual + v_technical + v_sentiment;
    v_visual := v_visual / v_total;
    v_technical := v_technical / v_total;
    v_sentiment := v_sentiment / v_total;

    -- Insert new weight snapshot
    INSERT INTO agent_weights (visual_weight, technical_weight, sentiment_weight, epoch)
    VALUES (v_visual, v_technical, v_sentiment, p_current_epoch);

    -- Record weight change in history
    INSERT INTO weight_history (
        analyst,
        weight_before,
        weight_after,
        weight_delta,
        trigger_type,
        trade_id,
        epoch
    ) VALUES (
        p_analyst,
        v_current_weight,
        CASE p_analyst
            WHEN 'visual' THEN v_visual
            WHEN 'technical' THEN v_technical
            WHEN 'sentiment' THEN v_sentiment
        END,
        CASE p_analyst
            WHEN 'visual' THEN v_visual - v_current_weight
            WHEN 'technical' THEN v_technical - v_current_weight
            WHEN 'sentiment' THEN v_sentiment - v_current_weight
        END,
        CASE WHEN p_is_win THEN 'WIN' ELSE 'LOSS' END,
        p_trade_id,
        p_current_epoch
    );
END;
$$ LANGUAGE plpgsql;

-- Function: Start New Evolution Epoch
CREATE OR REPLACE FUNCTION start_new_evolution_epoch(
    p_policy_version VARCHAR(20),
    p_notes TEXT DEFAULT NULL
) RETURNS INT AS $$
DECLARE
    v_new_epoch INT;
    v_prev_epoch INT;
BEGIN
    -- Get previous epoch number
    SELECT COALESCE(MAX(epoch), 0) INTO v_prev_epoch FROM evolution_epochs;
    v_new_epoch := v_prev_epoch + 1;

    -- Close previous epoch if exists
    IF v_prev_epoch > 0 THEN
        UPDATE evolution_epochs
        SET ended_at = NOW(),
            end_trigger = 'POLICY_EVOLUTION'
        WHERE epoch = v_prev_epoch AND ended_at IS NULL;
    END IF;

    -- Create new epoch
    INSERT INTO evolution_epochs (epoch, policy_version, notes)
    VALUES (v_new_epoch, p_policy_version, p_notes);

    -- Update agent weights with new epoch
    INSERT INTO agent_weights (visual_weight, technical_weight, sentiment_weight, epoch)
    SELECT visual_weight, technical_weight, sentiment_weight, v_new_epoch
    FROM agent_weights
    ORDER BY timestamp DESC
    LIMIT 1;

    RETURN v_new_epoch;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Initial Data
-- ============================================================

-- Insert baseline policy (V1.0)
INSERT INTO visual_agent_policies (version, prompt, deployed_by, is_active, evolution_rationale)
VALUES (
    'v1.0',
    'You are an expert technical analyst specializing in chart pattern recognition for XAUUSD (spot gold).

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
- Be conservative - it''s better to skip trades than force them
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

Provide only the JSON output, no additional text.',
    'system',
    TRUE,
    'Initial baseline policy with FVG/IFVG patterns'
) ON CONFLICT (version) DO NOTHING;

-- Insert baseline weights
INSERT INTO agent_weights (visual_weight, technical_weight, sentiment_weight, epoch)
VALUES (0.35, 0.40, 0.25, 1)
ON CONFLICT DO NOTHING;

-- Create first epoch
INSERT INTO evolution_epochs (epoch, policy_version, notes)
VALUES (1, 'v1.0', 'Initial baseline epoch')
ON CONFLICT (epoch) DO NOTHING;

-- ============================================================
-- Comments
-- ============================================================

COMMENT ON TABLE agent_weights IS 'Dynamic weights for weighted voting (Layer 1: Tactical adaptation)';
COMMENT ON TABLE weight_history IS 'Audit trail of weight changes after each trade outcome';
COMMENT ON TABLE visual_agent_policies IS 'Version control for Visual Agent prompts (Layer 2: Strategic evolution)';
COMMENT ON TABLE evolution_proposals IS 'Policy change proposals from Meta-Agent Strategist requiring human approval';
COMMENT ON TABLE evolution_epochs IS 'Tracks evolution epochs (periods between policy changes)';
COMMENT ON TABLE governance_metrics IS 'Circuit breaker triggers and performance anomalies (Paper B governance)';
