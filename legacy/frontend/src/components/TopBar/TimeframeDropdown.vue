<template>
  <n-popselect
    :value="currentTimeframeStore.value"
    scrollable
    style="width: 80px; height: 150px;"
    @update:value="onTimeframeChange"
    :options="timeframes"
  >
    <n-button
      round
      class="timeframe-button"
    >
      {{ currentTimeframeStore.label }}
    </n-button>
  </n-popselect>
</template>

<script>
import { useCurrentTimeframeStore } from "@/stores/currentTimeframeStore";

import { NPopselect, NButton } from "naive-ui";

export default {
  name: "TimeframeDropdown",

  components: {
    NPopselect,
    NButton,
  },

  data() {
    return {
      currentTimeframeStore: useCurrentTimeframeStore(),
      timeframes: [
        { label: "1m", value: 1 },
        { label: "5m", value: 5 },
        { label: "15m", value: 15 },
        { label: "30m", value: 30 },
        { label: "1H", value: 60 },
        { label: "4H", value: 240 },
        { label: "1D", value: 1440 },
      ],
    };
  },

  methods: {
    onTimeframeChange(value) {
      console.log('Timeframe changed to:', value);
      const option = this.timeframes.find(tf => tf.value === value);
      if (option) {
        console.log('Setting timeframe:', option);
        this.currentTimeframeStore.setCurrentTimeframe(option);
      }
    },
  },
};
</script>

<style scoped>
.timeframe-button {
  width: 80px;
}
</style>