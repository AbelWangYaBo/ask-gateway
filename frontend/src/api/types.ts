// 与后端 schemas/admin.py 对齐；金额字段后端序列化为字符串（分），前端不做浮点换算

export interface LoginResponse {
  token: string
  expires_at: string
}

export interface ApiKey {
  id: string
  key_prefix: string
  holder_name: string
  label: string
  status: 'active' | 'revoked'
  rpm_limit: number | null
  daily_budget_cents: number | null
  max_concurrency: number | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string
  disabled: boolean
  expires_at: string | null
}

export interface ApiKeyCreated extends ApiKey {
  plaintext: string
}

export interface KeyCreatePayload {
  holder_name: string
  label: string
  rpm_limit?: number | null
  daily_budget_cents?: number | null
  max_concurrency?: number | null
}

export type QuotaPayload = Partial<{
  rpm_limit: number | null
  daily_budget_cents: number | null
  disabled: boolean | null
  expires_at: string | null
  max_concurrency: number | null
}>

export interface RequestListItem {
  id: string
  request_id: string
  api_key_id: string
  holder_name: string
  key_label: string
  model: string
  stream: boolean
  status: string
  total_input_tokens: number | null
  total_output_tokens: number | null
  search_count: number
  total_cost_cents: string
  error_code: string | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
}

export interface RequestEvent {
  seq: number
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface UsageRecord {
  step: number
  complete: boolean
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  reasoning_tokens: number | null
  search_count: number
  pricing_snapshot: Record<string, unknown>
  cost_cents: string
  created_at: string
}

export interface RequestDetail extends RequestListItem {
  enable_web_search: boolean
  thinking_effort: string | null
  client_ip: string | null
  user_agent: string | null
  prompt_messages: Array<Record<string, unknown>>
  final_answer: string | null
  finish_reason: string | null
  error_message: string | null
  dsh_session_id: string | null
  upstream_key_label: string | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  reasoning_tokens: number | null
  thinking: string
  events: RequestEvent[]
  usage_records: UsageRecord[]
}

export interface RequestPage {
  items: RequestListItem[]
  total: number
  page: number
  page_size: number
}

export interface RequestFilters {
  holder_name?: string
  key_id?: string
  upstream_key_id?: string
  status?: string
  date_from?: string
  date_to?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface UsageSummaryRow {
  group: string
  group_label: string
  request_count: number
  input_tokens: number
  output_tokens: number
  search_count: number
  cost_cents: string
}

export interface UsageChart {
  categories: string[]
  series: Array<{ name: string; data: number[] }>
}

export interface UsageSummary {
  group_by: 'holder' | 'key' | 'model' | 'day' | 'upstream_key'
  date_from: string | null
  date_to: string | null
  rows: UsageSummaryRow[]
  chart: UsageChart
}

export interface PricingRule {
  id: string
  model: string
  rule_name: string
  peak_start_hour: number | null
  peak_end_hour: number | null
  weekdays_only: boolean
  is_active: boolean
  effective_from: string
  input_price_per_mtok: string
  output_price_per_mtok: string
  reasoning_price_per_mtok: string | null
  cache_read_price_per_mtok: string | null
  cache_write_price_per_mtok: string | null
  search_price_per_k: string
  created_at: string
}

export interface PricingPayload {
  model: string
  rule_name: string
  peak_start_hour?: number | null
  peak_end_hour?: number | null
  weekdays_only?: boolean
  effective_from: string
  input_price_per_mtok: number | string
  output_price_per_mtok: number | string
  reasoning_price_per_mtok?: number | string | null
  cache_read_price_per_mtok?: number | string | null
  cache_write_price_per_mtok?: number | string | null
  search_price_per_k?: number | string
  is_active?: boolean
}

export interface AuditLog {
  id: number
  action: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown> | null
  client_ip: string | null
  created_at: string
}

export interface AuditLogPage {
  items: AuditLog[]
  total: number
  page: number
  page_size: number
}

export interface Dashboard {
  total_requests: number
  succeeded: number
  success_rate: number
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  today_cost_cents: string
  active_keys: number
  status_distribution: Record<string, number>
}

export interface UpstreamKey {
  id: string
  label: string
  key_fingerprint: string
  key_preview: string
  is_active: boolean
  notes: string | null
  created_at: string
}

export interface UpstreamKeyPayload {
  label: string
  plaintext: string
  notes?: string | null
}

export interface KeyVerifyPayload {
  plaintext: string
}

export interface KeyVerifyResult {
  kind: 'platform' | 'upstream' | 'unknown'
  key_id: string | null
  label: string | null
  holder_name: string | null
  preview: string | null
  status: string | null
  expires_at: string | null
}

export interface PortalKeyItem {
  id: string
  key_prefix: string
  label: string
  status: string
  disabled: boolean
  expires_at: string | null
  rpm_limit: number | null
  daily_budget_cents: number | null
  max_concurrency: number | null
  last_used_at: string | null
  created_at: string
}

export interface PortalProfile {
  holder_name: string
  keys: PortalKeyItem[]
}

export interface PortalDashboard {
  total_requests: number
  succeeded: number
  success_rate: number
  total_cost_cents: number
  total_input_tokens: number
  total_output_tokens: number
}

export interface Conversation {
  id: string
  title: string
  forked_from_id: string | null
  fork_point_seq: number | null
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: string
  seq: number
  role: 'user' | 'assistant'
  content: string
  reasoning: string | null
  request_id: string | null
  created_at: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: ConversationMessage[]
}
