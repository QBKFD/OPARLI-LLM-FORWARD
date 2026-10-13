<template>
  <div class="backtest-view">
    <TheTopBar v-if="!embedded" />

    <div class="backtest-layout">
      <!-- Chart Section - same structure as ChartArea.vue -->
      <div class="chart-section">
        <div id="chart-wrapper">
          <div ref="mainChartContainer" class="chart-container"></div>
        </div>
      </div>

      <!-- Backtest Panel -->
      <div class="backtest-panel">
        <n-card title="Strategy Validation" :bordered="false">
          <!-- Strategy Selection -->
          <div class="strategy-section">
            <h3>Strategy</h3>
            <n-select
              v-model:value="selectedStrategy"
              :options="strategyOptions"
              placeholder="Select a strategy"
            />
          </div>

          <n-divider />

          <!-- Backtest Parameters -->
          <div class="parameters-section">
            <h3>Parameters</h3>
            <n-space vertical>
              <n-input-number
                v-model:value="initialBalance"
                placeholder="Initial Balance"
                :min="100"
                :step="100"
                style="width: 100%"
              >
                <template #prefix>
                  Balance
                </template>
              </n-input-number>

              <n-input-number
                v-model:value="riskPerTrade"
                placeholder="Risk per trade (%)"
                :min="0.1"
                :max="100"
                :step="0.1"
                style="width: 100%"
              >
                <template #prefix>
                  Risk %
                </template>
              </n-input-number>

              <n-button
                type="primary"
                block
                size="large"
                @click="runBacktest"
                :loading="isRunning"
                :disabled="!selectedStrategy"
              >
                {{ isRunning ? 'Running...' : 'Run Validation' }}
              </n-button>
            </n-space>
          </div>

          <n-divider />

          <!-- Validation Results (Train vs Test) -->
          <div v-if="validationResults" class="validation-section">
            <h3>Out-of-Sample Validation</h3>
            <n-alert
              :type="validationResults.isValidated ? 'success' : 'warning'"
              :title="validationResults.isValidated ? 'Strategy Validated' : 'Not Validated'"
              style="margin-bottom: 12px"
            >
              {{ validationResults.isValidated
                ? 'Train and test periods both show positive expectancy'
                : 'Strategy may be overfit - test period underperformed' }}
            </n-alert>

            <n-grid cols="2" x-gap="8" y-gap="8">
              <n-grid-item>
                <div class="validation-card train">
                  <div class="period-label">TRAIN</div>
                  <div class="period-dates">Apr 2021 - Dec 2023</div>
                  <div class="metric">
                    <span class="label">Trades:</span>
                    <span class="value">{{ validationResults.train.totalTrades }}</span>
                  </div>
                  <div class="metric">
                    <span class="label">Win Rate:</span>
                    <span class="value">{{ validationResults.train.winRate }}%</span>
                  </div>
                  <div class="metric">
                    <span class="label">Expectancy:</span>
                    <span class="value" :class="validationResults.train.expectancy >= 0 ? 'positive' : 'negative'">
                      {{ validationResults.train.expectancy > 0 ? '+' : '' }}{{ validationResults.train.expectancy }}R
                    </span>
                  </div>
                </div>
              </n-grid-item>
              <n-grid-item>
                <div class="validation-card test">
                  <div class="period-label">TEST</div>
                  <div class="period-dates">Jan 2024 - Jun 2025</div>
                  <div class="metric">
                    <span class="label">Trades:</span>
                    <span class="value">{{ validationResults.test.totalTrades }}</span>
                  </div>
                  <div class="metric">
                    <span class="label">Win Rate:</span>
                    <span class="value">{{ validationResults.test.winRate }}%</span>
                  </div>
                  <div class="metric">
                    <span class="label">Expectancy:</span>
                    <span class="value" :class="validationResults.test.expectancy >= 0 ? 'positive' : 'negative'">
                      {{ validationResults.test.expectancy > 0 ? '+' : '' }}{{ validationResults.test.expectancy }}R
                    </span>
                  </div>
                </div>
              </n-grid-item>
            </n-grid>
          </div>

          <n-divider v-if="validationResults" />

          <!-- Results -->
          <div v-if="backtestResults" class="results-section">
            <h3>Overall Results</h3>

            <!-- Performance Metrics -->
            <n-grid cols="2" x-gap="8" y-gap="8">
              <n-grid-item>
                <n-statistic label="Total Trades" :value="backtestResults.totalTrades" />
              </n-grid-item>
              <n-grid-item>
                <n-statistic label="Win Rate" :value="backtestResults.winRate + '%'" />
              </n-grid-item>
              <n-grid-item>
                <n-statistic
                  label="Net Profit (R)"
                  :value="backtestResults.netProfit"
                  :value-style="{ color: backtestResults.netProfit >= 0 ? '#18a058' : '#d03050' }"
                />
              </n-grid-item>
              <n-grid-item>
                <n-statistic
                  label="Net Profit ($)"
                  :value="'$' + dollarProfit"
                  :value-style="{ color: dollarProfit >= 0 ? '#18a058' : '#d03050' }"
                />
              </n-grid-item>
              <n-grid-item>
                <n-statistic
                  label="Final Balance"
                  :value="'$' + finalBalance"
                  :value-style="{ color: finalBalance >= initialBalance ? '#18a058' : '#d03050' }"
                />
              </n-grid-item>
              <n-grid-item>
                <n-statistic
                  label="Profit Factor"
                  :value="backtestResults.profitFactor"
                />
              </n-grid-item>
            </n-grid>

            <n-divider />

            <!-- Equity Curve -->
            <div class="equity-curve">
              <h4>Equity Curve</h4>
              <div ref="equityChartContainer" style="height: 200px;"></div>
            </div>

            <n-divider />

            <!-- Trade List -->
            <div class="trade-list">
              <h4>Trades ({{ backtestResults.trades.length }}) <span class="click-hint">- click row to view on chart</span></h4>
              <n-data-table
                :columns="tradeColumns"
                :data="backtestResults.trades"
                :pagination="{ pageSize: 10 }"
                size="small"
                max-height="300px"
                :row-props="(row) => ({ style: 'cursor: pointer;', onClick: () => handleRowClick(row) })"
              />
            </div>

            <n-divider />

            <!-- Export Results -->
            <n-space>
              <n-button @click="exportResults('csv')" secondary>
                Export CSV
              </n-button>
              <n-button @click="exportResults('json')" secondary>
                Export JSON
              </n-button>
            </n-space>
          </div>

          <n-empty
            v-else
            description="Select 'Liquidity Sweeps' and run backtest to see results"
            style="margin-top: 20px"
          />
        </n-card>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, h } from 'vue';
