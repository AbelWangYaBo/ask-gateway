import { describe, expect, it } from 'vitest'

import type { RequestEvent } from '@/api/types'
import {
  buildTimeline,
  extractSearchChain,
  previewMessages,
} from '@/utils/timeline'

function event(seq: number, event_type: string, payload: Record<string, unknown>): RequestEvent {
  return { seq, event_type, payload, created_at: `2026-11-09T10:00:0${seq}Z` }
}

describe('buildTimeline', () => {
  it('已知类型映射中文标题，未知类型保留原 type', () => {
    const items = buildTimeline([
      event(1, 'turn/start', { data: { turn: 1 } }),
      event(2, 'thinking', { text: '先推理' }),
      event(3, 'vendor/custom', {}),
    ])
    expect(items.map((i) => i.title)).toEqual(['回合开始', '思考过程', 'vendor/custom'])
    expect(items[1].raw).toEqual({ text: '先推理' })
  })
})

describe('extractSearchChain', () => {
  const events: RequestEvent[] = [
    event(1, 'tool/call', {
      data: {
        turn: 1,
        step: 1,
        callId: 'c1',
        name: 'web_search',
        arguments: JSON.stringify({ query: '量子计算进展' }),
      },
    }),
    event(2, 'tool/result', { data: { callId: 'c1', result: '三条结果' } }),
    event(3, 'tool/call', {
      data: {
        turn: 1,
        step: 2,
        callId: 'c2',
        name: 'web_search',
        arguments: 'not-json',
      },
    }),
    event(4, 'tool/call', {
      data: { turn: 1, step: 3, callId: 'c3', name: 'other_tool', arguments: '{}' },
    }),
  ]

  it('只抽取 web_search 并按 callId 配对结果', () => {
    const chain = extractSearchChain(events)
    expect(chain).toHaveLength(2)
    expect(chain[0]).toMatchObject({
      callId: 'c1',
      step: 1,
      query: '量子计算进展',
      hasResult: true,
      resultText: '三条结果',
    })
  })

  it('arguments 非法 JSON 不抛异常，query 留空；缺失结果事件标记未完成', () => {
    const chain = extractSearchChain(events)
    expect(chain[1].query).toBe('')
    expect(chain[1].hasResult).toBe(false)
  })

  it('无搜索事件返回空数组', () => {
    expect(extractSearchChain([event(1, 'turn/start', {})])).toEqual([])
  })
})

describe('previewMessages', () => {
  it('字符串 content 直接透传', () => {
    const previews = previewMessages([{ role: 'user', content: '你好' }])
    expect(previews).toEqual([{ role: 'user', text: '你好' }])
  })

  it('content block 数组拼接 text 字段', () => {
    const previews = previewMessages([
      {
        role: 'assistant',
        content: [
          { type: 'reasoning', text: '推理段' },
          { type: 'text', text: '回答段' },
        ],
      },
    ])
    expect(previews[0].role).toBe('assistant')
    expect(previews[0].text).toBe('推理段\n回答段')
  })

  it('缺 role 时兜底 unknown，不抛异常', () => {
    const previews = previewMessages([{ content: null }])
    expect(previews[0].role).toBe('unknown')
  })
})
