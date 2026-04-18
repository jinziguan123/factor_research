// 因子助手 API 层（Phase 0）
//
// 只有一个 mutation：把自然语言描述发给后端，后端调 LLM、做 AST 安全校验后
// 把生成的 .py 文件落盘到 backend/factors/llm_generated/。
// 前端在成功回调里 invalidate 因子列表 —— 后端启动了热加载 watchdog 时几乎实时
// 出现；没开热加载的话需要用户手动刷新或我们额外调 /api/factors/reload。
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { client } from './client'

export interface GenerateFactorIn {
  description: string
  hints?: string | null
}

export interface GenerateFactorOut {
  factor_id: string
  display_name: string
  category: string
  description: string
  default_params: Record<string, any>
  code: string
  saved_path: string
}

/** 调 /api/factor_assistant/translate，生成并落盘一个新的 LLM 因子。 */
export function useGenerateFactor() {
  const qc = useQueryClient()
  return useMutation<GenerateFactorOut, any, GenerateFactorIn>({
    mutationFn: (body) =>
      client.post('/factor_assistant/translate', body).then(r => r.data),
    onSuccess: () => {
      // 让 FactorList 自动拉最新列表；新因子被热加载扫进来后这里就能显示。
      qc.invalidateQueries({ queryKey: ['factors'] })
    },
  })
}