import {
  NCard,
  NSpace,
  NButton,
  NDivider,
  NSelect,
  NInputNumber,
  NEmpty,
  NGrid,
  NGridItem,
  NStatistic,
  NDataTable,
  NAlert,
  NTag,
  useMessage,
} from 'naive-ui';
import TheTopBar from '@/components/TopBar/TheTopBar.vue';
import { createChart, CandlestickSeries, LineSeries } from 'lightweight-charts';
import { API_CONFIG, getHeaders } from '@/config/api';
import { useCurrentTimeframeStore } from '@/stores/currentTimeframeStore';
import { useAuthStore } from '@/stores/authStore';
import { useRouter } from 'vue-router';

// Custom Box Primitive for TradingView-style trade zones
class BoxPaneRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      if (!this._source || !this._source._boxData || this._source._boxData.length === 0) return;
      if (!this._source._chart || !this._source._series) return;

      const ctx = scope.context;
      const horizontalPixelRatio = scope.horizontalPixelRatio;
      const verticalPixelRatio = scope.verticalPixelRatio;
      const chart = this._source._chart;
      const series = this._source._series;

      for (const box of this._source._boxData) {
        // Convert time/price to pixel coordinates on each render (handles pan/zoom)
        const x1 = chart.timeScale().timeToCoordinate(box.time1);
        const x2 = chart.timeScale().timeToCoordinate(box.time2);
        const y1 = series.priceToCoordinate(box.price1);
        const y2 = series.priceToCoordinate(box.price2);

        if (x1 === null || x2 === null || y1 === null || y2 === null) continue;

        const scaledX1 = Math.round(x1 * horizontalPixelRatio);
        const scaledX2 = Math.round(x2 * horizontalPixelRatio);
        const scaledY1 = Math.round(y1 * verticalPixelRatio);
        const scaledY2 = Math.round(y2 * verticalPixelRatio);

        const minY = Math.min(scaledY1, scaledY2);
        const boxHeight = Math.abs(scaledY2 - scaledY1);
        const boxWidth = Math.abs(scaledX2 - scaledX1);

        // Draw filled rectangle
        ctx.fillStyle = box.fillColor;
        ctx.fillRect(scaledX1, minY, boxWidth, boxHeight);

        // Draw border
        ctx.strokeStyle = box.borderColor;
        ctx.lineWidth = 2 * horizontalPixelRatio;
        ctx.strokeRect(scaledX1, minY, boxWidth, boxHeight);

        // Draw label inside the box
        if (box.label && boxWidth > 50 * horizontalPixelRatio) {
          ctx.font = `bold ${11 * verticalPixelRatio}px sans-serif`;
          ctx.fillStyle = box.labelColor || '#ffffff';
          ctx.textBaseline = 'middle';
          ctx.textAlign = 'left';
          const labelX = scaledX1 + 6 * horizontalPixelRatio;
          const labelY = minY + boxHeight / 2;
          ctx.fillText(box.label, labelX, labelY);
        }
      }
    });
  }
}

