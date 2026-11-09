<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { keysApi } from '@/api'
import type { ApiKey, ApiKeyCreated, KeyCreatePayload, QuotaPayload } from '@/api/types'
import { ApiError } from '@/api/client'
import { formatDateTime, formatYuan } from '@/utils/format'

const loading = ref(false)
const rows = ref<ApiKey[]>([])

async function load() {
  loading.value = true
  try {
    rows.value = await keysApi.list()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ---- 创建 ----

const createVisible = ref(false)
const createSubmitting = ref(false)
const createForm = reactive<KeyCreatePayload>({
  holder_name: '',
  label: '',
  rpm_limit: null,
  daily_budget_cents: null,
  max_concurrency: null,
})

function openCreate() {
  createForm.holder_name = ''
  createForm.label = ''
  createForm.rpm_limit = null
  createForm.daily_budget_cents = null
  createForm.max_concurrency = null
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.holder_name || !createForm.label) {
    ElMessage.warning('持有人与标签必填')
    return
  }
  createSubmitting.value = true
  try {
    const payload: KeyCreatePayload = {
      holder_name: createForm.holder_name,
      label: createForm.label,
      rpm_limit: createForm.rpm_limit ?? null,
      daily_budget_cents: createForm.daily_budget_cents ?? null,
      max_concurrency: createForm.max_concurrency ?? null,
    }
    const created: ApiKeyCreated = await keysApi.create(payload)
    createVisible.value = false
    createdPlain.value = created
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '创建失败')
  } finally {
    createSubmitting.value = false
  }
}

// ---- 一次性明文 ----

const createdPlain = ref<ApiKeyCreated | null>(null)

async function copyPlaintext() {
  if (!createdPlain.value) return
  await navigator.clipboard.writeText(createdPlain.value.plaintext)
  ElMessage.success('已复制到剪贴板')
}

// ---- 吊销 ----

async function revoke(row: ApiKey) {
  try {
    await ElMessageBox.confirm(
      `确认吊销 ${row.holder_name} / ${row.label} 的 Key？吊销后该 Key 立即失效且不可恢复。`,
      '吊销确认',
      { type: 'warning', confirmButtonText: '确认吊销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await keysApi.revoke(row.id)
    ElMessage.success('已吊销')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '吊销失败')
  }
}

// ---- 配额编辑 ----

const quotaVisible = ref(false)
const quotaSubmitting = ref(false)
const quotaTarget = ref<ApiKey | null>(null)
const quotaForm = reactive<{
  rpm_limit: number | null
  daily_budget_cents: number | null
  max_concurrency: number | null
}>({
  rpm_limit: null,
  daily_budget_cents: null,
  max_concurrency: null,
})

function openQuota(row: ApiKey) {
  quotaTarget.value = row
  quotaForm.rpm_limit = row.rpm_limit
  quotaForm.daily_budget_cents = row.daily_budget_cents
  quotaForm.max_concurrency = row.max_concurrency
  quotaVisible.value = true
}

async function submitQuota() {
  if (!quotaTarget.value) return
  // 全量提交当前表单值；显式 null 表示清除并回落全局默认，与后端 exclude_unset 语义对齐
  // el-input-number 清空可能给出 undefined；统一归一成 null，保证显式清除语义
  const payload: QuotaPayload = {
    rpm_limit: quotaForm.rpm_limit ?? null,
    daily_budget_cents: quotaForm.daily_budget_cents ?? null,
    max_concurrency: quotaForm.max_concurrency ?? null,
  }
  quotaSubmitting.value = true
  try {
    await keysApi.updateQuota(quotaTarget.value.id, payload)
    ElMessage.success('配额已更新')
    quotaVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '更新失败')
  } finally {
    quotaSubmitting.value = false
  }
}
</script>

<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建 Key</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-card>
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="Key 前缀" prop="key_prefix" width="160" />
        <el-table-column label="持有人" prop="holder_name" width="140" />
        <el-table-column label="标签" prop="label" width="160" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'">
              {{ row.status === 'active' ? '生效中' : '已吊销' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="RPM 上限" width="100">
          <template #default="{ row }">{{ row.rpm_limit ?? '默认' }}</template>
        </el-table-column>
        <el-table-column label="每日预算" width="130">
          <template #default="{ row }">
            {{ row.daily_budget_cents === null ? '默认' : formatYuan(row.daily_budget_cents) }}
          </template>
        </el-table-column>
        <el-table-column label="并发上限" width="100">
          <template #default="{ row }">{{ row.max_concurrency ?? '默认' }}</template>
        </el-table-column>
        <el-table-column label="最后使用" width="180">
          <template #default="{ row }">{{ formatDateTime(row.last_used_at) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.status !== 'active'" @click="openQuota(row)">
              配额
            </el-button>
            <el-button
              link
              type="danger"
              :disabled="row.status !== 'active'"
              @click="revoke(row)"
            >
              吊销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建对话框 -->
    <el-dialog v-model="createVisible" title="新建 API Key" width="520px">
      <el-form label-position="top">
        <el-form-item label="持有人" required>
          <el-input v-model="createForm.holder_name" placeholder="例如：用户 A" />
        </el-form-item>
        <el-form-item label="标签" required>
          <el-input v-model="createForm.label" placeholder="例如：A（系统 1）" />
        </el-form-item>
        <el-form-item label="RPM 上限（留空走全局默认）">
          <el-input-number v-model="createForm.rpm_limit" :min="1" controls-position="right" />
        </el-form-item>
        <el-form-item label="每日预算（分，留空走全局默认）">
          <el-input-number
            v-model="createForm.daily_budget_cents"
            :min="0"
            :step="100"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="并发上限（留空走全局默认）">
          <el-input-number v-model="createForm.max_concurrency" :min="1" controls-position="right" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createSubmitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 一次性明文对话框 -->
    <el-dialog
      :model-value="createdPlain !== null"
      title="Key 明文（仅显示这一次）"
      width="560px"
      :close-on-click-modal="false"
      @update:model-value="(open: boolean) => { if (!open) createdPlain = null }"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="平台只保存 Key 的 SHA-256 哈希，关闭后无法再次查看明文，请立即复制保存。"
        class="warn"
      />
      <el-input v-if="createdPlain" :model-value="createdPlain.plaintext" readonly>
        <template #append>
          <el-button @click="copyPlaintext">复制</el-button>
        </template>
      </el-input>
      <template #footer>
        <el-button type="primary" @click="createdPlain = null">我已妥善保存</el-button>
      </template>
    </el-dialog>

    <!-- 配额编辑对话框 -->
    <el-dialog v-model="quotaVisible" title="编辑配额（留空回落全局默认）" width="480px">
      <el-form label-position="top">
        <el-form-item label="RPM 上限">
          <el-input-number v-model="quotaForm.rpm_limit" :min="1" controls-position="right" />
        </el-form-item>
        <el-form-item label="每日预算（分）">
          <el-input-number
            v-model="quotaForm.daily_budget_cents"
            :min="0"
            :step="100"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="并发上限">
          <el-input-number v-model="quotaForm.max_concurrency" :min="1" controls-position="right" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="quotaVisible = false">取消</el-button>
        <el-button type="primary" :loading="quotaSubmitting" @click="submitQuota">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}
.warn {
  margin-bottom: 12px;
}
</style>
