<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-header">
        <h1>Dashboard Access</h1>
        <p>Enter password to continue</p>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
        <div class="input-group">
          <input
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            placeholder="Password"
            :disabled="isLoading"
            autofocus
            class="password-input"
          />
          <button
            type="button"
            @click="showPassword = !showPassword"
            class="toggle-password"
          >
            {{ showPassword ? 'Hide' : 'Show' }}
          </button>
        </div>

        <div v-if="error" class="error-message">
          {{ error }}
        </div>

        <button
          type="submit"
          :disabled="isLoading || !password"
          class="login-button"
        >
          {{ isLoading ? 'Authenticating...' : 'Enter Dashboard' }}
        </button>
      </form>

      <div class="login-footer">
        <a href="/" class="back-link">Back to main site</a>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'

const router = useRouter()
const authStore = useAuthStore()

const password = ref('')
const showPassword = ref(false)
const isLoading = ref(false)
const error = ref(null)

async function handleLogin() {
  if (!password.value) return

  isLoading.value = true
  error.value = null

  const success = await authStore.login(password.value)

  if (success) {
    // Redirect to dashboard
    router.push('/dashboard')
  } else {
    error.value = authStore.error || 'Invalid password'
    password.value = ''
  }

  isLoading.value = false
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  padding: 20px;
}

.login-card {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  padding: 40px;
  width: 100%;
  max-width: 400px;
  backdrop-filter: blur(10px);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-header h1 {
  color: #fff;
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 8px 0;
}

.login-header p {
  color: rgba(255, 255, 255, 0.6);
  font-size: 14px;
  margin: 0;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.input-group {
  position: relative;
  display: flex;
}

.password-input {
  flex: 1;
  padding: 14px 80px 14px 16px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 8px;
  color: #fff;
  font-size: 16px;
  outline: none;
  transition: all 0.2s;
}

.password-input:focus {
  border-color: #4CAF50;
  background: rgba(255, 255, 255, 0.1);
}

.password-input::placeholder {
  color: rgba(255, 255, 255, 0.4);
}

.toggle-password {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.5);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 8px;
}

.toggle-password:hover {
  color: rgba(255, 255, 255, 0.8);
}

.error-message {
  background: rgba(244, 67, 54, 0.15);
  border: 1px solid rgba(244, 67, 54, 0.3);
  color: #f44336;
  padding: 12px;
  border-radius: 8px;
  font-size: 14px;
  text-align: center;
}

.login-button {
  padding: 14px 24px;
  background: #4CAF50;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.login-button:hover:not(:disabled) {
  background: #45a049;
}

.login-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.login-footer {
  margin-top: 24px;
  text-align: center;
}

.back-link {
  color: rgba(255, 255, 255, 0.5);
  text-decoration: none;
  font-size: 14px;
}

.back-link:hover {
  color: rgba(255, 255, 255, 0.8);
}
</style>
