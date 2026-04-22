# 因子手册图表解读（IC 衰减）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `因子手册` 页新增“IC 衰减图表解读”教学模块，以同屏并排的“典型好图 vs 典型坏图”交互 mock + checklist 文案，帮助用户快速学会判别标准。

**Architecture:** 采用“页面静态文档 + 前端 fixture + 复用现有图表组件”的最短路径。数据不走后端，直接在 docs 侧维护 mock fixture；渲染层复用 `IcDecayChart.vue` 保证与评估页图表语义一致；`FactorGuide.vue` 通过新增一个可复用的解读区块组件承载 checklist 与双图布局。

**Tech Stack:** Vue 3 + TypeScript + Naive UI + ECharts（vue-echarts）；Vite 构建校验；手工交互验证

**Design doc**: `docs/plans/2026-04-22-chart-interpretation-design.md`（commit `d069575`）

---

## 前置说明（测试策略现实约束）

当前 `frontend/package.json` 没有 `test`/`vitest` 脚本，本计划采用：

1. `npm run build` 作为类型与构建回归检查
2. `npm run dev` 下手工验证交互与响应式布局

本批次不引入新测试框架（YAGNI），待 docs 交互模块扩展到多图后再评估统一前端测试基建。

执行中建议配套技能：
- 异常排查：`@superpowers:systematic-debugging`
- 完工前校验：`@superpowers:verification-before-completion`

---

### Task 1: 落地 IC 衰减教学 fixture（好图/坏图）

**Files:**
- Create: `frontend/src/pages/docs/fixtures/icDecayInterpretation.ts`

**Step 1: 写“失败验证”基线（手工）**

Run:
```bash
cd frontend
npm run dev
```
Expected: `/docs/factor-guide` 页面当前还没有“图表解读（IC 衰减）”模块。

**Step 2: 先写类型与数据结构（最小可用）**

在 `frontend/src/pages/docs/fixtures/icDecayInterpretation.ts` 创建：

```ts
export interface PeriodSeries {
  dates: string[]
  values: (number | null)[]
}

export interface IcDecayMockPayload {
  ic: Record<string, PeriodSeries>
  rankIc: Record<string, PeriodSeries>
}

export interface IcDecayInterpretationFixture {
  good: IcDecayMockPayload
  bad: IcDecayMockPayload
}
```

**Step 3: 填充“好图/坏图”两套 mock**

新增导出常量（示例）：

```ts
export const icDecayInterpretationFixture: IcDecayInterpretationFixture = {
  good: {
    ic: { '1': { dates: ['2025-01-01'], values: [0.058] }, '5': { dates: ['2025-01-01'], values: [0.046] }, '10': { dates: ['2025-01-01'], values: [0.032] } },
    rankIc: { '1': { dates: ['2025-01-01'], values: [0.062] }, '5': { dates: ['2025-01-01'], values: [0.049] }, '10': { dates: ['2025-01-01'], values: [0.037] } },
  },
  bad: {
    ic: { '1': { dates: ['2025-01-01'], values: [0.081] }, '5': { dates: ['2025-01-01'], values: [0.006] }, '10': { dates: ['2025-01-01'], values: [-0.015] } },
    rankIc: { '1': { dates: ['2025-01-01'], values: [0.012] }, '5': { dates: ['2025-01-01'], values: [-0.004] }, '10': { dates: ['2025-01-01'], values: [-0.013] } },
  },
}
```

**Step 4: 构建检查**

Run:
```bash
cd frontend
npm run build
```
Expected: BUILD SUCCESS（无 TypeScript 错误）。

**Step 5: Commit**

```bash
git add frontend/src/pages/docs/fixtures/icDecayInterpretation.ts
git commit -m "feat(因子手册): 新增IC衰减图表解读fixture数据"
```

---

### Task 2: 新增“图表解读区块”组件（checklist + 双图容器）

**Files:**
- Create: `frontend/src/components/docs/IcDecayInterpretationSection.vue`
- Modify: `frontend/src/components/charts/IcDecayChart.vue`（仅在需要时补可选 `height` prop；若不需要则不改）

**Step 1: 写“失败验证”基线（构建）**

先在 `FactorGuide.vue` 临时 import（但组件尚不存在），确认会失败：

```ts
import IcDecayInterpretationSection from '@/components/docs/IcDecayInterpretationSection.vue'
```

