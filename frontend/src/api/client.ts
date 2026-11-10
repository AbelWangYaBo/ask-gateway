import axios, { AxiosError, type AxiosInstance } from 'axios'

// 与 stores/auth.ts 共享的存储键；client 不依赖 Pinia，避免循环依赖
export const TOKEN_STORAGE_KEY = 'ask_gateway_admin_token'
export const EXPIRES_STORAGE_KEY = 'ask_gateway_admin_expires_at'

export interface ApiErrorBody {
  error?: {
    code: string
    message: string
    request_id?: string
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export function readStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY)
  localStorage.removeItem(EXPIRES_STORAGE_KEY)
}

export const http: AxiosInstance = axios.create({
  baseURL: '',
  timeout: 30_000,
})

http.interceptors.request.use((config) => {
  const token = readStoredToken()
  if (token) {
    config.headers.set('X-Admin-Token', token)
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0
    const envelope = error.response?.data?.error
    const message = envelope?.message ?? error.message ?? '网络异常'
    const code = envelope?.code ?? 'network_error'

    if (status === 401) {
      clearStoredToken()
      // 登录页自身的 401 由调用方提示；其余页面由路由守卫在下一跳转处理
      if (!window.location.pathname.startsWith('/login')) {
        window.location.assign('/login')
      }
    }
    return Promise.reject(new ApiError(status, code, message))
  },
)

// ---- 用户门户（API Key 即凭证，与管理台凭证互相独立） ----

export const PORTAL_KEY_STORAGE = 'ask_gateway_portal_api_key'

export function readPortalKey(): string | null {
  return localStorage.getItem(PORTAL_KEY_STORAGE)
}

export function clearPortalKey(): void {
  localStorage.removeItem(PORTAL_KEY_STORAGE)
}

function buildErrorInterceptor(prefix: string) {
  return (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0
    const envelope = error.response?.data?.error
    const message = envelope?.message ?? error.message ?? '网络异常'
    const code = envelope?.code ?? 'network_error'
    if (status === 401) {
      clearPortalKey()
      if (window.location.pathname.startsWith(prefix) && window.location.pathname !== `${prefix}/login`) {
        window.location.assign(`${prefix}/login`)
      }
    }
    return Promise.reject(new ApiError(status, code, message))
  }
}

export const portalHttp: AxiosInstance = axios.create({
  baseURL: '',
  timeout: 30_000,
})

portalHttp.interceptors.request.use((config) => {
  const key = readPortalKey()
  if (key) {
    config.headers.set('Authorization', `Bearer ${key}`)
  }
  return config
})

portalHttp.interceptors.response.use((response) => response, buildErrorInterceptor('/portal'))
