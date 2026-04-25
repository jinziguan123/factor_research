<script setup lang="ts">
/**
 * 因子手册：面向"第一次在本平台评估因子"的用户的参考页。
 * 三节：术语速查 / 理想因子指标红线表 / 常见失败模式。
 * 纯静态页面，不请求后端。数字来源是 A 股日频研究的常见经验阈值。
 */
import { h, ref } from 'vue'
import {
  NPageHeader, NCard, NSpace, NTag, NAlert, NDivider,
  NDataTable, NTabs, NTabPane,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import IcDecayInterpretationSection from '@/components/docs/IcDecayInterpretationSection.vue'
import GroupReturnsInterpretationSection from '@/components/docs/GroupReturnsInterpretationSection.vue'

interface GlossaryRow {
  term: string
  full: string
  meaning: string
}
interface MetricRow {
  metric: string
  present: string
  ideal: string
  red: string
}
interface PitfallRow {
  symptom: string
  cause: string
  fix: string
}

const glossary: GlossaryRow[] = [
  {
    term: 'IC',
    full: 'Information Coefficient · 信息系数（Pearson）',
    meaning: '每个截面日，因子值与未来 T 日收益率的 Pearson 相关系数；再在时间维度取均值。衡量因子的线性预测力。',
  },
  {
    term: 'Rank IC',
    full: '秩信息系数（Spearman）',
    meaning: 'IC 的秩相关版本：先把因子值和收益都转成截面排名再算 Pearson。对极端值稳健。与 Pearson IC 符号/量级背离 → 尾部污染或非线性。',
  },
  {
    term: 'ICIR',
    full: 'Information Ratio of IC',
    meaning: 'IC 均值 / IC 标准差。衡量 IC 的稳定性而非只是强度。日频 > 0.5 中等、> 1.0 好。',
  },
  {
    term: 't 统计量',
    full: 't-stat of IC',
    meaning: '把 IC 序列当成一段样本做单样本 t 检验。|t| > 2 ≈ 双尾 5% 显著。',
  },
  {
    term: '分组单调性',
    full: 'Monotonicity',
    meaning: '按因子值把截面分 N 组（通常 5 组），理想情况下 top→bottom 组的累计收益曲线单调分层、不交叉。量化可用 Spearman(组号, 累计收益)。',
  },
  {
    term: '多空组合',
    full: 'Long-Short Portfolio',
    meaning: 'top 组等权做多、bottom 组等权做空的对冲组合。多空 Sharpe 负 = 方向反。',
  },
  {
    term: '衰减 / 半衰期',
    full: 'IC Decay / Half-life',
    meaning: 'IC 随 forward_period (1/5/10 日...) 衰减的曲线。半衰期 = IC 降到 T=1 峰值 50% 的 T，决定合理的调仓频率。',
  },
  {
    term: '换手率',
    full: 'Turnover',
    meaning: '每期被调入/调出持仓的占比。越高越吃成本；日频 > 70% 的策略对 slippage 非常敏感。',
  },
  {
    term: '成本敏感性',
    full: 'Cost Sensitivity',
    meaning: '沿交易成本 (cost_bps) 扫 grid，看策略从 0bp → 10bp 的 Sharpe 衰减。5bp 就转负 = 因子 alpha 基本被成本吃光。',
  },
  {
    term: '中性化',
    full: 'Neutralization',
    meaning: '每日截面把因子值对行业哑变量 + log(mcap) 做 OLS 回归，取残差再算 IC。目的：剥离"风格暴露"，确认 IC 是真 alpha 而不是 size/行业 beta。',
  },
  {
    term: '覆盖率',
    full: 'Coverage / Full Rate',
    meaning: '截面上有有效因子值的股票比例。日频策略的分 5 组要求每组都满员，覆盖率 < 70% 通常意味着 rolling 窗口太长或信号过于稀疏。',
  },
  {
    term: '样本内 / 样本外 (IS / OOS)',
    full: 'In-Sample / Out-of-Sample',
    meaning: '用 split_date 把时间轴切两段：左半段调参（IS），右半段只跑不调（OOS）。OOS / IS 的 IC 比 < 0.3 ≈ 过拟合。',
  },
  {
    term: 'Walk-forward',
    full: '滚动样本外',
    meaning: '比单次 IS/OOS 更严格：固定训练窗（如 18m）+ 测试窗（6m）沿时间轴滚动多次。能看出因子是否随时间衰减或换制度失效。',
  },
  {
    term: '参数敏感性',
    full: 'Parameter Sensitivity',
    meaning: '沿因子超参（如 trend_window）扫 grid。若只有单点 peak、邻近全掉 30%+ → 过拟合到训练窗口的特定结构。',
  },
]

const glossaryCols: DataTableColumns<GlossaryRow> = [
  { title: '术语', key: 'term', width: 120, render: (r) => h(NTag, { size: 'small', type: 'info' }, { default: () => r.term }) },
  { title: '全称 / 含义', key: 'full', width: 280 },
  { title: '一句话解释', key: 'meaning' },
]

const metrics: MetricRow[] = [
  { metric: 'IC 均值 (5 日 forward)', present: 'EvalDetail - IC/RankIC 汇总', ideal: '> 0.03（强 > 0.05）', red: '|IC| < 0.01 或符号不稳' },
  { metric: 'Rank IC', present: '与 Pearson IC 并列', ideal: '与 Pearson 同号，量级差 < 30%', red: '与 Pearson 符号不一致 → 尾部污染' },
  { metric: 'ICIR (IC 均值/标准差)', present: 'IC 汇总', ideal: '> 0.5 中等、> 1.0 强', red: '< 0.3' },
  { metric: '年度 IC 胜率', present: '年度 IC 分解柱状图', ideal: '≥ 70% 年份符号一致', red: '< 60% 或连续 2 年反号' },
  { metric: 't 统计量 (|t|)', present: 'IC 汇总', ideal: '> 2（双尾 5% 显著）', red: '< 1.5' },
  { metric: '多空 Sharpe（含 3bp 成本）', present: '多空净值 / Backtest', ideal: '> 1.0', red: '< 0.5 或为负' },
  { metric: '多空年化', present: '多空净值', ideal: '> 10%', red: '< 5%' },
  { metric: '多空最大回撤', present: '多空净值 / Backtest', ideal: '< 20%', red: '> 30% 或单次跳水 > 15%' },
  { metric: '分组单调性', present: '5 组累计净值图', ideal: 'Spearman(组号, 收益) ≥ 0.9，曲线不交叉', red: '曲线缠绕 / top 组跑不过 mid' },
  { metric: 'Calmar (年化/MDD)', present: '待加', ideal: '> 1.0', red: '< 0.5' },
  { metric: '换手率（月均）', present: 'Backtest 待加曲线', ideal: '日频 < 40%，周频 < 15%', red: '> 70%（实际成本吃光）' },
  { metric: '衰减 / 半衰期', present: '待加图（批次 B）', ideal: 'IC 在 T=1..5 都 > 0，半衰期 ≥ 5 日', red: 'T=1 后立即反号 → 超短期噪声' },
  { metric: '成本敏感性 (0→10bp)', present: 'CostSensitivity grid', ideal: '10bp 时 Sharpe 仍 > 0.5', red: '5bp 即转负' },
  { metric: '覆盖率 (qcut_full_rate)', present: 'EvalDetail 指标', ideal: '≥ 90% 截面 5 组满员', red: '< 70% → 因子过稀疏' },
  { metric: 'OOS / IS IC 比', present: 'split_date 评估', ideal: '> 0.6', red: '< 0.3 → 过拟合' },
  { metric: '中性化后 IC 保留', present: '待加（批次 D）', ideal: '> 原 IC × 50%', red: '< 30% → 因子是行业/市值 beta 伪装' },
  { metric: '参数邻域稳定性', present: '待加（批次 C）', ideal: '±1 档参数 IC 下降 < 30%', red: '只在单点 peak' },
]

const metricsCols: DataTableColumns<MetricRow> = [
  { title: '指标', key: 'metric', width: 240 },
  { title: '当前呈现位置', key: 'present', width: 220 },
  { title: '✅ 理想值', key: 'ideal', width: 280, render: (r) => h('span', { style: 'color: #52C41A' }, r.ideal) },
  { title: '🚫 红线（触即 pass）', key: 'red', render: (r) => h('span', { style: 'color: #F5222D' }, r.red) },
]

const pitfalls: PitfallRow[] = [
  {
    symptom: 'Pearson IC ≈ 0 但 Rank IC 有 ±0.02',
    cause: '尾部被极端值污染。通常源于公式里某个分母可能趋 0（如停牌/一字涨停票的 vol→1e-8），+1e-12 兜底压不住。',
    fix: '分母加绝对下限（如 VOL_FLOOR=5e-3）；计算原始信号后做截面 winsorize(1%,99%) 再归一。',
  },
  {
    symptom: '多空 Sharpe 为负，但分组有一定单调性',
    cause: '方向假设反了——因子本来就是"值越小收益越大"。',
    fix: '在 compute 里对因子值整体取负，或在评估前 flip。不要只靠"调换 top/bottom 组"解决，不同下游用同一信号源。',
  },
  {
    symptom: '5 组净值曲线缠绕、甚至 top 组垫底',
    cause: '因子是"多个条件的 AND"，但复合时用了加法 z-score，让单个分量极端值的票和目标票得分相同。',
    fix: '每个分量先截面 winsorize + rank(pct=True)，三分量相乘而非相加；或用硬筛 mask 把不满足条件的票置 NaN。',
  },
  {
    symptom: 'IS 下 IC=0.05、OOS 下 IC=0.005',
    cause: '因子参数过拟合到 IS 的特定结构。',
    fix: '参数敏感性扫 grid（批次 C）；挑 "邻域 IC 都稳" 的参数，不挑单点 peak。也可以直接用稍差但跨参稳定的默认值。',
  },
  {
    symptom: '样本外 IC 衰减但分行业看时特定行业 IC 一直很高',
    cause: '因子本质不是 alpha 而是行业 beta（比如周期股 + 某宏观变量）。',
    fix: '做行业 + log(mcap) 中性化（批次 D）。中性化后 IC 保留 < 30% 的因子基本可以放弃。',
  },
  {
    symptom: '覆盖率低（qcut_full_rate < 70%）',
    cause: '因子用的 rolling 窗口过长（比如 180 日），新股和停复牌票长期没值；或因子逻辑本身只在小部分股票成立。',
    fix: '缩短窗口 / 放宽 min_periods；或接受稀疏性但把评估限定在满池子票上（分组仍要求每组至少 N 只）。',
  },
]

const pitfallsCols: DataTableColumns<PitfallRow> = [
  { title: '症状', key: 'symptom', width: 260 },
  { title: '最常见根因', key: 'cause' },
  { title: '对策', key: 'fix', width: 340 },
]

const activeTab = ref<'glossary' | 'metrics' | 'pitfalls' | 'charts'>('glossary')
const chartsSubTab = ref<'ic-decay' | 'group-returns'>('ic-decay')
</script>

<template>
  <div>
    <n-page-header title="因子手册" style="margin-bottom: 16px">
      <template #subtitle>
        术语速查 + 理想指标参考 + 常见失败模式
      </template>
    </n-page-header>

    <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
      这一页不连后端、不改任何数据，只是一张<b>参考表</b>。遇到具体评估结果说不清是好是坏时，
      到这里查"理想值 / 红线"一栏即可。所有数字是 A 股日频研究的常见经验阈值，周频 / 月频请相应放宽。
    </n-alert>

    <n-card size="small" :bordered="false" style="margin-bottom: 16px">
      <n-tabs v-model:value="activeTab" type="line" animated size="large">
        <n-tab-pane name="glossary" tab="一、术语速查">
          <n-data-table
            :columns="glossaryCols"
            :data="glossary"
            :bordered="false"
            :single-line="false"
            size="small"
          />
        </n-tab-pane>

        <n-tab-pane name="metrics" tab="二、指标红线表">
          <n-space vertical :size="12">
            <n-space :size="4">
              <n-tag size="small" type="success">✅ 理想</n-tag>
              <n-tag size="small" type="error">🚫 红线（触即 pass）</n-tag>
            </n-space>
            <n-data-table
              :columns="metricsCols"
              :data="metrics"
              :bordered="false"
              :single-line="false"
              size="small"
            />
            <n-divider style="margin: 4px 0" />
            <div>
              <n-tag type="warning" size="small" style="margin-right: 8px">及格线</n-tag>
              <span>IC 0.02 · ICIR 0.3 · 多空 Sharpe 0.5 · 换手 &lt; 50% · 半衰期 3 日 · 年度胜率 60%</span>
            </div>
            <div>
              <n-tag type="success" size="small" style="margin-right: 8px">优秀线</n-tag>
              <span>IC 0.04+ · ICIR 0.8+ · 多空 Sharpe 1.2+ · 换手 &lt; 30% · 半衰期 5+ 日 · 年度胜率 75%</span>
            </div>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="pitfalls" tab="三、失败模式">
          <n-data-table
            :columns="pitfallsCols"
            :data="pitfalls"
            :bordered="false"
            :single-line="false"
            size="small"
          />
        </n-tab-pane>

        <n-tab-pane name="charts" tab="四、图表解读（交互 Mock）">
          <n-space vertical :size="12">
            <n-alert type="info" :show-icon="false">
              以下图表均为教学用 mock 数据，仅用于说明“什么样算好、什么样算坏”，不代表任何真实评估结果。
            </n-alert>

            <n-tabs v-model:value="chartsSubTab" type="segment" size="medium">
              <n-tab-pane name="ic-decay" tab="4.1 IC 衰减曲线">
                <ic-decay-interpretation-section />
              </n-tab-pane>
              <n-tab-pane name="group-returns" tab="4.2 分组累计净值">
                <group-returns-interpretation-section />
              </n-tab-pane>
            </n-tabs>
          </n-space>
        </n-tab-pane>
      </n-tabs>
    </n-card>

    <n-alert type="warning" :show-icon="false">
      <b>使用顺序建议：</b>
      新写一个因子 → 跑一次评估 → 先看<b>分组单调性</b>和<b>Pearson/Rank IC 是否同号</b>（定方向和信号质量）→
      再看 <b>ICIR / 年度胜率</b>（判稳定性）→ 再看 <b>多空 Sharpe + 成本敏感性</b>（判可交易性）→
      最后上 <b>OOS 切分 + 参数敏感性 + 中性化</b>（判是否为真 alpha）。
    </n-alert>
  </div>
</template>
