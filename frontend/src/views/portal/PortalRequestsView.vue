<template>
  <div>
    <el-card class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="状态">
          <el-select v-model="status" clearable placeholder="全部状态" style="width: 130px" @change="search">
            <el-option value="succeeded" label="成功" />
            <el-option value="failed" label="失败" />
            <el-option value="client_disconnected" label="断连" />
            <el-option value="running" label="运行中" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间">
          <el-date-picker
            v-model="dateRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.queued_at) }}</template>
        </el-table-column>
        <el-table-column label="Key" prop="key_label" width="120" />
        <el-table-column label="模型" prop="model" width="150" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入" width="90">
          <template #default="{ row }">{{ formatNumber(row.total_input_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出" width="90">
          <template #default="{ row }">{{ formatNumber(row.total_output_tokens) }}</template>
        </el-table-column>
        <el-table-column label="搜索" width="70">
          <template #default="{ row }">{{ row.search_count }}</template>
        </el-table-column>
        <el-table-column label="费用" width="110">
          <template #default="{ row }">{{ formatYuan(row.total_cost_cents) }}</template>
        </el-table-column>
        <el-table-column label="提问" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ firstPrompt(row) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="pager"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="(p: number) => { page = p; void load() }"
      />
    </el-card>

    <RequestDetailDrawer v-model="drawerOpen" :detail="detail" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { portalApi } from '@/api'
import type { RequestDetail, RequestListItem } from '@/api/types'
import { ApiError } from '@/api/client'
import RequestDetailDrawer from '@/components/RequestDetailDrawer.vue'
import { formatDateTime, formatNumber, formatYuan, statusLabel, statusTagType } from '@/utils/format'

const loading = ref(false)
const rows = ref<RequestListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const status = ref('')
const dateRange = ref<Date[]>([])

const drawerOpen = ref(false)
const detail = ref<RequestDetail | null>(null)

function firstPrompt(row: RequestListItem): string {
  const prompt = (row as unknown as { prompt_preview?: string }).prompt_preview
  return prompt ?? '—'
}

async function load() {
  loading.value = true
  try {
    const result = await portalApi.requests({
      status: status.value || undefined,
      date_from: dateRange.value[0]?.toISOString(),
      date_to: dateRange.value[1]?.toISOString(),
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  void load()
}

async function openDetail(row: RequestListItem) {
  try {
    detail.value = await portalApi.requestDetail(row.request_id)
    drawerOpen.value = true
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  }
}

onMounted(load)
</script>

<style scoped>
.filter-card { margin-bottom: 12px; }
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
