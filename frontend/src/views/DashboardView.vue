<script setup lang="ts">
import type { EChartsCoreOption } from 'echarts/core'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { dashboardApi, usageApi } from '@/api'
import type { Dashboard, UsageSummary } from '@/api/types'
import { ApiError } from '@/api/client'
import EChart from '@/components/EChart.vue'
import { formatLatency, formatNumber, formatPercent, formatYuan } from '@/utils/format'

const loading = ref(false)
const dashboard = ref<Dashboard | null>(null)
const daily = ref<UsageSummary | null>(null)
const holder = ref<UsageSummary | null>(null)

async function load() {
  loading.value = true
  try {
    const [dash, daySummary, holderSummary] = await Promise.all([
      dashboardApi.get(),
      usageApi.summary('day'),
      usageApi.summary('holder'),
    ])
    dashboard.value = dash
    daily.value = daySummary
    holder.value = holderSummary
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

const lineOption = computed<EChartsCoreOption>(() => {
  if (!daily.value) return {}
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: daily.value.chart.series.map((s) => s.name) },
    grid: { left: 60, right: 60, top: 50, bottom: 40 },
    xAxis: { type: 'category', data: daily.value.chart.categories },
    yAxis: [{ type: 'value' }, { type: 'value' }],
    series: daily.value.chart.series.map((series, index) => ({
      name: series.name,
      type: 'line',
      smooth: true,
      yAxisIndex: index === 0 ? 0 : 1,
      data: series.data,
    })),
  }
})

// 状态语义色：成功绿、断连黄、失败红、其余中性色兜底
const STATUS_COLORS: Record<string, string> = {
  succeeded: '#22c55e',
  client_disconnected: '#eab308',
  failed: '#ef4444',
  upstream_error: '#f97316',
  upstream_timeout: '#f97316',
  rate_limited: '#a855f7',
  quota_exceeded: '#a855f7',
}

const pieOption = computed<EChartsCoreOption>(() => {
  if (!dashboard.value) return {}
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [
      {
        name: '状态分布',
        type: 'pie',
        radius: ['40%', '65%'],
        data: Object.entries(dashboard.value.status_distribution).map(([name, value]) => ({
          name,
          value,
          itemStyle: { color: STATUS_COLORS[name] ?? '#94a3b8' },
        })),
      },
    ],
  }
})

const barOption = computed<EChartsCoreOption>(() => {
  if (!holder.value) return {}
  const top = [...holder.value.rows]
    .sort((a, b) => b.request_count - a.request_count)
    .slice(0, 10)
    .reverse()
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 130, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: top.map((row) => row.group_label) },
    series: [{ name: '请求数', type: 'bar', data: top.map((row) => row.request_count) }],
  }
})
</script>

<template>
  <div v-loading="loading">
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card>
          <div class="stat-title">总请求数</div>
          <div class="stat-value">{{ formatNumber(dashboard?.total_requests) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="stat-title">成功率</div>
          <div class="stat-value">
            {{ dashboard ? formatPercent(dashboard.success_rate) : '—' }}
          </div>
          <div class="sub">成功 {{ formatNumber(dashboard?.succeeded) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="stat-title">今日费用</div>
          <div class="stat-value">{{ formatYuan(dashboard?.today_cost_cents) }}</div>
          <div class="sub">P50 {{ formatLatency(dashboard?.p50_latency_ms) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="stat-title">P95 延迟</div>
          <div class="stat-value">{{ formatLatency(dashboard?.p95_latency_ms) }}</div>
          <div class="sub">活跃 Key {{ formatNumber(dashboard?.active_keys) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="chart-card" header="每日趋势（费用 / 请求 / 搜索）">
      <EChart v-if="daily && daily.rows.length" :option="lineOption" />
      <el-empty v-else description="暂无数据" />
    </el-card>

    <el-row :gutter="16">
      <el-col :span="10">
        <el-card class="chart-card" header="状态分布">
          <EChart
            v-if="dashboard && Object.keys(dashboard.status_distribution).length"
            :option="pieOption"
          />
          <el-empty v-else description="暂无数据" />
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card class="chart-card" header="持有人请求 Top 10">
          <EChart v-if="holder && holder.rows.length" :option="barOption" />
          <el-empty v-else description="暂无数据" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.chart-card {
  margin-top: 16px;
}
.stat-title {
  color: #6b7280;
  font-size: 13px;
}
.stat-value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 600;
}
.sub {
  margin-top: 6px;
  color: #6b7280;
  font-size: 12px;
}
</style>