class BoxPaneView {
  constructor(source) {
    this._source = source;
  }

  renderer() {
    return new BoxPaneRenderer(this._source);
  }

  zOrder() {
    return 'bottom'; // Draw behind candles
  }
}

class TradeBoxPrimitive {
  constructor() {
    this._boxData = [];
    this._paneViews = [new BoxPaneView(this)];
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  updateAllViews() {
    // Called when views need updating
  }

  paneViews() {
    return this._paneViews;
  }

  attached(params) {
    this._chart = params.chart;
    this._series = params.series;
    this._requestUpdate = params.requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  setBoxes(boxes) {
    // Store the raw box data (time/price values, not pixel coords)
    // The renderer will convert to pixels on each draw
    this._boxData = boxes;
    if (this._requestUpdate) {
      this._requestUpdate();
    }
  }

  clear() {
    this._boxData = [];
    if (this._requestUpdate) {
      this._requestUpdate();
    }
  }
}

export default {
  name: 'BacktestView',

  props: {
    embedded: { type: Boolean, default: false },
  },

  components: {
    TheTopBar,
    NCard,
    NSpace,
    NButton,
    NDivider,
    NSelect,
    NInputNumber,
    NEmpty,
    NGrid,
    NGridItem,
    NStatistic,
    NDataTable,
    NAlert,
    NTag,
  },

  setup() {
    const message = useMessage();
    const router = useRouter();
    const authStore = useAuthStore();

    const selectedStrategy = ref(null);
    const initialBalance = ref(10000);
    const riskPerTrade = ref(2);
    const isRunning = ref(false);
    const backtestResults = ref(null);
    const validationResults = ref(null);
    const trades = ref([]);

    // Chart refs
    const mainChartContainer = ref(null);
    const equityChartContainer = ref(null);
    const mainChart = ref(null);
    const equityChart = ref(null);
    const candleSeries = ref(null);

    // Time mapping for gap-free chart (index -> real timestamp)
    const indexToTimestamp = ref({});
    const timestampToIndex = ref({});

    const strategyOptions = ref([
      { label: 'Liquidity Sweeps (Validated)', value: 'liquidity_sweeps', requiresAuth: true },
      { label: 'Moving Average Crossover', value: 'ma_crossover', disabled: true, requiresAuth: false },
      { label: 'RSI Oversold/Overbought', value: 'rsi_strategy', disabled: true, requiresAuth: false },
      { label: 'Bollinger Bands Breakout', value: 'bb_breakout', disabled: true, requiresAuth: false },
    ]);

    // Use shared timeframe store (same as Live page)
    const currentTimeframeStore = useCurrentTimeframeStore();
    const rawCandlesticks = ref([]); // Store raw 15min data for resampling

    // Store raw equity data for recalculation
    const rawEquityData = ref(null);
    const equityLineSeries = ref(null);

    // Computed dollar amounts based on balance/risk
    const dollarProfit = computed(() => {
      if (!backtestResults.value) return 0;
      // Each R = risk% of initial balance
      const riskAmount = initialBalance.value * (riskPerTrade.value / 100);
      return Math.round(backtestResults.value.netProfit * riskAmount);
    });

    const finalBalance = computed(() => {
      return initialBalance.value + dollarProfit.value;
    });

    // Recalculate equity curve when balance/risk changes
    const recalculateEquityCurve = () => {
      if (!trades.value.length || !equityLineSeries.value) return;

      let balance = initialBalance.value;
      const riskPct = riskPerTrade.value / 100;
      const equityData = [{ time: 0, value: balance }];

      for (let i = 0; i < trades.value.length; i++) {
        const trade = trades.value[i];
        const riskAmount = balance * riskPct;
        const pnl = trade.pnl * riskAmount;
        balance += pnl;
        equityData.push({ time: i + 1, value: Math.round(balance) });
      }

      equityLineSeries.value.setData(equityData);
    };

    // Watch for balance/risk changes
    watch([initialBalance, riskPerTrade], () => {
      recalculateEquityCurve();
    });

    // Trade box primitive for TradingView-style zones
    const tradeBoxPrimitive = ref(null);
    const currentTradeLines = ref([]);

    // Clear existing trade visualization
    const clearTradeVisualization = () => {
      // Clear box primitive
      if (tradeBoxPrimitive.value) {
        tradeBoxPrimitive.value.clear();
      }
      // Clear any remaining price lines
      for (const line of currentTradeLines.value) {
        try {
          candleSeries.value.removePriceLine(line);
        } catch (e) {
          // Ignore errors
        }
      }
      currentTradeLines.value = [];
    };

    // Show trade zone visualization (TradingView style with colored boxes)
    const showTradeZone = (trade) => {
      if (!candleSeries.value || !mainChart.value) return;

      // Clear previous trade visualization
      clearTradeVisualization();

      const entryPrice = trade.entryPrice;
      const slPrice = trade.stopLoss;
      const tpPrice = trade.takeProfit;
      const exitPrice = trade.exitPrice;
      const isWin = trade.result === 'win';

      // Get bar indices for trade time range
      const entryTimestamp = Math.floor(new Date(trade.entryTime).getTime() / 1000);
      const exitTimestamp = Math.floor(new Date(trade.exitTime).getTime() / 1000);

      const entryIdx = findClosestIndex(entryTimestamp, timestampToIndex.value, indexToTimestamp.value);
      const exitIdx = findClosestIndex(exitTimestamp, timestampToIndex.value, indexToTimestamp.value);

      if (entryIdx === null || exitIdx === null) {
        console.warn('Could not find trade indices for boxes');
        return;
      }

      // Box starts exactly at entry and extends slightly past exit
      const boxStartIdx = entryIdx;
      const boxEndIdx = exitIdx + 3;

      // Initialize primitive if needed
      if (!tradeBoxPrimitive.value) {
        tradeBoxPrimitive.value = new TradeBoxPrimitive();
        candleSeries.value.attachPrimitive(tradeBoxPrimitive.value);
      }

      // Create boxes for risk (entry-SL) and reward (entry-TP) zones
      const boxes = [
        // Risk zone (Entry to Stop Loss) - Red
        {
          time1: boxStartIdx,
          time2: boxEndIdx,
          price1: entryPrice,
          price2: slPrice,
          fillColor: 'rgba(239, 68, 68, 0.25)', // Red with transparency
          borderColor: 'rgba(239, 68, 68, 0.6)',
          label: 'RISK -1R',
          labelColor: '#ef4444',
        },
        // Reward zone (Entry to Take Profit) - Green
        {
          time1: boxStartIdx,
          time2: boxEndIdx,
          price1: entryPrice,
          price2: tpPrice,
          fillColor: 'rgba(34, 197, 94, 0.25)', // Green with transparency
          borderColor: 'rgba(34, 197, 94, 0.6)',
          label: `REWARD +${trade.pnl}R`,
          labelColor: '#22c55e',
        },
      ];

      tradeBoxPrimitive.value.setBoxes(boxes);

      // Add entry line (blue - solid) for clear entry point
      const entryLine = candleSeries.value.createPriceLine({
        price: entryPrice,
        color: '#3b82f6',
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: 'ENTRY',
      });
      currentTradeLines.value.push(entryLine);

      // Add exit marker line (shows actual exit)
      const exitLine = candleSeries.value.createPriceLine({
        price: exitPrice,
        color: isWin ? '#22c55e' : '#ef4444',
        lineWidth: 3,
        lineStyle: 1, // Dotted
        axisLabelVisible: true,
        title: isWin ? '✓ WIN' : '✗ LOSS',
      });
      currentTradeLines.value.push(exitLine);

      console.log(`Showing trade zones: Entry=${entryPrice} SL=${slPrice} TP=${tpPrice} Exit=${exitPrice} [${boxStartIdx}-${boxEndIdx}]`);
    };

    // Navigate chart to a specific trade (using bar indices)
    const goToTrade = (trade) => {
      if (!mainChart.value || !candleSeries.value) {
        message.warning('Run backtest first to view trades on chart');
        return;
      }

      // Only test period trades (2024+) have chart data
      if (trade.entryTime < '2024-01-01') {
        message.warning('Train period trades are not shown on chart');
        return;
      }

      // Convert trade times to bar indices
      const entryTimestamp = Math.floor(new Date(trade.entryTime).getTime() / 1000);
      const exitTimestamp = Math.floor(new Date(trade.exitTime).getTime() / 1000);

      const entryIdx = findClosestIndex(entryTimestamp, timestampToIndex.value, indexToTimestamp.value);
      const exitIdx = findClosestIndex(exitTimestamp, timestampToIndex.value, indexToTimestamp.value);

      if (entryIdx === null || exitIdx === null) {
        message.warning('Trade time not found in chart data');
        return;
      }

      // Calculate viewing range (50 bars before entry, 20 bars after exit)
      const from = Math.max(0, entryIdx - 50);
      const to = Math.min(Object.keys(indexToTimestamp.value).length - 1, exitIdx + 20);

      try {
        mainChart.value.timeScale().setVisibleRange({ from, to });

        // Show the trade zone visualization
        showTradeZone(trade);

        const resultEmoji = trade.result === 'win' ? '✓' : '✗';
        message.success(`${resultEmoji} ${trade.type} trade: ${trade.entryTime.split('T')[0]} ${trade.entryTime.split('T')[1]?.slice(0,5) || ''}`);
      } catch (e) {
        // If setVisibleRange fails, try scrollToPosition
        console.warn('setVisibleRange failed, using fallback:', e);
        mainChart.value.timeScale().scrollToPosition(entryIdx, false);
        showTradeZone(trade);
        message.info(`Trade: ${trade.entryTime.split('T')[0]}`);
      }
    };

    // Row click handler for trade table
    const handleRowClick = (row) => {
      goToTrade(row);
    };

    const tradeColumns = [
      {
        title: 'Entry',
        key: 'entryTime',
        width: 100,
        render(row) {
          return row.entryTime.split('T')[0];
        },
      },
      {
        title: 'Type',
        key: 'type',
        width: 60,
        render(row) {
          const color = row.type === 'LONG' ? '#18a058' : '#d03050';
          return h(NTag, { size: 'small', style: { color } }, () => row.type);
        },
      },
      {
        title: 'Entry',
        key: 'entryPrice',
        width: 70,
      },
      {
        title: 'SL',
        key: 'stopLoss',
        width: 70,
      },
      {
        title: 'TP',
        key: 'takeProfit',
        width: 70,
      },
      {
        title: 'Result',
        key: 'result',
        width: 60,
        render(row) {
          const color = row.result === 'win' ? '#18a058' : '#d03050';
          return h('span', { style: { color, fontWeight: 600 } }, row.result.toUpperCase());
        },
      },
      {
        title: 'P&L',
        key: 'pnl',
        width: 60,
        render(row) {
          const color = row.pnl >= 0 ? '#18a058' : '#d03050';
          return h('span', { style: { color, fontWeight: 600 } }, `${row.pnl >= 0 ? '+' : ''}${row.pnl}R`);
        },
      },
      {
        title: '',
        key: 'action',
        width: 50,
        render(row) {
          // Only show view button for test period trades (2024+)
          if (row.entryTime < '2024-01-01') {
            return h('span', { style: { color: '#666', fontSize: '11px' } }, 'Train');
          }
          return h(NButton, {
            size: 'tiny',
            onClick: () => goToTrade(row),
          }, () => 'View');
        },
      },
    ];

    // Filter out invalid bars (same as ChartArea)
    const isValidBar = (bar) => {
      if (!bar || !bar.time) return false;
      const open = parseFloat(bar.open);
      const high = parseFloat(bar.high);
      const low = parseFloat(bar.low);
      const close = parseFloat(bar.close);
      if (isNaN(open) || isNaN(high) || isNaN(low) || isNaN(close)) return false;
      if (open <= 0 || high <= 0 || low <= 0 || close <= 0) return false;
      if (high < low) return false;
      if (open > high || open < low) return false;
      if (close > high || close < low) return false;
      return true;
    };

    // Find closest bar index for a given timestamp
    const findClosestIndex = (targetTs, tsToIdx, idxToTs) => {
      // Direct match
      if (tsToIdx[targetTs] !== undefined) {
        return tsToIdx[targetTs];
      }

      // Find closest timestamp
      const timestamps = Object.keys(idxToTs).map(Number);
      let closest = null;
      let minDiff = Infinity;

      for (const idx of timestamps) {
        const ts = idxToTs[idx];
        const diff = Math.abs(ts - targetTs);
        if (diff < minDiff) {
          minDiff = diff;
          closest = idx;
        }
      }

      return closest;
    };

    // Resample 15min bars to larger timeframe (1H, 4H, etc)
    // Note: Backtest data is 15-min resolution, so 1m/5m will show 15m data
    const resampleBars = (bars, targetMinutes) => {
      // Data is 15-min resolution - can't go smaller
      if (targetMinutes <= 15) return bars;

      const barsPerPeriod = targetMinutes / 15; // 4 for 1H, 16 for 4H, 96 for 1D
      const resampled = [];

      for (let i = 0; i < bars.length; i += barsPerPeriod) {
        const chunk = bars.slice(i, Math.min(i + barsPerPeriod, bars.length));
        if (chunk.length === 0) continue;

        resampled.push({
          time: chunk[0].time, // Use first bar's timestamp
          open: chunk[0].open,
          high: Math.max(...chunk.map(b => b.high)),
          low: Math.min(...chunk.map(b => b.low)),
          close: chunk[chunk.length - 1].close,
        });
      }

      return resampled;
    };

    // Get effective timeframe for backtest (minimum 15min due to data resolution)
    const getEffectiveTimeframe = () => {
      const storeValue = currentTimeframeStore.value;
      // Backtest data is 15-min, so use 15 as minimum
      return Math.max(15, storeValue);
    };

    // Watch for timeframe changes from the TopBar dropdown
    watch(() => currentTimeframeStore.value, (newValue) => {
      if (rawCandlesticks.value.length === 0) {
        return; // No data to resample yet
      }

      const effectiveTf = Math.max(15, newValue);
      const resampled = resampleBars(rawCandlesticks.value, effectiveTf);
      renderChartWithData(resampled, trades.value);

      const tfLabel = effectiveTf >= 60 ? (effectiveTf / 60) + 'H' : effectiveTf + 'm';
      message.info(`Backtest chart: ${tfLabel}${newValue < 15 ? ' (15m minimum)' : ''}`);
    });

    const initMainChart = () => {
      if (!mainChartContainer.value) {
        console.error('Chart container ref not available');
        return false;
      }

      // Remove existing chart if any
      if (mainChart.value) {
        try {
          mainChart.value.remove();
        } catch (e) {
          console.warn('Error removing old chart:', e);
        }
        mainChart.value = null;
        candleSeries.value = null;
      }

      // Use container dimensions exactly like ChartArea.vue
      const width = mainChartContainer.value.clientWidth;
      const height = mainChartContainer.value.clientHeight || 600;

      console.log(`Initializing chart with dimensions: ${width}x${height}`);

      try {
        mainChart.value = createChart(mainChartContainer.value, {
          width: width,
          height: height,
          layout: {
            textColor: '#d1d4dc',
            background: { type: 'solid', color: 'transparent' },
          },
          grid: {
            vertLines: { color: 'transparent' },
            horzLines: { color: 'transparent' },
          },
          timeScale: {
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 12,
            barSpacing: 6,
            minBarSpacing: 3,
            fixLeftEdge: false,
            fixRightEdge: false,
            lockVisibleTimeRangeOnResize: true,
            rightBarStaysOnScroll: true,
            borderVisible: false,
            visible: true,
          },
          localization: {
            // Time formatter that converts bar index to real date/time
            timeFormatter: (barIndex) => {
              const realTimestamp = indexToTimestamp.value[barIndex];
              if (realTimestamp) {
                const date = new Date(realTimestamp * 1000);
                return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
              }
              return '';
            },
            dateFormatter: (barIndex) => {
              const realTimestamp = indexToTimestamp.value[barIndex];
              if (realTimestamp) {
                const date = new Date(realTimestamp * 1000);
                return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
              }
              return '';
            },
          },
        });

        candleSeries.value = mainChart.value.addSeries(CandlestickSeries, {
          upColor: '#26a69a',
          downColor: '#ef5350',
          borderVisible: false,
          wickUpColor: '#26a69a',
          wickDownColor: '#ef5350',
          priceScaleId: 'right',
          priceFormat: {
            type: 'price',
            minMove: 0.01,
            precision: 2,
          },
        });

        // Configure the right price scale
        mainChart.value.priceScale('right').applyOptions({
          visible: true,
          autoScale: true,
          scaleMargins: { top: 0.1, bottom: 0.1 },
        });

        console.log('Chart initialized successfully');
        return true;
      } catch (error) {
        console.error('Error creating chart:', error);
        return false;
      }
    };

    // Render chart with candlestick bars (can be raw or resampled)
    const renderChartWithData = (candlestickBars, tradeList) => {
      console.log('renderChartWithData called with:', candlestickBars?.length, 'bars,', tradeList?.length, 'trades');

      // Initialize chart if not already done
      if (!mainChart.value || !candleSeries.value) {
        console.log('Chart not initialized, initializing now...');
        const success = initMainChart();
        if (!success) {
          console.error('Failed to initialize chart');
          return;
        }
      }

      if (!mainChart.value || !candleSeries.value) {
        console.error('Chart or candleSeries is null after init');
        return;
      }

      // Resize chart to match container
      if (mainChartContainer.value) {
        const width = mainChartContainer.value.clientWidth;
        const height = mainChartContainer.value.clientHeight;
        mainChart.value.applyOptions({ width, height });
      }

      // Set candlestick data with SEQUENTIAL INDEXING (gap-free)
      if (candlestickBars && candlestickBars.length > 0) {
        // Create sequential indexed bars (removes all gaps)
        const indexedBars = [];
        const idxToTs = {};
        const tsToIdx = {};

        for (let i = 0; i < candlestickBars.length; i++) {
          const bar = candlestickBars[i];
          const realTime = bar.time;

          indexedBars.push({
            time: i,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          });

          idxToTs[i] = realTime;
          tsToIdx[realTime] = i;
        }

        indexToTimestamp.value = idxToTs;
        timestampToIndex.value = tsToIdx;

        console.log(`Setting ${indexedBars.length} candles with sequential indexing`);
        candleSeries.value.setData(indexedBars);
      }

      // Filter trades to test period only (2024+)
      const testTrades = tradeList.filter(t => t.entryTime >= '2024-01-01');

      // Add trade markers
      const markers = [];
      for (const trade of testTrades) {
        const entryTimestamp = Math.floor(new Date(trade.entryTime).getTime() / 1000);
        const entryIdx = findClosestIndex(entryTimestamp, timestampToIndex.value, indexToTimestamp.value);

        if (entryIdx !== null) {
          markers.push({
            time: entryIdx,
            position: trade.type === 'LONG' ? 'belowBar' : 'aboveBar',
            color: trade.type === 'LONG' ? '#26a69a' : '#ef5350',
            shape: trade.type === 'LONG' ? 'arrowUp' : 'arrowDown',
            text: trade.type === 'LONG' ? 'BUY' : 'SELL',
          });
        }

        const exitTimestamp = Math.floor(new Date(trade.exitTime).getTime() / 1000);
        const exitIdx = findClosestIndex(exitTimestamp, timestampToIndex.value, indexToTimestamp.value);

        if (exitIdx !== null) {
          markers.push({
            time: exitIdx,
            position: trade.type === 'LONG' ? 'aboveBar' : 'belowBar',
            color: trade.result === 'win' ? '#18a058' : '#d03050',
            shape: 'circle',
            text: trade.result === 'win' ? 'WIN' : 'LOSS',
          });
        }
      }

      markers.sort((a, b) => a.time - b.time);
      candleSeries.value.setMarkers(markers);
      mainChart.value.timeScale().fitContent();

      console.log(`Chart rendered: ${candlestickBars?.length} candles, ${testTrades.length} trades, ${markers.length} markers`);
    };

    // Main render function - stores raw data and renders with current timeframe
    const renderMainChart = (chartData, tradeList) => {
      if (!chartData?.candlesticks?.length) {
        console.warn('No candlestick data provided');
        return;
      }

      // Filter and store raw 15min bars for later resampling
      const validBars = chartData.candlesticks.filter(isValidBar);
      rawCandlesticks.value = validBars;

      // Resample to current timeframe from store (minimum 15m)
      const barsToRender = resampleBars(validBars, getEffectiveTimeframe());

      // Render chart
      renderChartWithData(barsToRender, tradeList);
    };

    const renderEquityCurve = () => {
      if (!equityChartContainer.value || !trades.value.length) return;

      if (equityChart.value) {
        equityChart.value.remove();
      }

      equityChart.value = createChart(equityChartContainer.value, {
        width: equityChartContainer.value.clientWidth,
        height: 200,
        layout: {
          background: { color: '#101014' },
          textColor: '#d1d4dc',
        },
        grid: {
          vertLines: { color: '#2c2c2c' },
          horzLines: { color: '#2c2c2c' },
        },
        rightPriceScale: {
          borderColor: '#2c2c2c',
        },
        timeScale: {
          borderColor: '#2c2c2c',
          visible: false, // Hide x-axis labels (just trade numbers)
        },
      });

      equityLineSeries.value = equityChart.value.addSeries(LineSeries, {
        color: '#2563eb',
        lineWidth: 2,
        priceFormat: {
          type: 'custom',
          formatter: (price) => '$' + Math.round(price).toLocaleString(),
        },
      });

      // Calculate equity curve with current balance/risk
      let balance = initialBalance.value;
      const riskPct = riskPerTrade.value / 100;
      const equityData = [{ time: 0, value: balance }];

      for (let i = 0; i < trades.value.length; i++) {
        const trade = trades.value[i];
        const riskAmount = balance * riskPct;
        const pnl = trade.pnl * riskAmount;
        balance += pnl;
        equityData.push({ time: i + 1, value: Math.round(balance) });
      }

      equityLineSeries.value.setData(equityData);
      equityChart.value.timeScale().fitContent();
    };

    const runBacktest = async () => {
      if (selectedStrategy.value !== 'liquidity_sweeps') {
        message.warning('Only Liquidity Sweeps strategy is currently available');
        return;
      }

      // Check if selected strategy requires authentication
      const strategy = strategyOptions.value.find(s => s.value === selectedStrategy.value);
      if (strategy?.requiresAuth) {
        // Verify user is authenticated
        const isValid = await authStore.verifyToken();
        if (!isValid) {
          message.warning('This strategy requires authentication');
          router.push('/login');
          return;
        }
      }

      isRunning.value = true;
      try {
        const response = await fetch(`${API_CONFIG.BASE_URL}/api/backtest/sweep`, {
          method: 'POST',
          headers: getHeaders(),
        });

        const data = await response.json();
        console.log('Backtest API response:', data);

        if (response.ok) {
          // Store results
          backtestResults.value = data.summary;
          backtestResults.value.trades = data.trades;
          validationResults.value = data.validation;
          trades.value = data.trades;

          console.log('Chart data:', data.chartData);
          console.log('Candlesticks count:', data.chartData?.candlesticks?.length);

          message.success(`Backtest complete: ${data.trades.length} trades`);

          // Render charts with a delay to ensure DOM is ready
          await nextTick();
          setTimeout(() => {
            console.log('About to render main chart...');
            renderMainChart(data.chartData, data.trades);
            renderEquityCurve();
          }, 200);
        } else {
          console.error('Backtest failed:', data);
          message.error(data.detail || 'Backtest failed');
        }
      } catch (error) {
        message.error('Failed to run backtest: ' + error.message);
      } finally {
        isRunning.value = false;
      }
    };

    const exportResults = (format) => {
      if (!backtestResults.value) return;

      const data = {
        summary: backtestResults.value,
        validation: validationResults.value,
        trades: trades.value,
      };

      if (format === 'json') {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'sweep_backtest_results.json';
        a.click();
        message.success('Results exported as JSON');
      } else if (format === 'csv') {
        const headers = ['entryTime', 'exitTime', 'type', 'entryPrice', 'exitPrice', 'stopLoss', 'takeProfit', 'result', 'pnl'];
        const csvRows = [headers.join(',')];
        for (const trade of trades.value) {
          const row = headers.map(h => trade[h] ?? '');
          csvRows.push(row.join(','));
        }
        const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'sweep_backtest_trades.csv';
        a.click();
        message.success('Trades exported as CSV');
      }
    };

    onMounted(async () => {
      // Wait for DOM to be fully rendered before initializing chart
      await nextTick();

      // Small delay to ensure layout is complete
      setTimeout(() => {
        initMainChart();
        console.log('Chart container dimensions:',
          mainChartContainer.value?.clientWidth,
          mainChartContainer.value?.clientHeight);
      }, 50);

      // Handle resize - same approach as ChartArea.vue
      const handleResize = () => {
        if (mainChart.value && mainChartContainer.value) {
          mainChart.value.applyOptions({
            width: mainChartContainer.value.clientWidth,
            height: mainChartContainer.value.clientHeight
          });
        }
        if (equityChart.value && equityChartContainer.value) {
          equityChart.value.applyOptions({
            width: equityChartContainer.value.clientWidth,
          });
        }
      };

      window.addEventListener('resize', handleResize);

      // Handle resize after layout settles
      setTimeout(handleResize, 100);
      setTimeout(handleResize, 300);
    });

    onBeforeUnmount(() => {
      if (mainChart.value) {
        mainChart.value.remove();
      }
      if (equityChart.value) {
        equityChart.value.remove();
      }
    });

    return {
      selectedStrategy,
      strategyOptions,
      initialBalance,
      riskPerTrade,
      isRunning,
      backtestResults,
      validationResults,
      tradeColumns,
      mainChartContainer,
      equityChartContainer,
      runBacktest,
      exportResults,
      dollarProfit,
      finalBalance,
      handleRowClick,
    };
  },
};
</script>

<style scoped>
.backtest-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #101014;
  overflow: hidden;
}

.backtest-layout {
  display: flex;
  flex: 1;
  gap: 10px;
  padding: 10px;
  overflow: hidden;
  min-height: 0; /* Important for flex children to shrink */
}

.chart-section {
  flex: 1;
  min-width: 0;
  height: 100%;
  background: #101014;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* Exact same structure as ChartArea.vue */
#chart-wrapper {
  position: relative;
  width: 100%;
  flex: 1;
  min-height: 0;
}

.chart-container {
  width: 100%;
  height: 100%;
}

.backtest-panel {
  width: 400px;
  overflow-y: auto;
}

.strategy-section h3,
.parameters-section h3,
.results-section h3,
.validation-section h3 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
  color: #fff;
}

.results-section h4 {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  color: #d1d4dc;
}

.equity-curve {
  margin-top: 10px;
}

/* Validation Cards */
.validation-card {
  background: #1a1a1e;
  border-radius: 8px;
  padding: 12px;
  border: 1px solid #2c2c2c;
}

.validation-card.train {
  border-left: 3px solid #2563eb;
}

.validation-card.test {
  border-left: 3px solid #18a058;
}

.period-label {
  font-size: 11px;
  font-weight: 700;
  color: #888;
  letter-spacing: 1px;
  margin-bottom: 2px;
}

.period-dates {
  font-size: 10px;
  color: #666;
  margin-bottom: 8px;
}

.metric {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
  font-size: 12px;
}

.metric .label {
  color: #888;
}

.metric .value {
  font-weight: 600;
  color: #d1d4dc;
}

.metric .value.positive {
  color: #18a058;
}

.metric .value.negative {
  color: #d03050;
}

.click-hint {
  font-size: 11px;
  font-weight: 400;
  color: #666;
}
</style>
