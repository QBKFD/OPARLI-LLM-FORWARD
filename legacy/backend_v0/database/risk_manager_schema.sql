-- ============================================================
-- Risk Manager Database Schema
-- ============================================================
-- Stores all Risk Manager decisions, account state, and limit violations
-- ============================================================

-- ============================================================
-- Risk Manager Decisions Table
-- ============================================================
-- Records all trade validation decisions
CREATE TABLE IF NOT EXISTS risk_manager_decisions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Trade request reference
    trade_request_id INT REFERENCES meta_agent_decisions(id),

    -- Decision
    status VARCHAR(20) NOT NULL,  -- 'APPROVED', 'REJECTED'
    rejection_reason TEXT,
    rejection_category VARCHAR(50),  -- 'hard_limit_violation', 'poor_risk_reward', etc.

    -- Approved trade parameters
    entry_price DECIMAL(10, 2),
    stop_loss DECIMAL(10, 2),
    take_profit DECIMAL(10, 2),
    position_size DECIMAL(10, 2),
    risk_dollars DECIMAL(10, 2),
    risk_pct DECIMAL(5, 2),
    risk_reward_ratio DECIMAL(5, 2),

    -- Calculation details
    stop_method VARCHAR(50),  -- 'hybrid_atr_technical', 'atr_only'
    tp_method VARCHAR(50),  -- 'structure_based', 'ratio_default'
    confidence_used DECIMAL(5, 2),
    atr_used DECIMAL(10, 2),

    -- Account state at decision time
    account_balance DECIMAL(12, 2),
    open_positions INT,
    daily_pnl DECIMAL(10, 2)
);

CREATE INDEX idx_risk_decisions_timestamp ON risk_manager_decisions(timestamp DESC);
CREATE INDEX idx_risk_decisions_status ON risk_manager_decisions(status);
CREATE INDEX idx_risk_decisions_trade_request ON risk_manager_decisions(trade_request_id);
CREATE INDEX idx_risk_decisions_rejection_category ON risk_manager_decisions(rejection_category);

-- ============================================================
-- Account State Table
-- ============================================================
-- Real-time account state tracking
CREATE TABLE IF NOT EXISTS account_state (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Balance tracking
    current_balance DECIMAL(12, 2) NOT NULL,
    peak_balance DECIMAL(12, 2) NOT NULL,
    start_of_day_balance DECIMAL(12, 2) NOT NULL,

    -- Position tracking
    open_positions INT DEFAULT 0,
    trades_today INT DEFAULT 0,

    -- Performance metrics
    daily_pnl DECIMAL(10, 2),
    daily_pnl_pct DECIMAL(5, 2),
    drawdown_from_peak DECIMAL(10, 2),
    drawdown_pct DECIMAL(5, 2)
);

CREATE INDEX idx_account_state_timestamp ON account_state(timestamp DESC);

-- ============================================================
-- Hard Limit Violations Table
-- ============================================================
-- Logs all hard limit violations (circuit breakers)
CREATE TABLE IF NOT EXISTS hard_limit_violations (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Violation details
    limit_type VARCHAR(50) NOT NULL,  -- 'daily_loss', 'max_drawdown', 'max_open_positions', etc.
    limit_value DECIMAL(10, 4) NOT NULL,
    actual_value DECIMAL(10, 4) NOT NULL,
    action_taken VARCHAR(50) NOT NULL  -- 'STOP_TRADING_FOR_DAY', 'STOP_ALL_TRADING', etc.
);

CREATE INDEX idx_hard_violations_timestamp ON hard_limit_violations(timestamp DESC);
CREATE INDEX idx_hard_violations_limit_type ON hard_limit_violations(limit_type);

-- ============================================================
-- Views
-- ============================================================

-- View: Recent Risk Manager Decisions
CREATE OR REPLACE VIEW v_recent_risk_decisions AS
SELECT
    id,
    timestamp,
    status,
    rejection_reason,
    rejection_category,
    entry_price,
    stop_loss,
    take_profit,
    position_size,
    risk_dollars,
    risk_pct,
    risk_reward_ratio
FROM risk_manager_decisions
ORDER BY timestamp DESC
LIMIT 100;

-- View: Risk Manager Performance Summary
CREATE OR REPLACE VIEW v_risk_manager_performance AS
SELECT
    COUNT(*) as total_decisions,
    COUNT(CASE WHEN status = 'APPROVED' THEN 1 END) as approved_count,
    COUNT(CASE WHEN status = 'REJECTED' THEN 1 END) as rejected_count,
    ROUND(
        COUNT(CASE WHEN status = 'APPROVED' THEN 1 END)::DECIMAL /
        COUNT(*) * 100,
        2
    ) as approval_rate_pct,
    AVG(CASE WHEN status = 'APPROVED' THEN risk_pct END) as avg_risk_pct,
    AVG(CASE WHEN status = 'APPROVED' THEN risk_reward_ratio END) as avg_risk_reward_ratio
FROM risk_manager_decisions;

-- View: Rejection Reasons Breakdown
CREATE OR REPLACE VIEW v_rejection_reasons AS
SELECT
    rejection_category,
    COUNT(*) as rejection_count,
    ROUND(
        COUNT(*)::DECIMAL /
        (SELECT COUNT(*) FROM risk_manager_decisions WHERE status = 'REJECTED') * 100,
        2
    ) as pct_of_rejections
