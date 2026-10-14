<template>
  <div class="validation-view">
    <TheTopBar />

    <!-- Subpage Selector -->
    <div class="subpage-selector">
      <n-select
        v-model:value="activeSubpage"
        :options="subpageOptions"
        size="small"
        style="width: 240px"
      />
    </div>

    <!-- Chart Explorer (pure chart inspection: any timeframe, endless scroll-back) -->
    <div v-if="activeSubpage === 'explorer'" class="regime-validation">
      <div class="regime-layout">
        <div class="chart-section">
          <div id="chart-wrapper">
            <div class="loading-overlay" v-if="isLoadingHistory">
              <span>Loading older history...</span>
            </div>
            <div ref="explorerChartContainer" class="chart-container"></div>
          </div>
        </div>
        <div class="controls-panel">
          <n-card title="Chart Explorer" :bordered="false">
            <n-space vertical size="large">
              <div>
                <label class="control-label">Symbol</label>
                <n-select v-model:value="symbol" :options="symbolOptions" size="small" @update:value="reloadExplorer" />
              </div>
              <div>
                <label class="control-label">Timeframe</label>
                <n-select v-model:value="timeframe" :options="timeframeOptions" size="small" @update:value="reloadExplorer" />
              </div>
              <p class="explorer-hint">
                Drag or scroll left to load more history — bars stream in continuously back to Jan 2020.
              </p>
              <p class="explorer-hint" v-if="historyExhausted">
                — Reached start of stored history —
              </p>
            </n-space>
          </n-card>
        </div>
      </div>
    </div>

    <!-- Strategy Validation (old backtest) -->
    <BacktestView v-else-if="activeSubpage === 'strategy'" :embedded="true" />

    <!-- Regime Validation -->
    <div v-else-if="activeSubpage === 'regime'" class="regime-validation">
      <div class="regime-layout">
        <!-- Chart with regime overlay -->
        <div class="chart-section">
          <div id="chart-wrapper">
            <div class="regime-legend" v-if="regimeData.length > 0">
              <span class="regime-item">
                <span class="regime-dot" style="background: rgba(76, 175, 80, 0.6)"></span>TREND
              </span>
              <span class="regime-item">
                <span class="regime-dot" style="background: rgba(33, 150, 243, 0.6)"></span>RANGE
              </span>
              <span class="regime-item">
                <span class="regime-dot" style="background: rgba(244, 67, 54, 0.6)"></span>VOLATILE
              </span>
            </div>
            <div class="loading-overlay" v-if="isLoading">
              <span>Computing regimes...</span>
            </div>
            <div ref="regimeChartContainer" class="chart-container"></div>
          </div>
        </div>

        <!-- Controls Panel -->
        <div class="controls-panel">
          <n-card title="Regime Validation" :bordered="false">
            <n-space vertical size="large">
              <div>
                <label class="control-label">Symbol</label>
                <n-select v-model:value="symbol" :options="symbolOptions" size="small" />
              </div>
              <div>
                <label class="control-label">Timeframe</label>
                <n-select v-model:value="timeframe" :options="timeframeOptions" size="small" />
              </div>
              <div>
                <label class="control-label">Bars to classify</label>
                <n-input-number v-model:value="limit" :min="200" :max="5000" :step="100" size="small" style="width: 100%" />
              </div>
              <n-button type="primary" block @click="runRegimeClassification" :loading="isLoading">
                {{ isLoading ? 'Computing...' : 'Classify Regimes' }}
              </n-button>
              <div v-if="summary" class="regime-summary">
                <h4>Classification Summary</h4>
                <div class="summary-row" v-if="summary.TRENDING">
                  <span class="regime-dot" style="background: rgba(76, 175, 80, 0.8)"></span>
                  <span>Trending: {{ summary.TRENDING }} bars</span>
                </div>
                <div class="summary-row" v-if="summary.RANGING">
                  <span class="regime-dot" style="background: rgba(33, 150, 243, 0.8)"></span>
                  <span>Ranging: {{ summary.RANGING }} bars</span>
                </div>
                <div class="summary-row" v-if="summary.VOLATILE">
                  <span class="regime-dot" style="background: rgba(244, 67, 54, 0.8)"></span>
                  <span>Volatile: {{ summary.VOLATILE }} bars</span>
                </div>
              </div>
            </n-space>
          </n-card>
        </div>
      </div>
    </div>

    <!-- Walk-Forward Validation -->
    <div v-else-if="activeSubpage === 'walkforward'" class="walkforward-validation">
      <div class="wf-layout">
        <!-- Results Area -->
        <div class="wf-results">
          <div class="loading-overlay" v-if="wfLoading">
            <span>Running walk-forward validation... This may take a few minutes.</span>
          </div>

          <!-- No results yet -->
          <div v-if="!wfReport && !wfLoading" class="wf-empty">
            <p>Configure parameters and click "Run Walk-Forward" to validate the regime classifier out-of-sample.</p>
          </div>

          <!-- Results -->
          <div v-if="wfReport" class="wf-report">
            <!-- Aggregate Summary -->
            <div class="wf-aggregate">
              <h3>Aggregate Results ({{ wfReport.total_folds }} folds{{ wfReport.degenerate_folds > 0 ? `, ${wfReport.degenerate_folds} degenerate` : '' }})</h3>
              <div class="wf-metrics-grid">
                <div class="wf-metric" :class="sharpeClass(wfReport.aggregate.sharpe_improvement)">
                  <span class="metric-label">Sharpe Improvement</span>
                  <span class="metric-value">{{ formatNum(wfReport.aggregate.sharpe_improvement, 4, true) }}</span>
                </div>
                <div class="wf-metric">
                  <span class="metric-label">Sharpe (Filtered)</span>
                  <span class="metric-value">{{ formatNum(wfReport.aggregate.sharpe_filtered, 4) }}</span>
                </div>
                <div class="wf-metric">
                  <span class="metric-label">Sharpe (Unfiltered)</span>
                  <span class="metric-value">{{ formatNum(wfReport.aggregate.sharpe_unfiltered, 4) }}</span>
                </div>
                <div class="wf-metric">
                  <span class="metric-label">Max DD (Filtered)</span>
                  <span class="metric-value">{{ formatPct(wfReport.aggregate.max_dd_filtered) }}</span>
                </div>
                <div class="wf-metric">
                  <span class="metric-label">Max DD (Unfiltered)</span>
                  <span class="metric-value">{{ formatPct(wfReport.aggregate.max_dd_unfiltered) }}</span>
                </div>
              </div>

              <!-- Regime Distribution -->
              <div class="wf-distribution" v-if="wfReport.aggregate.regime_distribution">
                <h4>Avg Regime Distribution</h4>
                <div class="dist-bars">
                  <div v-for="(pct, regime) in wfReport.aggregate.regime_distribution" :key="regime" class="dist-bar-row">
                    <span class="dist-label">{{ regime }}</span>
                    <div class="dist-bar-bg">
                      <div class="dist-bar-fill" :style="{ width: (pct * 100) + '%', background: regimeColor(regime) }"></div>
                    </div>
                    <span class="dist-pct">{{ (pct * 100).toFixed(1) }}%</span>
                  </div>
                </div>
              </div>

              <!-- Dwell Times -->
              <div class="wf-dwell" v-if="wfReport.aggregate.mean_dwell_time">
                <h4>Avg Dwell Times (bars)</h4>
                <div class="dwell-row" v-for="(val, regime) in wfReport.aggregate.mean_dwell_time" :key="regime">
                  <span class="regime-dot" :style="{ background: regimeColor(regime) }"></span>
                  <span>{{ regime }}: {{ val }} bars</span>
                </div>
              </div>
            </div>

            <!-- Per-Fold Table -->
            <div class="wf-folds-table">
              <h3>Per-Fold Results</h3>
              <table>
                <thead>
                  <tr>
                    <th>Fold</th>
                    <th>Test Period</th>
                    <th>Bars</th>
                    <th>Sharpe (F)</th>
                    <th>Sharpe (U)</th>
                    <th>Improv.</th>
                    <th>Max DD (F)</th>
                    <th>Win Rate</th>
                    <th>RF Acc.</th>
                    <th>Lambda</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="fold in wfReport.folds" :key="fold.fold_idx" :class="{ 'fold-warn': fold.warnings.length > 0 }">
                    <td>{{ fold.fold_idx + 1 }}</td>
                    <td>{{ fold.test_start }} - {{ fold.test_end }}</td>
                    <td>{{ fold.n_test_bars }}</td>
                    <td :class="sharpeClass(fold.sharpe_filtered)">{{ formatNum(fold.sharpe_filtered, 3) }}</td>
                    <td>{{ formatNum(fold.sharpe_unfiltered, 3) }}</td>
                    <td :class="sharpeClass(fold.sharpe_improvement)">{{ formatNum(fold.sharpe_improvement, 3, true) }}</td>
                    <td>{{ formatPct(fold.max_dd_filtered) }}</td>
                    <td>{{ formatPct(fold.win_rate_filtered) }}</td>
                    <td>{{ formatPct(fold.rf_accuracy) }}</td>
                    <td>{{ fold.best_lambda }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <!-- Controls Panel -->
        <div class="controls-panel">
          <n-card title="Walk-Forward Config" :bordered="false">
            <n-space vertical size="large">
              <div>
                <label class="control-label">Symbol</label>
                <n-select v-model:value="wfSymbol" :options="symbolOptions" size="small" />
              </div>
              <div>
                <label class="control-label">Timeframe</label>
                <n-select v-model:value="wfTimeframe" :options="wfTimeframeOptions" size="small" />
              </div>
              <div>
                <label class="control-label">Train window (months)</label>
                <n-input-number v-model:value="wfTrainMonths" :min="3" :max="24" :step="1" size="small" style="width: 100%" />
              </div>
              <div>
                <label class="control-label">Test window (months)</label>
                <n-input-number v-model:value="wfTestMonths" :min="1" :max="6" :step="1" size="small" style="width: 100%" />
              </div>
              <div>
                <label class="control-label">Embargo (months)</label>
                <n-input-number v-model:value="wfEmbargoMonths" :min="0" :max="3" :step="1" size="small" style="width: 100%" />
              </div>

              <!-- Data Info -->
              <div v-if="wfDataInfo" class="data-info">
                <div class="info-row">
                  <span>Data:</span>
                  <span>{{ wfDataInfo.data_start }} to {{ wfDataInfo.data_end }}</span>
                </div>
                <div class="info-row">
                  <span>Bars:</span>
                  <span>{{ wfDataInfo.total_bars?.toLocaleString() }}</span>
                </div>
                <div class="info-row">
                  <span>Months:</span>
                  <span>{{ wfDataInfo.months_available }}</span>
                </div>
                <div class="info-row">
                  <span>Est. folds:</span>
                  <span>{{ wfDataInfo.estimated_folds }}</span>
                </div>
              </div>

              <n-button type="primary" block @click="fetchDataInfo" :loading="wfInfoLoading" ghost>
                Check Data
              </n-button>

              <n-button type="primary" block @click="runWalkForward" :loading="wfLoading" :disabled="wfLoading">
                {{ wfLoading ? 'Running...' : 'Run Walk-Forward' }}
              </n-button>
            </n-space>
          </n-card>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, watch, nextTick } from 'vue';
