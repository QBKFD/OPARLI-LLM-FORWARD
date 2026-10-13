import { defineStore } from 'pinia';

export const useCurrentTimeframeStore = defineStore('currentTimeframe', {
  state: () => ({
    label: '1m',
    value: 1,
  }),
  actions: {
    setCurrentTimeframe(timeframe) {
      console.log('Store: setCurrentTimeframe called with:', timeframe);
      this.label = timeframe.label;
      this.value = timeframe.value;
      console.log('Store: new value =', this.value, 'label =', this.label);
    },
  },
});
