<template>
  <div class="dashboard">
    <!-- Main Layout: Left Panel + Right Content -->
    <div class="dashboard-layout">
      <!-- Left Panel - Control & Data -->
      <aside class="left-panel">
        <!-- Run Analysis Button -->
        <button @click="runAnalysis" :disabled="isAnalyzing || !isMarketOpen" class="run-button" :class="{ disabled: !isMarketOpen }">
          <span class="run-icon" :class="{ spinning: isAnalyzing }">⚡</span>
          {{ isAnalyzing ? 'Analyzing...' : (isMarketOpen ? 'Run Analysis' : 'Market Closed') }}
        </button>

        <!-- Analysis Time -->
        <div class="analysis-info">
          <div class="info-label">Last Analysis</div>
          <div class="info-value">{{ lastAnalysisTime || '--' }}</div>
        </div>

        <!-- Next Scheduled Run -->
        <div class="schedule-info">
          <div class="info-label">Next Scheduled Run</div>
          <div class="info-value schedule-time">{{ nextScheduledRun }}</div>
        </div>

        <!-- Market Status -->
        <div class="market-status" :class="{ open: isMarketOpen, closed: !isMarketOpen }">
          <div class="status-dot"></div>
          <span>{{ isMarketOpen ? 'Market Open' : 'Market Closed' }}</span>
        </div>

        <!-- Market Data -->
        <div class="market-data">
          <div class="market-header">
            <span class="market-symbol">XAUUSD</span>
            <span class="market-live" v-if="marketData.price && isMarketOpen && !isDataStale">LIVE</span>
            <span class="market-stale" v-else-if="marketData.price && isDataStale">DELAYED</span>
          </div>
          <div class="market-price">€{{ marketData.price?.toFixed(2) || '--' }}</div>
          <div class="market-time">{{ marketData.timestamp || '--' }}</div>
          <div class="market-age" v-if="dataAge">{{ dataAge }}</div>
        </div>

        <!-- Final Decision -->
        <div class="decision-panel" v-if="finalDecision" :class="finalDecision.type">
          <div class="decision-label">Decision</div>
          <div class="decision-value">{{ finalDecision.action }}</div>
          <div class="decision-reason" v-if="finalDecision.details">
            {{ finalDecision.details }}
          </div>
        </div>

        <!-- Trade Parameters -->
        <div class="trade-params" v-if="agents.risk?.approved">
          <div class="param-row">
            <span class="param-label">Position</span>
            <span class="param-value">{{ agents.risk.position_size }}</span>
          </div>
          <div class="param-row">
            <span class="param-label">Stop Loss</span>
            <span class="param-value">€{{ agents.risk.stop_loss?.toFixed(2) }}</span>
          </div>
          <div class="param-row">
            <span class="param-label">Take Profit</span>
            <span class="param-value">€{{ agents.risk.take_profit?.toFixed(2) }}</span>
          </div>
        </div>

        <!-- Logout -->
        <button @click="logout" class="logout-button">Logout</button>
      </aside>

      <!-- Right Content -->
      <main class="right-content">
        <!-- Header -->
        <header class="content-header">
          <h1>Agent Pipeline</h1>
          <span class="status-badge" :class="systemStatus">
            {{ systemStatus === 'running' ? 'Processing' : 'Ready' }}
          </span>
          <span class="data-timestamp" v-if="dataFetchedTime">
            Data from {{ dataFetchedTime }}
          </span>
        </header>

        <!-- Agent Pipeline - Horizontal -->
        <section class="pipeline-section">
          <div class="pipeline-container">
            <!-- Visual Analyst -->
            <div class="pipeline-stage" :class="getStageClass('visual')">
              <div class="stage-circle">
                <div class="pulse-ring" v-if="activeStage === 'visual'"></div>
              </div>
              <div class="stage-label">Visual</div>
              <div class="stage-result" v-if="agents.visual">
                <span class="result-signal" :class="agents.visual.signal?.toLowerCase()">
                  {{ agents.visual.signal }}
                </span>
                <span class="result-confidence">{{ formatConfidence(agents.visual.confidence) }}</span>
              </div>
            </div>

            <!-- Connection Line 1 -->
            <div class="pipeline-line" :class="{ active: isStageComplete('visual') }">
              <div class="line-progress"></div>
            </div>

            <!-- Technical Analyst -->
            <div class="pipeline-stage" :class="getStageClass('technical')">
              <div class="stage-circle">
                <div class="pulse-ring" v-if="activeStage === 'technical'"></div>
              </div>
              <div class="stage-label">Technical</div>
              <div class="stage-result" v-if="agents.technical">
                <span class="result-signal" :class="agents.technical.signal?.toLowerCase()">
                  {{ agents.technical.signal }}
                </span>
                <span class="result-confidence">{{ formatConfidence(agents.technical.confidence) }}</span>
              </div>
            </div>

            <!-- Connection Line 2 -->
            <div class="pipeline-line" :class="{ active: isStageComplete('technical') }">
              <div class="line-progress"></div>
            </div>

            <!-- Meta Agent -->
            <div class="pipeline-stage" :class="getStageClass('meta')">
              <div class="stage-circle">
                <div class="pulse-ring" v-if="activeStage === 'meta'"></div>
              </div>
              <div class="stage-label">Meta</div>
              <div class="stage-result" v-if="agents.meta">
                <span class="result-signal" :class="getMetaSignalClass(agents.meta.decision)">
                  {{ agents.meta.decision }}
                </span>
                <span class="result-confidence">{{ formatConfidence(agents.meta.confidence) }}</span>
              </div>
            </div>

            <!-- Connection Line 3 -->
            <div class="pipeline-line" :class="{ active: isStageComplete('meta') }">
              <div class="line-progress"></div>
            </div>

            <!-- Risk Manager -->
            <div class="pipeline-stage" :class="getStageClass('risk')">
              <div class="stage-circle">
                <div class="pulse-ring" v-if="activeStage === 'risk'"></div>
              </div>
              <div class="stage-label">Risk</div>
              <div class="stage-result" v-if="agents.risk?.checked">
                <span class="result-signal" :class="agents.risk.approved ? 'long' : 'neutral'">
                  {{ agents.risk.approved ? 'OK' : 'NO' }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <!-- Current Processing Info -->
        <section class="processing-section" v-if="currentStageDetails">
          <div class="processing-card">
            <span class="processing-title">{{ currentStageDetails.title }}</span>
            <span class="processing-status">{{ currentStageDetails.reasoning }}</span>
          </div>
        </section>

        <!-- Agent Details Grid -->
        <section class="agents-section" v-if="hasResults">
          <h2>Agent Details</h2>
          <div class="agents-grid">
            <!-- Visual Analyst -->
            <div class="agent-card" v-if="agents.visual">
              <div class="card-header">
                <span class="card-icon">👁️</span>
                <span class="card-name">Visual Analyst</span>
                <span class="card-signal" :class="agents.visual.signal?.toLowerCase()">
                  {{ agents.visual.signal }}
                </span>
              </div>
              <div class="card-confidence">
                Confidence: {{ formatConfidence(agents.visual.confidence) }}
              </div>
              <div class="card-timeframes" v-if="agents.visual.timeframes">
                <div v-for="(tf, key) in agents.visual.timeframes" :key="key" class="tf-item">
                  <span class="tf-name">{{ key }}</span>
                  <span class="tf-signal" :class="tf.signal?.toLowerCase()">{{ tf.signal }}</span>
                  <span class="tf-conf">{{ formatConfidence(tf.confidence) }}</span>
                </div>
              </div>
              <div class="card-reasoning" v-if="agents.visual.reasoning">
                {{ agents.visual.reasoning }}
              </div>
            </div>

            <!-- Technical Analyst -->
            <div class="agent-card" v-if="agents.technical">
              <div class="card-header">
                <span class="card-icon">📊</span>
                <span class="card-name">Technical</span>
                <span class="card-signal" :class="agents.technical.signal?.toLowerCase()">
                  {{ agents.technical.signal }}
                </span>
              </div>
              <div class="card-confidence">
                Confidence: {{ formatConfidence(agents.technical.confidence) }}
              </div>
              <div class="card-info" v-if="agents.technical.trend">
                Trend: {{ agents.technical.trend }}
              </div>
            </div>

            <!-- Meta Agent -->
            <div class="agent-card wide" v-if="agents.meta">
              <div class="card-header">
                <span class="card-icon">🧠</span>
                <span class="card-name">Meta Agent</span>
                <span class="card-signal" :class="getMetaSignalClass(agents.meta.decision)">
                  {{ agents.meta.decision }}
                </span>
              </div>
              <div class="card-metrics">
                <div class="metric">
                  <span class="metric-label">Confidence</span>
                  <span class="metric-value">{{ formatConfidence(agents.meta.confidence) }}</span>
                </div>
                <div class="metric">
                  <span class="metric-label">Weighted Score</span>
                  <span class="metric-value">{{ agents.meta.weighted_score?.toFixed(2) || '--' }}</span>
                </div>
                <div class="metric">
                  <span class="metric-label">Agreement</span>
                  <span class="metric-value">{{ formatConfidence(agents.meta.agreement) }}</span>
                </div>
              </div>
              <div class="card-reasoning" v-if="agents.meta.reasoning">
                {{ agents.meta.reasoning }}
              </div>
            </div>

            <!-- Risk Manager -->
            <div class="agent-card" v-if="agents.risk?.checked">
              <div class="card-header">
                <span class="card-icon">🛡️</span>
                <span class="card-name">Risk Manager</span>
                <span class="card-signal" :class="agents.risk.approved ? 'long' : 'neutral'">
                  {{ agents.risk.approved ? 'APPROVED' : 'REJECTED' }}
                </span>
              </div>
              <div class="card-reasoning" v-if="agents.risk.rejection_reason">
                {{ agents.risk.rejection_reason }}
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
              Click "Run Analysis" to start the agent pipeline
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import api, { getWebSocketUrl } from '@/config/api'

const router = useRouter()
const authStore = useAuthStore()

// State
const systemStatus = ref('idle')
const isAnalyzing = ref(false)
const lastAnalysisTime = ref(null)
const activeStage = ref(null)
const completedStages = ref([])
const progressWebSocket = ref(null)
const wsConnected = ref(false)
const currentTime = ref(new Date())
const dataFetchedTime = ref(null)

// Gold market hours (CME COMEX) - Sunday 6pm to Friday 5pm ET with daily break 5pm-6pm
const isMarketOpen = computed(() => {
  const now = currentTime.value
  const day = now.getDay() // 0 = Sunday, 6 = Saturday
  const hour = now.getHours()
  const minute = now.getMinutes()
  const timeInMinutes = hour * 60 + minute

  // Convert to ET (assuming local time, adjust if needed)
  // For simplicity, we check basic gold trading hours:
  // - Closed Saturday all day
  // - Closed Sunday until 6pm ET (18:00)
  // - Closed Friday after 5pm ET (17:00)
  // - Daily maintenance break 5pm-6pm ET

  // Saturday - fully closed
  if (day === 6) return false

  // Sunday - opens at 6pm ET (18:00)
  if (day === 0 && hour < 18) return false

  // Friday - closes at 5pm ET (17:00)
  if (day === 5 && hour >= 17) return false

  // Daily break 5pm-6pm ET (17:00-18:00) except Friday close
  if (hour === 17) return false

  return true
})

// Calculate next scheduled run (every hour at :00)
const nextScheduledRun = computed(() => {
  const now = new Date()
  const nextHour = new Date(now)
  nextHour.setHours(nextHour.getHours() + 1)
  nextHour.setMinutes(0)
  nextHour.setSeconds(0)

  const hours = nextHour.getHours()
  const ampm = hours >= 12 ? 'PM' : 'AM'
  const displayHours = hours % 12 || 12

  return `${displayHours}:00 ${ampm}`
})

const marketData = ref({
  price: null,
  timestamp: null,
  rawTime: null  // Unix timestamp for age calculation
})

const agents = ref({
  visual: null,
  technical: null,
  sentiment: null,
  meta: null,
  risk: null
})

const activityLog = ref([])

// Computed
const hasResults = computed(() => {
  return agents.value.visual || agents.value.technical || agents.value.meta || agents.value.risk?.checked
})

// Check if market data is stale (more than 5 minutes old)
const isDataStale = computed(() => {
  if (!marketData.value.rawTime) return false
  const ageMs = Date.now() - (marketData.value.rawTime * 1000)
  return ageMs > 5 * 60 * 1000  // 5 minutes
})

// Calculate data age for display
const dataAge = computed(() => {
  if (!marketData.value.rawTime) return null
  const ageMs = Date.now() - (marketData.value.rawTime * 1000)
  const ageMinutes = Math.floor(ageMs / 60000)

  if (ageMinutes < 1) return null  // Less than 1 minute, don't show
  if (ageMinutes < 60) return `${ageMinutes}m ago`

  const ageHours = Math.floor(ageMinutes / 60)
  const remainingMinutes = ageMinutes % 60
  if (ageHours < 24) return `${ageHours}h ${remainingMinutes}m ago`

  return `${Math.floor(ageHours / 24)}d ago`
})

const currentStageDetails = computed(() => {
  if (!activeStage.value) return null

  const titles = {
    visual: 'Visual Analyst',
    technical: 'Technical Analyst',
    meta: 'Meta Agent',
    risk: 'Risk Manager'
  }

  const reasonings = {
    visual: 'Analyzing chart patterns across multiple timeframes...',
    technical: 'Computing technical indicators and signals...',
    meta: 'Synthesizing all agent signals into final decision...',
    risk: 'Validating trade parameters and risk limits...'
  }

  return {
    title: titles[activeStage.value],
    reasoning: reasonings[activeStage.value]
  }
})

const finalDecision = computed(() => {
  if (!agents.value.meta) return null

  const decision = agents.value.meta.decision
  const riskApproved = agents.value.risk?.approved

  if (decision === 'LONG' || decision === 'BUY') {
    return {
      action: riskApproved ? 'BUY' : 'BUY (Risk Rejected)',
      type: 'buy',
      details: riskApproved ? null : agents.value.risk?.rejection_reason
    }
  } else if (decision === 'SHORT' || decision === 'SELL') {
    return {
      action: riskApproved ? 'SELL' : 'SELL (Risk Rejected)',
      type: 'sell',
      details: riskApproved ? null : agents.value.risk?.rejection_reason
    }
  } else {
    return {
      action: 'NO TRADE',
      type: 'neutral',
      details: 'Conditions not met'
    }
  }
})

// Helpers
function formatConfidence(value) {
  if (value === null || value === undefined) return '--'
  const normalized = value > 1 ? value / 100 : value
  return `${(normalized * 100).toFixed(0)}%`
}

function getStageClass(stage) {
  if (activeStage.value === stage) return 'active'
  if (completedStages.value.includes(stage)) return 'completed'
  return ''
}

function isStageComplete(stage) {
  return completedStages.value.includes(stage)
}

function getMetaSignalClass(decision) {
  if (!decision) return 'neutral'
  const d = decision.toUpperCase()
  if (d === 'LONG' || d === 'BUY') return 'long'
  if (d === 'SHORT' || d === 'SELL') return 'short'
  return 'neutral'
}

function addLog(agent, message, level = 'info') {
  const now = new Date()
  const time = now.toLocaleTimeString()
  activityLog.value.unshift({ time, agent, message, level })
  if (activityLog.value.length > 50) {
    activityLog.value.pop()
  }
}

// WebSocket for real-time progress updates
function connectProgressWebSocket() {
  try {
    const wsUrl = getWebSocketUrl('/api/dashboard/ws/progress')
    progressWebSocket.value = new WebSocket(wsUrl)

    progressWebSocket.value.onopen = () => {
      console.log('Progress WebSocket connected')
      wsConnected.value = true
    }

    progressWebSocket.value.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        handleProgressUpdate(data)
      } catch (err) {
        console.error('Error parsing WebSocket message:', err)
      }
    }

    progressWebSocket.value.onclose = () => {
      console.log('Progress WebSocket closed')
      wsConnected.value = false
      // Reconnect after 3 seconds
      setTimeout(connectProgressWebSocket, 3000)
    }

    progressWebSocket.value.onerror = (err) => {
      console.error('Progress WebSocket error:', err)
    }
  } catch (err) {
    console.error('Failed to connect WebSocket:', err)
  }
}

