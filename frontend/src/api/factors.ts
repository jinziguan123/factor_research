// 因子 API 层
//
// 读：list / detail / source code
// 写：PUT 覆写源码 / DELETE 删因子 / POST 空白模板新建（不经 LLM）
//
// 查看范围：所有因子均可读取源码。
// 编辑范围：所有因子均可 PUT 覆写源码（业务因子覆写会修改 git working tree，
// 前端按 factor.editable 字段分级显示黄色/红色警示）。后端覆写前自动备份到
// backend/factors/.backup/，响应里 backup_path 字段告知前端展示。
// 删除范围：仍仅限 backend/factors/llm_generated/ 下的因子，业务因子 DELETE 返回 403。
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { client } from './client'
import { computed, type Ref } from 'vue'

export interface Factor {
  factor_id: string
  display_name: string
  category: string
  description: string
  /** 研究假设：作者写的"为什么相信这个因子有 alpha"主观陈述（方向 + 机制 + 适用前提）。
   * 借鉴 RD-Agent 把 Hypothesis 作为一等公民的设计；旧因子未填留空字符串。 */
  hypothesis?: string
  params_schema: Record<string, any>
  default_params: Record<string, any>
  supported_freqs: string[]
  version?: number
  /** 仅详情接口返回：当前因子源码是否位于 llm_generated/，据此决定是否展示"编辑源码 / 删除"按钮。 */
  editable?: boolean
  /** L2.D 血缘 / SOTA 字段（list 接口已 enrich，详情接口同样返回）。 */
  parent_factor_id?: string | null
  parent_eval_run_id?: string | null
  generation?: number
  is_sota?: 0 | 1 | number
  root_factor_id?: string | null
}


export interface FactorLineage {
  factor_id: string
  self: any
  ancestors: { factor_id: string; display_name: string; generation: number; is_sota: number }[]
  descendants: { factor_id: string; display_name: string; generation: number; is_sota: number }[]
  same_root_sota: string | null
  root_factor_id: string
}

/** GET /api/factors/{id}/lineage，详情页族谱区块用。 */
export function useFactorLineage(factorId: Ref<string>) {
  return useQuery<FactorLineage>({
    queryKey: ['factor-lineage', factorId],
    queryFn: () => client.get(`/factors/${factorId.value}/lineage`).then(r => r.data),
    enabled: () => !!factorId.value,
  })
}

/** PUT /api/factors/{id}/sota，切换 SOTA 标记。 */
export function useSetSota() {
  const qc = useQueryClient()
  return useMutation<
    { factor_id: string; is_sota: number },
    any,
    { factor_id: string; is_sota: boolean }
  >({
    mutationFn: ({ factor_id, is_sota }) =>
      client.put(`/factors/${factor_id}/sota`, { is_sota }).then(r => r.data),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ['factors'] })
      qc.invalidateQueries({ queryKey: ['factor', vars.factor_id] })
      qc.invalidateQueries({ queryKey: ['factor-lineage'] })
    },
  })
}

export interface FactorCode {
  factor_id: string
  code: string
  editable: boolean
}

export interface FactorMutationResult {
  factor_id: string
  display_name: string
  category: string
  description: string
  hypothesis?: string
  version: number
  /**
   * PUT /api/factors/{id}/code 成功时，返回覆写前的备份路径（相对 repo 根）。
   * 新因子首次 PUT（文件原本不存在）为 null。POST 新建因子不返回此字段。
   */
  backup_path?: string | null
}

export interface FactorQuery {
  /** 按分类过滤 */
  category?: string
  /** 模糊搜索 factor_id / display_name / description / hypothesis */
  keyword?: string
  /** 仅 SOTA / 仅非 SOTA */
  is_sota?: boolean
}

/** 获取全部因子列表（支持可选筛选） */
export function useFactors(query?: Ref<FactorQuery | undefined>) {
  return useQuery<Factor[]>({
    queryKey: computed(() => ['factors', query?.value ?? {}]),
    queryFn: () => {
      const params: Record<string, string> = {}
      const q = query?.value
      if (q?.category) params.category = q.category
      if (q?.keyword) params.keyword = q.keyword
      if (q?.is_sota !== undefined) params.is_sota = String(q.is_sota)
      return client.get('/factors', { params }).then(r => r.data)
    },
  })
}

/** 获取所有因子分类（去重），供筛选下拉框使用 */
export function useFactorCategories() {
  return useQuery<string[]>({
    queryKey: ['factor-categories'],
    queryFn: () => client.get('/factors/categories').then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })
}

/** 获取单个因子详情 */
export function useFactor(factorId: Ref<string>) {
  return useQuery<Factor>({
    queryKey: ['factor', factorId],
    queryFn: () => client.get(`/factors/${factorId.value}`).then(r => r.data),
    enabled: () => !!factorId.value,
  })
}

/** 获取单个因子源码（所有已注册因子均可读）。 */
export function useFactorCode(factorId: Ref<string>, enabled: Ref<boolean>) {
  return useQuery<FactorCode>({
    queryKey: ['factor_code', factorId],
    queryFn: () => client.get(`/factors/${factorId.value}/code`).then(r => r.data),
    // 弹窗关闭时关掉查询，避免后台无意义重试
    enabled: () => !!factorId.value && enabled.value,
    // 编辑器里要拿最新磁盘内容，不走缓存，避免多次编辑看到陈旧 code
    staleTime: 0,
    gcTime: 0,
  })
}

/** PUT /api/factors/{id}/code：覆写源码（允许 backend/factors/ 下所有因子，覆写前自动备份）。 */
export function useUpdateFactorCode() {
  const qc = useQueryClient()
  return useMutation<FactorMutationResult, any, { factor_id: string; code: string }>({
    mutationFn: ({ factor_id, code }) =>
      client.put(`/factors/${factor_id}/code`, { code }).then(r => r.data),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['factors'] })
      qc.invalidateQueries({ queryKey: ['factor', vars.factor_id] })
      qc.invalidateQueries({ queryKey: ['factor_code', vars.factor_id] })
    },
  })
}

/** DELETE /api/factors/{id}：删文件 + 从注册表摘除（仅限 llm_generated/）。 */
export function useDeleteFactor() {
  const qc = useQueryClient()
  return useMutation<{ deleted: string }, any, string>({
    mutationFn: (factor_id) =>
      client.delete(`/factors/${factor_id}`).then(r => r.data),
    onSuccess: (_data, factor_id) => {
      qc.invalidateQueries({ queryKey: ['factors'] })
      qc.removeQueries({ queryKey: ['factor', factor_id] })
      qc.removeQueries({ queryKey: ['factor_code', factor_id] })
    },
  })
}

/** POST /api/factors：用用户填的源码直接创建（走 llm_generated/，不经 LLM）。 */
export function useCreateFactor() {
  const qc = useQueryClient()
  return useMutation<FactorMutationResult, any, { factor_id: string; code: string }>({
    mutationFn: (body) =>
      client.post('/factors', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['factors'] })
    },
  })
}
