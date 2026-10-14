<template>
  <div class="live-trading-view">
    <TheTopBar />

    <div class="trading-layout" :class="{ 'agent-expanded': isAgentExpanded }">
      <!-- Chart Section -->
      <div class="chart-section">
        <ChartArea ref="chartArea" />
      </div>

      <!-- Toggle Button -->
      <button class="toggle-panel-btn" @click="toggleAgentPanel" :class="{ expanded: isAgentExpanded }">
        <span class="toggle-icon">{{ isAgentExpanded ? '→' : '←' }}</span>
        <span class="toggle-text">{{ isAgentExpanded ? 'Chart' : 'Agents' }}</span>
      </button>

      <!-- Right Panel -->
      <div class="right-panel">
        <!-- Connection Status -->
        <div class="connection-status">
          <n-space align="center" justify="space-between">
            <n-badge :type="isConnected ? 'success' : 'error'" dot processing>
              <span class="status-text">
                {{ isConnected ? 'Live' : 'Offline' }}
              </span>
            </n-badge>
            <n-button
              :type="isConnected ? 'error' : 'success'"
              size="small"
              @click="toggleConnection"
            >
              {{ isConnected ? 'Disconnect' : 'Connect' }}
            </n-button>
          </n-space>
        </div>

        <!-- Agent Pipeline -->
        <AgentPipeline ref="agentPipeline" :expanded="isAgentExpanded" />

        <!-- Demo Button (for testing) -->
        <n-button
          size="small"
          style="margin-top: 10px; width: 100%"
          @click="runAgentDemo"
        >
          Run Agent Demo
        </n-button>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, watch, nextTick } from 'vue';
import {
  NCard,
  NSpace,
  NBadge,
  NButton,
  NDivider,
  NEmpty,
  NDescriptions,
  NDescriptionsItem,
  NTag,
  NInputNumber,
  NDataTable,
  useMessage,
} from 'naive-ui';
import TheTopBar from '@/components/TopBar/TheTopBar.vue';
import ChartArea from '@/components/Chart/ChartArea.vue';
import AgentPipeline from '@/components/AgentPipeline/AgentPipeline.vue';
import { useCurrentMarketStore } from '@/stores/currentMarketStore';
import { useMarketsStore } from '@/stores/marketsStore';
import { API_CONFIG, getHeaders, getWebSocketUrl } from '@/config/api';

