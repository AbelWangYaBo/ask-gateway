<template>
  <div class="chat-shell">
    <!-- 对话列表侧栏 -->
    <el-aside width="240px" class="sidebar">
      <el-button type="primary" size="small" style="width: 100%" @click="newConversation">
        ＋ 新对话
      </el-button>
      <div class="conv-list">
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === currentId }"
          @click="openConversation(c.id)"
        >
          <div class="conv-title">
            <span v-if="c.forked_from_id" class="fork-mark">⑂</span>
            {{ c.title }}
          </div>
          <el-button
            link
            size="small"
            class="conv-del"
            @click.stop="removeConversation(c.id)"
          >
            删
          </el-button>
        </div>
      </div>
    </el-aside>

    <!-- 当前对话 -->
    <div class="chat-page">
      <div class="chat-head">
        <span class="conv-name">{{ current?.title ?? '未选择对话' }}</span>
        <el-select v-model="model" style="width: 180px" size="small">
          <el-option value="deepseek-flash" label="deepseek-flash（快）" />
          <el-option value="deepseek-v4-pro" label="deepseek-v4-pro（强）" />
        </el-select>
        <el-switch v-model="webSearch" active-text="联网搜索" size="small" style="margin-left: 16px" />
      </div>

      <div ref="scrollRef" class="chat-body">
        <div v-if="!messages.length" class="empty">
          {{ currentId ? '开始对话吧；历史与分支都会保存在这个对话里。' : '左侧选择或新建一个对话开始。' }}
        </div>
        <div v-for="msg in messages" :key="msg.seq" class="msg" :class="msg.role">
          <div class="bubble">
            <div v-if="msg.role === 'assistant' && msg.reasoning" class="reasoning">
              <el-collapse>
                <el-collapse-item :title="`思考过程（${msg.reasoning.length} 字）`">
                  <pre class="reasoning-text">{{ msg.reasoning }}</pre>
                </el-collapse-item>
              </el-collapse>
            </div>
            <div v-if="msg.role === 'assistant' && msg.streaming && !msg.content" class="status">
              <span class="pulse" />
              {{ msg.status || '正在思考 …' }}
              <span v-if="msg.elapsed">已进行 {{ msg.elapsed }}s</span>
            </div>
            <pre class="content">{{ msg.content }}</pre>
            <div v-if="msg.usage" class="usage">{{ msg.usage }}</div>
            <div v-if="msg.role === 'assistant' && !msg.streaming" class="bubble-actions">
              <el-button link size="small" @click="forkAt(msg.seq)">⑂ 从这里分叉</el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          :disabled="streaming || !currentId"
          :placeholder="currentId ? '输入问题，Enter 发送 / Shift+Enter 换行' : '请先在左侧选择或新建对话'"
          @keydown.enter.exact.prevent="send"
        />
        <el-button v-if="!streaming" type="primary" :disabled="!draft.trim() || !currentId" @click="send">
          发送
        </el-button>
        <el-button v-else type="danger" plain @click="abort">停止</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { conversationApi } from '@/api'
import type { Conversation } from '@/api/types'
import { ApiError } from '@/api/client'
import { parseChatDelta, SseParser } from '@/utils/sse'

interface DisplayMessage {
  seq: number
  role: 'user' | 'assistant'
  content: string
  reasoning: string | null
  status?: string
  elapsed?: number
  streaming?: boolean
  usage?: string
}

const conversations = ref<Conversation[]>([])
const currentId = ref<string | null>(null)
const current = ref<Conversation | null>(null)
const messages = ref<DisplayMessage[]>([])
const model = ref('deepseek-flash')
const webSearch = ref(true)
const draft = ref('')
const streaming = ref(false)
const scrollRef = ref<HTMLElement | null>(null)

let controller: AbortController | null = null

function scrollBottom() {
  void nextTick(() => {
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
  })
}

async function loadList() {
  try {
    conversations.value = await conversationApi.list()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载对话列表失败')
  }
}

async function newConversation() {
  try {
    const created = await conversationApi.create()
    conversations.value.unshift(created)
    await select(created)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '新建失败')
  }
}

async function openConversation(id: string) {
  if (streaming.value) return
  await select(conversations.value.find((c) => c.id === id))
}

async function select(conversation: Conversation | undefined) {
  if (!conversation) return
  currentId.value = conversation.id
  current.value = conversation
  try {
    const detail = await conversationApi.detail(conversation.id)
    current.value = detail.conversation
    messages.value = detail.messages.map((m) => ({
      seq: m.seq,
      role: m.role,
      content: m.content,
      reasoning: m.reasoning,
    }))
    scrollBottom()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载对话失败')
  }
}