FROM risk_manager_decisions
WHERE status = 'REJECTED'
GROUP BY rejection_category
ORDER BY rejection_count DESC;

-- View: Current Account State
CREATE OR REPLACE VIEW v_current_account_state AS
SELECT
    timestamp,
    current_balance,
    peak_balance,
    start_of_day_balance,
    open_positions,
    trades_today,
    daily_pnl,
    daily_pnl_pct,
    drawdown_from_peak,
    drawdown_pct
FROM account_state
ORDER BY timestamp DESC
LIMIT 1;

-- View: Hard Limit Violations Summary
CREATE OR REPLACE VIEW v_hard_violations_summary AS
SELECT
    limit_type,
    action_taken,
    COUNT(*) as violation_count,
    MAX(timestamp) as last_occurred
FROM hard_limit_violations
WHERE timestamp >= NOW() - INTERVAL '30 days'
GROUP BY limit_type, action_taken
ORDER BY violation_count DESC;

-- ============================================================
-- Functions
-- ============================================================

-- Function: Record Risk Manager Decision
CREATE OR REPLACE FUNCTION record_risk_decision(
    p_trade_request_id INT,
    p_status VARCHAR(20),
    p_rejection_reason TEXT,
    p_rejection_category VARCHAR(50),
    p_entry_price DECIMAL(10,2),
    p_stop_loss DECIMAL(10,2),
    p_take_profit DECIMAL(10,2),
    p_position_size DECIMAL(10,2),
    p_risk_dollars DECIMAL(10,2),
    p_risk_pct DECIMAL(5,2),
    p_risk_reward_ratio DECIMAL(5,2),
    p_stop_method VARCHAR(50),
    p_tp_method VARCHAR(50),
    p_confidence_used DECIMAL(5,2),
    p_atr_used DECIMAL(10,2),
    p_account_balance DECIMAL(12,2),
    p_open_positions INT,
    p_daily_pnl DECIMAL(10,2)
) RETURNS INT AS $$
DECLARE
    v_decision_id INT;
BEGIN
    INSERT INTO risk_manager_decisions (
        trade_request_id,
        status,
        rejection_reason,
        rejection_category,
        entry_price,
        stop_loss,
        take_profit,
        position_size,
        risk_dollars,
        risk_pct,
        risk_reward_ratio,
        stop_method,
        tp_method,
        confidence_used,
        atr_used,
        account_balance,
        open_positions,
        daily_pnl
    ) VALUES (
        p_trade_request_id,
        p_status,
        p_rejection_reason,
        p_rejection_category,
        p_entry_price,
        p_stop_loss,
        p_take_profit,
        p_position_size,
        p_risk_dollars,
        p_risk_pct,
        p_risk_reward_ratio,
        p_stop_method,
        p_tp_method,
        p_confidence_used,
        p_atr_used,
        p_account_balance,
        p_open_positions,
        p_daily_pnl
    ) RETURNING id INTO v_decision_id;

    RETURN v_decision_id;
END;
$$ LANGUAGE plpgsql;

-- Function: Log Hard Limit Violation
CREATE OR REPLACE FUNCTION log_hard_limit_violation(
    p_limit_type VARCHAR(50),
    p_limit_value DECIMAL(10,4),
    p_actual_value DECIMAL(10,4),
    p_action_taken VARCHAR(50)
) RETURNS INT AS $$
DECLARE
    v_violation_id INT;
BEGIN
    INSERT INTO hard_limit_violations (
        limit_type,
        limit_value,
        actual_value,
        action_taken
    ) VALUES (
        p_limit_type,
        p_limit_value,
        p_actual_value,
        p_action_taken
    ) RETURNING id INTO v_violation_id;

    RETURN v_violation_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Comments
-- ============================================================

COMMENT ON TABLE risk_manager_decisions IS 'All Risk Manager validation decisions (APPROVED or REJECTED)';
COMMENT ON TABLE account_state IS 'Real-time account balance, positions, and performance tracking';
COMMENT ON TABLE hard_limit_violations IS 'Circuit breaker triggers and account protection events';

COMMENT ON COLUMN risk_manager_decisions.status IS 'APPROVED: Trade validated and sent to Execution, REJECTED: Trade blocked';
COMMENT ON COLUMN risk_manager_decisions.rejection_category IS 'Categorized rejection reason for analysis';
COMMENT ON COLUMN risk_manager_decisions.stop_method IS 'hybrid_atr_technical: Uses technical levels, atr_only: Pure ATR-based';
COMMENT ON COLUMN risk_manager_decisions.tp_method IS 'structure_based: Uses support/resistance, ratio_default: Fixed R:R ratio';

COMMENT ON COLUMN account_state.peak_balance IS 'Highest balance ever reached (for drawdown calculation)';
COMMENT ON COLUMN account_state.start_of_day_balance IS 'Balance at market open (resets daily)';
COMMENT ON COLUMN account_state.drawdown_pct IS 'Percentage drop from peak balance';

COMMENT ON COLUMN hard_limit_violations.action_taken IS 'STOP_TRADING_FOR_DAY, STOP_ALL_TRADING, STOP_ALL_TRADING_PERMANENTLY, WAIT_FOR_POSITION_CLOSE';
