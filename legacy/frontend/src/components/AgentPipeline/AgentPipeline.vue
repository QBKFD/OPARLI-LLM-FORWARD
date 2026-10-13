<template>
  <div class="agent-pipeline" :class="{ expanded: expanded }">
    <h3>Agent Pipeline</h3>

    <!-- Compact Mode: Vertical Pipeline -->
    <template v-if="!expanded">
      <div class="pipeline-stages">
        <!-- Vision Engine -->
        <div class="stage" :class="{ active: activeStage === 'vision', completed: completedStages.includes('vision') }">
          <div class="stage-circle">
            <div class="pulse-ring" v-if="activeStage === 'vision'"></div>
            <span class="stage-number">1</span>
          </div>
          <div class="stage-info">
            <div class="stage-label">Vision Engine</div>
          </div>
        </div>

        <div class="connection-line" :class="{ active: activeStage === 'signal' || completedStages.includes('vision') }">
          <div class="line-progress"></div>
        </div>

        <!-- Signal Processor -->
        <div class="stage" :class="{ active: activeStage === 'signal', completed: completedStages.includes('signal') }">
          <div class="stage-circle">
            <div class="pulse-ring" v-if="activeStage === 'signal'"></div>
            <span class="stage-number">2</span>
          </div>
          <div class="stage-info">
            <div class="stage-label">Signal Processor</div>
          </div>
        </div>

        <div class="connection-line" :class="{ active: activeStage === 'risk' || completedStages.includes('signal') }">
          <div class="line-progress"></div>
        </div>

        <!-- Risk Oversight -->
        <div class="stage" :class="{ active: activeStage === 'risk', completed: completedStages.includes('risk') }">
          <div class="stage-circle">
            <div class="pulse-ring" v-if="activeStage === 'risk'"></div>
            <span class="stage-number">3</span>
          </div>
          <div class="stage-info">
            <div class="stage-label">Risk Oversight</div>
          </div>
        </div>

        <div class="connection-line" :class="{ active: activeStage === 'execution' || completedStages.includes('risk') }">
          <div class="line-progress"></div>
        </div>

        <!-- Execution Logic -->
        <div class="stage" :class="{ active: activeStage === 'execution', completed: completedStages.includes('execution') }">
          <div class="stage-circle">
            <div class="pulse-ring" v-if="activeStage === 'execution'"></div>
            <span class="stage-number">4</span>
          </div>
          <div class="stage-info">
            <div class="stage-label">Execution Logic</div>
          </div>
        </div>
      </div>

      <!-- Compact Reasoning Panel -->
      <div class="reasoning-panel">
        <div class="reasoning-header">
          <span class="reasoning-title">{{ currentStageTitle }}</span>
          <span class="reasoning-time" v-if="lastUpdate">{{ lastUpdate }}</span>
        </div>
        <div class="reasoning-content">
          <p v-if="reasoning">{{ reasoning }}</p>
          <p v-else class="reasoning-idle">Waiting for next analysis cycle...</p>
        </div>
      </div>

      <!-- Compact Last Decision -->
      <div class="last-decision" v-if="lastDecision">
        <div class="decision-header">Last Signal</div>
        <div class="decision-content" :class="lastDecision.action.toLowerCase()">
          <span class="decision-action">{{ lastDecision.action }}</span>
          <span class="decision-price">{{ lastDecision.price.toFixed(2) }}</span>
          <span class="decision-time">{{ lastDecision.time }}</span>
        </div>
      </div>

      <!-- Compact Agent Trades -->
      <div class="agent-trades">
        <div class="trades-header">Agent Trades</div>
        <div class="trades-list" v-if="agentTrades.length > 0">
          <div
            class="trade-item"
            v-for="(trade, index) in agentTrades"
            :key="index"
            :class="trade.type.toLowerCase()"
          >
            <span class="trade-time">{{ trade.time }}</span>
            <span class="trade-type">{{ trade.type }}</span>
            <span class="trade-price">{{ trade.price.toFixed(2) }}</span>
            <span class="trade-pnl" v-if="trade.pnl !== null" :class="trade.pnl >= 0 ? 'profit' : 'loss'">
              {{ trade.pnl >= 0 ? '+' : '' }}{{ trade.pnl.toFixed(2) }}
            </span>
          </div>
        </div>
        <div class="trades-empty" v-else>
          No trades yet
        </div>
      </div>
    </template>

    <!-- ========== EXPANDED MODE: Dashboard-Style Layout ========== -->
    <template v-if="expanded">
      <!-- Header with Status -->
      <div class="dashboard-header">
        <span class="status-badge" :class="activeStage ? 'running' : 'idle'">
          {{ activeStage ? 'Processing' : 'Ready' }}
        </span>
        <span class="data-timestamp" v-if="lastUpdate">
          Last update: {{ lastUpdate }}
        </span>
      </div>

      <!-- Horizontal Pipeline with Large Circles -->
      <section class="pipeline-section">
        <div class="pipeline-container">
          <!-- Visual Analyst -->
          <div class="pipeline-stage" :class="{ active: activeStage === 'vision', completed: completedStages.includes('vision') }">
            <div class="stage-circle-lg">
              <div class="pulse-ring-lg" v-if="activeStage === 'vision'"></div>
              <span class="stage-number-lg">1</span>
            </div>
            <div class="stage-label-lg">Visual</div>
            <div class="stage-result" v-if="agentResults.visual">
              <span class="result-signal" :class="agentResults.visual.signal?.toLowerCase()">
                {{ agentResults.visual.signal }}
              </span>
              <span class="result-confidence">{{ formatConfidence(agentResults.visual.confidence) }}</span>
            </div>
          </div>

          <!-- Connection Line 1 -->
          <div class="pipeline-line" :class="{ active: completedStages.includes('vision') }">
            <div class="line-progress"></div>
          </div>

          <!-- Technical Analyst -->
          <div class="pipeline-stage" :class="{ active: activeStage === 'signal', completed: completedStages.includes('signal') }">
            <div class="stage-circle-lg">
              <div class="pulse-ring-lg" v-if="activeStage === 'signal'"></div>
              <span class="stage-number-lg">2</span>
            </div>
            <div class="stage-label-lg">Technical</div>
            <div class="stage-result" v-if="agentResults.technical">
              <span class="result-signal" :class="agentResults.technical.signal?.toLowerCase()">
                {{ agentResults.technical.signal }}
              </span>
              <span class="result-confidence">{{ formatConfidence(agentResults.technical.confidence) }}</span>
            </div>
          </div>

          <!-- Connection Line 2 -->
          <div class="pipeline-line" :class="{ active: completedStages.includes('signal') }">
            <div class="line-progress"></div>
          </div>

          <!-- Meta Agent -->
          <div class="pipeline-stage" :class="{ active: activeStage === 'risk', completed: completedStages.includes('risk') }">
            <div class="stage-circle-lg">
              <div class="pulse-ring-lg" v-if="activeStage === 'risk'"></div>
              <span class="stage-number-lg">3</span>
            </div>
            <div class="stage-label-lg">Meta</div>
            <div class="stage-result" v-if="agentResults.meta">
              <span class="result-signal" :class="getMetaSignalClass(agentResults.meta.decision)">
                {{ agentResults.meta.decision }}
              </span>
              <span class="result-confidence">{{ formatConfidence(agentResults.meta.confidence) }}</span>
            </div>
          </div>

          <!-- Connection Line 3 -->
          <div class="pipeline-line" :class="{ active: completedStages.includes('risk') }">
            <div class="line-progress"></div>
          </div>

          <!-- Risk Manager -->
          <div class="pipeline-stage" :class="{ active: activeStage === 'execution', completed: completedStages.includes('execution') }">
            <div class="stage-circle-lg">
              <div class="pulse-ring-lg" v-if="activeStage === 'execution'"></div>
              <span class="stage-number-lg">4</span>
            </div>
            <div class="stage-label-lg">Risk</div>
            <div class="stage-result" v-if="agentResults.risk">
              <span class="result-signal" :class="agentResults.risk.approved ? 'long' : 'neutral'">
                {{ agentResults.risk.approved ? 'OK' : 'NO' }}
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- Current Processing Info -->
      <section class="processing-section" v-if="activeStage">
        <div class="processing-card">
          <span class="processing-title">{{ currentStageTitle }}</span>
          <span class="processing-status">{{ reasoning || 'Processing...' }}</span>
        </div>
      </section>

      <!-- Agent Detail Cards -->
      <section class="agents-section">
        <h2>Agent Details</h2>
        <div class="agents-grid">
          <!-- Visual Analyst Card -->
          <div class="agent-card" v-if="agentResults.visual">
            <div class="card-header">
              <span class="card-number">1</span>
              <span class="card-name">Visual Analyst</span>
              <span class="card-signal" :class="agentResults.visual.signal?.toLowerCase()">
                {{ agentResults.visual.signal }}
              </span>
            </div>
            <div class="card-confidence">
              Confidence: {{ formatConfidence(agentResults.visual.confidence) }}
            </div>
            <div class="card-reasoning" v-if="agentResults.visual.reasoning">
              {{ agentResults.visual.reasoning }}
            </div>
          </div>

          <!-- Technical Analyst Card -->
          <div class="agent-card" v-if="agentResults.technical">
            <div class="card-header">
              <span class="card-number">2</span>
              <span class="card-name">Technical</span>
              <span class="card-signal" :class="agentResults.technical.signal?.toLowerCase()">
                {{ agentResults.technical.signal }}
              </span>
            </div>
            <div class="card-confidence">
              Confidence: {{ formatConfidence(agentResults.technical.confidence) }}
            </div>
            <div class="card-info" v-if="agentResults.technical.trend">
              Trend: {{ agentResults.technical.trend }}
            </div>
          </div>

          <!-- Meta Agent Card -->
          <div class="agent-card wide" v-if="agentResults.meta">
            <div class="card-header">
              <span class="card-number">3</span>
              <span class="card-name">Meta Agent</span>
              <span class="card-signal" :class="getMetaSignalClass(agentResults.meta.decision)">
                {{ agentResults.meta.decision }}
              </span>
            </div>
            <div class="card-metrics">
              <div class="metric">
                <span class="metric-label">Confidence</span>
                <span class="metric-value">{{ formatConfidence(agentResults.meta.confidence) }}</span>
              </div>
              <div class="metric">
                <span class="metric-label">Weighted Score</span>
                <span class="metric-value">{{ agentResults.meta.weighted_score?.toFixed(2) || '--' }}</span>
              </div>
              <div class="metric">
                <span class="metric-label">Agreement</span>
                <span class="metric-value">{{ formatConfidence(agentResults.meta.agreement) }}</span>
              </div>
            </div>
            <div class="card-reasoning" v-if="agentResults.meta.reasoning">
              {{ agentResults.meta.reasoning }}
            </div>
          </div>

          <!-- Risk Manager Card -->
          <div class="agent-card" v-if="agentResults.risk">
            <div class="card-header">
              <span class="card-number">4</span>
              <span class="card-name">Risk Manager</span>
              <span class="card-signal" :class="agentResults.risk.approved ? 'long' : 'neutral'">
                {{ agentResults.risk.approved ? 'APPROVED' : 'REJECTED' }}
              </span>
            </div>
            <div class="card-reasoning" v-if="agentResults.risk.rejection_reason">
              {{ agentResults.risk.rejection_reason }}
            </div>
            <div class="card-params" v-if="agentResults.risk.approved">
              <div class="param-row">
                <span class="param-label">Position</span>
                <span class="param-value">{{ agentResults.risk.position_size }}</span>
              </div>
              <div class="param-row">
                <span class="param-label">Stop Loss</span>
                <span class="param-value">{{ agentResults.risk.stop_loss?.toFixed(2) }}</span>
              </div>
              <div class="param-row">
                <span class="param-label">Take Profit</span>
                <span class="param-value">{{ agentResults.risk.take_profit?.toFixed(2) }}</span>
              </div>
            </div>
          </div>

          <!-- Placeholder cards when no results -->
          <div class="agent-card placeholder" v-if="!hasAnyResults">
            <div class="placeholder-text">
              Run the agent demo to see results here
            </div>
          </div>
        </div>
      </section>

      <!-- Activity Log -->
      <section class="log-section">
        <h2>Activity Log</h2>
        <div class="log-container">
          <div
            v-for="(log, index) in activityLog"
            :key="index"
            class="log-entry"
            :class="log.level"
          >
            <span class="log-time">{{ log.time }}</span>
            <span class="log-agent">{{ log.agent }}</span>
            <span class="log-message">{{ log.message }}</span>
          </div>
          <div v-if="activityLog.length === 0" class="log-empty">
            Click "Run Agent Demo" to start the agent pipeline
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<script>
export default {
  name: 'AgentPipeline',

  props: {
    expanded: {
      type: Boolean,
      default: false,
    },
  },

  data() {
    return {
      activeStage: null,
      completedStages: [],
      reasoning: '',
      lastUpdate: null,
      lastDecision: null,
      agentTrades: [],
      agentResults: {
        visual: null,
        technical: null,
        meta: null,
        risk: null,
      },
      activityLog: [],
    };
  },

  computed: {
    currentStageTitle() {
      const titles = {
        vision: 'Visual Analyst Processing...',
        signal: 'Technical Analysis...',
        risk: 'Meta Agent Synthesizing...',
        execution: 'Risk Manager Checking...',
      };
      return titles[this.activeStage] || 'Agent Reasoning';
    },
    hasAnyResults() {
      return this.agentResults.visual || this.agentResults.technical ||
             this.agentResults.meta || this.agentResults.risk;
    },
  },

  methods: {
    formatConfidence(value) {
      if (value === null || value === undefined) return '--';
      const normalized = value > 1 ? value / 100 : value;
      return `${(normalized * 100).toFixed(0)}%`;
    },

    getMetaSignalClass(decision) {
      if (!decision) return 'neutral';
      const d = decision.toUpperCase();
      if (d === 'LONG' || d === 'BUY') return 'long';
      if (d === 'SHORT' || d === 'SELL') return 'short';
      return 'neutral';
    },

    addLog(agent, message, level = 'info') {
      const now = new Date();
      const time = now.toLocaleTimeString();
      this.activityLog.unshift({ time, agent, message, level });
      if (this.activityLog.length > 50) {
        this.activityLog.pop();
      }
    },

    handleAgentEvent(event) {
      console.log('Agent event:', event);

      this.activeStage = event.stage;
      this.reasoning = event.reasoning || '';
      this.lastUpdate = new Date().toLocaleTimeString();

      const agentNames = {
        vision: 'Visual Analyst',
        signal: 'Technical Analyst',
        risk: 'Meta Agent',
        execution: 'Risk Manager',
      };
      this.addLog(
        agentNames[event.stage] || event.stage,
        event.reasoning || `${event.status === 'completed' ? 'Completed' : 'Processing'}...`,
        event.status === 'completed' ? 'success' : 'info'
      );

      if (event.status === 'completed') {
        if (!this.completedStages.includes(event.stage)) {
          this.completedStages.push(event.stage);
        }

        if (event.stage === 'execution' && event.action) {
          this.lastDecision = {
            action: event.action,
            price: event.price || 0,
            time: new Date().toLocaleTimeString(),
          };

          if (event.action === 'BUY' || event.action === 'SELL') {
            this.agentTrades.unshift({
              time: new Date().toLocaleTimeString(),
              type: event.action,
              price: event.price || 0,
              pnl: null,
            });

            if (this.agentTrades.length > 10) {
              this.agentTrades.pop();
            }
          }
        }
      }

      if (event.stage === 'vision' && event.status === 'active') {
        this.completedStages = [];
        this.agentResults = {
          visual: null,
          technical: null,
          meta: null,
          risk: null,
        };
      }
    },

    simulateAgentCycle() {
      const stages = ['vision', 'signal', 'risk', 'execution'];
      const reasonings = {
        vision: 'Analyzing 1-min and 5-min XAUUSD chart patterns...',
        signal: 'Computing technical indicators: EMA, RSI, Bollinger Bands...',
        risk: 'Synthesizing all signals into final trading decision...',
        execution: 'Validating trade parameters and risk limits...',
      };

      this.agentResults = {
        visual: null,
        technical: null,
        meta: null,
        risk: null,
      };
      this.activityLog = [];
      this.addLog('System', 'Starting analysis pipeline...', 'info');

      let i = 0;
      const runStage = () => {
        if (i >= stages.length) {
          setTimeout(() => {
            this.activeStage = null;
            this.reasoning = '';
            this.addLog('System', 'Analysis complete', 'success');
          }, 2000);
          return;
        }

        const stage = stages[i];
        this.handleAgentEvent({
          stage,
          status: 'active',
          reasoning: reasonings[stage],
        });

        setTimeout(() => {
          if (stage === 'vision') {
            this.agentResults.visual = {
              signal: 'LONG',
              confidence: 0.78,
              reasoning: 'Bullish engulfing pattern detected on 5-min chart. Higher lows forming on 1-min timeframe.',
            };
          } else if (stage === 'signal') {
            this.agentResults.technical = {
              signal: 'LONG',
              confidence: 0.72,
              trend: 'Bullish',
              reasoning: 'EMA20 crossed above EMA50. RSI at 38 (recovering from oversold). Price bouncing off lower Bollinger Band.',
            };
          } else if (stage === 'risk') {
            this.agentResults.meta = {
              decision: 'LONG',
              confidence: 0.75,
              weighted_score: 0.74,
              agreement: 0.85,
              reasoning: 'Both visual and technical analysts agree on bullish signal with high confidence alignment.',
            };
          } else if (stage === 'execution') {
            this.agentResults.risk = {
              approved: true,
              position_size: '0.1 lots',
              stop_loss: 2635.00,
              take_profit: 2665.00,
            };
          }

          this.handleAgentEvent({
            stage,
            status: 'completed',
            reasoning: reasonings[stage],
            action: stage === 'execution' ? 'BUY' : null,
            price: stage === 'execution' ? 2648.50 : null,
          });
          i++;
          setTimeout(runStage, 500);
        }, 1500);
      };

      runStage();
    },
  },
};
</script>

