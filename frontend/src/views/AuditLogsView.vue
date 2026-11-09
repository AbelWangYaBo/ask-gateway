<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'

import { auditLogApi } from '@/api'
import type { AuditLog } from '@/api/types'
import { ApiError } from '@/api/client'
import { formatDateTime } from '@/utils/format'

const loading = ref(false)
const rows = ref<AuditLog[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const action = ref('')

const actionOptions = [
  { value: '', label: '全部动作' },
  { value: 'login', label: '登录' },
  { value: 'key_create', label: '创建 Key' },
  { value: 'key_revoke', label: '吊销 Key' },
  { value: 'key_update', label: '修改配额' },
  { value: 'pricing_update', label: '定价变更' },
  { value: 'audit_view', label: '审计查看' },
  { value: 'audit_export', label: '审计导出' },
]

async function load() {
  loading.value = true
  try {
    const result = await auditLogApi.list(action.value || undefined, page.value, pageSize.value)
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <el-card class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="动作">
          <el-select v-model="action" style="width: 160px" @change="page = 1; load()">
            <el-option
              v-for="option in actionOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="动作" prop="action" width="150">
          <template #default="{ row }">
            <el-tag size="small">{{ row.action }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="对象类型" prop="target_type" width="130" />
        <el-table-column label="对象 ID" prop="target_id" width="280" show-overflow-tooltip />
        <el-table-column label="来源 IP" prop="client_ip" width="140" />
        <el-table-column label="详情">
          <template #default="{ row }">
            <code class="detail">{{ JSON.stringify(row.detail) }}</code>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        layout="total, prev, pager, next, jumper"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        @current-change="(p: number) => { page = p; load() }"
      />
    </el-card>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 12px;
}
.detail {
  font-size: 12px;
  word-break: break-all;
  white-space: pre-wrap;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
