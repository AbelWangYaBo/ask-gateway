import { describe, expect, it } from 'vitest'

import { buildCsv, type CsvColumn } from '@/utils/csv'

interface Row extends Record<string, unknown> {
  name: string
  amount: number
}

const columns: Array<CsvColumn<Row>> = [
  { key: 'name', label: '名称' },
  { key: 'amount', label: '金额' },
]

describe('buildCsv', () => {
  it('带 UTF-8 BOM 与表头', () => {
    const csv = buildCsv(columns, [{ name: '张三', amount: 100 }])
    expect(csv.startsWith('\uFEFF')).toBe(true)
    expect(csv).toContain('名称,金额')
    expect(csv).toContain('张三,100')
  })

  it('逗号、引号、换行按 RFC 4180 转义', () => {
    const csv = buildCsv(columns, [
      { name: 'a,b', amount: 1 },
      { name: 'he said "hi"', amount: 2 },
      { name: 'line1\nline2', amount: 3 },
    ])
    expect(csv).toContain('"a,b"')
    expect(csv).toContain('"he said ""hi"""')
    expect(csv).toContain('"line1\nline2"')
  })

  it('空值输出空字段，行分隔为 CRLF', () => {
    const csv = buildCsv(columns, [{ name: '', amount: 0 }])
    expect(csv).toContain(',0')
    expect(csv).toContain('\r\n')
  })

  it('value 取值器优先于 key', () => {
    const custom: Array<CsvColumn<Row>> = [
      { key: 'ignored', label: '双倍', value: (row) => row.amount * 2 },
    ]
    const csv = buildCsv(custom, [{ name: 'x', amount: 21 }])
    expect(csv).toContain('42')
  })
})
