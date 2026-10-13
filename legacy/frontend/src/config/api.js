// API Configuration
// Using domain for production

export const API_CONFIG = {
  // Base URL for REST API
  BASE_URL: import.meta.env.VITE_API_BASE_URL || 'https://oparli.com',

  // WebSocket URL
  WS_URL: import.meta.env.VITE_WS_URL || 'wss://oparli.com',

  // API key from environment (set VITE_API_KEY in frontend/.env at build time)
  API_KEY: import.meta.env.VITE_API_KEY || '',
};

// Helper to get headers
export function getHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': API_CONFIG.API_KEY,
  };
}

// Helper to build WebSocket URL
export function getWebSocketUrl(path) {
  return `${API_CONFIG.WS_URL}${path}?api_key=${API_CONFIG.API_KEY}`;
}

// Default export for simpler imports
export default {
  baseUrl: API_CONFIG.BASE_URL,
  wsUrl: API_CONFIG.WS_URL,
  apiKey: API_CONFIG.API_KEY,
  getHeaders,
  getWebSocketUrl
};