import {
  NSelect, NCard, NSpace, NButton, NInputNumber,
} from 'naive-ui';
import * as LightweightCharts from 'lightweight-charts';
import TheTopBar from '@/components/TopBar/TheTopBar.vue';
import BacktestView from '@/views/BacktestView.vue';
import { API_CONFIG, getHeaders } from '@/config/api';
import { RegimeOverlayPrimitive } from '@/utils/chart/RegimeOverlayPrimitive';
import { AsianSessionBoxPrimitive } from '@/utils/chart/AsianSessionBoxPrimitive';

export default {
  name: 'ValidationView',

  components: {
    NSelect, NCard, NSpace, NButton, NInputNumber,
    TheTopBar, BacktestView,
  },

  props: {
    embedded: { type: Boolean, default: false },
  },

  data() {
    return {
      activeSubpage: 'explorer',
      subpageOptions: [
        { label: 'Chart Explorer', value: 'explorer' },
        { label: 'Regime Validation', value: 'regime' },
        { label: 'Walk-Forward Validation', value: 'walkforward' },
        { label: 'Strategy Validation', value: 'strategy' },
      ],
      // Regime validation state
      symbol: 'XAUUSD',
      symbolOptions: [
        { label: 'XAUUSD', value: 'XAUUSD' },
      ],
      timeframe: '1H',
      timeframeOptions: [
        { label: '5 min', value: '5min' },
        { label: '15 min', value: '15min' },
        { label: '30 min', value: '30min' },
        { label: '1 Hour', value: '1H' },
        { label: '4 Hours', value: '4H' },
        { label: '1 Day', value: '1D' },
      ],
      limit: 2000,
      isLoading: false,
      regimeData: [],
      summary: null,
      // Infinite history scroll-back state
      loadedBars: [],
      isLoadingHistory: false,
      historyExhausted: false,
      // Chart instances (non-reactive)
      chart: null,
      candlestickSeries: null,
      regimePrimitive: null,
      asianBoxPrimitive: null,
      visibleRangeHandler: null,
      // Chart Explorer instances (separate from regime chart)
      explorerChart: null,
      explorerSeries: null,
      explorerRangeHandler: null,

      // Walk-forward state
      wfSymbol: 'XAUUSD',
      wfTimeframe: '1H',
      wfTimeframeOptions: [
        { label: '15 min', value: '15min' },
        { label: '30 min', value: '30min' },
        { label: '1 Hour', value: '1H' },
        { label: '4 Hours', value: '4H' },
        { label: '1 Day', value: '1D' },
      ],
      wfTrainMonths: 6,
      wfTestMonths: 1,
      wfEmbargoMonths: 1,
      wfLoading: false,
      wfInfoLoading: false,
      wfReport: null,
      wfDataInfo: null,
    };
  },

  watch: {
    activeSubpage(val) {
      if (val === 'regime') {
        nextTick(() => this.initRegimeChart());
      } else if (val === 'explorer') {
        nextTick(() => this.initExplorerChart());
      }
    },
  },

  mounted() {
    if (this.activeSubpage === 'explorer') {
      this.initExplorerChart();
    } else if (this.activeSubpage === 'regime') {
      this.initRegimeChart();
    }
  },

  beforeUnmount() {
    this.destroyChart();
    this.destroyExplorerChart();
  },

  methods: {
    // ---- Formatting helpers ----
    formatNum(val, decimals = 4, showSign = false) {
      if (val === null || val === undefined) return '-';
      const prefix = showSign && val > 0 ? '+' : '';
      return prefix + val.toFixed(decimals);
    },

    formatPct(val) {
      if (val === null || val === undefined) return '-';
      return (val * 100).toFixed(2) + '%';
    },

    sharpeClass(val) {
      if (val === null || val === undefined) return '';
      return val > 0 ? 'positive' : val < 0 ? 'negative' : '';
    },

    regimeColor(regime) {
      const colors = {
        TRENDING: 'rgba(76, 175, 80, 0.8)',
        RANGING: 'rgba(33, 150, 243, 0.8)',
        VOLATILE: 'rgba(244, 67, 54, 0.8)',
      };
      return colors[regime] || '#888';
    },

    // ---- Chart Explorer (pure inspection, endless scroll, no regime overlay) ----
    initExplorerChart() {
      if (!this.$refs.explorerChartContainer || this.explorerChart) return;

      this.explorerChart = LightweightCharts.createChart(this.$refs.explorerChartContainer, {
        autoSize: true,
        layout: { textColor: '#d1d4dc', background: { type: 'solid', color: 'transparent' } },
        grid: {
          vertLines: { color: 'rgba(255,255,255,0.04)' },
          horzLines: { color: 'rgba(255,255,255,0.04)' },
        },
        timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 12, barSpacing: 6 },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      });

      this.explorerSeries = this.explorerChart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        priceFormat: { type: 'price', minMove: 0.01, precision: 2 },
      });

      this.explorerRangeHandler = (range) => {
        if (!range) return;
        if (range.from < 60 && !this.isLoadingHistory && !this.historyExhausted) {
          this.loadOlderExplorerBars();
        }
      };
      this.explorerChart.timeScale().subscribeVisibleLogicalRangeChange(this.explorerRangeHandler);

      this.loadExplorerData();
    },

    async loadExplorerData() {
      try {
        const url = `${API_CONFIG.BASE_URL}/api/chart-data/${this.symbol}/${this.timeframe}?limit=2000`;
        const res = await fetch(url, { headers: getHeaders() });
        const data = await res.json();
        const bars = (data.bars || []).map(b => ({
          time: b.time, open: parseFloat(b.open), high: parseFloat(b.high),
          low: parseFloat(b.low), close: parseFloat(b.close),
        }));
        this.loadedBars = bars;
        this.historyExhausted = false;
        if (this.explorerSeries) {
          this.explorerSeries.setData(bars);
          this.explorerChart.timeScale().fitContent();
        }
      } catch (err) {
        console.error('Explorer load failed:', err);
      }
    },

    async loadOlderExplorerBars() {
      if (!this.loadedBars.length) return;
      this.isLoadingHistory = true;
      try {
        const oldest = this.loadedBars[0].time;
        const url = `${API_CONFIG.BASE_URL}/api/chart-data/${this.symbol}/${this.timeframe}?limit=2000&before=${oldest}`;
        const res = await fetch(url, { headers: getHeaders() });
        const data = await res.json();
        const older = (data.bars || []).map(b => ({
          time: b.time, open: parseFloat(b.open), high: parseFloat(b.high),
          low: parseFloat(b.low), close: parseFloat(b.close),
        }));
        if (older.length === 0) { this.historyExhausted = true; return; }
        this.loadedBars = older.concat(this.loadedBars);
        if (this.explorerSeries) this.explorerSeries.setData(this.loadedBars);
      } catch (err) {
        console.error('Explorer history load failed:', err);
      } finally {
        this.isLoadingHistory = false;
      }
    },

    reloadExplorer() {
      if (this.activeSubpage === 'explorer' && this.explorerChart) {
        nextTick(() => this.loadExplorerData());
      }
    },

    destroyExplorerChart() {
      if (this.explorerChart && this.explorerRangeHandler) {
        try { this.explorerChart.timeScale().unsubscribeVisibleLogicalRangeChange(this.explorerRangeHandler); } catch (e) { /* */ }
        this.explorerRangeHandler = null;
      }
      if (this.explorerChart) {
        this.explorerChart.remove();
        this.explorerChart = null;
        this.explorerSeries = null;
      }
    },

    // ---- Regime Chart ----
    initRegimeChart() {
      if (!this.$refs.regimeChartContainer) return;
      if (this.chart) return;

      this.chart = LightweightCharts.createChart(this.$refs.regimeChartContainer, {
        autoSize: true,
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
        },
      });

      this.candlestickSeries = this.chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderVisible: false,
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
        priceFormat: { type: 'price', minMove: 0.01, precision: 2 },
      });

      // Infinite history: when the user scrolls near the left edge, page in older bars
      this.visibleRangeHandler = (range) => {
        if (!range) return;
        if (range.from < 60 && !this.isLoadingHistory && !this.historyExhausted) {
          this.loadOlderBars();
        }
      };
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.visibleRangeHandler);

      this.runRegimeClassification();
    },

    async loadOlderBars() {
      if (!this.loadedBars.length) return;
      this.isLoadingHistory = true;
      try {
        const oldest = this.loadedBars[0].time;
        const url = `${API_CONFIG.BASE_URL}/api/chart-data/${this.symbol}/${this.timeframe}` +
                    `?limit=2000&before=${oldest}`;
        const res = await fetch(url, { headers: getHeaders() });
        const data = await res.json();
        const older = (data.bars || []).map(b => ({
          time: b.time,
          open: parseFloat(b.open),
          high: parseFloat(b.high),
          low: parseFloat(b.low),
          close: parseFloat(b.close),
        }));
        if (older.length === 0) {
          this.historyExhausted = true;   // reached the start of stored history
          return;
        }
        this.loadedBars = older.concat(this.loadedBars);
        if (this.candlestickSeries) {
          this.candlestickSeries.setData(this.loadedBars);
          if (this.asianBoxPrimitive) {
            this.asianBoxPrimitive.setFromBars(this.loadedBars);
          }
        }
      } catch (err) {
        console.error('Failed to load older bars:', err);
      } finally {
        this.isLoadingHistory = false;
      }
    },

    destroyChart() {
      if (this.chart && this.visibleRangeHandler) {
        try { this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.visibleRangeHandler); } catch (e) { /* */ }
        this.visibleRangeHandler = null;
      }
      if (this.regimePrimitive && this.candlestickSeries) {
        try { this.candlestickSeries.detachPrimitive(this.regimePrimitive); } catch (e) { /* */ }
      }
      if (this.asianBoxPrimitive && this.candlestickSeries) {
        try { this.candlestickSeries.detachPrimitive(this.asianBoxPrimitive); } catch (e) { /* */ }
      }
      if (this.chart) {
        this.chart.remove();
        this.chart = null;
        this.candlestickSeries = null;
        this.regimePrimitive = null;
      }
    },

    handleResize() {
      if (this.chart && this.$refs.regimeChartContainer) {
        this.chart.applyOptions({
          width: this.$refs.regimeChartContainer.clientWidth,
          height: this.$refs.regimeChartContainer.clientHeight,
        });
      }
    },

    async runRegimeClassification() {
      this.isLoading = true;
      try {
        const chartUrl = `${API_CONFIG.BASE_URL}/api/chart-data/${this.symbol}/${this.timeframe}?limit=${this.limit}`;
        const chartRes = await fetch(chartUrl, { headers: getHeaders() });
        const chartData = await chartRes.json();

        if (!chartData.bars || chartData.bars.length === 0) {
          console.warn('No chart data returned');
          return;
        }

        const bars = chartData.bars.map(b => ({
          time: b.time,
          open: parseFloat(b.open),
          high: parseFloat(b.high),
          low: parseFloat(b.low),
          close: parseFloat(b.close),
        }));

        // reset infinite-scroll state for this (symbol, timeframe) load
        this.loadedBars = bars;
        this.historyExhausted = false;

        if (this.candlestickSeries) {
          this.candlestickSeries.setData(bars);
          this.chart.timeScale().fitContent();

          // Asian session boxes
          if (this.asianBoxPrimitive) {
            try { this.candlestickSeries.detachPrimitive(this.asianBoxPrimitive); } catch (e) { /* */ }
          }
          this.asianBoxPrimitive = new AsianSessionBoxPrimitive(this.chart, this.candlestickSeries);
          this.asianBoxPrimitive.setFromBars(bars);
          this.candlestickSeries.attachPrimitive(this.asianBoxPrimitive);
        }

        const regimeUrl = `${API_CONFIG.BASE_URL}/api/regime/${this.symbol}/${this.timeframe}?limit=${this.limit}`;
        const regimeRes = await fetch(regimeUrl, { headers: getHeaders() });

        if (!regimeRes.ok) {
          console.warn('Regime API error:', regimeRes.status);
          return;
        }

        const regimeData = await regimeRes.json();
        this.regimeData = regimeData.regimes || [];
        this.summary = regimeData.summary || null;
        this.renderRegimes(this.regimeData);
      } catch (err) {
        console.error('Regime classification failed:', err);
      } finally {
        this.isLoading = false;
      }
    },

    renderRegimes(regimes) {
      if (!this.chart || !this.candlestickSeries || !regimes || regimes.length === 0) return;

      if (this.regimePrimitive) {
        try { this.candlestickSeries.detachPrimitive(this.regimePrimitive); } catch (e) { /* */ }
        this.regimePrimitive = null;
      }

      this.regimePrimitive = new RegimeOverlayPrimitive(this.chart);
      this.regimePrimitive.setData(regimes);
      this.candlestickSeries.attachPrimitive(this.regimePrimitive);
    },

    // ---- Walk-Forward Validation ----
    async fetchDataInfo() {
      this.wfInfoLoading = true;
      try {
        const params = new URLSearchParams({
          train_months: this.wfTrainMonths,
          test_months: this.wfTestMonths,
          embargo_months: this.wfEmbargoMonths,
        });
        const url = `${API_CONFIG.BASE_URL}/api/validation/data-info/${this.wfSymbol}/${this.wfTimeframe}?${params}`;
        const res = await fetch(url, { headers: getHeaders() });
        if (res.ok) {
          this.wfDataInfo = await res.json();
        }
      } catch (err) {
        console.error('Failed to fetch data info:', err);
      } finally {
        this.wfInfoLoading = false;
      }
    },

    async runWalkForward() {
      this.wfLoading = true;
      this.wfReport = null;
      try {
        const params = new URLSearchParams({
          train_months: this.wfTrainMonths,
          test_months: this.wfTestMonths,
          embargo_months: this.wfEmbargoMonths,
          force_rerun: 'true',
        });
        const url = `${API_CONFIG.BASE_URL}/api/validation/walk-forward/${this.wfSymbol}/${this.wfTimeframe}?${params}`;
        const res = await fetch(url, { headers: getHeaders() });

        if (!res.ok) {
          const err = await res.json();
          console.error('Walk-forward failed:', err);
          return;
        }

        const data = await res.json();
        this.wfReport = data.report;
      } catch (err) {
        console.error('Walk-forward validation failed:', err);
      } finally {
        this.wfLoading = false;
      }
    },
  },
};
</script>

