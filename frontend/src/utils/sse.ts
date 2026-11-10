/** SSE 流解析：按行拆 event/data，喂给回调；纯函数式，可在 Node 单测。 */

export interface SseMessage {
  event: string | null
  data: string
}

/** 增量解析器：feed() 可多次传入任意切块，自动处理跨块换行。 */
export class SseParser {
  private buffer = ''

  feed(chunk: string, onMessage: (message: SseMessage) => void): void {
    this.buffer += chunk
    let index: number
    while ((index = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, index).replace(/\r$/, '')
      this.buffer = this.buffer.slice(index + 1)
      this.processLine(line, onMessage)
    }
  }

  /** 流结束时冲出无尾换行的残留行。 */
  end(onMessage: (message: SseMessage) => void): void {
    const rest = this.buffer
    this.buffer = ''
    if (rest.trim() !== '') {
      this.processLine(rest.replace(/\r$/, ''), onMessage)
    }
  }

  private pendingEvent: string | null = null

  private processLine(line: string, onMessage: (message: SseMessage) => void): void {
    if (line === '') {
      // 空行 = 事件结束；无 data 的 event 丢弃
      this.pendingEvent = null
      return
    }
    if (line.startsWith(':')) return // 注释/心跳
    if (line.startsWith('event:')) {
      this.pendingEvent = line.slice(6).trim()
      return
    }
    if (line.startsWith('data:')) {
      onMessage({ event: this.pendingEvent, data: line.slice(5).trim() })
      this.pendingEvent = null
    }
  }
}

/** chat/completions 流式 chunk 的字段提取；非 chunk 消息返回 null。 */
export interface ChatDelta {
  content: string
  reasoning: string
}

export function parseChatDelta(data: string): ChatDelta | null {
  if (!data || data === '[DONE]') return null
  try {
    const parsed = JSON.parse(data) as {
      choices?: Array<{ delta?: { content?: string | null; reasoning_content?: string | null } }>
    }
    const delta = parsed.choices?.[0]?.delta
    if (!delta) return null
    return {
      content: delta.content ?? '',
      reasoning: delta.reasoning_content ?? '',
    }
  } catch {
    return null
  }
}