function handleProgressUpdate(data) {
  if (data.type !== 'progress') return

  const stage = data.stage
  const message = data.message
  const level = data.level

  // Map stage names to our internal names
  const stageMap = {
    'visual_analyst': 'visual',
    'visual': 'visual',
    'technical_analyst': 'technical',
    'technical': 'technical',
    'meta_agent': 'meta',
    'meta': 'meta',
    'risk_manager': 'risk',
    'risk': 'risk',
    'system': 'System',
    'complete': null,
    'error': null
  }

  const mappedStage = stageMap[stage] || stage

  // Update active stage
  if (mappedStage && mappedStage !== 'System' && level !== 'error') {
    if (!completedStages.value.includes(mappedStage)) {
      activeStage.value = mappedStage
    }
  }

  // Mark stage as complete on success
  if (level === 'success' && mappedStage && mappedStage !== 'System') {
    if (!completedStages.value.includes(mappedStage)) {
      completedStages.value.push(mappedStage)
    }
  }

  // Handle completion
  if (stage === 'complete') {
    activeStage.value = null
  }

  // Handle errors
  if (stage === 'error' || level === 'error') {
    addLog(mappedStage || 'System', message, 'error')
  }

  // Log all progress messages
  const agentName = {
    'visual': 'Visual Analyst',
    'technical': 'Technical Analyst',
    'meta': 'Meta Agent',
    'risk': 'Risk Manager',
    'system': 'System',
    'complete': 'System',
    'error': 'System'
  }[stage] || stage

  addLog(agentName, message, level)
}