<style scoped>
.validation-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #101014;
  padding-top: 60px;
}

.subpage-selector {
  padding: 10px 16px;
  border-bottom: 1px solid #1f1f23;
}

/* ---- Regime Validation ---- */
.regime-validation {
  flex: 1;
  overflow: hidden;
}

.regime-layout {
  display: flex;
  height: 100%;
}

.chart-section {
  flex: 1;
  position: relative;
  min-height: 0;
}

#chart-wrapper {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
}

.explorer-hint {
  font-size: 12px;
  color: #888;
  line-height: 1.5;
  margin: 0;
}

.chart-container {
  width: 100%;
  height: 100%;
}

.controls-panel {
  width: 280px;
  border-left: 1px solid #1f1f23;
  overflow-y: auto;
  padding: 12px;
}

.control-label {
  display: block;
  font-size: 12px;
  color: #888;
  margin-bottom: 4px;
}

.regime-summary {
  padding: 10px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
}

.regime-summary h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: #d1d4dc;
}

.summary-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
  color: #aaa;
}

.regime-legend {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 10;
  display: flex;
  gap: 12px;
  padding: 4px 10px;
  background: rgba(16, 16, 20, 0.85);
  border-radius: 4px;
  font-size: 11px;
  color: #d1d4dc;
}