async function removeConversation(id: string) {
  try {
    await ElMessageBox.confirm('删除该对话及其全部分支内容？', '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await conversationApi.remove(id)
    conversations.value = conversations.value.filter((c) => c.id !== id)
    if (currentId.value === id) {
      currentId.value = null
      current.value = null
      messages.value = []
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '删除失败')
  }
}

/** 从某条 assistant 消息之后分叉出新对话（可反复从同一点分叉） */
async function forkAt(seq: number) {
  if (streaming.value || !currentId.value) return
  try {
    const forked = await conversationApi.fork(currentId.value, seq)
    ElMessage.success(`已从第 ${seq} 条消息分叉`)
    await loadList()
    await select(forked)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '分叉失败')
  }
}

function describeDshEvent(type: string): string | null {
  if (type === 'tool/call') return '正在调用工具 …'
  if (type.startsWith('web/') || type.includes('search')) return '正在联网搜索 …'
  if (type === 'thinking' || type === 'assistant/message') return '正在思考 …'
  if (type.startsWith('step/') || type.startsWith('turn/')) return '正在处理 …'
  return null
}

async function send() {
  const text = draft.value.trim()
  if (!text || streaming.value || !currentId.value) return
  draft.value = ''

  messages.value.push({ seq: -1, role: 'user', content: text, reasoning: null })
  const assistant: DisplayMessage = {
    seq: -2,
    role: 'assistant',
    content: '',
    reasoning: null,
    streaming: true,
  }
  messages.value.push(assistant)
  streaming.value = true
  scrollBottom()

  const startedAt = Date.now()
  const timer = window.setInterval(() => {
    if (assistant.streaming) assistant.elapsed = Math.round((Date.now() - startedAt) / 1000)
  }, 1000)

  controller = new AbortController()
  try {
    const response = await conversationApi.chatStream(
      currentId.value,
      { content: text, model: model.value, enable_web_search: webSearch.value },
      controller.signal,
    )
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

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }), (message) => {
        if (message.data === '[DONE]') return
        try {
          const payload = JSON.parse(message.data) as {
            choices?: Array<{ delta?: Record<string, string | null> }>
            usage?: { prompt_tokens?: number; completion_tokens?: number }
          }
          if (message.event === 'x-dsh-event') {
            const described = describeDshEvent((payload as { type?: string }).type ?? '')
            if (described) assistant.status = described
          } else {
            const delta = parseChatDelta(message.data)
            if (delta) {
              assistant.content += delta.content
              assistant.reasoning = (assistant.reasoning ?? '') + delta.reasoning
              if (delta.content) assistant.status = undefined
            }
          }
          if (payload.usage) {
            usageText = `${payload.usage.prompt_tokens ?? 0} in / ${payload.usage.completion_tokens ?? 0} out`
          }
          scrollBottom()
        } catch {
          // 忽略非 JSON 帧
        }
      })
    }
    parser.end(() => undefined)
    assistant.usage = usageText
  } catch (error) {
    if ((error as Error).name === 'AbortError') {
      assistant.content += '\n（已停止）'
    } else {
      assistant.content = error instanceof ApiError ? `⚠ ${error.message}` : '⚠ 请求失败'
      ElMessage.error(error instanceof ApiError ? error.message : '请求失败')
    }
  } finally {
    window.clearInterval(timer)
    assistant.streaming = false
    assistant.status = undefined
    assistant.elapsed = undefined
    streaming.value = false
    controller = null
    // 服务端已把最终回答落进对话；重载拿权威内容与分叉点
    if (currentId.value) await select(current.value ?? undefined)
    scrollBottom()
  }
}

function abort() {
  controller?.abort()
}

onBeforeUnmount(() => controller?.abort())

onMounted(async () => {
  await loadList()
  if (conversations.value.length) await select(conversations.value[0])
})
</script>

<style scoped>
.chat-shell {
  display: flex;
  gap: 12px;
  height: calc(100vh - 100px);
  min-height: 480px;
}
.sidebar {
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  margin-top: 10px;
}
.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.conv-item:hover {
  background: #f3f4f6;
}
.conv-item.active {
  background: #dbeafe;
}
.conv-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fork-mark {
  color: #f59e0b;
  margin-right: 4px;
}
.conv-del {
  visibility: hidden;
}
.conv-item:hover .conv-del {
  visibility: visible;
}
.chat-page {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.chat-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.conv-name {
  font-weight: 600;
  font-size: 15px;
  margin-right: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 40%;
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
.bubble-actions {
  margin-top: 6px;
  opacity: 0.7;
}
.usage {
  margin-top: 6px;
  font-size: 12px;
  color: #9ca3af;
}
.status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #6b7280;
  font-size: 13px;
}
.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #3b82f6;
  animation: blink 1.2s ease-in-out infinite;
}
@keyframes blink {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}
.chat-input {
  display: flex;
  gap: 12px;
  margin-top: 10px;
  align-items: flex-end;
}
</style>
