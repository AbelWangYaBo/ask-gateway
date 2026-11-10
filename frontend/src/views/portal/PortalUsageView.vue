<template>
  <div>
    <el-card class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="维度">
          <el-select v-model="groupBy" style="width: 120px" @change="load">
            <el-option value="day" label="按天" />
            <el-option value="key" label="按 Key" />
            <el-option value="model" label="按模型" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-radio-group :model-value="preset" @update:model-value="(v: string | number | boolean) => applyPreset(v as Preset)">
            <el-radio-button value="today">今天</el-radio-button>
            <el-radio-button value="last7">近 7 天</el-radio-button>
            <el-radio-button value="last30">近 30 天</el-radio-button>
            <el-radio-button value="month">本月</el-radio-button>
            <el-radio-button value="custom">自定义</el-radio-button>
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
          <el-button :disabled="!summary || !summary.rows.length" @click="exportCsv">导出 CSV</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card>
      <EChart v-if="summary" :option="chartOption" height="320px" />
      <el-table :data="summary?.rows ?? []" stripe style="margin-top: 12px">
        <el-table-column label="分组" prop="group_label" min-width="160" />
        <el-table-column label="请求数" prop="request_count" width="100" />
        <el-table-column label="输入 token" width="120">
          <template #default="{ row }">{{ formatNumber(row.input_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出 token" width="120">
          <template #default="{ row }">{{ formatNumber(row.output_tokens) }}</template>
        </el-table-column>
        <el-table-column label="搜索次数" prop="search_count" width="100" />
        <el-table-column label="费用" width="110">
          <template #default="{ row }">{{ formatYuan(row.cost_cents) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import type { EChartsCoreOption } from 'echarts/core'
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { portalApi } from '@/api'
import type { UsageSummary } from '@/api/types'
import { ApiError } from '@/api/client'
import EChart from '@/components/EChart.vue'
import { buildCsv, downloadCsv, type CsvColumn } from '@/utils/csv'
import { formatNumber, formatYuan } from '@/utils/format'

type Preset = 'today' | 'last7' | 'last30' | 'month' | 'custom'

const groupBy = ref<'day' | 'key' | 'model'>('day')
const preset = ref<Preset>('month')
const dateRange = ref<Date[]>([])
const summary = ref<UsageSummary | null>(null)
const loading = ref(false)

function applyPreset(value: Preset) {
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
  } else if (value === 'month') {
    from.setDate(1)
  }
  from.setHours(0, 0, 0, 0)
  const to = new Date(now)
  to.setHours(23, 59, 59, 999)
  dateRange.value = [from, to]
  void load()
}

async function load() {
  loading.value = true
  try {
    summary.value = await portalApi.usage(
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

onMounted(() => applyPreset('month'))

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
  downloadCsv(`ask-gateway-my-usage-${groupBy.value}.csv`, csv)
}
</script>

<style scoped>
.filter-card { margin-bottom: 12px; }
</style>