.regime-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.regime-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.loading-overlay {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 20;
  padding: 8px 16px;
  background: rgba(16, 16, 20, 0.9);
  border-radius: 6px;
  color: #d1d4dc;
  font-size: 13px;
}

/* ---- Walk-Forward Validation ---- */
.walkforward-validation {
  flex: 1;
  overflow: hidden;
}

.wf-layout {
  display: flex;
  height: 100%;
}

.wf-results {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  position: relative;
}

.wf-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
  font-size: 14px;
}

.wf-report {
  max-width: 1000px;
}

/* Aggregate */
.wf-aggregate {
  margin-bottom: 24px;
}

.wf-aggregate h3 {
  color: #d1d4dc;
  font-size: 16px;
  margin: 0 0 16px;
}

.wf-metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.wf-metric {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 8px;
  padding: 12px;
  border: 1px solid #1f1f23;
}

.metric-label {
  display: block;
  font-size: 11px;
  color: #888;
  margin-bottom: 4px;
}

.metric-value {
  font-size: 20px;
  font-weight: 600;
  color: #d1d4dc;
}

.positive .metric-value { color: #26a69a; }
.negative .metric-value { color: #ef5350; }

/* Distribution bars */
.wf-distribution, .wf-dwell {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 8px;
  padding: 12px;
  border: 1px solid #1f1f23;
  margin-bottom: 12px;
}

.wf-distribution h4, .wf-dwell h4 {
  color: #d1d4dc;
  font-size: 13px;
  margin: 0 0 10px;
}

.dist-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dist-bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dist-label {
  width: 80px;
  font-size: 11px;
  color: #aaa;
}

.dist-bar-bg {
  flex: 1;
  height: 16px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 3px;
  overflow: hidden;
}

.dist-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}

.dist-pct {
  width: 45px;
  text-align: right;
  font-size: 11px;
  color: #aaa;
}

.dwell-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 12px;
  color: #aaa;
}

/* Folds Table */
.wf-folds-table {
  margin-top: 20px;
}

.wf-folds-table h3 {
  color: #d1d4dc;
  font-size: 14px;
  margin: 0 0 12px;
}

.wf-folds-table table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.wf-folds-table th {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid #2c2c2c;
  color: #888;
  font-weight: 500;
}

.wf-folds-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #1f1f23;
  color: #d1d4dc;
}

.wf-folds-table tr:hover td {
  background: rgba(255, 255, 255, 0.02);
}

.wf-folds-table .positive { color: #26a69a; }
.wf-folds-table .negative { color: #ef5350; }

.fold-warn td {
  opacity: 0.5;
}

/* Data Info */
.data-info {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  padding: 10px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 12px;
  color: #aaa;
}

.info-row span:first-child {
  color: #666;
}
</style>