export default {
  name: 'LiveDevView',

  components: {
    TheTopBar,
    ChartArea,
    AgentPipeline,
    NSpace,
    NBadge,
    NButton,
  },

  setup() {
    const message = useMessage();
    const currentMarketStore = useCurrentMarketStore();
    const marketsStore = useMarketsStore();

    const isConnected = ref(false);
    const isAgentExpanded = ref(false);
    const chartArea = ref(null);
    const agentPipeline = ref(null);
    let websocket = null;

    // Track current 1-minute bar aggregation from 5-second bars
    let currentMinuteBar = null;

    // Toggle agent panel expansion
    const toggleAgentPanel = () => {
      isAgentExpanded.value = !isAgentExpanded.value;
      // Trigger chart resize after animation
      nextTick(() => {
        setTimeout(() => {
          if (chartArea.value) {
            chartArea.value.handleResize?.();
          }
        }, 350); // Match transition duration
      });
    };

    // Initialize markets and set default symbol on mount
    onMounted(async () => {
      console.log('LiveTradingView: Fetching markets...');
      await marketsStore.fetch();
      console.log('LiveTradingView: Markets fetched:', marketsStore.all);

      // Find XAUUSD (the symbol we stream from TWS)
      const usgoldMarket = marketsStore.all.find(m => m.symbol_id === 'XAUUSD');

      if (usgoldMarket) {
        console.log('LiveTradingView: Setting market to XAUUSD:', usgoldMarket);
        currentMarketStore.setMarket(usgoldMarket);
        console.log('LiveTradingView: Current market set to:', currentMarketStore.symbol_id);
      } else if (marketsStore.all.length > 0) {
        // Fallback to first available market
        console.log('LiveTradingView: XAUUSD not found, using first market:', marketsStore.all[0]);
        currentMarketStore.setMarket(marketsStore.all[0]);
      } else {
        console.warn('LiveTradingView: No markets available');
      }

      // Auto-connect to live data stream
      await new Promise(resolve => setTimeout(resolve, 1000)); // Wait for chart to initialize
      console.log('LiveTradingView: Auto-connecting to live data...');
      await toggleConnection();
    });

    // Run agent demo animation
    const runAgentDemo = () => {
      if (agentPipeline.value) {
        agentPipeline.value.simulateAgentCycle();
      }
    };

    const toggleConnection = async () => {
      try {
        if (isConnected.value) {
          // Disconnect WebSocket
          if (websocket) {
            websocket.close();
            websocket = null;
          }
          isConnected.value = false;
          message.info('Disconnected from real-time data');
        } else {
          // Use XAUUSD for API/TWS streaming (database symbol)
          const twsSymbol = currentMarketStore.symbol_id || 'XAUUSD';
          const displaySymbol = currentMarketStore.symbol || 'XAUUSD';  // Display name

          // Try to start streaming service (optional - will fail if TWS not running)
          console.log('Attempting to start streaming service...');
          try {
            const startResponse = await fetch(`${API_CONFIG.BASE_URL}/api/streaming/start`, {
              method: 'POST',
              headers: getHeaders(),
            });

            if (startResponse.ok) {
              console.log('Streaming service started');

              // Subscribe to XAUUSD commodity with correct parameters
              const subscribeResponse = await fetch(`${API_CONFIG.BASE_URL}/api/streaming/subscribe/${twsSymbol}?bar_size=5&sec_type=CMDTY&exchange=IBMETAL`, {
                method: 'POST',
                headers: getHeaders(),
              });

              if (subscribeResponse.ok) {
                console.log(`Subscribed to ${twsSymbol}`);
                message.success(`Connected to live ${displaySymbol} data stream`);
              } else {
                const subError = await subscribeResponse.json();
                console.error('Subscription failed:', subError);
                message.error(`Failed to subscribe: ${subError.detail}`);
              }
            } else {
              const error = await startResponse.json();
              console.warn('Streaming service not available:', error.detail);
              message.warning('TWS not connected - showing historical data only');
            }
          } catch (streamError) {
            console.warn('Streaming not available:', streamError.message);
            message.warning('Real-time streaming unavailable - showing historical data only');
          }

          // Connect WebSocket (use XAUUSD to match TWS streaming symbol)
          console.log(`Connecting WebSocket for ${twsSymbol}...`);
          websocket = new WebSocket(getWebSocketUrl(`/ws/live-data/${twsSymbol}`));

          websocket.onopen = async () => {
            console.log('WebSocket connected');
            isConnected.value = true;

            // Refresh chart data to fill any gaps (fetch latest from database)
            if (chartArea.value) {
              console.log('🔄 Refreshing chart data to fill gaps...');
              await chartArea.value.fetchCandlesticks();
            }

            console.log('✅ Ready to receive live updates');
          };

          websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('📊 Received 5-sec bar:', data);

            // Convert ISO timestamp to epoch seconds
            const timestamp = data.timestamp || data.time;
            const timeEpoch = Math.floor(new Date(timestamp).getTime() / 1000);

            if (isNaN(timeEpoch)) {
              console.error('❌ Invalid timestamp - timeEpoch is NaN');
              return;
            }

            // Round down to nearest minute for aggregation
            const minuteTime = Math.floor(timeEpoch / 60) * 60;

            // Parse OHLC values
            const open = parseFloat(data.open);
            const high = parseFloat(data.high);
            const low = parseFloat(data.low);
            const close = parseFloat(data.close);

            // Aggregate 5-second bars into 1-minute bar
            if (!currentMinuteBar || currentMinuteBar.time !== minuteTime) {
              // New minute started - start fresh aggregation
              console.log(`📊 New minute bar started: ${new Date(minuteTime * 1000).toLocaleTimeString()}`);
              currentMinuteBar = {
                time: minuteTime,
                open: open,      // First bar's open
                high: high,      // Track highest
                low: low,        // Track lowest
                close: close     // Most recent close
              };
            } else {
              // Same minute - aggregate with existing bar
              currentMinuteBar.high = Math.max(currentMinuteBar.high, high);
              currentMinuteBar.low = Math.min(currentMinuteBar.low, low);
              currentMinuteBar.close = close; // Update to most recent close
              console.log(`📊 Updated minute bar: H=${currentMinuteBar.high} L=${currentMinuteBar.low} C=${currentMinuteBar.close}`);
            }

            // Update chart with current aggregated 1-minute bar
            if (chartArea.value) {
              const plainBar = JSON.parse(JSON.stringify(currentMinuteBar));
              chartArea.value.updateCandlestick(plainBar);
            } else {
              console.error('❌ chartArea.value is null or undefined!');
            }

            message.success(`Live: €${close.toFixed(2)}`);
          };

          websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
          };

          websocket.onclose = () => {
            console.log('WebSocket closed');
            if (isConnected.value) {
              isConnected.value = false;
              message.info('Disconnected');
            }
          };
        }
      } catch (error) {
        console.error('Connection error:', error);
        message.error('Failed to connect: ' + error.message);
      }
    };

    const placeOrder = async () => {
      try {
        const symbol = currentMarketStore.currentMarket;
        const response = await fetch('http://localhost:8001/api/live/order', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            symbol,
            type: orderType.value,
            volume: orderVolume.value,
            stopLoss: stopLoss.value,
            takeProfit: takeProfit.value,
          }),
        });

        const data = await response.json();
        if (response.ok) {
          message.success(`Order placed: ${orderType.value} ${orderVolume.value} ${symbol}`);
          // Update position
          currentPosition.value = data.position;
        } else {
          message.error(data.detail || 'Failed to place order');
        }
      } catch (error) {
        message.error('Failed to place order: ' + error.message);
      }
    };

    const closePosition = async () => {
      try {
        const response = await fetch('http://localhost:8001/api/live/close-position', {
          method: 'POST',
        });

        const data = await response.json();
        if (response.ok) {
          message.success(`Position closed. P&L: ${data.pnl}`);
          currentPosition.value = null;
          recentTrades.value.unshift(data.trade);
        } else {
          message.error(data.detail || 'Failed to close position');
        }
      } catch (error) {
        message.error('Failed to close position: ' + error.message);
      }
    };

    return {
      isConnected,
      isAgentExpanded,
      chartArea,
      agentPipeline,
      toggleConnection,
      toggleAgentPanel,
      runAgentDemo,
    };
  },
};
</script>

