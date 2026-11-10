<template>
  <div class="chat-page">
    <div class="chat-head">
      <el-select v-model="model" style="width: 180px" size="small">
        <el-option value="deepseek-flash" label="deepseek-flash（快）" />
        <el-option value="deepseek-v4-pro" label="deepseek-v4-pro（强）" />
      </el-select>
      <el-switch
        v-model="webSearch"
        active-text="联网搜索"
        size="small"
        style="margin-left: 16px"
      />
      <el-button size="small" style="margin-left: auto" @click="clearChat">清空对话</el-button>
    </div>

    <div ref="scrollRef" class="chat-body">
      <div v-if="!messages.length" class="empty">
        开始对话吧。多轮对话已支持：本轮会带上之前的上下文。
      </div>
      <div v-for="(msg, i) in messages" :key="i" class="msg" :class="msg.role">
        <div class="bubble">
          <div v-if="msg.role === 'assistant' && msg.reasoning" class="reasoning">
            <el-collapse>
              <el-collapse-item :title="`思考过程（${msg.reasoning.length} 字）`">
                <pre class="reasoning-text">{{ msg.reasoning }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
          <pre class="content">{{ msg.content || (msg.streaming ? '…' : '') }}</pre>
          <div v-if="msg.usage" class="usage">
            {{ msg.usage }} · {{ msg.searchCount ? `联网搜索 ×${msg.searchCount}` : '未联网' }}
          </div>
        </div>
      </div>
    </div>

    <div class="chat-input">
      <el-input
        v-model="draft"
        type="textarea"
        :rows="2"
        placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
        :disabled="streaming"
        @keydown.enter.exact.prevent="send"
      />
      <el-button
        v-if="!streaming"
        type="primary"
        :disabled="!draft.trim()"
        @click="send"
      >
        发送
      </el-button>
      <el-button v-else type="danger" plain @click="abort">停止</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { portalHttp } from '@/api/client'
import { ApiError } from '@/api/client'
import { readPortalKey } from '@/api/client'
import { parseChatDelta, SseParser } from '@/utils/sse'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  reasoning: string
  streaming?: boolean
  usage?: string
  searchCount?: number
}

const model = ref('deepseek-flash')
const webSearch = ref(true)
const messages = ref<ChatMessage[]>([])
const draft = ref('')
const streaming = ref(false)
const scrollRef = ref<HTMLElement | null>(null)

let controller: AbortController | null = null

function clearChat() {
  if (streaming.value) return
  messages.value = []
}

function scrollBottom() {
  void nextTick(() => {
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
  })
}

async function send() {
  const text = draft.value.trim()
  if (!text || streaming.value) return
  draft.value = ''

  // OpenAI 标准多轮：历史全量随请求上送，网关转录进上下文
  const history = messages.value.map((m) => ({ role: m.role, content: m.content }))
  messages.value.push({ role: 'user', content: text, reasoning: '' })
  const assistant: ChatMessage = { role: 'assistant', content: '', reasoning: '', streaming: true }
  messages.value.push(assistant)
  streaming.value = true
  scrollBottom()

  controller = new AbortController()
  try {
    const response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${readPortalKey() ?? ''}`,
      },
      body: JSON.stringify({
        model: model.value,
        stream: true,
        enable_web_search: webSearch.value,
        messages: [...history, { role: 'user', content: text }],
      }),
      signal: controller.signal,
    })
    if (!response.ok || !response.body) {
      const envelope = await response.json().catch(() => null)
      throw new ApiError(
        response.status,
        envelope?.error?.code ?? 'upstream_error',
        envelope?.error?.message ?? `HTTP ${response.status}`,
      )
    }

    const parser = new SseParser()
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let usageText = ''
    let searchCount = 0

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }), (message) => {
        if (message.data === '[DONE]') return
        try {
          const payload = JSON.parse(message.data) as {
            choices?: Array<{ delta?: Record<string, string | null> }>
            usage?: { prompt_tokens?: number; completion_tokens?: number; search_count?: number }
          }
          if (message.event === null || message.event === 'message') {
            const delta = parseChatDelta(message.data)
            if (delta) {
              assistant.content += delta.content
              assistant.reasoning += delta.reasoning
              scrollBottom()
            }
          }
          if (payload.usage) {
            usageText = `${payload.usage.prompt_tokens ?? 0} in / ${payload.usage.completion_tokens ?? 0} out`
            searchCount = payload.usage.search_count ?? 0
          }
        } catch {
          // 忽略非 JSON 心跳
        }
      })
    }
    parser.end(() => undefined)
    assistant.usage = usageText
    assistant.searchCount = searchCount
  } catch (error) {
    if ((error as Error).name === 'AbortError') {
      assistant.content += '\n（已停止）'
    } else {
      assistant.content = error instanceof ApiError ? `⚠ ${error.message}` : '⚠ 请求失败'
      ElMessage.error(error instanceof ApiError ? error.message : '请求失败')
    }
  } finally {
    assistant.streaming = false
    streaming.value = false
    controller = null
    scrollBottom()
  }
}

function abort() {
  controller?.abort()
}

onBeforeUnmount(() => controller?.abort())

// 静态引用，避免 portalHttp 在本文件被 tree-shaking 误报（发送走 fetch 以支持流式）
void portalHttp
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  min-height: 480px;
}
.chat-head {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
}
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
.empty {
  color: #9ca3af;
  text-align: center;
  padding: 40px 0;
}
.msg {
  display: flex;
  margin-bottom: 12px;
}
.msg.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 10px;
  background: #f3f4f6;
}
.msg.user .bubble {
  background: #dbeafe;
}
.content {
  margin: 0;
  font-family: inherit;
  white-space: pre-wrap;
  word-break: break-word;
}
.reasoning {
  border-bottom: 1px dashed #d1d5db;
  margin-bottom: 8px;
  padding-bottom: 6px;
}
.reasoning-text {
  white-space: pre-wrap;
  font-size: 12px;
  color: #6b7280;
}
.usage {
  margin-top: 6px;
  font-size: 12px;
  color: #9ca3af;
}
.chat-input {
  display: flex;
  gap: 12px;
  margin-top: 10px;
  align-items: flex-end;
}
</style>
