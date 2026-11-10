import { http } from './client'
import type {
  ApiKey,
  ApiKeyCreated,
  AuditLogPage,
  Dashboard,
  KeyCreatePayload,
  LoginResponse,
  PricingPayload,
  PricingRule,
  QuotaPayload,
  RequestDetail,
  RequestFilters,
  RequestPage,
  KeyVerifyPayload,
  KeyVerifyResult,
  UpstreamKey,
  UpstreamKeyPayload,
  UsageSummary,
} from './types'

// ---- 认证 ----

export const authApi = {
  login(password: string): Promise<LoginResponse> {
    return http.post('/admin/auth/login', { password }).then((r) => r.data)
  },
}

// ---- Key 管理 ----

export const keysApi = {
  list(): Promise<ApiKey[]> {
    return http.get('/admin/keys').then((r) => r.data)
  },
  create(payload: KeyCreatePayload): Promise<ApiKeyCreated> {
    return http.post('/admin/keys', payload).then((r) => r.data)
  },
  revoke(keyId: string): Promise<ApiKey> {
    return http.post(`/admin/keys/${keyId}/revoke`).then((r) => r.data)
  },
  updateQuota(keyId: string, payload: QuotaPayload): Promise<ApiKey> {
    return http.patch(`/admin/keys/${keyId}`, payload).then((r) => r.data)
  },
  verify(payload: KeyVerifyPayload): Promise<KeyVerifyResult> {
    return http.post('/admin/keys/verify', payload).then((r) => r.data)
  },
}

// ---- 审计检索 ----

export const requestsApi = {
  list(filters: RequestFilters): Promise<RequestPage> {
    return http.get('/admin/requests', { params: filters }).then((r) => r.data)
  },
  detail(requestId: string): Promise<RequestDetail> {
    return http.get(`/admin/requests/${requestId}`).then((r) => r.data)
  },
  exportUrl(requestId: string): string {
    return `/admin/requests/${requestId}/export`
  },
}

// ---- 费用看板 ----

export const usageApi = {
  summary(
    groupBy: UsageSummary['group_by'],
    dateFrom?: string,
    dateTo?: string,
  ): Promise<UsageSummary> {
    return http
      .get('/admin/usage/summary', {
        params: { group_by: groupBy, date_from: dateFrom, date_to: dateTo },
      })
      .then((r) => r.data)
  },
}

// ---- 定价 ----

export const pricingApi = {
  list(): Promise<PricingRule[]> {
    return http.get('/admin/pricing').then((r) => r.data)
  },
  create(payload: PricingPayload): Promise<PricingRule> {
    return http.put('/admin/pricing', payload).then((r) => r.data)
  },
}

// ---- 管理操作日志 ----

export const auditLogApi = {
  list(
    action: string | undefined,
    page: number,
    pageSize: number,
  ): Promise<AuditLogPage> {
    return http
      .get('/admin/audit-logs', {
        params: { action, page, page_size: pageSize },
      })
      .then((r) => r.data)
  },
}

// ---- 看板 ----

export const dashboardApi = {
  get(): Promise<Dashboard> {
    return http.get('/admin/dashboard').then((r) => r.data)
  },
}

// ---- 上游 Key 管理 ----

export const upstreamKeysApi = {
  list(): Promise<UpstreamKey[]> {
    return http.get('/admin/upstream-keys').then((r) => r.data)
  },
  create(payload: UpstreamKeyPayload): Promise<UpstreamKey> {
    return http.post('/admin/upstream-keys', payload).then((r) => r.data)
  },
  update(keyId: string, payload: Partial<UpstreamKeyPayload> & { is_active?: boolean }): Promise<UpstreamKey> {
    return http.patch(`/admin/upstream-keys/${keyId}`, payload).then((r) => r.data)
  },
  remove(keyId: string): Promise<void> {
    return http.delete(`/admin/upstream-keys/${keyId}`).then(() => undefined)
  },
}