<style scoped>
.live-trading-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #101014;
}

.trading-layout {
  display: flex;
  flex: 1;
  gap: 10px;
  overflow: hidden;
  position: relative;
}

/* Chart Section - Animated */
.chart-section {
  flex: 1;
  min-width: 0;
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}

.trading-layout.agent-expanded .chart-section {
  flex: 0 0 35%;
  max-width: 35%;
}

/* Toggle Button */
.toggle-panel-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  right: 330px;
  z-index: 100;
  background: linear-gradient(135deg, #2d5a27, #1a3518);
  border: 1px solid rgba(76, 175, 80, 0.4);
  border-radius: 8px 0 0 8px;
  padding: 12px 8px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: -2px 0 10px rgba(0, 0, 0, 0.3);
}

.toggle-panel-btn:hover {
  background: linear-gradient(135deg, #3d7a37, #2a5528);
  box-shadow: -4px 0 20px rgba(76, 175, 80, 0.3);
}

.toggle-panel-btn.expanded {
  right: calc(65% - 10px);
}

.toggle-icon {
  font-size: 16px;
  color: #4CAF50;
  font-weight: bold;
}

.toggle-text {
  writing-mode: vertical-rl;
  text-orientation: mixed;
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.8);
  letter-spacing: 1px;
  text-transform: uppercase;
}

/* Right Panel - Animated */
.right-panel {
  width: 320px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  padding: 10px;
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}

.trading-layout.agent-expanded .right-panel {
  width: 65%;
  flex: 0 0 65%;
}

.connection-status {
  background: #18181c;
  border-radius: 8px;
  padding: 12px 16px;
}

.status-section {
  margin-bottom: 10px;
}

.status-text {
  font-weight: 600;
  font-size: 14px;
}

.position-section h3,
.trading-controls h3,
.recent-trades h3 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
  color: #fff;
}

.profit {
  color: #18a058;
  font-weight: 600;
}

.loss {
  color: #d03050;
  font-weight: 600;
}

/* Mobile Responsive */
@media (max-width: 768px) {
  .trading-layout {
    flex-direction: column;
    overflow: auto;
  }

  .chart-section {
    flex: none;
    height: 50vh;
    min-height: 300px;
  }

  .trading-layout.agent-expanded .chart-section {
    flex: none;
    max-width: 100%;
    height: 30vh;
  }

  .right-panel {
    width: 100%;
    flex: none;
    padding: 10px;
  }

  .trading-layout.agent-expanded .right-panel {
    width: 100%;
    flex: none;
  }

  .toggle-panel-btn {
    display: none;
  }
}
</style>
