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

---

## Task 4: L2.D 因子进化 + 血缘 + SOTA 选择（~1.5 天）

**用户校准**：原 L2.B"代码自修 retry"是隐式安全网，跟用户真实需求"研究层面进化"
不是一回事。L2.B 保留作为底层，新加 L2.D 显式因子进化 + 血缘可视化 + SOTA。

### 关键概念

- **进化（evolve）**：用户读完 v_n 的评估反馈后，点按钮让 LLM 基于
  (原代码 + hypothesis + metrics + feedback + 用户额外指令) 生成 v_{n+1}
- **血缘**：parent_factor_id 形成链；root_factor_id 是同链最早祖先（用于"同链
  SOTA 唯一"和族谱查询）
- **SOTA**：同链下用户标记一个最优因子；进一步进化默认从 SOTA 出发

### Schema（migration 015）

```sql
ALTER TABLE fr_factor_meta
  ADD COLUMN parent_factor_id   varchar(64) DEFAULT NULL,
  ADD COLUMN parent_eval_run_id varchar(64) DEFAULT NULL,
  ADD COLUMN generation         tinyint NOT NULL DEFAULT 1,
  ADD COLUMN is_sota            tinyint NOT NULL DEFAULT 0,
  ADD COLUMN root_factor_id     varchar(64) DEFAULT NULL,
  ADD INDEX idx_root (root_factor_id),
  ADD INDEX idx_parent (parent_factor_id);
```

- root_factor_id NULL = 自己就是根（v1 / 手写因子 / negate 出来的因子）
- 同 root 下 is_sota=1 至多一个（应用层保证，不加 unique 约束让它柔性）

### 后端（Files & Steps）

**Files:**
- Modify: `backend/services/factor_assistant.py`（加 `evolve_factor`）
- Modify: `backend/runtime/factor_registry.py`（持久化 + list 暴露新字段）
- Modify: `backend/api/routers/factor_assistant.py`（加 `POST /evolve`）
- Modify: `backend/api/routers/factors.py`（加 `PUT /sota` + `GET /lineage`）
- New: `backend/scripts/migrations/015_factor_meta_evolve_lineage.sql`
- Test: `backend/tests/test_factor_assistant.py` / `test_factor_registry.py`

**evolve_factor 流程**：
1. 拿 parent factor 源码 + hypothesis；若给了 parent_eval_run_id，再读它的
   structured + payload + feedback_text
2. 决定 new factor_id = `<root>_evo<next_generation>`（root 用 parent.root 或
   parent 自己）
3. 构造 system prompt（保持核心思路、调整细节）+ user prompt（携带上述上下文 +
   extra_hint）
4. 复用现有 `_call_openai_compatible` + 反馈循环 + AST 校验链路
5. 落盘 + 写 fr_factor_meta 时填 parent / root / generation
6. 可选派发 auto-eval

**SOTA 切换**：
- `PUT /api/factors/{factor_id}/sota` body `{is_sota: bool}`
- True 时把同 root 其它行 is_sota=0；False 直接清当前

**lineage 查询**：
- `GET /api/factors/{factor_id}/lineage` 返回 `{ancestors: [...], descendants: [...], same_root_sota: factor_id?}`
- ancestors 沿 parent_factor_id 上溯到根；descendants 用 SQL `WHERE parent_factor_id = ?` 一层（MVP）

### 前端

**EvalDetail（success 状态）**：
- "🧬 进化下一代"按钮 + dialog（额外指令文本框 + auto_eval_pool 下拉）
- 触发后跳到新 factor 的 EvalDetail（auto-eval 派发后的 run_id）

**FactorDetail**：
- 族谱区块（n-descriptions）：父代链接 / 子代列表 / generation / SOTA 状态切换按钮（⭐）
- 朴素列表渲染，不上 react-flow（YAGNI）

**FactorList**：
- 因子卡片标题旁加 ⭐ 徽章（is_sota=1 时显示）

### Tests

- evolve_factor mock LLM 返回新 payload，验证 parent / root / generation 写入
- SOTA 切换：同 root 唯一性
- lineage 查询返回正确 ancestors / descendants

### 不做的事

- 不上 react-flow / 树状图（FactorDetail 内文本列表足够）
- 不做"基于 SOTA 自动选下一代探索方向"——bandit / 智能调度仍是 L3
- 不强制 same-root SOTA unique 约束（应用层切换时 reset 旧 SOTA 即可）
