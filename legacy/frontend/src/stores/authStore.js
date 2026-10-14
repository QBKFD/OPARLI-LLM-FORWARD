// frontend/src/stores/authStore.js
/**
 * Authentication Store
 *
 * Manages JWT token and auth state for dashboard access
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/config/api'

export const useAuthStore = defineStore('auth', () => {
  // State
  const token = ref(localStorage.getItem('dashboard_token') || null)
  const isLoading = ref(false)
  const error = ref(null)

  // Computed
  const isAuthenticated = computed(() => !!token.value)

  // Actions
  async function login(password) {
    isLoading.value = true
    error.value = null

    try {
      const response = await fetch(`${api.baseUrl}/api/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ password })
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Login failed')
      }

      const data = await response.json()

      // Store token
      token.value = data.access_token
      localStorage.setItem('dashboard_token', data.access_token)

      return true
    } catch (err) {
      error.value = err.message
      return false
    } finally {
      isLoading.value = false
    }
  }

  async function verifyToken() {
    if (!token.value) {
      return false
    }

    try {
      const response = await fetch(`${api.baseUrl}/api/auth/verify`, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token.value}`
        }
      })

      if (!response.ok) {
        // Token invalid or expired
        logout()
        return false
      }

      return true
    } catch (err) {
      console.error('Token verification failed:', err)
      logout()
      return false
    }
  }

  function logout() {
    token.value = null
    localStorage.removeItem('dashboard_token')
    error.value = null
  }

  function getAuthHeaders() {
    if (!token.value) return {}
    return {
      'Authorization': `Bearer ${token.value}`
    }
  }

  // Initialize - verify token on store creation
  async function init() {
    if (token.value) {
      await verifyToken()
    }
  }

  return {
    // State
    token,
    isLoading,
    error,

    // Computed
    isAuthenticated,

    // Actions
    login,
    logout,
    verifyToken,
    getAuthHeaders,
    init
  }
})
