import { defineStore } from 'pinia';
import { API_CONFIG, getHeaders } from '@/config/api';

export const useCandlesticksStore = defineStore('candlesticks', {
  state: () => ({
    type: 'candlestick',
    data: [],
  }),

  actions: {
    async fetch(symbolID, timeframe, startDate = null, endDate = null, limit = 1000, append = false) {
      try {
        // Convert numeric timeframe to API format
        const timeframeMap = {
          1: '1min',
          5: '5min',
          15: '15min',
          30: '30min',
          60: '1H',
          240: '4H',
          1440: '1D'
        };
        
        const apiTimeframe = timeframeMap[timeframe] || '1min';
        
        const url = `${API_CONFIG.BASE_URL}/api/chart-data/${symbolID}/${apiTimeframe}?limit=${limit}`;
        console.log('Fetching candlesticks from:', url);

        const response = await fetch(url, { headers: getHeaders() });
        const result = await response.json();

        console.log('API Response:', result);

        if (result.error) {
          console.error('API Error:', result.error);
          return;
        }

        if (!result.bars || result.bars.length === 0) {
          console.warn('No bars returned from API');
          return;
        }

        console.log('First bar from API:', result.bars[0]);

        let newData = result.bars.map((candle) => ({
          time: candle.time,
          open: parseFloat(candle.open),
          high: parseFloat(candle.high),
          low: parseFloat(candle.low),
          close: parseFloat(candle.close),
        }));

        if (append) {
          // Merge and deduplicate by timestamp
          const merged = [...newData, ...this.data];
          const uniqueMap = new Map();
          merged.forEach(bar => uniqueMap.set(bar.time, bar));
          this.data = Array.from(uniqueMap.values()).sort((a, b) => a.time - b.time);
        } else {
          // Deduplicate newData just in case
          const uniqueMap = new Map();
          newData.forEach(bar => uniqueMap.set(bar.time, bar));
          this.data = Array.from(uniqueMap.values()).sort((a, b) => a.time - b.time);
        }
      } catch (err) {
        console.error('Failed to fetch candlestick data:', err);
      }
    },
  },
});