<template>
  <div>
    <div class="greeting">你好，{{ profile?.holder_name ?? '—' }}</div>

    <el-row :gutter="16" class="cards">
      <el-col :span="4"><el-card>总请求<div class="num">{{ dash.total_requests }}</div></el-card></el-col>
      <el-col :span="4"><el-card>成功<div class="num">{{ dash.succeeded }}</div></el-card></el-col>
      <el-col :span="4"><el-card>成功率<div class="num">{{ formatPercent(dash.success_rate) }}</div></el-card></el-col>
      <el-col :span="4"><el-card>累计费用<div class="num">{{ formatYuan(dash.total_cost_cents) }}</div></el-card></el-col>
      <el-col :span="4"><el-card>输入 tokens<div class="num">{{ formatNumber(dash.total_input_tokens) }}</div></el-card></el-col>
      <el-col :span="4"><el-card>输出 tokens<div class="num">{{ formatNumber(dash.total_output_tokens) }}</div></el-card></el-col>
    </el-row>

    <el-card class="keys">
      <template #header>我的 Key（只读，如需变更请联系管理员）</template>
      <el-table :data="profile?.keys ?? []" stripe>
        <el-table-column label="Key 前缀" prop="key_prefix" width="160" />
        <el-table-column label="标签" prop="label" width="160" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row).type" size="small">{{ statusTag(row).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="RPM" width="80">
          <template #default="{ row }">{{ row.rpm_limit ?? '默认' }}</template>
        </el-table-column>
        <el-table-column label="日预算" width="110">
          <template #default="{ row }">
            {{ row.daily_budget_cents === null ? '默认' : formatYuan(row.daily_budget_cents) }}
          </template>
        </el-table-column>
        <el-table-column label="并发" width="80">
          <template #default="{ row }">{{ row.max_concurrency ?? '默认' }}</template>
        </el-table-column>
        <el-table-column label="到期时间" width="180">
          <template #default="{ row }">{{ row.expires_at ? formatDateTime(row.expires_at) : '永不过期' }}</template>
        </el-table-column>
        <el-table-column label="最后使用" width="180">
          <template #default="{ row }">{{ formatDateTime(row.last_used_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { portalApi } from '@/api'
import type { PortalDashboard, PortalKeyItem, PortalProfile } from '@/api/types'
import { ApiError } from '@/api/client'
import { formatDateTime, formatNumber, formatPercent, formatYuan } from '@/utils/format'

const profile = ref<PortalProfile | null>(null)
const dash = ref<PortalDashboard>({
  total_requests: 0, succeeded: 0, success_rate: 0,
  total_cost_cents: 0, total_input_tokens: 0, total_output_tokens: 0,
})

function statusTag(row: PortalKeyItem): { type: 'success' | 'warning' | 'danger' | 'info'; text: string } {
  if (row.status === 'revoked') return { type: 'info', text: '已吊销' }
  if (row.expires_at && new Date(`${row.expires_at}Z`).getTime() <= Date.now())
    return { type: 'danger', text: '已到期' }
  if (row.disabled) return { type: 'warning', text: '已停用' }
  return { type: 'success', text: '生效中' }
}

onMounted(async () => {
  try {
    ;[profile.value, dash.value] = await Promise.all([portalApi.profile(), portalApi.dashboard()])
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  }
})
</script>

<style scoped>
.greeting { font-size: 18px; font-weight: 600; margin-bottom: 16px; }
.cards { margin-bottom: 16px; }
.num { font-size: 22px; font-weight: 700; margin-top: 6px; }
</style>
