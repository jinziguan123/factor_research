import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  { path: '/', component: () => import('@/pages/dashboard/DashboardPage.vue') },
  { path: '/factors', component: () => import('@/pages/factors/FactorList.vue') },
  { path: '/factors/:factorId', component: () => import('@/pages/factors/FactorDetail.vue') },
  { path: '/pools', component: () => import('@/pages/pools/PoolList.vue') },
  { path: '/pools/new', component: () => import('@/pages/pools/PoolEditor.vue') },
  { path: '/pools/:poolId', component: () => import('@/pages/pools/PoolEditor.vue') },
  { path: '/evals', component: () => import('@/pages/evals/EvalList.vue') },
  { path: '/evals/new', component: () => import('@/pages/evals/EvalCreate.vue') },
  { path: '/evals/:runId', component: () => import('@/pages/evals/EvalDetail.vue') },
  { path: '/backtests', component: () => import('@/pages/backtests/BacktestList.vue') },
  { path: '/backtests/new', component: () => import('@/pages/backtests/BacktestCreate.vue') },
  { path: '/backtests/:runId', component: () => import('@/pages/backtests/BacktestDetail.vue') },
  { path: '/cost-sensitivity', component: () => import('@/pages/cost-sensitivity/CostSensitivityList.vue') },
  { path: '/cost-sensitivity/new', component: () => import('@/pages/cost-sensitivity/CostSensitivityCreate.vue') },
  { path: '/cost-sensitivity/:runId', component: () => import('@/pages/cost-sensitivity/CostSensitivityDetail.vue') },
  { path: '/compositions', component: () => import('@/pages/compositions/CompositionList.vue') },
  { path: '/compositions/new', component: () => import('@/pages/compositions/CompositionCreate.vue') },
  { path: '/compositions/:runId', component: () => import('@/pages/compositions/CompositionDetail.vue') },
  { path: '/klines', component: () => import('@/pages/klines/KlineViewer.vue') },
  { path: '/admin', component: () => import('@/pages/admin/DataOps.vue') },
]
