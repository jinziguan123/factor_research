<script setup lang="ts">
/**
 * 回测创建页
 * 因子选择 → 动态参数 → 股票池 → 日期区间 → 回测参数 → 提交
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NButton, NSwitch, NTooltip, NCollapse, NCollapseItem, useMessage,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateBacktest } from '@/api/backtests'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import ParamsFormRenderer from '@/components/forms/ParamsFormRenderer.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

// 因子列表（按分类分组做 NSelect option group）
const { data: factors, isLoading: factorsLoading } = useFactors()

const factorOptions = computed(() => {
  const groups: Record<string, { label: string; value: string }[]> = {}
  for (const f of factors.value ?? []) {
    const cat = f.category || 'custom'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push({ label: f.display_name, value: f.factor_id })
  }
  return Object.entries(groups).map(([cat, children]) => ({
    type: 'group' as const,
    label: cat,
    key: cat,
    children,
  }))
})

// 表单状态
const selectedFactorId = ref(route.query.factor_id as string || '')
const selectedFactor = useFactor(selectedFactorId)
const factorParams = ref<Record<string, any>>({})
const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const rebalancePeriod = ref(1)
const position = ref('top')
const initCash = ref(1e7)
const filterPriceLimit = ref(true)
// 执行/成本模型（后端 2026-06-23 改造）
const execPrice = ref('open')
const commissionBps = ref(2.5)
const stampTaxBps = ref(5.0)
const transferFeeBps = ref(0.1)
const slippageBps = ref(5.0)
const impactCoef = ref(0.1)
const maxVolumePct = ref(0.10)
// 组内权重方法 + 组合级风控
const weighting = ref('equal')
const weightLookback = ref(60)
const maxPositionWeight = ref(0.0)
const targetVol = ref(0.0)
const volLookback = ref(60)
const lockPriceLimit = ref(true)

const positionOptions = [
  { label: '做多头部 (top)', value: 'top' },
  { label: '多空对冲 (long_short)', value: 'long_short' },
]

const execPriceOptions = [
  { label: '次日开盘价 (T+1 open)', value: 'open' },
  { label: '次日 VWAP (T+1 vwap)', value: 'vwap' },
]

const weightingOptions = [
  { label: '等权 (equal)', value: 'equal' },
  { label: '逆波动率 (inverse_vol)', value: 'inverse_vol' },
  { label: '风险平价 (risk_parity)', value: 'risk_parity' },
]

// 当选择因子变化时，用默认参数初始化
watch(() => selectedFactor.data.value, (f) => {
  if (f?.default_params) {
    factorParams.value = { ...f.default_params }
  }
}, { immediate: true })

const createBacktest = useCreateBacktest()

async function handleSubmit() {
  if (!selectedFactorId.value) {
    message.warning('请选择因子')
    return
  }
  if (!poolId.value) {
    message.warning('请选择股票池')
    return
  }
  if (!dateRange.value) {
    message.warning('请选择日期区间')
    return
  }

  const startDate = new Date(dateRange.value[0]).toISOString().slice(0, 10)
  const endDate = new Date(dateRange.value[1]).toISOString().slice(0, 10)

  const body = {
    factor_id: selectedFactorId.value,
    params: factorParams.value,
    pool_id: poolId.value,
    start_date: startDate,
    end_date: endDate,
    n_groups: nGroups.value,
    rebalance_period: rebalancePeriod.value,
    position: position.value,
    exec_price: execPrice.value,
    commission_bps: commissionBps.value,
    stamp_tax_bps: stampTaxBps.value,
    transfer_fee_bps: transferFeeBps.value,
    slippage_bps: slippageBps.value,
    impact_coef: impactCoef.value,
    max_volume_pct: maxVolumePct.value,
    weighting: weighting.value,
    weight_lookback: weightLookback.value,
    max_position_weight: maxPositionWeight.value,
    target_vol: targetVol.value,
    vol_lookback: volLookback.value,
    init_cash: initCash.value,
    filter_price_limit: filterPriceLimit.value,
    lock_price_limit: lockPriceLimit.value,
  }

  const result = await createBacktest.mutateAsync(body)
  message.success('回测任务已提交')
  router.push(`/backtests/${result.run_id}`)
}
</script>

<template>
  <div>
    <n-page-header title="创建回测" @back="router.back()" style="margin-bottom: 16px" />

    <n-form label-placement="left" label-width="120px" style="max-width: 700px">
      <!-- 因子选择 -->
      <n-form-item label="因子" required>
        <n-select
          v-model:value="selectedFactorId"
          :options="factorOptions"
          :loading="factorsLoading"
          placeholder="选择因子"
          filterable
          style="width: 100%"
        />
      </n-form-item>

      <!-- 动态参数表单 -->
      <n-form-item v-if="selectedFactor.data.value?.params_schema" label="因子参数">
        <params-form-renderer
          :schema="selectedFactor.data.value.params_schema"
          v-model="factorParams"
        />
      </n-form-item>

      <!-- 股票池 -->
      <n-form-item label="股票池" required>
        <pool-selector v-model:value="poolId" style="width: 100%" />
      </n-form-item>

      <!-- 日期区间 -->
      <n-form-item label="日期区间" required>
        <n-date-picker
          v-model:value="dateRange"
          type="daterange"
          clearable
          style="width: 100%"
        />
      </n-form-item>

      <!-- 分组数 -->
      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 160px" />
      </n-form-item>

      <!-- 调仓周期 -->
      <n-form-item label="调仓周期(天)">
        <n-input-number v-model:value="rebalancePeriod" :min="1" :max="60" style="width: 160px" />
      </n-form-item>

      <!-- 持仓方式 -->
      <n-form-item label="持仓方式">
        <n-select
          v-model:value="position"
          :options="positionOptions"
          style="width: 240px"
        />
      </n-form-item>

      <!-- 成交价口径 -->
      <n-form-item label="成交价">
        <n-select v-model:value="execPrice" :options="execPriceOptions" style="width: 240px" />
      </n-form-item>

      <!-- 组内权重方法 -->
      <n-form-item label="权重方法">
        <n-select v-model:value="weighting" :options="weightingOptions" style="width: 240px" />
      </n-form-item>
      <n-form-item v-if="weighting !== 'equal'" label="权重回看(天)">
        <n-input-number v-model:value="weightLookback" :min="5" :max="252" style="width: 160px" />
      </n-form-item>

      <!-- 初始资金 -->
      <n-form-item label="初始资金">
        <n-input-number v-model:value="initCash" :min="10000" :step="1000000" style="width: 200px" />
      </n-form-item>

      <!-- 涨跌停过滤 -->
      <n-form-item>
        <template #label>
          <n-tooltip>
            <template #trigger>
              <span style="cursor: help; border-bottom: 1px dashed #999">
                涨跌停过滤
              </span>
            </template>
            开启后按 |pct_change| ≥ 0.097 的近似口径剔除当日触板票（多空两侧都剔），
            以更接近"明日不可成交"的真实约束。<br/>
            注意：未区分主板 / 创业板（20% 板）/ ST（5% 板），口径偏保守，
            可能"误剔"少量科创板 / 创业板的合法 10% 涨幅交易。
          </n-tooltip>
        </template>
        <n-switch v-model:value="filterPriceLimit" />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          {{ filterPriceLimit ? '已开启（更接近实盘）' : '已关闭（与历史回测可对比）' }}
        </span>
      </n-form-item>

      <!-- 封板滞留 -->
      <n-form-item>
        <template #label>
          <n-tooltip>
            <template #trigger>
              <span style="cursor: help; border-bottom: 1px dashed #999">封板滞留</span>
            </template>
            执行侧约束：成交日封涨停则买不进、封跌停则卖不掉，持仓滞留至可成交日。
          </n-tooltip>
        </template>
        <n-switch v-model:value="lockPriceLimit" />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          {{ lockPriceLimit ? '已开启（真实交易约束）' : '已关闭（理想成交）' }}
        </span>
      </n-form-item>

      <!-- 高级参数：成本/滑点/风控（默认折叠） -->
      <n-collapse style="margin: 8px 0 20px">
        <n-collapse-item title="成本与滑点（高级）" name="cost">
          <n-form-item label="佣金(bps)">
            <n-input-number v-model:value="commissionBps" :min="0" :max="100" :precision="2" style="width: 160px" />
          </n-form-item>
          <n-form-item label="印花税(bps·仅卖出)">
            <n-input-number v-model:value="stampTaxBps" :min="0" :max="100" :precision="2" style="width: 160px" />
          </n-form-item>
          <n-form-item label="过户费(bps)">
            <n-input-number v-model:value="transferFeeBps" :min="0" :max="100" :precision="2" style="width: 160px" />
          </n-form-item>
          <n-form-item label="滑点(bps)">
            <n-input-number v-model:value="slippageBps" :min="0" :max="200" :precision="2" style="width: 160px" />
          </n-form-item>
          <n-form-item label="市场冲击系数">
            <n-input-number v-model:value="impactCoef" :min="0" :max="10" :precision="3" style="width: 160px" />
          </n-form-item>
          <n-form-item label="单日成交额上限(0=关闭)">
            <n-input-number v-model:value="maxVolumePct" :min="0" :max="1" :precision="3" style="width: 160px" />
          </n-form-item>
        </n-collapse-item>
        <n-collapse-item title="组合风控（高级）" name="risk">
          <n-form-item label="个股权重上限(0=关闭)">
            <n-input-number v-model:value="maxPositionWeight" :min="0" :max="1" :precision="3" style="width: 160px" />
          </n-form-item>
          <n-form-item label="目标年化波动率(0=关闭)">
            <n-input-number v-model:value="targetVol" :min="0" :max="1" :precision="3" style="width: 160px" />
          </n-form-item>
          <n-form-item label="波动率回看(天)">
            <n-input-number v-model:value="volLookback" :min="5" :max="252" style="width: 160px" />
          </n-form-item>
        </n-collapse-item>
      </n-collapse>

      <!-- 提交按钮 -->
      <n-form-item>
        <n-button
          type="primary"
          :loading="createBacktest.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交回测
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
