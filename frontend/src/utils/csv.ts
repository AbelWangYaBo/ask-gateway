// CSV 导出：RFC 4180 转义 + UTF-8 BOM（Excel 直接打开不乱码）

export interface CsvColumn<T> {
  key: string
  label: string
  value?: (row: T) => string | number | null | undefined
}

function escapeField(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}

export function buildCsv<T extends Record<string, unknown>>(
  columns: Array<CsvColumn<T>>,
  rows: T[],
): string {
  const header = columns.map((c) => escapeField(c.label)).join(',')
  const lines = rows.map((row) =>
    columns
      .map((col) => {
        const raw = col.value ? col.value(row) : row[col.key]
        return escapeField(raw === null || raw === undefined ? '' : String(raw))
      })
      .join(','),
  )
  // \uFEFF = UTF-8 BOM，Excel 直接打开不乱码
  return '\uFEFF' + [header, ...lines].join('\r\n')
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