// Actions
async function fetchMarketData() {
  try {
    const response = await fetch(`${api.baseUrl}/api/latest/XAUUSD`)
    if (response.ok) {
      const data = await response.json()
      marketData.value.price = data.close
      marketData.value.rawTime = data.time  // Store raw timestamp for age calculation
      marketData.value.timestamp = new Date(data.time * 1000).toLocaleString()
    }
  } catch (err) {
    console.error('Failed to fetch market data:', err)
  }
}

async function runAnalysis() {
  isAnalyzing.value = true
  systemStatus.value = 'running'
  activeStage.value = 'visual'
  completedStages.value = []

  // Reset agent states
  agents.value = {
    visual: null,
    technical: null,
    sentiment: null,
    meta: null,
    risk: null
  }

  const startTime = new Date()
  lastAnalysisTime.value = startTime.toLocaleString()

  addLog('System', 'Starting analysis pipeline...', 'info')

  try {
    // Call the analysis endpoint with timeout
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 180000) // 3 min timeout

    const response = await fetch(`${api.baseUrl}/api/dashboard/analyze`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authStore.getAuthHeaders()
      },
      body: JSON.stringify({
        symbol: 'XAUUSD',
        timeframes: ['5min'],  // Single timeframe for speed
        include_technical: true
      }),
      signal: controller.signal
    })

    clearTimeout(timeoutId)

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `Analysis failed (${response.status})`)
    }

    const result = await response.json()

    // Capture data timestamp from server
    if (result.timestamp) {
      const ts = new Date(result.timestamp)
      dataFetchedTime.value = ts.toLocaleTimeString()
    } else if (result.market_data?.timestamp) {
      dataFetchedTime.value = result.market_data.timestamp
    }

    // Process results from server
    if (result.visual) {
      agents.value.visual = result.visual
      if (!completedStages.value.includes('visual')) {
        completedStages.value.push('visual')
      }
    }

    if (result.technical) {
      agents.value.technical = result.technical
      if (!completedStages.value.includes('technical')) {
        completedStages.value.push('technical')
      }
    }

    if (result.meta) {
      agents.value.meta = result.meta
      if (!completedStages.value.includes('meta')) {
        completedStages.value.push('meta')
      }
    }

    if (result.risk) {
      agents.value.risk = result.risk
      if (!completedStages.value.includes('risk')) {
        completedStages.value.push('risk')
      }
    }

    // Add server logs to activity log
    if (result.logs && Array.isArray(result.logs)) {
      result.logs.forEach(log => {
        // Check if this log is already added (avoid duplicates from WebSocket)
        const exists = activityLog.value.some(
          existing => existing.message === log.message && existing.agent === log.agent
        )
        if (!exists) {
          activityLog.value.push({
            time: log.time,
            agent: log.agent,
            message: log.message,
            level: log.level
          })
        }
      })
      // Sort by time (most recent first)
      activityLog.value.sort((a, b) => {
        const timeA = a.time.split(':').map(Number)
        const timeB = b.time.split(':').map(Number)
        return (timeB[0] * 3600 + timeB[1] * 60 + timeB[2]) - (timeA[0] * 3600 + timeA[1] * 60 + timeA[2])
      })
    }

    activeStage.value = null
    addLog('System', 'Analysis complete', 'success')

  } catch (err) {
    console.error('Analysis error:', err)
    if (err.name === 'AbortError') {
      addLog('System', 'Analysis timed out after 3 minutes', 'error')
    } else {
      addLog('System', `Error: ${err.message}`, 'error')
    }
    activeStage.value = null
  } finally {
    isAnalyzing.value = false
    systemStatus.value = 'idle'
  }
}