Run:
```bash
cd frontend
npm run build
```
Expected: FAIL，提示组件文件不存在（确认接下来创建组件确实消除失败）。

**Step 2: 创建组件骨架（最小渲染）**

在 `frontend/src/components/docs/IcDecayInterpretationSection.vue` 写入：

```vue
<script setup lang="ts">
import { NCard, NGrid, NGridItem, NTag, NSpace } from 'naive-ui'
import IcDecayChart from '@/components/charts/IcDecayChart.vue'
import { icDecayInterpretationFixture } from '@/pages/docs/fixtures/icDecayInterpretation'
</script>

<template>
  <n-card title="图表解读：IC 衰减（好图 vs 坏图）" size="small">
    <!-- checklist + 双图 -->
  </n-card>
</template>
```

**Step 3: 完成 checklist 与并排图布局**

要求：
- 上方 checklist（3-5 条，句式统一，直接可判别）
- 下方双列：左“典型好图”、右“典型坏图”
- 移动端自动改单列（`n-grid` 响应式 span）
- 每张图下有 1 行“读图结论”

**Step 4: 构建检查**

Run:
```bash
cd frontend
npm run build
```
Expected: PASS，组件可被正常编译。

**Step 5: Commit**

```bash
git add frontend/src/components/docs/IcDecayInterpretationSection.vue frontend/src/components/charts/IcDecayChart.vue
git commit -m "feat(因子手册): 新增IC衰减图表解读区块组件"
```

---

### Task 3: 接入 FactorGuide 页面并完成文案融合

**Files:**
- Modify: `frontend/src/pages/docs/FactorGuide.vue`

**Step 1: 写“失败验证”基线（手工）**

保持 `FactorGuide.vue` 未接入组件时，访问页面确认仍看不到“图表解读”模块。

**Step 2: 接入解读模块**

在 `FactorGuide.vue`：
- import `IcDecayInterpretationSection`
- 在现有三大板块后追加“图表解读”模块（保持页面语义顺序：术语/阈值/失败模式/图表解读）
- 在模块前加一段过渡说明（说明这是教学 mock，不代表真实评估结论）

示例插入片段：

```vue
<n-card title="四、图表解读（交互 Mock）" size="small">
  <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
    下图为教学 mock，用于训练“什么样算好、什么样算坏”的判别能力。
  </n-alert>
  <ic-decay-interpretation-section />
</n-card>
```

**Step 3: 构建检查**

Run:
```bash
cd frontend
npm run build
```
Expected: PASS。

**Step 4: 手工验收（桌面 + 移动）**

Run:
```bash
cd frontend
npm run dev
```
Checklist:
- 桌面宽屏：好图/坏图并排显示
- 窄屏：自动单列，无内容重叠
- 图表 tooltip、legend、缩放交互正常
- 文案与图能逐条对照

**Step 5: Commit**

```bash
git add frontend/src/pages/docs/FactorGuide.vue
git commit -m "feat(因子手册): 接入IC衰减好坏对照图表解读模块"
```

---

### Task 4: 收口与回归检查（文案一致性 + 风险兜底）

**Files:**
- Modify: `docs/plans/2026-04-22-chart-interpretation-design.md`（若实施细节偏离，补充“实现备注”）
- Modify: `frontend/src/components/docs/IcDecayInterpretationSection.vue`（仅按验收问题微调）

**Step 1: 回归构建**

Run:
```bash
cd frontend
npm run build
```
Expected: PASS。

**Step 2: 手工冒烟**

路径检查：
- `/docs/factor-guide`
- `/evals/:runId`（确认本次改动未影响评估页图表）

**Step 3: 最终提交**

```bash
git add docs/plans/2026-04-22-chart-interpretation-design.md frontend/src/components/docs/IcDecayInterpretationSection.vue
git commit -m "chore(因子手册): 完成IC衰减图表解读模块收口与回归"
```

---

## 完成定义（Definition of Done）

- `FactorGuide` 页面新增“IC 衰减图表解读”模块
- 使用独立 fixture 文件提供好图/坏图 mock 数据
- 同屏并排对比 + checklist 文案全部可见
- 桌面/移动端布局均可用
- `npm run build` 通过

## 后续增量（非本批次）

按相同模板扩展到：
1. 分组累计净值
2. 多空净值曲线
3. 换手率

每次只新增一张图，保持迭代节奏短、回归成本可控。

