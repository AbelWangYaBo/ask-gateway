<script setup lang="ts">
import { computed } from 'vue'

import type { RequestDetail } from '@/api/types'
import { requestsApi } from '@/api'
import {
  formatDateTime,
  formatLatency,
  formatNumber,
  formatYuan,
  statusLabel,
  statusTagType,
} from '@/utils/format'
import { buildTimeline, extractSearchChain, previewMessages } from '@/utils/timeline'

// 详情由父页面预取传入，抽屉只做展示；open 与 detail 各自双向绑定
const open = defineModel<boolean>('modelValue', { default: false })
const detail = defineModel<RequestDetail | null>('detail', { default: null })

const messages = computed(() =>
  detail.value ? previewMessages(detail.value.prompt_messages) : [],
)
const timeline = computed(() => (detail.value ? buildTimeline(detail.value.events) : []))
const searchChain = computed(() =>
  detail.value ? extractSearchChain(detail.value.events) : [],
)

function latency(row: RequestDetail): string {
  if (!row.started_at || !row.finished_at) return '—'
  const ms = new Date(row.finished_at).getTime() - new Date(row.started_at).getTime()
  return formatLatency(ms)
}

function exportUrl(): string {
  return detail.value ? requestsApi.exportUrl(detail.value.request_id) : ''
}
</script>

<template>
  <el-drawer v-model="open" title="请求详情" size="62%" destroy-on-close>
    <template v-if="detail">
      <el-descriptions :column="2" border size="small" class="block">
        <el-descriptions-item label="Request ID">{{ detail.request_id }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusTagType(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="持有人 / 标签">
          {{ detail.holder_name }} / {{ detail.key_label }}
        </el-descriptions-item>
        <el-descriptions-item label="模型">{{ detail.model }}</el-descriptions-item>
        <el-descriptions-item label="联网搜索">
          {{ detail.enable_web_search ? '开启' : '关闭' }}
        </el-descriptions-item>
        <el-descriptions-item label="思考强度">{{ detail.thinking_effort ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="上游 Key 组">
          {{ detail.upstream_key_label ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="DSH 会话">
          {{ detail.dsh_session_id ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="客户端 IP">{{ detail.client_ip ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="User-Agent">
          <span class="ua">{{ detail.user_agent ?? '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="排队时间">
          {{ formatDateTime(detail.queued_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="端到端耗时">{{ latency(detail) }}</el-descriptions-item>
        <el-descriptions-item v-if="detail.error_code" label="错误">
          {{ detail.error_code }}：{{ detail.error_message }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider content-position="left">请求全文</el-divider>
      <div v-for="(message, index) in messages" :key="index" class="message-block">
        <el-tag size="small" class="role-tag">{{ message.role }}</el-tag>
        <pre class="text">{{ message.text }}</pre>
      </div>

      <el-divider content-position="left">最终回复</el-divider>
      <pre class="text answer">{{ detail.final_answer ?? '—' }}</pre>

      <el-collapse v-if="detail.thinking" class="block">
        <el-collapse-item title="思考过程（thinking）" name="thinking">
          <pre class="text">{{ detail.thinking }}</pre>
        </el-collapse-item>
      </el-collapse>

      <el-divider content-position="left">
        联网搜索链路（{{ searchChain.length }} 次）
      </el-divider>
      <el-empty v-if="!searchChain.length" description="本次请求无联网搜索" :image-size="60" />
      <el-timeline v-else>
        <el-timeline-item
          v-for="item in searchChain"
          :key="item.callId"
          :timestamp="item.step !== null ? `step ${item.step}` : ''"
          placement="top"
        >
          <el-card shadow="never">
            <div class="query">{{ item.query || '（未解析到 query）' }}</div>
            <el-tag size="small" :type="item.hasResult ? 'success' : 'warning'">
              {{ item.hasResult ? '已返回结果' : '无结果事件' }}
            </el-tag>
            <el-collapse v-if="item.resultText" class="result-collapse">
              <el-collapse-item title="原始结果" name="result">
                <pre class="text small">{{ item.resultText }}</pre>
              </el-collapse-item>
            </el-collapse>
          </el-card>
        </el-timeline-item>
      </el-timeline>

      <el-divider content-position="left">Usage 明细</el-divider>
      <el-table :data="detail.usage_records" size="small" border>
        <el-table-column label="step" prop="step" width="70" />
        <el-table-column label="完整" width="70">
          <template #default="{ row }">
            <el-tag :type="row.complete ? 'success' : 'info'" size="small">
              {{ row.complete ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入" width="90">
          <template #default="{ row }">{{ formatNumber(row.input_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出" width="90">
          <template #default="{ row }">{{ formatNumber(row.output_tokens) }}</template>
        </el-table-column>
        <el-table-column label="缓存读" width="90">
          <template #default="{ row }">{{ formatNumber(row.cache_read_tokens) }}</template>
        </el-table-column>
        <el-table-column label="推理" width="90">
          <template #default="{ row }">{{ formatNumber(row.reasoning_tokens) }}</template>
        </el-table-column>
        <el-table-column label="搜索" prop="search_count" width="70" />
        <el-table-column label="费用" width="110">
          <template #default="{ row }">{{ formatYuan(row.cost_cents) }}</template>
        </el-table-column>
      </el-table>
      <div class="total">
        合计：输入 {{ formatNumber(detail.total_input_tokens) }} ·
        输出 {{ formatNumber(detail.total_output_tokens) }} ·
        搜索 {{ detail.search_count }} 次 ·
        费用 {{ formatYuan(detail.total_cost_cents) }}
      </div>

      <el-divider content-position="left">完整事件时间线（{{ timeline.length }}）</el-divider>
      <el-timeline>
        <el-timeline-item
          v-for="item in timeline"
          :key="item.seq"
          :timestamp="formatDateTime(item.time)"
          placement="top"
        >
          <div class="event-title">
            <span class="seq">#{{ item.seq }}</span>
            <el-tag size="small">{{ item.title }}</el-tag>
            <span class="event-type">{{ item.type }}</span>
          </div>
          <el-collapse>
            <el-collapse-item title="事件 payload" :name="String(item.seq)">
              <pre class="text small">{{ JSON.stringify(item.raw, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-timeline-item>
      </el-timeline>

      <div class="footer">
        <el-link type="primary" :href="exportUrl()" target="_blank">导出完整 JSON（含全部原始字段）</el-link>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.block {
  margin-bottom: 8px;
}
.message-block {
  margin-bottom: 12px;
}
.role-tag {
  margin-bottom: 4px;
}
.text {
  margin: 0;
  padding: 10px;
  background: #f9fafb;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  line-height: 1.6;
}
.text.small {
  max-height: 280px;
  overflow: auto;
  font-size: 12px;
}
.answer {
  border-left: 3px solid #2563eb;
}
.ua {
  word-break: break-all;
  font-size: 12px;
}
.query {
  margin-bottom: 6px;
  font-weight: 600;
}
.result-collapse {
  margin-top: 6px;
}
.event-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.seq {
  color: #6b7280;
  font-size: 12px;
}
.event-type {
  color: #9ca3af;
  font-size: 12px;
}
.total {
  margin-top: 8px;
  font-weight: 600;
}
.footer {
  margin-top: 16px;
  text-align: right;
}
</style>