function logout() {
  authStore.logout()
  router.push('/login')
}

// Lifecycle
let marketInterval = null
let timeInterval = null

onMounted(() => {
  fetchMarketData()
  marketInterval = setInterval(fetchMarketData, 30000)
  // Update time every minute for market status
  timeInterval = setInterval(() => {
    currentTime.value = new Date()
  }, 60000)
  // Connect to progress WebSocket
  connectProgressWebSocket()
})

onUnmounted(() => {
  if (marketInterval) {
    clearInterval(marketInterval)
  }
  if (timeInterval) {
    clearInterval(timeInterval)
  }
  if (progressWebSocket.value) {
    progressWebSocket.value.close()
  }
})
</script>

<style scoped>
.dashboard {
  min-height: 100vh;
  background: #08080f;
  color: #fff;
}

.dashboard-layout {
  display: flex;
  min-height: 100vh;
}

/* Left Panel */
.left-panel {
  width: 280px;
  background: #0d0d15;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.run-button {
  width: 100%;
  padding: 16px 20px;
  background: linear-gradient(135deg, #4CAF50, #2E7D32);
  color: #fff;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  transition: all 0.2s;
}

.run-button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(76, 175, 80, 0.35);
}

.run-button:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.run-button.disabled {
  background: linear-gradient(135deg, #555, #333);
}

.run-icon {
  font-size: 20px;
}

/* Market Status */
.market-status {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
}

.market-status.open {
  background: rgba(76, 175, 80, 0.1);
  color: #4CAF50;
  border: 1px solid rgba(76, 175, 80, 0.3);
}

.market-status.closed {
  background: rgba(244, 67, 54, 0.1);
  color: #f44336;
  border: 1px solid rgba(244, 67, 54, 0.3);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.market-status.open .status-dot {
  background: #4CAF50;
  box-shadow: 0 0 8px #4CAF50;
  animation: pulse-dot 2s ease infinite;
}

.market-status.closed .status-dot {
  background: #f44336;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.run-icon.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.analysis-info {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 10px;
  padding: 14px;
}

.info-label {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.4);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}

.info-value {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.8);
}

