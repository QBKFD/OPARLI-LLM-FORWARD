<template>
  <div class="main-navigation">
    <!-- Top Bar -->
    <div class="top-bar">
      <!-- Menu Toggle Button -->
      <button class="menu-toggle" @click="toggleSidebar">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
          <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/>
        </svg>
      </button>

      <!-- Logo -->
      <router-link to="/live" class="logo">
        <span class="logo-text">OPARLI</span>
      </router-link>
    </div>

    <!-- Sidebar Overlay -->
    <div
      class="sidebar-overlay"
      :class="{ active: isSidebarOpen }"
      @click="closeSidebar"
    ></div>

    <!-- Sidebar -->
    <div class="sidebar" :class="{ open: isSidebarOpen }">
      <div class="sidebar-header">
        <h2>OPARLI</h2>
        <button class="close-btn" @click="closeSidebar">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
            <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
          </svg>
        </button>
      </div>

      <nav class="sidebar-nav">
        <router-link
          to="/live"
          class="nav-item"
          :class="{ active: $route.path === '/live' }"
          @click="closeSidebar"
        >
          <div class="nav-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="12" r="2"/>
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/>
            </svg>
          </div>
          <span class="nav-label">Live Trading</span>
          <div class="nav-indicator" v-if="$route.path === '/live'"></div>
        </router-link>

        <router-link
          to="/validation"
          class="nav-item"
          :class="{ active: $route.path === '/validation' }"
          @click="closeSidebar"
        >
          <div class="nav-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
            </svg>
          </div>
          <span class="nav-label">Validation</span>
          <div class="nav-indicator" v-if="$route.path === '/validation'"></div>
        </router-link>

        <div class="nav-divider"></div>

        <!-- Additional menu items -->
        <router-link
          to="/about"
          class="nav-item"
          :class="{ active: $route.path === '/about' }"
          @click="closeSidebar"
        >
          <div class="nav-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
            </svg>
          </div>
          <span class="nav-label">About</span>
          <div class="nav-indicator" v-if="$route.path === '/about'"></div>
        </router-link>

        <a href="#" class="nav-item" @click.prevent>
          <div class="nav-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
            </svg>
          </div>
          <span class="nav-label">Settings</span>
        </a>
      </nav>

    </div>
  </div>
</template>

<script>
import { ref } from 'vue';

export default {
  name: 'MainNavigation',

  setup() {
    const isSidebarOpen = ref(false);

    const toggleSidebar = () => {
      isSidebarOpen.value = !isSidebarOpen.value;
    };

    const closeSidebar = () => {
      isSidebarOpen.value = false;
    };

    return {
      isSidebarOpen,
      toggleSidebar,
      closeSidebar,
    };
  },
};
</script>

<style scoped>
/* Top Bar */
.top-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 60px;
  background: #0f0f13;
  border-bottom: 1px solid #1f1f23;
  display: flex;
  align-items: center;
  padding: 0 20px;
  z-index: 1000;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

.menu-toggle {
  background: none;
  border: none;
  color: #fff;
  cursor: pointer;
  padding: 8px;
  border-radius: 8px;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.menu-toggle:hover {
  background: #1f1f23;
}

.menu-toggle svg {
  width: 24px;
  height: 24px;
}

.logo {
  margin-left: 16px;
  text-decoration: none;
}

.logo-text {
  font-size: 20px;
  font-weight: 400;
  color: #ffffff;
  letter-spacing: 2px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
}


/* Sidebar Overlay */
.sidebar-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 1001;
  opacity: 0;
  visibility: hidden;
  transition: all 0.3s ease;
}

.sidebar-overlay.active {
  opacity: 1;
  visibility: visible;
}

/* Sidebar */
.sidebar {
  position: fixed;
  top: 0;
  left: -280px;
  width: 280px;
  height: 100vh;
  background: #18181c;
  border-right: 1px solid #2c2c2c;
  z-index: 1002;
  transition: left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
  box-shadow: 4px 0 16px rgba(0, 0, 0, 0.3);
}

.sidebar.open {
  left: 0;
}

/* Sidebar Header */
.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid #2c2c2c;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 60px;
}

.sidebar-header h2 {
  font-size: 20px;
  font-weight: 400;
  color: #ffffff;
  margin: 0;
  letter-spacing: 2px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
}

.close-btn {
  background: none;
  border: none;
  color: #888;
  cursor: pointer;
  padding: 6px;
  border-radius: 6px;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.close-btn:hover {
  background: #2c2c2c;
  color: #fff;
}

.close-btn svg {
  width: 20px;
  height: 20px;
}

/* Sidebar Navigation */
.sidebar-nav {
  flex: 1;
  padding: 20px 0;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  padding: 14px 24px;
  color: #888;
  text-decoration: none;
  transition: all 0.2s ease;
  position: relative;
  cursor: pointer;
}

.nav-item:hover {
  background: #1f1f23;
  color: #fff;
}

.nav-item.active {
  background: #2c2c2c;
  color: #fff;
}

.nav-icon {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 16px;
}

.nav-icon svg {
  width: 20px;
  height: 20px;
}

.nav-label {
  font-weight: 500;
  font-size: 15px;
  letter-spacing: 0.3px;
}

.nav-indicator {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 32px;
  background: #fff;
  border-radius: 0 2px 2px 0;
}

.nav-divider {
  height: 1px;
  background: #2c2c2c;
  margin: 12px 24px;
}


/* Responsive */
@media (max-width: 768px) {
  .logo-text {
    font-size: 18px;
  }
}
</style>
