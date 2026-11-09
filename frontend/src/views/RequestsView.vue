<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { requestsApi } from '@/api'
import type { RequestDetail, RequestFilters, RequestListItem } from '@/api/types'
import { ApiError } from '@/api/client'
import RequestDetailDrawer from '@/components/RequestDetailDrawer.vue'
import {
  formatDateTime,
  formatNumber,
  formatYuan,
  statusLabel,
  statusTagType,
} from '@/utils/format'

const loading = ref(false)
const rows = ref<RequestListItem[]>([])
const total = ref(0)

const filters = reactive({
  holder_name: '',
  key_id: '',
  status: '',
  keyword: '',
  dateRange: [] as Date[],
})
const page = ref(1)
const pageSize = ref(20)

const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

async function load() {
  loading.value = true
  try {
    const params: RequestFilters = {
      holder_name: filters.holder_name || undefined,
      key_id: filters.key_id || undefined,
      status: filters.status || undefined,
      keyword: filters.keyword || undefined,
      date_from: filters.dateRange[0]
        ? filters.dateRange[0].toISOString()
        : undefined,
      date_to: filters.dateRange[1]
        ? filters.dateRange[1].toISOString()
        : undefined,
      page: page.value,
      page_size: pageSize.value,
    }
    const result = await requestsApi.list(params)
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

function reset() {
  filters.holder_name = ''
  filters.key_id = ''
  filters.status = ''
  filters.keyword = ''
  filters.dateRange = []
  page.value = 1
  void load()
}

onMounted(load)

// ---- 详情抽屉 ----

const drawerOpen = ref(false)
const detail = ref<RequestDetail | null>(null)

async function openDetail(row: RequestListItem) {
  try {
    detail.value = await requestsApi.detail(row.request_id)
    drawerOpen.value = true
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '详情加载失败')
  }
}
</script>

<template>
  <div>
    <el-card class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="持有人">
          <el-input v-model="filters.holder_name" placeholder="精确匹配" clearable />
        </el-form-item>
        <el-form-item label="Key ID">
          <el-input v-model="filters.key_id" placeholder="UUID" clearable />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 130px">
            <el-option
              v-for="option in statusOptions.filter((o) => o.value)"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" placeholder="提问/回复全文检索" clearable />
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="filters.dateRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="rows" stripe @row-click="openDetail" class="request-table">
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.queued_at) }}</template>
        </el-table-column>
        <el-table-column label="持有人" prop="holder_name" width="120" />
        <el-table-column label="标签" prop="key_label" width="140" />
        <el-table-column label="模型" prop="model" width="150" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入 / 输出 token" width="160">
          <template #default="{ row }">
            {{ formatNumber(row.total_input_tokens) }} / {{ formatNumber(row.total_output_tokens) }}
          </template>
        </el-table-column>
        <el-table-column label="搜索" prop="search_count" width="70" />
        <el-table-column label="费用" width="110">
          <template #default="{ row }">{{ formatYuan(row.total_cost_cents) }}</template>
        </el-table-column>
        <el-table-column label="错误码" prop="error_code" width="120" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        layout="total, sizes, prev, pager, next, jumper"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        @current-change="(p: number) => { page = p; load() }"
        @size-change="(s: number) => { pageSize = s; page = 1; load() }"
      />
    </el-card>

    <RequestDetailDrawer v-model="drawerOpen" v-model:detail="detail" />
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 12px;
}
.request-table :deep(.el-table__row) {
  cursor: pointer;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
