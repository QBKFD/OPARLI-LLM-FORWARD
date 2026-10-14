-- ============================================================
-- Agent System Database Schema
-- ============================================================
--
-- Tables for storing:
-- 1. Agent decisions and analysis
-- 2. Trade signals and execution
-- 3. Screenshots and chart data
-- 4. Performance tracking
-- ============================================================

-- ============================================================
-- Agent Analyses Table
-- ============================================================
-- Stores analysis results from Visual, Technical, and Sentiment analysts
CREATE TABLE IF NOT EXISTS agent_analyses (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    analyst VARCHAR(50) NOT NULL,  -- 'visual', 'technical', 'sentiment'
    signal VARCHAR(10) NOT NULL,   -- 'BUY', 'SELL', 'NEUTRAL'
    confidence DECIMAL(5, 2) NOT NULL,  -- 0-100
    reasoning TEXT,
    analysis_data JSONB,  -- Full analysis details
    timestamp TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Meta-Agent Decisions Table
-- ============================================================
-- Stores final decisions from Meta-Agent
CREATE TABLE IF NOT EXISTS meta_decisions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    signal VARCHAR(10) NOT NULL,  -- 'BUY', 'SELL', 'NEUTRAL'
    confidence DECIMAL(5, 2) NOT NULL,
    reasoning TEXT,

    -- Analyst votes
    buy_votes INT DEFAULT 0,
    sell_votes INT DEFAULT 0,
    neutral_votes INT DEFAULT 0,

    -- Weighted scores
    buy_score DECIMAL(5, 2) DEFAULT 0,
    sell_score DECIMAL(5, 2) DEFAULT 0,
    neutral_score DECIMAL(5, 2) DEFAULT 0,

    -- Individual analyst signals
    visual_signal VARCHAR(10),
    visual_confidence DECIMAL(5, 2),
    technical_signal VARCHAR(10),
    technical_confidence DECIMAL(5, 2),
    sentiment_signal VARCHAR(10),
    sentiment_confidence DECIMAL(5, 2),

    decision_data JSONB,  -- Full decision details
    timestamp TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Trade Signals Table
-- ============================================================
-- Stores trade signals sent to Risk Manager
CREATE TABLE IF NOT EXISTS trade_signals (
    id SERIAL PRIMARY KEY,
    meta_decision_id INT REFERENCES meta_decisions(id),
    symbol VARCHAR(20) NOT NULL,
    signal VARCHAR(10) NOT NULL,
    confidence DECIMAL(5, 2) NOT NULL,

    -- Risk Management
    approved BOOLEAN DEFAULT FALSE,
    rejection_reason TEXT,

    -- Position sizing
    quantity DECIMAL(18, 8),
    entry_price DECIMAL(18, 2),
    stop_loss DECIMAL(18, 2),
    take_profit DECIMAL(18, 2),
    risk_amount DECIMAL(18, 2),
    position_value DECIMAL(18, 2),
    risk_reward_ratio DECIMAL(5, 2),

    timestamp TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Executed Trades Table
-- ============================================================
-- Stores actual executed trades
CREATE TABLE IF NOT EXISTS executed_trades (
    id SERIAL PRIMARY KEY,
    trade_signal_id INT REFERENCES trade_signals(id),
    symbol VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL,  -- 'BUY', 'SELL'
    quantity DECIMAL(18, 8) NOT NULL,

    -- Order details
    order_id INT,
    order_type VARCHAR(20),  -- 'MARKET', 'LIMIT'
    order_status VARCHAR(20),  -- 'PENDING', 'FILLED', 'REJECTED', 'CANCELLED'

    -- Execution prices
    entry_price DECIMAL(18, 2),
    fill_price DECIMAL(18, 2),
    stop_loss DECIMAL(18, 2),
    take_profit DECIMAL(18, 2),

    -- Exit details
    exit_price DECIMAL(18, 2),
    exit_timestamp TIMESTAMP,
    exit_reason VARCHAR(50),  -- 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL', 'TIMEOUT'

    -- P&L
    gross_pnl DECIMAL(18, 2),
    net_pnl DECIMAL(18, 2),  -- After commissions
    pnl_percentage DECIMAL(8, 4),

    -- Metadata
    execution_timestamp TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Chart Screenshots Table
-- ============================================================
-- Stores chart screenshots for Visual Analyst
CREATE TABLE IF NOT EXISTS chart_screenshots (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    screenshot_base64 TEXT NOT NULL,  -- Base64 encoded PNG
    timeframe VARCHAR(10) DEFAULT '5min',

    -- Market context at screenshot time
    price DECIMAL(18, 2),
    volume BIGINT,

    -- Link to analysis
    analysis_id INT REFERENCES agent_analyses(id),

    timestamp TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Agent Performance Metrics Table
-- ============================================================
-- Tracks individual agent accuracy over time
CREATE TABLE IF NOT EXISTS agent_performance (
    id SERIAL PRIMARY KEY,
    analyst VARCHAR(50) NOT NULL,

    -- Accuracy metrics
    total_signals INT DEFAULT 0,
    correct_signals INT DEFAULT 0,
    incorrect_signals INT DEFAULT 0,
    accuracy_pct DECIMAL(5, 2) DEFAULT 0,

    -- Signal breakdown
    buy_signals INT DEFAULT 0,
    sell_signals INT DEFAULT 0,
    neutral_signals INT DEFAULT 0,

    -- Average confidence
    avg_confidence DECIMAL(5, 2) DEFAULT 0,

    -- Time period
    period_start TIMESTAMP,
    period_end TIMESTAMP,

    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Daily Trading Statistics Table
-- ============================================================
-- Aggregate daily statistics
CREATE TABLE IF NOT EXISTS daily_statistics (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL UNIQUE,

    -- Trade counts
    total_trades INT DEFAULT 0,
    winning_trades INT DEFAULT 0,
    losing_trades INT DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,

    -- P&L
    gross_pnl DECIMAL(18, 2) DEFAULT 0,
    net_pnl DECIMAL(18, 2) DEFAULT 0,
    largest_win DECIMAL(18, 2) DEFAULT 0,
    largest_loss DECIMAL(18, 2) DEFAULT 0,

    -- Risk metrics
    max_drawdown DECIMAL(18, 2) DEFAULT 0,
    sharpe_ratio DECIMAL(8, 4) DEFAULT 0,

    -- Agent stats
    scans_performed INT DEFAULT 0,
    opportunities_detected INT DEFAULT 0,
    trades_rejected INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- System Logs Table
-- ============================================================
-- Stores important system events and errors
CREATE TABLE IF NOT EXISTS system_logs (
    id SERIAL PRIMARY KEY,
    level VARCHAR(20) NOT NULL,  -- 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    agent VARCHAR(50),
    message TEXT NOT NULL,
    details JSONB,
    timestamp TIMESTAMP DEFAULT NOW()
);

-- Indexes converted from inline (MySQL) syntax to valid PostgreSQL
CREATE INDEX IF NOT EXISTS idx_agent_analyses_symbol ON agent_analyses (symbol);
CREATE INDEX IF NOT EXISTS idx_agent_analyses_timestamp ON agent_analyses (timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_analyses_analyst ON agent_analyses (analyst);
CREATE INDEX IF NOT EXISTS idx_meta_decisions_symbol ON meta_decisions (symbol);
CREATE INDEX IF NOT EXISTS idx_meta_decisions_timestamp ON meta_decisions (timestamp);
CREATE INDEX IF NOT EXISTS idx_meta_decisions_signal ON meta_decisions (signal);
CREATE INDEX IF NOT EXISTS idx_trade_signals_symbol ON trade_signals (symbol);
CREATE INDEX IF NOT EXISTS idx_trade_signals_timestamp ON trade_signals (timestamp);
CREATE INDEX IF NOT EXISTS idx_trade_signals_approved ON trade_signals (approved);
CREATE INDEX IF NOT EXISTS idx_executed_trades_symbol ON executed_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_executed_trades_timestamp ON executed_trades (execution_timestamp);
CREATE INDEX IF NOT EXISTS idx_executed_trades_status ON executed_trades (order_status);
CREATE INDEX IF NOT EXISTS idx_screenshots_symbol ON chart_screenshots (symbol);
CREATE INDEX IF NOT EXISTS idx_screenshots_timestamp ON chart_screenshots (timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_performance_analyst ON agent_performance (analyst);
CREATE INDEX IF NOT EXISTS idx_agent_performance_updated ON agent_performance (updated_at);
CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_statistics (trade_date);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs (level);
CREATE INDEX IF NOT EXISTS idx_system_logs_agent ON system_logs (agent);
CREATE INDEX IF NOT EXISTS idx_system_logs_timestamp ON system_logs (timestamp);

-- ============================================================
-- Views for Easy Querying
-- ============================================================

-- View: Recent Decisions with Full Context
CREATE OR REPLACE VIEW v_recent_decisions AS
SELECT
    md.id,
    md.symbol,
    md.signal,
    md.confidence,
    md.reasoning,
    md.buy_votes,
    md.sell_votes,
    md.neutral_votes,
    md.visual_signal,
    md.visual_confidence,
    md.technical_signal,
    md.technical_confidence,
    md.sentiment_signal,
    md.sentiment_confidence,
    ts.approved,
    ts.rejection_reason,
    ts.quantity,
    ts.entry_price,
    ts.stop_loss,
    ts.take_profit,
    et.order_status,
    et.fill_price,
    et.gross_pnl,
    md.timestamp
FROM meta_decisions md
LEFT JOIN trade_signals ts ON md.id = ts.meta_decision_id
LEFT JOIN executed_trades et ON ts.id = et.trade_signal_id
ORDER BY md.timestamp DESC;

-- View: Open Positions
CREATE OR REPLACE VIEW v_open_positions AS
SELECT
    et.id,
    et.symbol,
    et.action,
    et.quantity,
    et.fill_price AS entry_price,
    et.stop_loss,
    et.take_profit,
    et.execution_timestamp,
    EXTRACT(EPOCH FROM (NOW() - et.execution_timestamp)) / 3600 AS hours_open
FROM executed_trades et
WHERE et.order_status = 'FILLED'
  AND et.exit_timestamp IS NULL
ORDER BY et.execution_timestamp DESC;

-- View: Trade History with Performance
CREATE OR REPLACE VIEW v_trade_history AS
SELECT
    et.id,
    et.symbol,
    et.action,
    et.quantity,
    et.fill_price AS entry_price,
    et.exit_price,
    et.gross_pnl,
    et.net_pnl,
    et.pnl_percentage,
    et.exit_reason,
    et.execution_timestamp,
    et.exit_timestamp,
    EXTRACT(EPOCH FROM (et.exit_timestamp - et.execution_timestamp)) / 3600 AS duration_hours,
    md.confidence AS meta_confidence,
    md.visual_signal,
    md.technical_signal,
    md.sentiment_signal
FROM executed_trades et
JOIN trade_signals ts ON et.trade_signal_id = ts.id
JOIN meta_decisions md ON ts.meta_decision_id = md.id
WHERE et.exit_timestamp IS NOT NULL
ORDER BY et.exit_timestamp DESC;

-- ============================================================
-- Functions
-- ============================================================

-- Function: Update Agent Performance
CREATE OR REPLACE FUNCTION update_agent_performance()
RETURNS TRIGGER AS $$
BEGIN
    -- This would be triggered when trades are closed
    -- to update agent accuracy based on results

    -- Implementation would compare agent signals
    -- to actual trade outcomes

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function: Update Daily Statistics
CREATE OR REPLACE FUNCTION update_daily_statistics()
RETURNS TRIGGER AS $$
DECLARE
    trade_date DATE;
BEGIN
    trade_date := DATE(NEW.exit_timestamp);

    INSERT INTO daily_statistics (
        trade_date,
        total_trades,
        winning_trades,
        losing_trades,
        gross_pnl,
        net_pnl,
        largest_win,
        largest_loss
    )
    VALUES (
        trade_date,
        1,
        CASE WHEN NEW.net_pnl > 0 THEN 1 ELSE 0 END,
        CASE WHEN NEW.net_pnl < 0 THEN 1 ELSE 0 END,
        NEW.gross_pnl,
        NEW.net_pnl,
        CASE WHEN NEW.net_pnl > 0 THEN NEW.net_pnl ELSE 0 END,
        CASE WHEN NEW.net_pnl < 0 THEN ABS(NEW.net_pnl) ELSE 0 END
    )
    ON CONFLICT (trade_date) DO UPDATE SET
        total_trades = daily_statistics.total_trades + 1,
        winning_trades = daily_statistics.winning_trades + CASE WHEN NEW.net_pnl > 0 THEN 1 ELSE 0 END,
        losing_trades = daily_statistics.losing_trades + CASE WHEN NEW.net_pnl < 0 THEN 1 ELSE 0 END,
        gross_pnl = daily_statistics.gross_pnl + NEW.gross_pnl,
        net_pnl = daily_statistics.net_pnl + NEW.net_pnl,
        largest_win = GREATEST(daily_statistics.largest_win, CASE WHEN NEW.net_pnl > 0 THEN NEW.net_pnl ELSE 0 END),
        largest_loss = GREATEST(daily_statistics.largest_loss, CASE WHEN NEW.net_pnl < 0 THEN ABS(NEW.net_pnl) ELSE 0 END),
        win_rate = ((daily_statistics.winning_trades + CASE WHEN NEW.net_pnl > 0 THEN 1 ELSE 0 END)::DECIMAL /
                   (daily_statistics.total_trades + 1)) * 100;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger: Update daily statistics when trade closes
CREATE TRIGGER trigger_update_daily_stats
AFTER UPDATE OF exit_timestamp ON executed_trades
FOR EACH ROW
WHEN (NEW.exit_timestamp IS NOT NULL AND OLD.exit_timestamp IS NULL)
EXECUTE FUNCTION update_daily_statistics();

-- ============================================================
-- Indexes for Performance
-- ============================================================

-- Compound indexes for common queries
CREATE INDEX IF NOT EXISTS idx_analyses_symbol_timestamp
ON agent_analyses(symbol, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_decisions_symbol_timestamp
ON meta_decisions(symbol, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_status
ON executed_trades(symbol, order_status);

CREATE INDEX IF NOT EXISTS idx_trades_exit_timestamp
ON executed_trades(exit_timestamp)
WHERE exit_timestamp IS NOT NULL;

-- ============================================================
-- Comments
-- ============================================================

COMMENT ON TABLE agent_analyses IS 'Stores individual agent analysis results (Visual, Technical, Sentiment)';
COMMENT ON TABLE meta_decisions IS 'Stores Meta-Agent final decisions after weighing all analyst opinions';
COMMENT ON TABLE trade_signals IS 'Stores trade signals with risk management approval status';
COMMENT ON TABLE executed_trades IS 'Stores actual executed trades with entry/exit details and P&L';
COMMENT ON TABLE chart_screenshots IS 'Stores chart screenshots for Visual Analyst and audit trail';
COMMENT ON TABLE agent_performance IS 'Tracks individual agent accuracy and performance metrics';
COMMENT ON TABLE daily_statistics IS 'Aggregate daily trading statistics and performance';
COMMENT ON TABLE system_logs IS 'System events, errors, and operational logs';