.schedule-info {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 10px;
  padding: 14px;
}

.schedule-time {
  color: #4CAF50;
  font-weight: 600;
  font-size: 16px;
}

.market-data {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 10px;
  padding: 16px;
}

.market-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.market-symbol {
  font-size: 12px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.6);
}

.market-live {
  font-size: 9px;
  padding: 3px 8px;
  background: rgba(76, 175, 80, 0.2);
  color: #4CAF50;
  border-radius: 10px;
  font-weight: 600;
}

.market-stale {
  font-size: 9px;
  padding: 3px 8px;
  background: rgba(255, 152, 0, 0.2);
  color: #FF9800;
  border-radius: 10px;
  font-weight: 600;
}

.market-age {
  font-size: 10px;
  color: #FF9800;
  margin-top: 2px;
}

.market-price {
  font-size: 28px;
  font-weight: 700;
  color: #4CAF50;
}

.market-time {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.3);
  margin-top: 4px;
}

.decision-panel {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}

.decision-panel.buy {
  background: rgba(76, 175, 80, 0.1);
  border: 1px solid rgba(76, 175, 80, 0.3);
}

.decision-panel.sell {
  background: rgba(244, 67, 54, 0.1);
  border: 1px solid rgba(244, 67, 54, 0.3);
}

.decision-panel.neutral {
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.decision-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: rgba(255, 255, 255, 0.4);
  margin-bottom: 8px;
}

