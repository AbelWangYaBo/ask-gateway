// 纯函数格式化工具：金额单位为人民币分，时间统一按北京时区展示

export function formatYuan(cents: string | number | null | undefined): string {
  if (cents === null || cents === undefined || cents === '') return '—'
  const value = typeof cents === 'string' ? Number(cents) : cents
  if (!Number.isFinite(value)) return '—'
  // 成本可能到毫分（Numeric(12,3)），保留有效小数并去掉尾随 0
  const yuan = value / 100
  return `¥${yuan.toLocaleString('zh-CN', { maximumFractionDigits: 3 })}`
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('zh-CN')
}

export function formatPercent(rate: number): string {
  return `${(rate * 100).toFixed(2)}%`
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  // 后端序列化为 naive UTC（无 Z/偏移）：补 Z 后按北京时间展示
  const normalized = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatDay(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

export function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'succeeded':
      return 'success'
    case 'running':
      return 'warning'
    case 'failed':
      return 'danger'
    default:
      return 'info'
  }
}
