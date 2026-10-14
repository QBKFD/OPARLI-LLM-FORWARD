import { defineStore } from 'pinia';
import { API_CONFIG, getHeaders } from '@/config/api';

// Display name mapping (database symbol -> display name)
// Maps internal DB symbols to user-friendly display names
const SYMBOL_DISPLAY_NAMES = {
  // XAUUSD is now stored directly in database
};

// Force cache bust by logging on module load
console.log('[marketsStore] Markets store loaded');

export const useMarketsStore = defineStore('markets', {
  state: () => ({
    all: [],
  }),
  actions: {
    async fetch() {
      try {
        const response = await fetch(`${API_CONFIG.BASE_URL}/api/symbols`, { headers: getHeaders() });
        const data = await response.json();

        // Get latest price for each symbol to determine min_move
        const symbolsWithMinMove = await Promise.all(
          data.symbols.map(async (symbol) => {
            try {
              const priceResponse = await fetch(`${API_CONFIG.BASE_URL}/api/latest/${symbol}`, { headers: getHeaders() });
              const priceData = await priceResponse.json();

              // Auto-detect min_move based on price level
              let min_move;
              if (priceData.close > 100) {
                min_move = 0.1;  // Gold, indices
              } else if (priceData.close > 10) {
                min_move = 0.01; // Stocks
              } else {
                min_move = 0.0001; // Forex
              }

              // Get display name if mapped, otherwise use symbol as-is
              const displayName = SYMBOL_DISPLAY_NAMES[symbol] || symbol;

              return {
                symbol_id: symbol,       // Keep original for API calls
                id: symbol,
                symbol: displayName,     // Display name shown in UI
                name: displayName,
                min_move: min_move,
              };
            } catch (err) {
              const displayName = SYMBOL_DISPLAY_NAMES[symbol] || symbol;
              return {
                symbol_id: symbol,
                id: symbol,
                symbol: displayName,
                name: displayName,
                min_move: 0.01, // Fallback
              };
            }
          })
        );

        this.all = symbolsWithMinMove;
      } catch (err) {
        console.error('Failed to fetch markets:', err);
      }
    },
  },
});