.decision-value {
  font-size: 24px;
  font-weight: 700;
}

.decision-panel.buy .decision-value {
  color: #4CAF50;
}

.decision-panel.sell .decision-value {
  color: #f44336;
}

.decision-panel.neutral .decision-value {
  color: rgba(255, 255, 255, 0.5);
}

.decision-reason {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.4);
  margin-top: 8px;
}

.trade-params {
  background: rgba(76, 175, 80, 0.05);
  border: 1px solid rgba(76, 175, 80, 0.2);
  border-radius: 10px;
  padding: 14px;
}

.param-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  font-size: 13px;
}

.param-row .param-label {
  color: rgba(255, 255, 255, 0.5);
}

.param-row .param-value {
  color: #4CAF50;
  font-weight: 600;
}

.logout-button {
  margin-top: auto;
  width: 100%;
  padding: 12px;
  background: transparent;
  color: rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  font-size: 13px;
  cursor: pointer;
}

.logout-button:hover {
  border-color: rgba(255, 255, 255, 0.2);
  color: rgba(255, 255, 255, 0.7);
}

/* Right Content */
.right-content {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}

.content-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.content-header h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
}

.status-badge {
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.running {
  background: rgba(76, 175, 80, 0.15);
  color: #4CAF50;
  animation: pulse-badge 2s ease infinite;
}

.status-badge.idle {
  background: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.5);
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

@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Pipeline Section */
.pipeline-section {
  margin-bottom: 24px;
}

.pipeline-container {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  gap: 0;
  padding: 48px 24px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 16px;
}

.pipeline-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 120px;
}

