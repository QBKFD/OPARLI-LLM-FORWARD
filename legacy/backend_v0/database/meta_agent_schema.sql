-- ============================================================
-- Meta-Agent Database Schema
-- ============================================================
-- Stores all Meta-Agent decisions, evolution proposals, and audit logs
-- ============================================================

-- ============================================================
-- Meta-Agent Decisions Table
-- ============================================================
-- Records all decisions made by Meta-Agent across all 4 modes
CREATE TABLE IF NOT EXISTS meta_agent_decisions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Decision mode
    mode VARCHAR(20) NOT NULL,  -- 'PRE_TRADE', 'MID_TRADE', 'POST_TRADE', 'EVOLUTION'

    -- Symbol and trade reference
    symbol VARCHAR(20),
    trade_id INT REFERENCES executed_trades(id),

    -- Decision details
    decision VARCHAR(50) NOT NULL,  -- 'LONG', 'SHORT', 'PASS', 'HOLD', 'PARTIAL_EXIT', etc.
    confidence DECIMAL(5, 2),
    reasoning TEXT NOT NULL,

    -- Pre-trade specific
    weighted_score DECIMAL(5, 4),
    agreement DECIMAL(5, 4),
    analyst_signals JSONB,  -- {'visual': 'LONG', 'technical': 'LONG', 'sentiment': 'PASS'}
    analyst_confidences JSONB,  -- {'visual': 0.85, 'technical': 0.70, 'sentiment': 0.60}
    weights_used JSONB,  -- {'visual': 0.35, 'technical': 0.40, 'sentiment': 0.25}

    -- Mid-trade specific
    trigger_reason VARCHAR(50),  -- Soft trigger that caused mid-trade decision
    exit_percentage INT,
    new_sl DECIMAL(10, 2),
    new_tp DECIMAL(10, 2),

    -- Full context (JSONB for flexibility)
    full_context JSONB,

    -- Outcome tracking
    outcome VARCHAR(20),  -- 'WIN', 'LOSS', 'PENDING', 'CANCELLED'
    outcome_pnl DECIMAL(10, 2),

    -- Metadata
    execution_time_ms INT,  -- How long decision took
    llm_used BOOLEAN DEFAULT TRUE,
    llm_tokens_used INT
);

CREATE INDEX idx_meta_decisions_timestamp ON meta_agent_decisions(timestamp DESC);
CREATE INDEX idx_meta_decisions_mode ON meta_agent_decisions(mode);
CREATE INDEX idx_meta_decisions_symbol ON meta_agent_decisions(symbol);
CREATE INDEX idx_meta_decisions_decision ON meta_agent_decisions(decision);
CREATE INDEX idx_meta_decisions_outcome ON meta_agent_decisions(outcome);
CREATE INDEX idx_meta_decisions_trade_id ON meta_agent_decisions(trade_id);

-- ============================================================
-- Meta-Agent Evolution Proposals Table
-- ============================================================
-- Stores symbolic policy change proposals (Paper A framework)
CREATE TABLE IF NOT EXISTS meta_agent_evolution_proposals (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Proposal details
    type VARCHAR(20) NOT NULL,  -- 'ADD', 'MODIFY', 'REMOVE'
    target VARCHAR(50) NOT NULL,  -- 'meta_agent', 'visual_agent', 'technical_agent', 'sentiment_agent'
    instruction TEXT NOT NULL,  -- The specific change to make
    rationale TEXT NOT NULL,  -- Why this will improve performance

    -- Evidence
    evidence JSONB,  -- {sample_size: 150, p_value: 0.01, win_rate_before: 0.52, ...}
    performance_analysis JSONB,  -- Full performance breakdown

    -- Approval workflow
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- 'PENDING', 'APPROVED', 'REJECTED', 'DEPLOYED'
    reviewed_at TIMESTAMPTZ,
    reviewed_by VARCHAR(100),
    rejection_reason TEXT,

    -- Deployment tracking
    deployed_at TIMESTAMPTZ,
    deployed_version VARCHAR(20),  -- e.g., 'v1.6'
    rollback_version VARCHAR(20),  -- Previous version for rollback

    -- Post-deployment monitoring
    monitoring_trades INT DEFAULT 0,
    monitoring_win_rate DECIMAL(5, 2),
    monitoring_status VARCHAR(20),  -- 'MONITORING', 'SUCCESS', 'DEGRADED', 'ROLLED_BACK'

    -- Metadata
    epoch INT,  -- Evolution epoch when proposed
    proposal_month DATE  -- Month this was proposed (for monthly limit)
);

