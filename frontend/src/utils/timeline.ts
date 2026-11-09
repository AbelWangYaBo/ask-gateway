// 审计详情适配：把后端事件流转成时间线、搜索链路与消息预览。
// DSH 原始 payload 形状可能随 SDK 版本变化，全部防御式读取，不抛异常。

import type { RequestEvent } from '@/api/types'

export interface TimelineItem {
  seq: number
  type: string
  time: string
  title: string
  // 供折叠面板展示的原始 JSON
  raw: Record<string, unknown>
}

const TYPE_TITLES: Record<string, string> = {
  'turn/start': '回合开始',
  'turn/end': '回合结束',
  'step/start': '步骤开始',
  'step/end': '步骤结束',
  'assistant/attempt': '模型尝试',
  'assistant/message': '模型消息',
  'tool/call': '工具调用',
  'tool/result': '工具结果',
  thinking: '思考过程',
  text: '文本输出',
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

export function buildTimeline(events: RequestEvent[]): TimelineItem[] {
  return events.map((event) => ({
    seq: event.seq,
    type: event.event_type,
    time: event.created_at,
    title: TYPE_TITLES[event.event_type] ?? event.event_type,
    raw: event.payload,
  }))
}

export interface SearchChainItem {
  callId: string
  step: number | null
  query: string
  argumentsText: string
  hasResult: boolean
  resultText: string
}

function parseArguments(raw: unknown): Record<string, unknown> {
  if (typeof raw !== 'string' || raw.trim() === '') return {}
  try {
    const parsed = JSON.parse(raw)
    return asRecord(parsed)
  } catch {
    return {}
  }
}

function stringifyResult(raw: unknown): string {
  if (raw === null || raw === undefined) return ''
  if (typeof raw === 'string') return raw
  try {
    return JSON.stringify(raw, null, 2)
  } catch {
    return String(raw)
  }
}

// web_search 调用与结果按 callId 配对；无结果时保留调用半链，体现未完成的调用
export function extractSearchChain(events: RequestEvent[]): SearchChainItem[] {
  const calls = new Map<string, SearchChainItem>()
  const order: string[] = []

  for (const event of events) {
    const data = asRecord(event.payload?.data)
    if (event.event_type === 'tool/call' && data.name === 'web_search') {
      const callId = String(data.callId ?? '')
      const args = parseArguments(data.arguments)
      const query =
        typeof args.query === 'string'
          ? args.query
          : typeof args.keyword === 'string'
            ? args.keyword
            : ''
      calls.set(callId, {
        callId,
        step: typeof data.step === 'number' ? data.step : null,
        query,
        argumentsText: stringifyResult(data.arguments),
        hasResult: false,
        resultText: '',
      })
      order.push(callId)
    } else if (event.event_type === 'tool/result' && calls.has(String(data.callId ?? ''))) {
      const item = calls.get(String(data.callId))!
      item.hasResult = true
      item.resultText = stringifyResult(data.result ?? data.output ?? data)
    }
  }
  return order.map((id) => calls.get(id)!)
}

export interface MessagePreview {
  role: string
  text: string
}

// prompt_messages 为 OpenAI 风格消息数组；content 可能是字符串或 content block 数组
export function previewMessages(messages: Array<Record<string, unknown>>): MessagePreview[] {
  return messages.map((message) => {
    const role = typeof message.role === 'string' ? message.role : 'unknown'
    const content = message.content
    let text = ''
    if (typeof content === 'string') {
      text = content
    } else if (Array.isArray(content)) {
      text = content
        .map((block) => {
          const record = asRecord(block)
          if (typeof record.text === 'string') return record.text
          return stringifyResult(block)
        })
        .join('\n')
    } else {
      text = stringifyResult(content)
    }
    return { role, text }
  })
}
