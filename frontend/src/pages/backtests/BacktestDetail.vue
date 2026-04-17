<script setup lang="ts">
/**
 * 回测详情页
 * 自动轮询到完成，展示 metrics 表格 + 产物下载
 * 净值图：payload 中不含 equity 时序，MVP 阶段显示下载提示
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NButton, NSpace, NAlert, NGrid, NGridItem, NEmpty,
} from 'naive-ui'
import { useBacktest } from '@/api/backtests'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: btRun, isLoading } = useBacktest(runId)

const metrics = computed(() => btRun.value?.metrics ?? null)
const artifacts = computed(() => (btRun.value as any)?.artifacts ?? [])

const isRunning = computed(() =>
  btRun.value?.status === 'pending' || btRun.value?.status === 'running'
)

function fmtPct(v: any, digits = 2): string {
  if (v == null) return '-'
  return typeof v === 'number' ? (v * 100).toFixed(digits) + '%' : String(v)
}
function fmtNum(v: any, digits = 4): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}

function hasArtifact(type: string): boolean {
  return artifacts.value.some((a: any) => a.artifact_type === type)
}

function downloadArtifact(type: string) {
  window.open(`/api/backtests/${runId.value}/${type}`, '_blank')
}
</script>

<template>
  <div>
    <n-page-header
      :title="`回测 ${runId.slice(0, 8)}...`"
      @back="router.back()"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <status-badge v-if="btRun" :status="btRun.status" />
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <!-- 运行中进度 -->
      <n-progress
        v-if="isRunning"
        type="line"
        :percentage="btRun?.status === 'running' ? 50 : 10"
        :show-indicator="false"
        status="warning"
        style="margin-bottom: 16px"
      />

      <!-- 失败提示 -->
      <n-alert v-if="btRun?.status === 'failed'" type="error" title="运行失败" style="margin-bottom: 16px">
        {{ btRun.error || '未知错误' }}
      </n-alert>

      <!-- 任务基本信息 -->
      <n-descriptions v-if="btRun" bordered :column="3" label-placement="left" style="margin-bottom: 24px">
        <n-descriptions-item label="因子">{{ btRun.factor_id }}</n-descriptions-item>
        <n-descriptions-item label="股票池">{{ btRun.pool_id }}</n-descriptions-item>
        <n-descriptions-item label="日期区间">{{ btRun.start_date }} ~ {{ btRun.end_date }}</n-descriptions-item>
        <n-descriptions-item label="创建时间">{{ btRun.created_at }}</n-descriptions-item>
        <n-descriptions-item label="完成时间">{{ btRun.finished_at ?? '-' }}</n-descriptions-item>
        <n-descriptions-item label="参数">
          <code style="font-size: 12px">{{ JSON.stringify(btRun.params) }}</code>
        </n-descriptions-item>
      </n-descriptions>

      <!-- 成功时展示 -->
      <template v-if="btRun?.status === 'success'">
        <!-- 净值图区域 - MVP 阶段提示下载 -->
        <n-card title="净值曲线" size="small" style="margin-bottom: 16px">
          <n-empty description="请下载 equity.parquet 查看净值曲线详情">
            <template #extra>
              <n-button
                v-if="hasArtifact('equity')"
                size="small"
                type="primary"
                @click="downloadArtifact('equity')"
              >
                下载 equity.parquet
              </n-button>
            </template>
          </n-empty>
        </n-card>

        <!-- Metrics 指标 -->
        <h3 style="margin-bottom: 12px">回测指标</h3>
        <n-descriptions v-if="metrics" bordered :column="3" label-placement="left" style="margin-bottom: 24px">
          <n-descriptions-item label="总收益率">{{ fmtPct(metrics.total_return) }}</n-descriptions-item>
          <n-descriptions-item label="年化收益率">{{ fmtPct(metrics.annual_return) }}</n-descriptions-item>
          <n-descriptions-item label="Sharpe 比率">{{ fmtNum(metrics.sharpe_ratio, 2) }}</n-descriptions-item>
          <n-descriptions-item label="最大回撤">{{ fmtPct(metrics.max_drawdown) }}</n-descriptions-item>
          <n-descriptions-item label="胜率">{{ fmtPct(metrics.win_rate) }}</n-descriptions-item>
          <n-descriptions-item label="交易次数">{{ metrics.trade_count ?? '-' }}</n-descriptions-item>
        </n-descriptions>
        <n-alert v-else type="info" style="margin-bottom: 24px">
          暂无指标数据
        </n-alert>

        <!-- 产物下载 -->
        <h3 style="margin-bottom: 12px">产物下载</h3>
        <n-space>
          <n-button
            v-if="hasArtifact('equity')"
            @click="downloadArtifact('equity')"
            secondary
          >
            equity.parquet
          </n-button>
          <n-button
            v-if="hasArtifact('orders')"
            @click="downloadArtifact('orders')"
            secondary
          >
            orders.parquet
          </n-button>
          <n-button
            v-if="hasArtifact('trades')"
            @click="downloadArtifact('trades')"
            secondary
          >
            trades.parquet
          </n-button>
        </n-space>
      </template>
    </n-spin>
  </div>
</template>