<style scoped>
/* ========== BASE & TRANSITIONS ========== */
.agent-pipeline {
  padding: 16px;
  background: #18181c;
  border-radius: 8px;
  color: #fff;
  will-change: transform, opacity;
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.agent-pipeline.expanded {
  padding: 20px;
  background: #0d0d12;
}

.agent-pipeline h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 16px 0;
  color: #fff;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.agent-pipeline.expanded h3 {
  font-size: 20px;
  margin-bottom: 12px;
}

/* ========== COMPACT MODE STYLES ========== */
.pipeline-stages {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  margin-bottom: 20px;
  padding: 10px 0;
}

.stage {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.stage-circle {
  position: relative;
  width: 36px;
  height: 36px;
  min-width: 36px;
  border-radius: 50%;
  background: #2a2a2e;
  border: 2px solid #3a3a3e;
  display: flex;
  align-items: center;
  justify-content: center;
  will-change: transform, box-shadow, border-color, background;
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.stage-number {
  font-size: 14px;
  font-weight: 600;
  color: #666;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.stage.active .stage-number,
.stage.completed .stage-number {
  color: #18a058;
}

.stage.active .stage-circle {
  border-color: #18a058;
  background: rgba(24, 160, 88, 0.2);
  box-shadow: 0 0 30px rgba(24, 160, 88, 0.5);
  transform: scale(1.05);
}

.stage.completed .stage-circle {
  border-color: #18a058;
  background: rgba(24, 160, 88, 0.3);
}

.stage-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stage-label {
  font-size: 12px;
  color: #888;
  font-weight: 500;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.stage.active .stage-label,
.stage.completed .stage-label {
  color: #18a058;
}

/* Compact Connection Lines */
.connection-line {
  width: 2px;
  height: 24px;
  background: #3a3a3e;
  margin-left: 17px;
  position: relative;
  overflow: hidden;
  border-radius: 1px;
}

.connection-line .line-progress {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 0;
  background: linear-gradient(180deg, #18a058, #2ecc71);
  will-change: height;
  transition: height 0.6s cubic-bezier(0.22, 1, 0.36, 1);
  border-radius: 1px;
}

.connection-line.active .line-progress {
  height: 100%;
}

/* Pulse Animation - Smoother */
.pulse-ring {
  position: absolute;
  top: -4px;
  left: -4px;
  right: -4px;
  bottom: -4px;
  border-radius: 50%;
  border: 2px solid #18a058;
  will-change: transform, opacity;
  animation: pulse 2s cubic-bezier(0.22, 1, 0.36, 1) infinite;
}

@keyframes pulse {
  0% {
    transform: scale(1);
    opacity: 0.8;
  }
  100% {
    transform: scale(1.5);
    opacity: 0;
  }
}

/* Compact Reasoning Panel */
.reasoning-panel {
  background: #1e1e22;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 16px;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.reasoning-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.reasoning-title {
  font-size: 12px;
  font-weight: 600;
  color: #18a058;
}

.reasoning-time {
  font-size: 10px;
  color: #666;
}

.reasoning-content {
  font-size: 12px;
  color: #aaa;
  line-height: 1.5;
}

.reasoning-content p {
  margin: 0;
}

.reasoning-idle {
  color: #555;
  font-style: italic;
}

/* Compact Last Decision */
.last-decision {
  background: #1e1e22;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 16px;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.decision-header {
  font-size: 11px;
  color: #666;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.decision-content {
  display: flex;
  align-items: center;
  gap: 12px;
  transition: all 0.3s ease;
}

.decision-content.buy { color: #18a058; }
.decision-content.sell { color: #d03050; }
.decision-content.hold { color: #f0a020; }

.decision-action {
  font-size: 16px;
  font-weight: 700;
}

.decision-price {
  font-size: 14px;
}

.decision-time {
  font-size: 11px;
  color: #666;
  margin-left: auto;
}

/* Compact Agent Trades */
.agent-trades {
  background: #1e1e22;
  border-radius: 6px;
  padding: 12px;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.trades-header {
  font-size: 11px;
  color: #666;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.trades-list {
  max-height: 150px;
  overflow-y: auto;
}

.trade-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid #2a2a2e;
  font-size: 11px;
  transition: all 0.3s ease;
}

.trade-item:last-child {
  border-bottom: none;
}

.trade-time {
  color: #666;
  width: 60px;
}

.trade-type {
  font-weight: 600;
  width: 40px;
}

.trade-item.buy .trade-type { color: #18a058; }
.trade-item.sell .trade-type { color: #d03050; }

.trade-price {
  color: #aaa;
}

.trade-pnl {
  margin-left: auto;
  font-weight: 600;
}

.trade-pnl.profit { color: #18a058; }
.trade-pnl.loss { color: #d03050; }

.trades-empty {
  color: #555;
  font-size: 12px;
  font-style: italic;
  text-align: center;
  padding: 10px;
}

/* ========== EXPANDED MODE (DASHBOARD-STYLE) ========== */

/* Dashboard Header */
.dashboard-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
  opacity: 0;
  transform: translateY(-10px);
  animation: fadeSlideIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

@keyframes fadeSlideIn {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.status-badge {
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.status-badge.running {
  background: rgba(24, 160, 88, 0.15);
  color: #18a058;
  animation: pulse-badge 2.5s ease infinite;
}

.status-badge.idle {
  background: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.5);
}

@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.data-timestamp {
  margin-left: auto;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.4);
  background: rgba(255, 255, 255, 0.04);
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

/* Pipeline Section - Horizontal */
.pipeline-section {
  margin-bottom: 24px;
  opacity: 0;
  transform: translateY(-10px);
  animation: fadeSlideIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.1s forwards;
}

.pipeline-container {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  gap: 0;
  padding: 40px 24px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 16px;
}

.pipeline-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 100px;
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.stage-circle-lg {
  position: relative;
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.03);
  border: 3px solid rgba(255, 255, 255, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
  will-change: transform, box-shadow, border-color, background;
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.pipeline-stage.active .stage-circle-lg {
  border-color: #18a058;
  background: rgba(24, 160, 88, 0.12);
  box-shadow: 0 0 50px rgba(24, 160, 88, 0.5);
  transform: scale(1.08);
}

.pipeline-stage.completed .stage-circle-lg {
  border-color: #18a058;
  background: rgba(24, 160, 88, 0.25);
}

.stage-number-lg {
  font-size: 28px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.3);
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.pipeline-stage.active .stage-number-lg,
.pipeline-stage.completed .stage-number-lg {
  color: #18a058;
}

/* Large Pulse Ring */
.pulse-ring-lg {
  position: absolute;
  top: -6px;
  left: -6px;
  right: -6px;
  bottom: -6px;
  border-radius: 50%;
  border: 3px solid #18a058;
  will-change: transform, opacity;
  animation: pulse-lg 2s cubic-bezier(0.22, 1, 0.36, 1) infinite;
}

@keyframes pulse-lg {
  0% {
    transform: scale(1);
    opacity: 0.7;
  }
  100% {
    transform: scale(1.4);
    opacity: 0;
  }
}

.stage-label-lg {
  margin-top: 14px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.5);
  transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1);
}

.pipeline-stage.active .stage-label-lg,
.pipeline-stage.completed .stage-label-lg {
  color: #18a058;
}

.stage-result {
  margin-top: 8px;
  text-align: center;
  opacity: 0;
  transform: translateY(5px);
  animation: fadeSlideIn 0.4s cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.result-signal {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
  transition: all 0.3s ease;
}

.result-signal.long { background: rgba(24, 160, 88, 0.2); color: #18a058; }
.result-signal.short { background: rgba(208, 48, 80, 0.2); color: #d03050; }
.result-signal.neutral { color: rgba(255, 255, 255, 0.5); }

.result-confidence {
  display: block;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.35);
  margin-top: 4px;
}

/* Pipeline Connection Lines */
.pipeline-line {
  width: 50px;
  height: 4px;
  background: rgba(255, 255, 255, 0.08);
  margin-top: 38px;
  position: relative;
  overflow: hidden;
  border-radius: 2px;
}

.pipeline-line .line-progress {
  position: absolute;
  top: 0;
  left: 0;
  width: 0;
  height: 100%;
  background: linear-gradient(90deg, #18a058, #2ecc71);
  will-change: width;
  transition: width 0.7s cubic-bezier(0.22, 1, 0.36, 1);
  border-radius: 2px;
}

.pipeline-line.active .line-progress {
  width: 100%;
}

/* Processing Section */
.processing-section {
  margin-bottom: 24px;
  opacity: 0;
  transform: translateY(-10px);
  animation: fadeSlideIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.15s forwards;
}

.processing-card {
  background: rgba(24, 160, 88, 0.08);
  border: 1px solid rgba(24, 160, 88, 0.2);
  border-radius: 12px;
  padding: 14px 18px;
  display: flex;
  align-items: center;
  gap: 16px;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.processing-title {
  font-size: 14px;
  font-weight: 600;
  color: #18a058;
}

.processing-status {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
}

/* Agents Section */
.agents-section {
  margin-bottom: 24px;
  opacity: 0;
  transform: translateY(-10px);
  animation: fadeSlideIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.2s forwards;
}

.agents-section h2 {
  font-size: 16px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.7);
  margin: 0 0 16px 0;
}

.agents-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}

.agent-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 12px;
  padding: 16px;
  opacity: 0;
  transform: translateY(10px);
  animation: fadeSlideIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) forwards;
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}

.agent-card:hover {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.1);
}

.agent-card.wide {
  grid-column: span 2;
}

.agent-card.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  grid-column: span 2;
}

.placeholder-text {
  color: rgba(255, 255, 255, 0.3);
  font-style: italic;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.card-number {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: rgba(24, 160, 88, 0.15);
  color: #18a058;
  font-size: 14px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-name {
  font-size: 14px;
  font-weight: 500;
  flex: 1;
}

.card-signal {
  font-size: 12px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.1);
  transition: all 0.3s ease;
}

.card-signal.long { background: rgba(24, 160, 88, 0.2); color: #18a058; }
.card-signal.short { background: rgba(208, 48, 80, 0.2); color: #d03050; }
.card-signal.neutral { color: rgba(255, 255, 255, 0.5); }

.card-confidence {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin-bottom: 10px;
}

.card-info {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
}

.card-metrics {
  display: flex;
  gap: 24px;
  margin-bottom: 12px;
}

.metric {
  display: flex;
  flex-direction: column;
}

.metric-label {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  text-transform: uppercase;
}

.metric-value {
  font-size: 16px;
  font-weight: 600;
}

.card-reasoning {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
  line-height: 1.5;
  padding: 10px;
  background: rgba(0, 0, 0, 0.2);
  border-radius: 8px;
}

.card-params {
  margin-top: 12px;
  background: rgba(24, 160, 88, 0.05);
  border: 1px solid rgba(24, 160, 88, 0.15);
  border-radius: 8px;
  padding: 10px;
}

.param-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 12px;
}

.param-label {
  color: rgba(255, 255, 255, 0.5);
}

.param-value {
  color: #18a058;
  font-weight: 600;
}

/* Activity Log */
.log-section {
  margin-bottom: 16px;
  opacity: 0;
  transform: translateY(-10px);
  animation: fadeSlideIn 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.25s forwards;
}

.log-section h2 {
  font-size: 16px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.7);
  margin: 0 0 16px 0;
}

.log-container {
  background: rgba(0, 0, 0, 0.25);
  border-radius: 12px;
  padding: 16px;
  max-height: 200px;
  overflow-y: auto;
}

.log-entry {
  display: flex;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  font-size: 12px;
  transition: all 0.3s ease;
}

.log-entry:last-child {
  border-bottom: none;
}

.log-time {
  color: rgba(255, 255, 255, 0.3);
  min-width: 65px;
}

.log-agent {
  color: #64B5F6;
  min-width: 100px;
  font-weight: 500;
}

.log-message {
  color: rgba(255, 255, 255, 0.65);
}

.log-entry.success .log-message { color: #18a058; }
.log-entry.warning .log-message { color: #FFC107; }
.log-entry.error .log-message { color: #d03050; }

.log-empty {
  color: rgba(255, 255, 255, 0.3);
  text-align: center;
  padding: 24px;
  font-size: 13px;
}
</style>
