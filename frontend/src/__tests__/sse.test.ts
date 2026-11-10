import { describe, expect, it } from 'vitest'

import { parseChatDelta, SseParser } from '@/utils/sse'

describe('SseParser', () => {
  it('解析跨块切断的 event/data 行', () => {
    const messages: Array<{ event: string | null; data: string }> = []
    const parser = new SseParser()
    parser.feed('event: x-request-id\nda', (m) => messages.push(m))
    parser.feed('ta: {"request_id":"r1"}\n\ndata: [DONE]', (m) => messages.push(m))
    parser.end((m) => messages.push(m))
    expect(messages).toEqual([
      { event: 'x-request-id', data: '{"request_id":"r1"}' },
      { event: null, data: '[DONE]' },
    ])
  })

  it('忽略注释行与无 data 事件', () => {
    const messages: Array<{ event: string | null; data: string }> = []
    const parser = new SseParser()
    parser.feed(': heartbeat\n\nevent: noop\n\n', (m) => messages.push(m))
    expect(messages).toEqual([])
  })
})

describe('parseChatDelta', () => {
  it('提取 content 与 reasoning_content', () => {
    const d = parseChatDelta(
      '{"choices":[{"delta":{"content":"你好","reasoning_content":"思考"}}]}',
    )
    expect(d).toEqual({ content: '你好', reasoning: '思考' })
  })

  it('[DONE] 与非 chunk 返回 null', () => {
    expect(parseChatDelta('[DONE]')).toBeNull()
    expect(parseChatDelta('not json')).toBeNull()
    expect(parseChatDelta('{"object":"list"}')).toBeNull()
  })
})
