<template>
  <div id="chart-wrapper">
    <div class="regime-legend" v-if="showRegimes && regimeData.length > 0">
      <span class="regime-item" @click="toggleRegimes" title="Click to hide">
        <span class="regime-dot" style="background: rgba(76, 175, 80, 0.6)"></span>TREND
      </span>
      <span class="regime-item">
        <span class="regime-dot" style="background: rgba(33, 150, 243, 0.6)"></span>RANGE
      </span>
      <span class="regime-item">
        <span class="regime-dot" style="background: rgba(244, 67, 54, 0.6)"></span>VOLATILE
      </span>
    </div>
    <div class="regime-legend regime-off" v-else-if="!showRegimes" @click="toggleRegimes">
      <span class="regime-item">Regimes OFF</span>
    </div>
    <div ref="chartContainer" id="lightweight-chart" class="chart-container" />
  </div>
</template>

<script>
import { useCandlesticksStore } from "@/stores/candlesticksStore";
import { useCurrentMarketStore } from "@/stores/currentMarketStore";
import { useCurrentTimeframeStore } from "@/stores/currentTimeframeStore";
import * as LightweightCharts from 'lightweight-charts';
import { API_CONFIG, getHeaders } from '@/config/api';
import { RegimeOverlayPrimitive } from '@/utils/chart/RegimeOverlayPrimitive';

