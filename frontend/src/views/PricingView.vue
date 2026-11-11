<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { pricingApi } from '@/api'
import type { PricingPayload, PricingRule } from '@/api/types'
import { ApiError } from '@/api/client'
import { formatDateTime } from '@/utils/format'

const loading = ref(false)
const rows = ref<PricingRule[]>([])

async function load() {
  loading.value = true
  try {
    rows.value = await pricingApi.list()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

const dialogVisible = ref(false)
const submitting = ref(false)
// null = 新建；非 null = 编辑该规则
const editingId = ref<string | null>(null)
const form = reactive({
  model: 'deepseek-flash',
  rule_name: '',
  peak_start_hour: null as number | null,
  peak_end_hour: null as number | null,
  weekdays_only: false,
  effective_from: new Date(),
  effective_to: null as Date | null,
  priority: 0,
  input_price_per_mtok: null as number | null,
  output_price_per_mtok: null as number | null,
  cache_read_price_per_mtok: null as number | null,
  search_price_per_k: 0,
  is_active: true,
})

function openCreate() {
  editingId.value = null
  form.model = 'deepseek-flash'
  form.rule_name = ''
  form.peak_start_hour = null
  form.peak_end_hour = null
  form.weekdays_only = false
  form.effective_from = new Date()
  form.effective_to = null
  form.priority = 0
  form.input_price_per_mtok = null
  form.output_price_per_mtok = null
  form.cache_read_price_per_mtok = null
  form.search_price_per_k = 0
  form.is_active = true
  dialogVisible.value = true
}

function openEdit(row: PricingRule) {
  editingId.value = row.id
  form.model = row.model
  form.rule_name = row.rule_name
  form.peak_start_hour = row.peak_start_hour
  form.peak_end_hour = row.peak_end_hour
  form.weekdays_only = row.weekdays_only
  form.effective_from = new Date(row.effective_from)
  form.effective_to = row.effective_to ? new Date(row.effective_to) : null
  form.priority = row.priority
  form.input_price_per_mtok = Number(row.input_price_per_mtok)
  form.output_price_per_mtok = Number(row.output_price_per_mtok)
  form.cache_read_price_per_mtok =
    row.cache_read_price_per_mtok === null ? null : Number(row.cache_read_price_per_mtok)
  form.search_price_per_k = Number(row.search_price_per_k)
  form.is_active = row.is_active
  dialogVisible.value = true
}

async function submit() {
  if (!form.rule_name || form.input_price_per_mtok === null || form.output_price_per_mtok === null) {
    ElMessage.warning('规则名、输入/输出单价必填')
    return
  }
  // 窗口两端必须同时提供或同时留空，后端有同等校验
  if ((form.peak_start_hour === null) !== (form.peak_end_hour === null)) {
    ElMessage.warning('高峰窗口起止小时需同时填写或同时留空')
    return
  }
  if (form.effective_to && form.effective_to <= form.effective_from) {
    ElMessage.warning('生效截止必须晚于生效时间')
    return
  }
  const payload: PricingPayload = {
    model: form.model,
    rule_name: form.rule_name,
    peak_start_hour: form.peak_start_hour,
    peak_end_hour: form.peak_end_hour,
    weekdays_only: form.weekdays_only,
    effective_from: form.effective_from.toISOString(),
    effective_to: form.effective_to ? form.effective_to.toISOString() : null,
    priority: form.priority,
    input_price_per_mtok: form.input_price_per_mtok,
    output_price_per_mtok: form.output_price_per_mtok,
    cache_read_price_per_mtok: form.cache_read_price_per_mtok,
    search_price_per_k: form.search_price_per_k,
    is_active: form.is_active,
  }
  submitting.value = true
  try {
    if (editingId.value) {
      await pricingApi.update(editingId.value, payload)
      ElMessage.success('规则已保存')
    } else {
      await pricingApi.create(payload)
      ElMessage.success('规则已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '保存失败')
  } finally {
    submitting.value = false
  }
}

/** 快速启停：以该行现行值整体提交，仅翻转 is_active */
async function toggleActive(row: PricingRule) {
  const payload: PricingPayload = {
    model: row.model,
    rule_name: row.rule_name,
    peak_start_hour: row.peak_start_hour,
    peak_end_hour: row.peak_end_hour,
    weekdays_only: row.weekdays_only,
    effective_from: row.effective_from,
    effective_to: row.effective_to,
    priority: row.priority,
    input_price_per_mtok: row.input_price_per_mtok,
    output_price_per_mtok: row.output_price_per_mtok,
    cache_read_price_per_mtok: row.cache_read_price_per_mtok,
    search_price_per_k: row.search_price_per_k,
    is_active: !row.is_active,
  }
  try {
    await pricingApi.update(row.id, payload)
    ElMessage.success(row.is_active ? '已停用' : '已启用')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '操作失败')
  }
}

function windowText(row: PricingRule): string {
  if (row.peak_start_hour === null || row.peak_end_hour === null) return '全时段'
  const scope = row.weekdays_only ? '工作日' : '每日'
  return `${scope} ${row.peak_start_hour}:00–${row.peak_end_hour}:00（北京时区）`
}

function periodText(row: PricingRule): string {
  return row.effective_to
    ? `${formatDateTime(row.effective_from)} → ${formatDateTime(row.effective_to)}`
    : `${formatDateTime(row.effective_from)} → 长期`
}
</script>

<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建规则</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-card>
      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column label="模型" prop="model" width="160" />
        <el-table-column label="规则名" prop="rule_name" width="200" />
        <el-table-column label="生效窗口" width="240">
          <template #default="{ row }">{{ windowText(row) }}</template>
        </el-table-column>
        <el-table-column label="仅工作日" width="90">
          <template #default="{ row }">{{ row.weekdays_only ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">
              {{ row.is_active ? '生效中' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入(分/百万)" prop="input_price_per_mtok" width="130" />
        <el-table-column label="输出(分/百万)" prop="output_price_per_mtok" width="130" />
        <el-table-column label="缓存读(分/百万)" width="140">
          <template #default="{ row }">{{ row.cache_read_price_per_mtok ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="搜索(分/千次)" prop="search_price_per_k" width="130" />
        <el-table-column label="权重" prop="priority" width="70" />
        <el-table-column label="生效周期" width="320">
          <template #default="{ row }">{{ periodText(row) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link :type="row.is_active ? 'danger' : 'success'" size="small" @click="toggleActive(row)">
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑定价规则' : '新建定价规则'" width="560px">
      <el-form label-position="top">
        <el-form-item label="模型" required>
          <el-input v-model="form.model" placeholder="deepseek-flash / deepseek-v4-pro" />
        </el-form-item>
        <el-form-item label="规则名" required>
          <el-input v-model="form.rule_name" placeholder="例如：flash 高峰档(工作日9-12)" />
        </el-form-item>
        <el-form-item label="高峰窗口（留空为全时段规则；小时，左闭右开）">
          <div class="window-row">
            <el-input-number v-model="form.peak_start_hour" :min="0" :max="23" placeholder="起" />
            <span>至</span>
            <el-input-number v-model="form.peak_end_hour" :min="1" :max="24" placeholder="止" />
          </div>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.weekdays_only">仅工作日生效</el-checkbox>
          <el-checkbox v-model="form.is_active">启用</el-checkbox>
        </el-form-item>
        <el-form-item label="生效时间" required>
          <el-date-picker v-model="form.effective_from" type="datetime" />
        </el-form-item>
        <el-form-item label="生效截止（可空 = 长期；临时优惠/未来调价到点自动失效回落）">
          <el-date-picker v-model="form.effective_to" type="datetime" :clearable="true" />
        </el-form-item>
        <el-form-item label="权重（同时段多条匹配时大者胜：常规价 0、临时优惠 1）">
          <el-input-number v-model="form.priority" :min="0" :max="99" />
        </el-form-item>
        <el-form-item label="输入单价（分 / 百万 token）" required>
          <el-input-number v-model="form.input_price_per_mtok" :min="0" :precision="4" />
        </el-form-item>
        <el-form-item label="输出单价（分 / 百万 token）" required>
          <el-input-number v-model="form.output_price_per_mtok" :min="0" :precision="4" />
        </el-form-item>
        <el-form-item label="缓存读单价（分 / 百万 token，可空）">
          <el-input-number v-model="form.cache_read_price_per_mtok" :min="0" :precision="4" />
        </el-form-item>
        <el-form-item label="搜索单价（分 / 千次）">
          <el-input-number v-model="form.search_price_per_k" :min="0" :precision="4" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">
          {{ editingId ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}
.window-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
