<script setup lang="ts">
import type { EChartsCoreOption } from 'echarts/core'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { usageApi } from '@/api'
import type { UsageSummary } from '@/api/types'
import { ApiError } from '@/api/client'
import EChart from '@/components/EChart.vue'
import { buildCsv, downloadCsv, type CsvColumn } from '@/utils/csv'
import { formatNumber, formatYuan } from '@/utils/format'

type GroupBy = UsageSummary['group_by']

const loading = ref(false)
const groupBy = ref<GroupBy>('day')
const dateRange = ref<Date[]>([])
const summary = ref<UsageSummary | null>(null)

const groupOptions: Array<{ value: GroupBy; label: string }> = [
  { value: 'holder', label: '按持有人' },
  { value: 'key', label: '按 Key' },
  { value: 'model', label: '按模型' },
  { value: 'day', label: '按天' },
  { value: 'upstream_key', label: '按上游 Key' },
]

// ---- 快捷时间范围（本周/本月均截止到当天） ----

type RangePreset = 'today' | 'last7' | 'last30' | 'week' | 'month' | 'custom'
const preset = ref<RangePreset>('custom')

function dayStart(d: Date): Date {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}

function dayEnd(d: Date): Date {
  const x = new Date(d)
  x.setHours(23, 59, 59, 999)
  return x
}

function applyPreset(value: RangePreset) {
  preset.value = value
  if (value === 'custom') return
  const now = new Date()
  const from = new Date(now)
  if (value === 'today') {
    // 当天
  } else if (value === 'last7') {
    from.setDate(now.getDate() - 6)
  } else if (value === 'last30') {
    from.setDate(now.getDate() - 29)
  } else if (value === 'week') {
    const dow = (now.getDay() + 6) % 7 // 周一为一周之始
    from.setDate(now.getDate() - dow)
  } else if (value === 'month') {
    from.setDate(1)
  }
  dateRange.value = [dayStart(from), dayEnd(now)]
  void load()
}

const presetOptions: Array<{ value: RangePreset; label: string }> = [
  { value: 'today', label: '今天' },
  { value: 'last7', label: '近 7 天' },
  { value: 'last30', label: '近 30 天' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
  { value: 'custom', label: '自定义' },
]

async function load() {
  loading.value = true
  try {
    summary.value = await usageApi.summary(
      groupBy.value,
      dateRange.value[0]?.toISOString(),
      dateRange.value[1]?.toISOString(),
    )
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

const chartOption = computed<EChartsCoreOption>(() => {
  if (!summary.value) return {}
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: summary.value.chart.series.map((s) => s.name) },
    grid: { left: 70, right: 70, top: 50, bottom: 40 },
    xAxis: { type: 'category', data: summary.value.chart.categories },
    yAxis: [{ type: 'value' }, { type: 'value' }],
    series: summary.value.chart.series.map((series, index) => ({
      name: series.name,
      type: 'bar',
      yAxisIndex: index === 0 ? 0 : 1,
      data: series.data,
    })),
  }
})

const csvColumns: Array<CsvColumn<UsageSummary['rows'][number]>> = [
  { key: 'group_label', label: '分组' },
  { key: 'request_count', label: '请求数' },
  { key: 'input_tokens', label: '输入 token' },
  { key: 'output_tokens', label: '输出 token' },
  { key: 'search_count', label: '搜索次数' },
  { key: 'cost_cents', label: '费用(分)' },
]

function exportCsv() {
  if (!summary.value) return
  const csv = buildCsv(csvColumns, summary.value.rows)
  downloadCsv(`ask-gateway-usage-${groupBy.value}.csv`, csv)
}

const totalCost = computed(() =>
  summary.value?.rows.reduce((sum, row) => sum + Number(row.cost_cents), 0) ?? 0,
)
</script>

<template>
  <div>
    <el-card class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="汇总维度">
          <el-select v-model="groupBy" style="width: 140px" @change="load">
            <el-option
              v-for="option in groupOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-radio-group :model-value="preset" @update:model-value="(v: string | number | boolean) => applyPreset(v as RangePreset)">
            <el-radio-button
              v-for="option in presetOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-if="preset === 'custom'"
            v-model="dateRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            style="margin-left: 12px; width: 360px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
          <el-button :disabled="!summary || !summary.rows.length" @click="exportCsv">
            导出 CSV
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-loading="loading">
      <div class="total">费用合计：<strong>{{ formatYuan(totalCost) }}</strong></div>
      <EChart v-if="summary && summary.rows.length" :option="chartOption" height="360px" />
      <el-empty v-else description="暂无数据" />

      <el-table :data="summary?.rows ?? []" stripe class="table">
        <el-table-column label="分组" prop="group_label" />
        <el-table-column label="请求数" width="110">
          <template #default="{ row }">{{ formatNumber(row.request_count) }}</template>
        </el-table-column>
        <el-table-column label="输入 token" width="130">
          <template #default="{ row }">{{ formatNumber(row.input_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出 token" width="130">
          <template #default="{ row }">{{ formatNumber(row.output_tokens) }}</template>
        </el-table-column>
        <el-table-column label="搜索次数" width="110">
          <template #default="{ row }">{{ formatNumber(row.search_count) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="140">
          <template #default="{ row }">{{ formatYuan(row.cost_cents) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 12px;
}
.total {
  margin-bottom: 8px;
}
.table {
  margin-top: 12px;
}
</style>