CREATE INDEX idx_evolution_proposals_status ON meta_agent_evolution_proposals(status);
CREATE INDEX idx_evolution_proposals_target ON meta_agent_evolution_proposals(target);
CREATE INDEX idx_evolution_proposals_created_at ON meta_agent_evolution_proposals(created_at DESC);
CREATE INDEX idx_evolution_proposals_deployed_at ON meta_agent_evolution_proposals(deployed_at DESC);

-- ============================================================
-- Meta-Agent Evolved Instructions Table
-- ============================================================
-- Stores active evolved instructions (Section 7 of prompt)
CREATE TABLE IF NOT EXISTS meta_agent_evolved_instructions (
    id SERIAL PRIMARY KEY,
    proposal_id INT REFERENCES meta_agent_evolution_proposals(id),

    -- Instruction details
    instruction TEXT NOT NULL,
    target VARCHAR(50) NOT NULL,  -- Which agent this applies to
    version VARCHAR(20) NOT NULL,  -- Version this was deployed in

    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,

    -- Performance tracking
    trades_since_activation INT DEFAULT 0,
    win_rate_with_instruction DECIMAL(5, 2),

    -- Metadata
    priority INT DEFAULT 0  -- Order of application if multiple instructions
);

CREATE INDEX idx_evolved_instructions_active ON meta_agent_evolved_instructions(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_evolved_instructions_target ON meta_agent_evolved_instructions(target);

-- ============================================================
-- Circuit Breaker Events Table
-- ============================================================
-- Logs all circuit breaker triggers (Paper B governance)
CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Event details
    event_type VARCHAR(50) NOT NULL,  -- 'WIN_RATE_LOW', 'DAILY_LOSS_LIMIT', 'WEIGHT_EXTREME', 'OVERTRADING'
    severity VARCHAR(20) NOT NULL,  -- 'WARNING', 'CRITICAL'

    -- Trigger values
    metric_name VARCHAR(50) NOT NULL,
    metric_value DECIMAL(10, 4) NOT NULL,
    threshold_value DECIMAL(10, 4) NOT NULL,

    -- Action taken
    action_taken VARCHAR(50) NOT NULL,  -- 'ALERT', 'PAUSE_TRADING', 'CLOSE_ALL', 'CAP_WEIGHT'
    action_details TEXT,

    -- Context
    agent VARCHAR(50),  -- Which agent triggered it (if applicable)
    lookback_period INT,  -- Window size for rolling metrics
    sample_size INT,  -- Number of trades/data points

    -- Resolution
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(100),
    resolution_notes TEXT
);

CREATE INDEX idx_circuit_breaker_timestamp ON circuit_breaker_events(timestamp DESC);
CREATE INDEX idx_circuit_breaker_type ON circuit_breaker_events(event_type);
CREATE INDEX idx_circuit_breaker_severity ON circuit_breaker_events(severity);
CREATE INDEX idx_circuit_breaker_resolved ON circuit_breaker_events(resolved_at);

-- ============================================================
-- Views
-- ============================================================

-- View: Recent Meta-Agent Decisions
CREATE OR REPLACE VIEW v_recent_meta_decisions AS
SELECT
    id,
    timestamp,
    mode,
    symbol,
    decision,
    confidence,
    weighted_score,
    agreement,
    reasoning,
    outcome,
    outcome_pnl
FROM meta_agent_decisions
ORDER BY timestamp DESC
LIMIT 100;

-- View: Pending Evolution Proposals
CREATE OR REPLACE VIEW v_pending_evolution_proposals AS
SELECT
    id,
    created_at,
    type,
    target,
    instruction,
    rationale,
    evidence,
    status
FROM meta_agent_evolution_proposals
WHERE status = 'PENDING'
ORDER BY created_at DESC;

-- View: Active Evolved Instructions
CREATE OR REPLACE VIEW v_active_evolved_instructions AS
SELECT
    ei.id,
    ei.instruction,
    ei.target,
    ei.version,
    ei.activated_at,
    ei.trades_since_activation,
    ei.win_rate_with_instruction,
    ep.rationale
FROM meta_agent_evolved_instructions ei
LEFT JOIN meta_agent_evolution_proposals ep ON ei.proposal_id = ep.id
WHERE ei.is_active = TRUE
ORDER BY ei.priority DESC, ei.activated_at ASC;

-- View: Circuit Breaker Summary
CREATE OR REPLACE VIEW v_circuit_breaker_summary AS
SELECT
    event_type,
    severity,
    COUNT(*) as event_count,
    MAX(timestamp) as last_occurred,
    COUNT(CASE WHEN resolved_at IS NULL THEN 1 END) as unresolved_count
FROM circuit_breaker_events
WHERE timestamp >= NOW() - INTERVAL '30 days'
GROUP BY event_type, severity
ORDER BY event_count DESC;

