<script setup lang="ts">
/**
 * 学习型选股：你标注「正例👍/反例👎」，系统训练打分器再给股票池打分。
 * 判别力来自正反对比——所以必须既有正例又有反例。标得越多越准（主动学习循环）。
 */
import { computed, ref } from 'vue'
import {
  NPageHeader, NCard, NInput, NSelect, NButton, NSpace, NTag, NAlert,
  NDatePicker, NRadioGroup, NRadioButton, useMessage,
} from 'naive-ui'
import { useRouter } from 'vue-router'
import { usePools } from '@/api/pools'
import {
  usePatternLabels, useAddLabel, useDeleteLabel, useCreateLearnedSearch, usePatternNames,
} from '@/api/patternSearch'
import { normalizeSymbol } from '@/utils/symbol'

const message = useMessage()
const router = useRouter()

// 新形态用 input 输入名字；旧形态用 select 直接选已有的。
const mode = ref<'new' | 'old'>('new')
const newName = ref('')
const oldName = ref<string | null>(null)
const patternName = computed(() => (mode.value === 'old' ? (oldName.value ?? '') : newName.value).trim())
const { data: patternNames } = usePatternNames()
const nameOptions = () =>
  (patternNames.value ?? []).map(p => ({ label: `${p.pattern_name}（${p.cnt}条标注）`, value: p.pattern_name }))

const poolId = ref<number | null>(null)
const searchMode = ref<'realtime' | 'history'>('realtime')
const sym = ref('')
const range = ref<[number, number] | null>(null)

const { data: pools } = usePools()
const poolOptions = () => (pools.value ?? []).map(p => ({ label: `${p.pool_name} (#${p.pool_id})`, value: p.pool_id }))

const { data: labels } = usePatternLabels(patternName)
const addLabel = useAddLabel()
const delLabel = useDeleteLabel()
const createLearned = useCreateLearnedSearch()

const posCount = computed(() => (labels.value ?? []).filter(l => l.label === 1).length)
const negCount = computed(() => (labels.value ?? []).filter(l => l.label === 0).length)
const canTrain = computed(() => posCount.value >= 1 && negCount.value >= 1 && poolId.value != null)

function toIso(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function add(label: number) {
  if (!patternName.value.trim()) { message.warning('请先填形态名'); return }
  if (!sym.value.trim()) { message.warning('请填股票代码'); return }
  sym.value = normalizeSymbol(sym.value)
  try {
    await addLabel.mutateAsync({
      pattern_name: patternName.value.trim(),
      symbol: sym.value,
      start: range.value ? toIso(range.value[0]) : undefined,
      end: range.value ? toIso(range.value[1]) : undefined,
      label,
    })
    message.success(label === 1 ? '已加为正例' : '已加为反例')
    sym.value = ''
  } catch (e: any) {
    message.error(e?.message || '添加失败')
  }
}

async function removeLabel(id: number) {
  try { await delLabel.mutateAsync(id) } catch (e: any) { message.error(e?.message || '删除失败') }
}

async function trainAndSearch() {
  if (!canTrain.value) { message.warning('至少各需 1 个正例和反例，并选好股票池'); return }
  try {
    const res = await createLearned.mutateAsync({ pattern_name: patternName.value.trim(), pool_id: poolId.value!, mode: searchMode.value })
    message.success('训练+选股任务已创建')
    router.push(`/pattern/runs/${res.run_id}`)
  } catch (e: any) {
    message.error(e?.message || '创建任务失败')
  }
}
</script>

<template>
  <div>
    <n-page-header title="学习型选股" style="margin-bottom: 16px">
      <template #subtitle>标注正例👍/反例👎 → 系统学特征 → 给股票池打分选股。标得越多越准。</template>
    </n-page-header>

    <n-alert type="info" style="margin-bottom: 16px">
      判别力来自<b>正反对比</b>：既要标「我要的」(正例)，也要标「看着像但我不要的」(反例，比如下跌中的坑、圆弧顶)。
      结果页里可以对每条 👍👎 继续标，再回来重新训练，越用越准。
    </n-alert>

    <n-card title="形态 / 股票池" style="margin-bottom: 16px">
      <n-space :size="12" align="center" wrap>
        <n-radio-group v-model:value="mode" size="small">
          <n-radio-button value="new">新形态</n-radio-button>
          <n-radio-button value="old">旧形态</n-radio-button>
        </n-radio-group>
        <n-input
          v-if="mode === 'new'"
          v-model:value="newName"
          placeholder="新形态名，如：涨一波后跌穿"
          style="width: 220px"
        />
        <n-select
          v-else
          v-model:value="oldName"
          :options="nameOptions()"
          placeholder="选择已有形态"
          filterable
          style="width: 240px"
        />
        <n-select v-model:value="poolId" :options="poolOptions()" placeholder="选择股票池" filterable style="width: 240px" />
        <n-radio-group v-model:value="searchMode" size="small">
          <n-radio-button value="realtime">实时选股（最近）</n-radio-button>
          <n-radio-button value="history">学习（历史）</n-radio-button>
        </n-radio-group>
        <n-button type="primary" :disabled="!canTrain" :loading="createLearned.isPending.value" @click="trainAndSearch">
          训练并选股
        </n-button>
        <span style="font-size: 12px; opacity: 0.6">正例 {{ posCount }} · 反例 {{ negCount }}（各≥1 才能训练）</span>
      </n-space>
    </n-card>

    <n-card v-if="patternName.trim()" title="加标注">
      <n-space :size="10" align="center" wrap style="margin-bottom: 12px">
        <n-input v-model:value="sym" placeholder="输入6位代码自动补全，如 000001" style="width: 180px" @keyup.enter="add(1)" @blur="sym = normalizeSymbol(sym)" />
        <n-date-picker v-model:value="range" type="daterange" clearable style="width: 260px" />
        <span style="font-size:12px;color:#d97706">建议选你看中的那段历史区间；不选则只用「最近60日」，很可能不是你想要的形态</span>
        <n-button type="success" :loading="addLabel.isPending.value" @click="add(1)">👍 加为正例</n-button>
        <n-button :loading="addLabel.isPending.value" @click="add(0)">👎 加为反例</n-button>
      </n-space>

      <div v-if="(labels ?? []).length === 0" style="opacity:.5;font-size:13px">还没有标注。</div>
      <n-space :size="6" wrap>
        <n-tag
          v-for="l in labels ?? []" :key="l.id"
          :type="l.label === 1 ? 'success' : (l.start_date ? 'default' : 'warning')"
          closable @close="removeLabel(l.id)"
        >
          {{ l.label === 1 ? '👍' : '👎' }} {{ l.symbol }}
          <template v-if="l.start_date"> {{ l.start_date }}~{{ l.end_date }}</template>
          <template v-else> ⚠️最近60日</template>
        </n-tag>
      </n-space>
    </n-card>
  </div>
</template>
