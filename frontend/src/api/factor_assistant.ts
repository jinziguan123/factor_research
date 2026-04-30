// 因子助手 API 层（Phase 0）
//
// 只有一个 mutation：把自然语言描述发给后端，后端调 LLM、做 AST 安全校验后
// 把生成的 .py 文件落盘到 backend/factors/llm_generated/。
// 前端在成功回调里 invalidate 因子列表 —— 后端启动了热加载 watchdog 时几乎实时
// 出现；没开热加载的话需要用户手动刷新或我们额外调 /api/factors/reload。
//
// 超时说明：全局 axios client 默认 30s（适合普通 CRUD 快失败），但 LLM 调用
// 可能长达分钟级（后端 OPENAI_TIMEOUT_S 默认 60s、可配到 120s）。所以这里单独
// 放大到 180s —— 给后端 httpx 留够上游等待 + 网络往返余量，避免浏览器先断。
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { client } from './client'

// 浏览器侧要比后端 OPENAI_TIMEOUT_S 大一点，保证后端有机会把超时错误作为 502
// 回给浏览器，而不是浏览器自己先断然后后端日志一堆悬空请求。
const LLM_REQUEST_TIMEOUT_MS = 180_000

export interface GenerateFactorIn {
  description: string
  hints?: string | null
  /**
   * 可选：data URI 列表（`data:image/...;base64,...`），最多 4 张、每张 ≤ 2MB。
   * 让用户用 K 线截图辅助 vision 模型理解因子意图；后端同步使用、不落盘、用完即抛。
   */
  images?: string[] | null
  /**
   * 可选：给定 pool_id 时生成因子后自动派发一次 60 天 IC 评估（auto-eval）。
   * 留空跳过；后端响应 auto_eval_run_id 给前端跳详情页。
   */
  auto_eval_pool_id?: number | null
}

export interface GenerateFactorOut {
  factor_id: string
  display_name: string
  category: string
  description: string
  /** 研究假设（方向 + 机制 + 适用前提）。LLM 强制填，不会为空。 */
  hypothesis: string
  default_params: Record<string, any>
  code: string
  saved_path: string
  /** 若 auto_eval_pool_id 传入且派发成功，返回评估 run_id；否则 null。 */
  auto_eval_run_id?: string | null
}

/** 调 /api/factor_assistant/translate，生成并落盘一个新的 LLM 因子。 */
export function useGenerateFactor() {
  const qc = useQueryClient()
  return useMutation<GenerateFactorOut, any, GenerateFactorIn>({
    mutationFn: (body) =>
      client
        .post('/factor_assistant/translate', body, {
          timeout: LLM_REQUEST_TIMEOUT_MS,
        })
        .then(r => r.data),
    onSuccess: () => {
      // 让 FactorList 自动拉最新列表；新因子被热加载扫进来后这里就能显示。
      qc.invalidateQueries({ queryKey: ['factors'] })
    },
  })
}


export interface NegateFactorIn {
  factor_id: string
  auto_eval_pool_id?: number | null
}

/** L2.A 反向因子：调 /api/factor_assistant/negate，AST 改写一个 negated 版本。
 *
 * 用于 EvalDetail 诊断卡片"反向"按钮——评估显示多空 Sharpe 为负时一键
 * 生成 ``<orig>_neg`` 因子并自动派发 60 天评估。AST 改写路径不调 LLM，
 * 失败可能性低；用 axios 默认 30s timeout。
 */
export function useNegateFactor() {
  const qc = useQueryClient()
  return useMutation<GenerateFactorOut, any, NegateFactorIn>({
    mutationFn: (body) =>
      client.post('/factor_assistant/negate', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['factors'] })
    },
  })
}
