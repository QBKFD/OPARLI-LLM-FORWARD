<template>
  <n-config-provider :theme="darkTheme">
    <n-message-provider>
      <div class="app-container">
        <MainNavigation />
        <main>
          <router-view />
        </main>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<script>
import { NConfigProvider, NMessageProvider, darkTheme } from 'naive-ui';
import MainNavigation from '@/components/Navigation/MainNavigation.vue';

export default {
  name: 'App',

  components: {
    NConfigProvider,
    NMessageProvider,
    MainNavigation,
  },

  data() {
    return {
      darkTheme,
    };
  },

  beforeUnmount() {
    if (this.$wss) {
      this.$wss.close();
    }
  },
};
</script>

<style>
.app-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #101014;
}

main {
  margin-top: 60px; /* Height of top bar */
  flex: 1;
  overflow: auto;
  padding: 0;
}
</style>