.stage-circle {
  position: relative;
  width: 90px;
  height: 90px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.03);
  border: 3px solid rgba(255, 255, 255, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.pipeline-stage.active .stage-circle {
  border-color: #4CAF50;
  background: rgba(76, 175, 80, 0.12);
  box-shadow: 0 0 50px rgba(76, 175, 80, 0.5);
}

.pipeline-stage.completed .stage-circle {
  border-color: #4CAF50;
  background: rgba(76, 175, 80, 0.25);
}

.stage-label {
  margin-top: 16px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.5);
  transition: color 0.3s;
}

.pipeline-stage.active .stage-label,
.pipeline-stage.completed .stage-label {
  color: #4CAF50;
}

.stage-result {
  margin-top: 8px;
  text-align: center;
}

.result-signal {
  display: block;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
}

.result-signal.long {
  background: rgba(76, 175, 80, 0.2);
  color: #4CAF50;
}

.result-signal.short {
  background: rgba(244, 67, 54, 0.2);
  color: #f44336;
}

.result-signal.neutral {
  color: rgba(255, 255, 255, 0.5);
}

.result-confidence {
  display: block;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.35);
  margin-top: 4px;
}

/* Pulse Animation */
.pulse-ring {
  position: absolute;
  top: -6px;
  left: -6px;
  right: -6px;
  bottom: -6px;
  border-radius: 50%;
  border: 3px solid #4CAF50;
  animation: pulse 1.5s ease-out infinite;
}