-- View: Meta-Agent Performance by Mode
CREATE OR REPLACE VIEW v_meta_agent_performance_by_mode AS
SELECT
    mode,
    COUNT(*) as decision_count,
    AVG(confidence) as avg_confidence,
    COUNT(CASE WHEN outcome = 'WIN' THEN 1 END) as wins,
    COUNT(CASE WHEN outcome = 'LOSS' THEN 1 END) as losses,
    CASE
        WHEN COUNT(CASE WHEN outcome IN ('WIN', 'LOSS') THEN 1 END) > 0
        THEN ROUND(
            COUNT(CASE WHEN outcome = 'WIN' THEN 1 END)::DECIMAL /
            COUNT(CASE WHEN outcome IN ('WIN', 'LOSS') THEN 1 END) * 100,
            2
        )
        ELSE 0
    END as win_rate_pct,
    SUM(COALESCE(outcome_pnl, 0)) as total_pnl
FROM meta_agent_decisions
WHERE mode IN ('PRE_TRADE', 'MID_TRADE')
GROUP BY mode;

-- ============================================================
-- Functions
-- ============================================================

-- Function: Record Meta-Agent Decision
CREATE OR REPLACE FUNCTION record_meta_decision(
    p_mode VARCHAR(20),
    p_symbol VARCHAR(20),
    p_decision VARCHAR(50),
    p_confidence DECIMAL(5,2),
    p_reasoning TEXT,
    p_context JSONB
) RETURNS INT AS $$
DECLARE
    v_decision_id INT;
BEGIN
    INSERT INTO meta_agent_decisions (
        mode, symbol, decision, confidence, reasoning, full_context
    ) VALUES (
        p_mode, p_symbol, p_decision, p_confidence, p_reasoning, p_context
    ) RETURNING id INTO v_decision_id;

    RETURN v_decision_id;
END;
$$ LANGUAGE plpgsql;

-- Function: Log Circuit Breaker Event
CREATE OR REPLACE FUNCTION log_circuit_breaker(
    p_event_type VARCHAR(50),
    p_severity VARCHAR(20),
    p_metric_name VARCHAR(50),
    p_metric_value DECIMAL(10,4),
    p_threshold_value DECIMAL(10,4),
    p_action_taken VARCHAR(50),
    p_action_details TEXT DEFAULT NULL,
    p_agent VARCHAR(50) DEFAULT NULL
) RETURNS INT AS $$
DECLARE
    v_event_id INT;
BEGIN
    INSERT INTO circuit_breaker_events (
        event_type, severity, metric_name, metric_value, threshold_value,
        action_taken, action_details, agent
    ) VALUES (
        p_event_type, p_severity, p_metric_name, p_metric_value, p_threshold_value,
        p_action_taken, p_action_details, p_agent
    ) RETURNING id INTO v_event_id;

    RETURN v_event_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Comments
-- ============================================================

COMMENT ON TABLE meta_agent_decisions IS 'All Meta-Agent decisions across 4 modes (Pre-Trade, Mid-Trade, Post-Trade, Evolution)';
COMMENT ON TABLE meta_agent_evolution_proposals IS 'Symbolic policy change proposals (Paper A framework) requiring human approval';
COMMENT ON TABLE meta_agent_evolved_instructions IS 'Active evolved instructions (Section 7 of Meta-Agent prompt)';
COMMENT ON TABLE circuit_breaker_events IS 'Circuit breaker triggers and governance events (Paper B framework)';

COMMENT ON COLUMN meta_agent_decisions.mode IS 'PRE_TRADE: Initial signal coordination, MID_TRADE: Position management, POST_TRADE: Weight updates, EVOLUTION: Monthly analysis';
COMMENT ON COLUMN meta_agent_decisions.weighted_score IS 'Calculated weighted score: sum(confidence × weight) for each analyst';
COMMENT ON COLUMN meta_agent_decisions.agreement IS 'Agreement score: 1.0 = full agreement, 0.5 = split, PASS is neutral not opposing';

COMMENT ON COLUMN meta_agent_evolution_proposals.type IS 'ADD: New instruction, MODIFY: Change existing, REMOVE: Delete instruction';
COMMENT ON COLUMN meta_agent_evolution_proposals.target IS 'Which agent this change applies to (meta_agent, visual_agent, technical_agent, sentiment_agent)';
COMMENT ON COLUMN meta_agent_evolution_proposals.evidence IS 'Statistical evidence: sample_size, p_value, win_rate_before, win_rate_after_backtest, improvement_pct';

-- ============================================================
-- Initial Data (Optional)
-- ============================================================

-- Insert initial monitoring thresholds (can be referenced by circuit breaker logic)
-- (Implementation-specific, add if needed)
