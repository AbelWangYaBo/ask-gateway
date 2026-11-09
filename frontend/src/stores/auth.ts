import { defineStore } from 'pinia'

import { authApi } from '@/api'
import {
  EXPIRES_STORAGE_KEY,
  TOKEN_STORAGE_KEY,
  clearStoredToken,
  readStoredToken,
} from '@/api/client'

interface AuthState {
  token: string
  expiresAt: string
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: readStoredToken() ?? '',
    expiresAt: localStorage.getItem(EXPIRES_STORAGE_KEY) ?? '',
  }),
  getters: {
    isAuthenticated(state): boolean {
      if (!state.token) return false
      if (!state.expiresAt) return true
      const expires = new Date(state.expiresAt).getTime()
      if (Number.isNaN(expires)) return true
      return expires > Date.now()
    },
  },
  actions: {
    async login(password: string): Promise<void> {
      const result = await authApi.login(password)
      this.token = result.token
      this.expiresAt = result.expires_at
      localStorage.setItem(TOKEN_STORAGE_KEY, result.token)
      localStorage.setItem(EXPIRES_STORAGE_KEY, result.expires_at)
    },
    logout(): void {
      this.token = ''
      this.expiresAt = ''
      clearStoredToken()
    },
  },
})