export default {
  name: "ChartArea",

  data() {
    return {
      candlesticksStore: useCandlesticksStore(),
      currentMarketStore: useCurrentMarketStore(),
      currentTimeframeStore: useCurrentTimeframeStore(),
      // Vanilla chart instances (stored outside Vue reactivity)
      chart: null,
      candlestickSeries: null,
      regimePrimitive: null,
      lastBarTime: null,
      regimeData: [],
      showRegimes: true,
    };
  },

  watch: {
    // Load chart data when candlesticks store updates
    "candlesticksStore.data": {
      handler(newData) {
        this.loadChartData(newData);
      },
      deep: true,
    },

    // Fetch historical data when symbol changes
    "currentMarketStore.symbol_id": {
      handler() {
        this.fetchCandlesticks();
      },
      immediate: true,  // Fetch on mount
    },

    // Fetch when timeframe changes
    "currentTimeframeStore.value": {
      handler() {
        this.fetchCandlesticks();
      },
    },
  },

  mounted() {
    this.initChart();
  },

  beforeUnmount() {
    if (this.regimePrimitive && this.candlestickSeries) {
      try { this.candlestickSeries.detachPrimitive(this.regimePrimitive); } catch (e) { /* ignore */ }
    }
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
    }
  },

  methods: {
    initChart() {
      if (!this.$refs.chartContainer) return;

      // Create chart with vanilla JS (no Vue reactivity)
      this.chart = LightweightCharts.createChart(this.$refs.chartContainer, {
        width: this.$refs.chartContainer.clientWidth,
        height: this.$refs.chartContainer.clientHeight || 600,
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
          timeFormatter: (timestamp) => {
            const date = new Date(timestamp * 1000);
            return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
          },
          dateFormatter: (timestamp) => {
            const date = new Date(timestamp * 1000);
            return date.toLocaleDateString('en-GB');
          },
        },
      });

      this.candlestickSeries = this.chart.addSeries(LightweightCharts.CandlestickSeries, {
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

      // Configure the right price scale for auto-scaling
      this.chart.priceScale('right').applyOptions({
        visible: true,
        autoScale: true,
        mode: 0, // Normal mode
        scaleMargins: {
          top: 0.1,
          bottom: 0.1,
        },
      });

      // Handle window resize
      window.addEventListener('resize', this.handleResize);
    },

    handleResize() {
      if (this.chart && this.$refs.chartContainer) {
        this.chart.applyOptions({
          width: this.$refs.chartContainer.clientWidth,
          height: this.$refs.chartContainer.clientHeight
        });
      }
    },

    loadChartData(data) {
      if (!this.candlestickSeries || !data || data.length === 0) {
        console.warn('⚠️ No historical data available, will start with live data only');
        this.lastBarTime = null; // Reset so we accept any live data
        return;
      }

      // Convert to plain objects (strip Vue reactivity)
      const plainBars = JSON.parse(JSON.stringify(data));

      // Process bars to add whitespace for gaps (TradingView style)
      const processedBars = this.processGapsInBars(plainBars);

      // Log data age for debugging
      const now = Math.floor(Date.now() / 1000);
      const lastBarTime = plainBars[plainBars.length - 1].time;
      const ageInMinutes = (now - lastBarTime) / 60;

      console.log(`📊 Loading ${processedBars.length} bars to chart (${plainBars.length} original, last bar ${ageInMinutes.toFixed(1)} minutes ago)`);

      this.candlestickSeries.setData(processedBars);

      // Auto-fit the chart to show all data properly scaled
      this.chart.timeScale().fitContent();

      // Set lastBarTime from the last bar we actually loaded
      this.lastBarTime = lastBarTime;
      console.log('✅ Loaded', processedBars.length, 'bars, last time:', this.lastBarTime, '=', new Date(this.lastBarTime * 1000).toISOString());
    },

    processGapsInBars(bars) {
      // Just filter out invalid bars
      // The vertical lines are caused by how the data looks, not the chart
      return bars.filter(bar => this.isValidBar(bar));
    },

    isValidBar(bar) {
      // Check for null/undefined/0 values
      if (!bar || !bar.time) return false;

      // Check if OHLC values are valid numbers and not 0
      const open = parseFloat(bar.open);
      const high = parseFloat(bar.high);
      const low = parseFloat(bar.low);
      const close = parseFloat(bar.close);

      // All values must be positive numbers
      if (isNaN(open) || isNaN(high) || isNaN(low) || isNaN(close)) return false;
      if (open <= 0 || high <= 0 || low <= 0 || close <= 0) return false;

      // High must be >= Low
      if (high < low) return false;

      // Open and Close must be between High and Low
      if (open > high || open < low) return false;
      if (close > high || close < low) return false;

      return true;
    },

    async fetchCandlesticks() {
      if (this.currentMarketStore.symbol_id === null) {
        console.warn('ChartArea: symbol_id is null, skipping fetch');
        return;
      }

      const symbolID = this.currentMarketStore.symbol_id;
      const timeframe = this.currentTimeframeStore.value;

      // Fetch more bars for better historical view
      // 1min bars: 500 bars = ~8 hours of recent data
      // This ensures we get RECENT data, not old data from months ago
      const limit = timeframe === '1min' ? 500 : 2000;

      console.log('ChartArea: Fetching', limit, 'candlesticks for', symbolID, 'timeframe:', timeframe);
      await this.candlesticksStore.fetch(symbolID, timeframe, null, null, limit);

      // Fetch regime overlay
      if (this.showRegimes) {
        await this.fetchRegimeData(symbolID, timeframe, limit);
      }
    },

    async fetchRegimeData(symbol, timeframe, limit) {
      try {
        const timeframeMap = {
          1: '1min', 5: '5min', 15: '15min', 30: '30min',
          60: '1H', 240: '4H', 1440: '1D'
        };
        const apiTimeframe = timeframeMap[timeframe] || '1H';

        const url = `${API_CONFIG.BASE_URL}/api/regime/${symbol}/${apiTimeframe}?limit=${limit}`;
        console.log('Fetching regime data from:', url);

        const response = await fetch(url, { headers: getHeaders() });

        if (!response.ok) {
          console.warn('Regime API returned', response.status);
          return;
        }

        const result = await response.json();

        if (!result.regimes || result.regimes.length === 0) {
          console.warn('No regime data returned');
          return;
        }

        console.log(`Regime data: ${result.count} bars, summary:`, result.summary);
        this.regimeData = result.regimes;
        this.renderRegimeOverlay(result.regimes);
      } catch (err) {
        console.warn('Failed to fetch regime data:', err);
      }
    },

    renderRegimeOverlay(regimes) {
      if (!this.chart || !this.candlestickSeries || !regimes || regimes.length === 0) return;

      // Remove existing primitive
      if (this.regimePrimitive) {
        try {
          this.candlestickSeries.detachPrimitive(this.regimePrimitive);
        } catch (e) { /* ignore */ }
        this.regimePrimitive = null;
      }

      // Create and attach regime primitive (draws full-height colored rectangles)
      this.regimePrimitive = new RegimeOverlayPrimitive(this.chart);
      this.regimePrimitive.setData(regimes);
      this.candlestickSeries.attachPrimitive(this.regimePrimitive);

      const blockCount = this.regimePrimitive.getBlocks().length;
      console.log(`Regime rectangles rendered: ${blockCount} blocks from ${regimes.length} bars`);
    },

    toggleRegimes() {
      this.showRegimes = !this.showRegimes;
      if (!this.showRegimes && this.regimePrimitive) {
        try {
          this.candlestickSeries.detachPrimitive(this.regimePrimitive);
        } catch (e) { /* ignore */ }
        this.regimePrimitive = null;
      } else if (this.showRegimes && this.regimeData.length > 0) {
        this.renderRegimeOverlay(this.regimeData);
      }
    },

    // Method called from LiveTradingView to update chart
    updateCandlestick(bar) {
      if (!this.candlestickSeries) {
        console.error('❌ No candlestick series');
        return;
      }

      // Extract clean primitives
      const timeNum = parseInt(String(bar.time), 10);
      const openNum = parseFloat(String(bar.open));
      const highNum = parseFloat(String(bar.high));
      const lowNum = parseFloat(String(bar.low));
      const closeNum = parseFloat(String(bar.close));

      const plainBar = {
        time: timeNum,
        open: openNum,
        high: highNum,
        low: lowNum,
        close: closeNum
      };

      console.log('📊 Update attempt - incoming:', timeNum, 'last:', this.lastBarTime);

      // Only update if this bar is >= the last bar time
      if (this.lastBarTime === null || timeNum >= this.lastBarTime) {
        try {
          this.candlestickSeries.update(plainBar);
          this.lastBarTime = timeNum;
          console.log('✅ Chart updated:', closeNum, 'Time:', new Date(timeNum * 1000).toLocaleTimeString());
        } catch (e) {
          console.error('❌ Update failed:', e.message);
        }
      } else {
        console.log('⚠️ Skipping old bar - incoming', timeNum, 'is older than last', this.lastBarTime);
      }
    },

    // Expose series for LiveTradingView
    getSeries() {
      return new Map([['ohlc', { series: this.candlestickSeries }]]);
    },
  },
};
</script>

<style scoped>
#chart-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
}

.chart-container {
  width: 100%;
  height: 100%;
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
  cursor: pointer;
  user-select: none;
}

.regime-legend.regime-off {
  opacity: 0.5;
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
</style>
