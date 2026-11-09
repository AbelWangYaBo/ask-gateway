import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { authApi } from '@/api'
import {
  EXPIRES_STORAGE_KEY,
  TOKEN_STORAGE_KEY,
} from '@/api/client'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api', () => ({
  authApi: {
    login: vi.fn(),
  },
}))

describe('auth store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.mocked(authApi.login).mockReset()
  })

  it('login 成功后写入 state 与 localStorage', async () => {
    const expiresAt = new Date(Date.now() + 3600_000).toISOString()
    vi.mocked(authApi.login).mockResolvedValue({ token: 'tok-1', expires_at: expiresAt })

    const store = useAuthStore()
    await store.login('pw')

    expect(store.token).toBe('tok-1')
    expect(store.isAuthenticated).toBe(true)
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe('tok-1')
    expect(localStorage.getItem(EXPIRES_STORAGE_KEY)).toBe(expiresAt)
  })

  it('过期 token 视为未认证', () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'old')
    localStorage.setItem(
      EXPIRES_STORAGE_KEY,
      new Date(Date.now() - 1000).toISOString(),
    )
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(false)
  })

  it('无 token 时未认证', () => {
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(false)
  })

  it('logout 清空 state 与 localStorage', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      token: 'tok-2',
      expires_at: new Date(Date.now() + 1000).toISOString(),
    })
    const store = useAuthStore()
    await store.login('pw')
    store.logout()
    expect(store.token).toBe('')
    expect(store.isAuthenticated).toBe(false)
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })
})
