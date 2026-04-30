# RD-Agent L2：反馈循环 / 反向因子 / LLM 解读 Payload 实施计划

> 关联调研：[docs/research/2026-04-30-qlib-rd-agent.md](../research/2026-04-30-qlib-rd-agent.md)
> 前置：L1（hypothesis + feedback_text + auto-eval）已 merge 到 master（commit `b733f79`）

## Goal

把 L1 的"诊断只能读"升级为"诊断能行动"——三件事独立发布、各自闭环：

1. **反向因子**：用户读到"多空 Sharpe 为负——试将因子取负号"时，一键生成 negated 版本并自动 auto-eval（最简）
2. **反馈循环**：factor_assistant 生成的因子若 import / smoke 失败，自动把 traceback 喂回 LLM 改写，最多 3 轮（中等）
3. **LLM 解读 Payload**：`_build_eval_feedback` 从规则版升级到 LLM 版，能看 IC 衰减 / 分组单调 / 健康度等结构化指标的全貌（最大）

## Architecture

| 项 | 入口 | 落点 |
|---|---|---|
| L2.A 反向因子 | EvalDetail 诊断卡片"反向"按钮（只在多空 Sharpe < 0 时显示）| `POST /api/factor_assistant/negate` 派发；产出新 factor `<orig_id>_neg` + 自动 eval |
| L2.B 反馈循环 | `factor_assistant.translate_and_save` 内部 | 失败时把 traceback / AST 错喂回 LLM，max_retries=3，递增温度 |
| L2.C LLM 解读 Payload | `eval_service` 末尾（替换 `_build_eval_feedback`）| 调 LLM，prompt 包含完整 payload，输出诊断文本；失败回落到规则版 |

## Tech Stack

- 沿用现有 LLM 接入（`_call_openai_compatible`）
- AST 改写用 stdlib `ast` 模块（L2.A：取负号）
- L2.B 用知识库：临时方案存在内存 dict（同进程内复用），不持久化（YAGNI）
- L2.C 失败回落：try/except 包裹 LLM 调用，`_build_eval_feedback` 改名为 `_build_eval_feedback_rule_based` 作为兜底

## Tasks

### Task 1: L2.A 反向因子（最简，~半天）

**Files:**
- Modify: `backend/services/factor_assistant.py`（加 `negate_factor(factor_id) -> GeneratedFactor`）
- Modify: `backend/api/routers/factor_assistant.py`（加 `POST /negate`）
- Modify: `frontend/src/api/factor_assistant.ts`（加 hook）
- Modify: `frontend/src/pages/evals/EvalDetail.vue`（诊断卡片"反向"按钮）
- Test: `backend/tests/test_factor_assistant.py`（4 个 case：正常 negate / 已存在 / AST 改写正确性 / 调用 auto-eval）

**关键设计**：
- 不调 LLM，直接 AST 改写：找到 `compute` 方法的 `return ...` 表达式，包一层 `-(...)` 或 `(...) * -1`
- factor_id 自动加后缀 `_neg`；display_name 加"（取负）"；hypothesis 翻译为反向（"原假设：值大未来涨；取负后：值大未来跌"）
- 落盘后立即 auto-eval（沿用 `_dispatch_auto_eval`）

**Step 1**：写失败测试 `test_negate_factor_writes_negated_compute`
**Step 2**：实现 `negate_factor`：load 原源码 → AST 解析 → 找 `def compute` → 包装 return → 写入 `<orig>_neg.py`
**Step 3**：路由 + 前端按钮
**Step 4**：测试通过 + commit

### Task 2: L2.B 反馈循环（中等，~1 天）

**Files:**
- Modify: `backend/services/factor_assistant.py`（`translate_and_save` 内部加 retry loop）
- Modify: `backend/tests/test_factor_assistant.py`（3 个 case）

**关键设计**：
- max_retries=3；每轮失败把 (上次代码、错误类型、错误消息) 加进 messages 喂回 LLM
- 失败类型：
  - AST 校验失败（已知错误，最易喂）
  - import 失败（语法 OK 但运行时炸——加一个 smoke import 步骤）
  - LLM JSON 解析失败（直接给原始响应让 LLM 修自己）
- temperature 第 N 轮逐步增加（0.2 → 0.4 → 0.6），鼓励差异化
- 失败仍然抛 `FactorAssistantError`，但 message 里附"已尝试 N 次"

**Step 1**：写失败测试 `test_translate_retries_on_ast_failure`（mock LLM 第一次返回坏代码、第二次返回好代码）
**Step 2**：抽 `_run_translate_with_retry(messages, max_retries) -> dict`
**Step 3**：把 AST / smoke / JSON 三类失败统一收口进 retry
**Step 4**：测试通过 + commit

### Task 3: L2.C LLM 解读 Payload（最大，~1.5 天）

**Files:**
- Modify: `backend/services/eval_service.py`（`_build_eval_feedback` 加 LLM 分支）
- New: `backend/services/llm_eval_diagnose.py`（独立模块，prompt + LLM 调用）
- Modify: `backend/tests/test_metrics.py`（4 个 case：LLM 成功 / LLM 失败回落 / 完整 payload 进 prompt / 输出格式校验）

**关键设计**：
- 新模块隔离 LLM 依赖：`_build_eval_feedback` 先尝试 LLM，失败 catch 回到规则版
- prompt 输入：完整 payload（IC 系列 / 分组累计净值 / 健康度 / Alphalens 增强等），裁剪到 LLM context 内
- 输出格式：JSON `{"summary": "...", "actionable_suggestions": [...]}`，service 拼成 `feedback_text`
- 失败兜底：LLM 超时 / JSON 不合法 → log warn + 用规则版

**Step 1**：写测试 `test_llm_diagnose_payload_returns_summary`（mock LLM 返回 fixed JSON）
**Step 2**：写 `_build_diagnose_prompt(payload, hypothesis, factor_id)` + `diagnose_with_llm`
**Step 3**：`_build_eval_feedback` 加 try/except LLM → 回落规则
**Step 4**：测试通过 + commit

## Execution Order & Quick Wins

1. **先 L2.A**（最简，半天）—— 立刻能感受到"诊断 → 行动"的闭环
2. **再 L2.C**（中等，1.5 天，但依赖度低）—— 让诊断从"机械"变"智能"
3. **最后 L2.B**（中等，1 天）—— 提升生成成功率，已经成熟时再做

## Frequent Commits

每个 Task 内部至少 1 commit；Task 完成单独 push 一次。三个 Task 在同一分支（`claude/rdagent-l2-loops`），最后整体 PR。

## DoD（每个 Task）

- 后端单元测试 +3 以上
- 前端 vue-tsc 通过
- 路径上的旧测试 0 回归
- 分支 push，对应 commit message 标 `[L2.A/B/C]`
