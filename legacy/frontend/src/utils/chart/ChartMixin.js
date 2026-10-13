import { ChartManager } from './ChartManager.js';
import { IndicatorManager } from './IndicatorManager.js';
import { markRaw } from 'vue';

export const ChartMixin = {
  data() {
    return {
      chartManager: null,
      indicatorManager: null,
      chartInitialized: false,
      chartError: null,
      _rawOhlcSeries: null, // Store raw series reference outside Vue reactivity
      _lastBarTime: null, // Track last bar timestamp to prevent old data updates
    };
  },

  mounted() {
    this.initializeChart();
  },

  beforeUnmount() {
    this.cleanupChart();
  },

  methods: {
    initializeChart() {
      try {
        this.chartManager = new ChartManager(this.getChartOptions());
        this.indicatorManager = new IndicatorManager(this.chartManager);

        if (this.$refs.chartContainer) {
          this.chartInitialized = this.chartManager.init(
            this.$refs.chartContainer,
          );
        }
      } catch (error) {
        console.error('Failed to initialize chart:', error);
        this.chartError = error;
      }
    },

    getChartOptions() {
      return this.chartOptions || {};
    },

    getSeries() {
      return this.chartManager.series;
    },

    addCandlestickData(data, seriesOptions = {}) {
      const defaultOptions = {
        priceFormat: {
          type: 'price',
          minMove: 0.00001,
        },
        ...seriesOptions,
      };

      // Filter out future bars (timezone bug workaround)
      const now = Math.floor(Date.now() / 1000);
      const validData = data.filter(bar => bar.time <= now + 120); // Allow 2 min buffer

      console.log(`📊 Filtered ${data.length} bars to ${validData.length} valid bars (removed future bars)`);

      const seriesInfo = this.chartManager.addSeries('ohlc', 'candlestick', validData, defaultOptions);

      // Store raw series reference and track last bar time
      if (seriesInfo && seriesInfo.series) {
        this._rawOhlcSeries = markRaw(seriesInfo.series);

        // Set last bar time from the data
        if (validData.length > 0) {
          this._lastBarTime = validData[validData.length - 1].time;
          console.log('✅ Stored raw series reference, last bar time:', this._lastBarTime);
        }
      } else {
        console.error('❌ seriesInfo or seriesInfo.series is null/undefined');
      }

      return seriesInfo;
    },

    updateCandlestick(bar) {
      if (!this._rawOhlcSeries) {
        console.error('❌ No raw series reference');
        return;
      }

      // Extract primitives - ensure clean numbers
      const timeNum = parseInt(String(bar.time), 10);
      const openNum = parseFloat(String(bar.open));
      const highNum = parseFloat(String(bar.high));
      const lowNum = parseFloat(String(bar.low));
      const closeNum = parseFloat(String(bar.close));

      // Create plain bar object
      const plainBar = {
        time: timeNum,
        open: openNum,
        high: highNum,
        low: lowNum,
        close: closeNum
      };

      console.log('📊 Update attempt - incoming:', timeNum, 'last:', this._lastBarTime);

      // Only update if this bar is >= the last bar time
      if (this._lastBarTime === null || timeNum >= this._lastBarTime) {
        try {
          this._rawOhlcSeries.update(plainBar);
          this._lastBarTime = timeNum; // Update our tracking
          console.log('✅ Chart updated:', closeNum, 'Time:', new Date(timeNum * 1000).toLocaleTimeString());
        } catch (e) {
          console.error('❌ Update failed:', e.message);
        }
      } else {
        console.log('⚠️ Skipping old bar - incoming', timeNum, 'is older than last', this._lastBarTime);
      }
    },

    subscribeCrosshairMove(callback) {
      this.chartManager.subscribeCrosshairMove(callback);
    },

    unsubscribeCrosshairMove() {
      this.chartManager.unsubscribeCrosshairMove();
    },

    subscribeVisibleLogicalRangeChange(callback) {
      this.chartManager.subscribeVisibleLogicalRangeChange(callback);
    },

    unsubscribeVisibleLogicalRangeChange() {
      this.chartManager.unsubscribeVisibleLogicalRangeChange();
    },

    cleanupChart() {
      if (this.indicatorManager) {
        this.indicatorManager.destroy();
        this.indicatorManager = null;
      }

      if (this.chartManager) {
        this.chartManager.destroy();
        this.chartManager = null;
      }

      this.chartInitialized = false;
    },
  },
};