@keyframes pulse {
  0% {
    transform: scale(1);
    opacity: 1;
  }
  100% {
    transform: scale(1.5);
    opacity: 0;
  }
}

/* Connection Lines */
.pipeline-line {
  width: 60px;
  height: 4px;
  background: rgba(255, 255, 255, 0.08);
  margin-top: 43px;
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
  background: linear-gradient(90deg, #4CAF50, #81C784);
  transition: width 0.4s ease;
  border-radius: 2px;
}

.pipeline-line.active .line-progress {
  width: 100%;
}

/* Processing Section */
.processing-section {
  margin-bottom: 24px;
}

.processing-card {
  background: rgba(76, 175, 80, 0.08);
  border: 1px solid rgba(76, 175, 80, 0.2);
  border-radius: 12px;
  padding: 14px 18px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.processing-title {
  font-size: 14px;
  font-weight: 600;
  color: #4CAF50;
}

.processing-status {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
}

/* Agents Section */
.agents-section {
  margin-bottom: 24px;
}

.agents-section h2 {
  font-size: 16px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.7);
  margin: 0 0 16px 0;
}

.agents-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}

.agent-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 12px;
  padding: 16px;
}

.agent-card.wide {
  grid-column: span 2;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.card-icon {
  font-size: 18px;
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
}

.card-signal.long {
  background: rgba(76, 175, 80, 0.2);
  color: #4CAF50;
}

.card-signal.short {
  background: rgba(244, 67, 54, 0.2);
  color: #f44336;
}

.card-signal.neutral {
  color: rgba(255, 255, 255, 0.5);
}

.card-confidence {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin-bottom: 10px;
}

.card-timeframes {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 10px;
}

.tf-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 12px;
}

.tf-name {
  color: rgba(255, 255, 255, 0.5);
}

.tf-signal {
  font-weight: 600;
}

.tf-signal.long {
  color: #4CAF50;
}

.tf-signal.short {
  color: #f44336;
}

.tf-conf {
  color: rgba(255, 255, 255, 0.4);
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

/* Log Section */
.log-section {
  margin-bottom: 24px;
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
  max-height: 220px;
  overflow-y: auto;
}

.log-entry {
  display: flex;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  font-size: 12px;
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

.log-entry.success .log-message {
  color: #4CAF50;
}

.log-entry.warning .log-message {
  color: #FFC107;
}

.log-entry.error .log-message {
  color: #f44336;
}

.log-empty {
  color: rgba(255, 255, 255, 0.3);
  text-align: center;
  padding: 24px;
  font-size: 13px;
}

/* Responsive */
@media (max-width: 900px) {
  .dashboard-layout {
    flex-direction: column;
  }

  .left-panel {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    flex-direction: row;
    flex-wrap: wrap;
    gap: 12px;
    padding: 16px;
  }

  .run-button {
    flex: 1;
    min-width: 150px;
  }

  .analysis-info,
  .market-data,
  .decision-panel {
    flex: 1;
    min-width: 150px;
  }

  .trade-params {
    width: 100%;
  }

  .logout-button {
    width: auto;
    margin-top: 0;
  }

  .pipeline-container {
    flex-wrap: wrap;
    gap: 16px;
  }

  .pipeline-line {
    display: none;
  }

  .agent-card.wide {
    grid-column: span 1;
  }
}
</style>
