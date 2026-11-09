import { describe, expect, it } from 'vitest'

import {
  formatDateTime,
  formatLatency,
  formatNumber,
  formatPercent,
  formatYuan,
  statusLabel,
  statusTagType,
} from '@/utils/format'

describe('formatYuan', () => {
  it('分转元并保留毫分精度', () => {
    expect(formatYuan(100)).toBe('¥1')
    expect(formatYuan('10500')).toBe('¥105')
    expect(formatYuan(3)).toBe('¥0.03')
    // Numeric(12,3) 允许毫分：0.3 分 = 0.003 元
    expect(formatYuan(0.3)).toBe('¥0.003')
  })

  it('空值与非法值显示占位', () => {
    expect(formatYuan(null)).toBe('—')
    expect(formatYuan(undefined)).toBe('—')
    expect(formatYuan('')).toBe('—')
    expect(formatYuan('abc')).toBe('—')
  })
})

describe('其他格式化', () => {
  it('数字千分位', () => {
    expect(formatNumber(1234567)).toMatch(/1[, \s]234[, \s]567/)
    expect(formatNumber(null)).toBe('—')
  })

  it('百分比', () => {
    expect(formatPercent(0.98765)).toBe('98.77%')
  })

  it('延迟毫秒/秒切换', () => {
    expect(formatLatency(820)).toBe('820 ms')
    expect(formatLatency(2500)).toBe('2.50 s')
    expect(formatLatency(null)).toBe('—')
  })

  it('时间按北京时区渲染', () => {
    const text = formatDateTime('2026-11-09T10:00:00Z')
    // 北京 = UTC+8，必然落在 18 点
    expect(text).toContain('2026')
    expect(text).toContain('18:00:00')
    expect(formatDateTime(null)).toBe('—')
  })

  it('状态标签与 tag 类型', () => {
    expect(statusLabel('succeeded')).toBe('成功')
    expect(statusLabel('unknown-x')).toBe('unknown-x')
    expect(statusTagType('failed')).toBe('danger')
    expect(statusTagType('queued')).toBe('info')
  })
})
