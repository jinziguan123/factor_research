# 调研：Microsoft RD-Agent（含 RD-Agent-Quant）

> 收集时间：2026-04-30
> 目的：摸清 RD-Agent 的设计要点，对照本项目找借鉴点，给出分级建议（什么该抄、什么不该抄）。

---

## 1. 一句话定位

**RD-Agent**：微软研究院出品的"让 AI 驱动数据驱动 AI"的研发自动化框架；**RD-Agent-Quant**（即 RD-Agent(Q)）是它在量化金融上的实例，号称首个数据中心化的多 Agent 量化研发框架，能在 < $10 LLM 成本下跑出 ~2× 基准 ARR。

| 维度 | 信息 |
|---|---|
| 仓库 | github.com/microsoft/RD-Agent（与 microsoft/qlib 互引） |
| 论文 | arXiv:2505.15155v2（2025）+ Co-STEER arXiv:2407.18690 |
| 技术栈 | Python + LiteLLM（多 LLM 后端）+ Qlib（回测）+ Vue 前端 |
| 量化基准 | 在真实股票市场实验：ARR ~2× 基准因子库、用因子数 -70%、低于 $10 LLM 成本 |
| MLE-bench | 在 75 个 Kaggle 任务上 30.22%，目前榜首 |
| 最近更新 | 2026 ACL Findings 接收（Reasoning as Gradient）；新前端发布；LiteLLM 默认 |

---

## 2. 核心架构（量化场景）

### 2.1 五 Agent 闭环

```
Specification → Synthesis → Implementation → Validation → Analysis
       ↑                                                        ↓
       └────────────────── 反馈 / 行动选择 ──────────────────────┘
```

| Agent | 输入 | 输出 | 职责 |
|---|---|---|---|
| **Specification** | 优化目标 + 市场上下文 | 元组 `S=(B,D,F,M)`：背景假设 / 数据接口 / 输出格式 / 执行环境 | 锚定问题边界 |
| **Synthesis** | 历史假设 + 反馈 + SOTA 集 | 新假设 `h^(t+1)` + 子任务列表 | 提出"试什么"，分解为可执行任务 |
| **Implementation (Co-STEER)** | 任务描述 + 知识库 K | 代码 `c_j` + 执行反馈 `f_j` | 把假设翻译成可跑代码，维护 task-code-feedback 三元组 |
| **Validation** | 候选因子 / 模型 | IC 去重结果（阈值 0.99）+ Qlib 回测指标 | 防重复、防过拟合，落地真实测度 |
| **Analysis** | 实验结果 + 性能状态 | 反馈 + 下一步动作（factor or model） | 评估并用 bandit 选下一轮探什么 |

### 2.2 Co-STEER（实现 Agent 的内部算法）

**核心**：把多个子任务建成 DAG，按"复杂度加权拓扑序"调度——失败的任务复杂度 +δ 后重排，简单任务先做以累积知识。

```
Input: 任务集 T = {t_1, ..., t_n}, 知识库 K
Output: 代码集 {c_1, ..., c_n}

1. 初始化 DAG G=(V,E), V=T
2. 复杂度 α_j = 1
3. while T not empty:
   a. 用 α 加权拓扑排序得 π_s
   b. for t_j in π_s:
      - 检索 K 中相似任务 → 拿参考代码 c_ref
      - 用 LLM 生成 c_j = I(t_j, c_ref, K)
      - 执行 → 得反馈 f_j
      - K ← K ∪ {(t_j, c_j, f_j)}
      - 失败：α_j += δ, break 重排
      - 成功：T ← T \ {t_j}
```

**关键洞察**：失败惩罚而非丢弃，让简单任务先成功 → 知识库优先扩充可用样例 → 难任务后做时有更多参考。

### 2.3 知识森林（Knowledge Forest）

- **节点**：假设的 Sentence-BERT 嵌入 `h_t ∈ R^d`
- **边**：隐式聚类（agglomerative + 余弦相似度）
- **演化模式**：
  - 局部精化（diagonal blocks）：同一概念多步迭代
  - 方向切换（cluster transition）：达到瓶颈后跳到新主题
  - 战略回访（strategic revisitation）：后期回到早期 cluster 用积累的知识再尝试

实验观察：36 次试验里 8 次入选 SOTA，跨 5/6 个聚类——说明多方向探索产生互补信号。

### 2.4 Bandit 调度器（在 Analysis Agent 里）

**问题建模**：双臂上下文 bandit：动作 A = {factor, model}，根据 8 维性能向量决定下一轮优化方向。

**上下文向量**（8D）：

```
x_t = [IC, ICIR, Rank(IC), Rank(ICIR), ARR, IR, -MDD, SR]^T
```

**线性奖励 + Thompson Sampling**：
- 每个 arm 维护贝叶斯线性回归后验 `θ^(a) ~ N(μ^(a), P^(a)^-1)`
- 采样 → 选 argmax → 观察奖励 → 更新所选 arm 的后验

消融实验：bandit 调度优于纯 factor / 纯 model / 轮转 baseline，IC、ARR、SOTA 选中数皆最高。

### 2.5 关键数据结构

**因子任务对象**：

```python
FactorTask = {
  id: str,
  hypothesis: str,              # 自然语言描述
  mapping_fn: Φ,                # ℝ^(ℓ×P) → ℝ^m
  window_length: ℓ,
  output_dim: m,
  dependencies: List[task_id],  # DAG 边
  status: pending | running | success | failed,
  feedback: str,
  generated_code: str,
}
```

**模型任务对象**：

```python
ModelTask = {
  id, hypothesis, architecture, input_shape, loss_fn,
  training_regime, status, feedback, generated_code,
}
```

**知识库三元组**：`(task_description, code, execution_feedback{pass/fail, error_msg, perf_metrics})`

---

## 3. 量化场景三条 workflow

| 场景 | CLI 入口 | 描述 |
|---|---|---|
| 联合 factor+model 优化 | `rdagent fin_quant` | 双臂 bandit 交替 12h |
| 仅 factor | `rdagent fin_factor` | 单链路 6h |
| 仅 model | `rdagent fin_model` | 单链路 6h |
| 报告抽取因子 | （专项 scenario） | 从研报 PDF 自动抽 → 实现 → 评估 |

---

## 4. 与本项目的对照

### 4.1 架构定位

| 维度 | RD-Agent-Quant | 本项目（factor_research） |
|---|---|---|
| 主要驱动 | **AI 自演化**（闭环跑） | **人在回路**（用户驱动每步） |
| 核心动作 | 自动提假设 → 自动生成代码 → 自动跑 → 自动选下一步 | 用户拍板每个 run / 每次评估 |
| LLM 用途 | 假设生成 + 代码生成 + 反馈解读（核心） | `factor_assistant` 单次代码生成（边缘） |
| 反馈闭环 | 跑完结果回灌 LLM 决定下一轮 | 跑完结果只展示给用户看 |
| 任务对象 | DAG / status / feedback 完整建模 | 各 service 自管，无统一 task schema |
| 多 Agent | 5 个角色 + bandit 调度 | 单流水线（无 agent 概念） |

### 4.2 模块对照表

| RD-Agent 模块 | 本项目对应 | 差距点 |
|---|---|---|
| Specification Agent | 无（隐式在 `body: dict`） | 没有"研究上下文"对象，所有 run 配置散落在 body 字段 |
| Synthesis Agent | 无 | 假设由用户脑内产生，没有 LLM 提议 |
| Co-STEER 实现 Agent | `factor_assistant.py` | 单次生成，无 task DAG，无知识库，无失败重排 |
| Validation Agent | `eval_service.py` + `backtest_service.py` | 已有指标计算；缺 IC 去重（0.99 阈值）；缺与"知识库"绑定 |
| Analysis Agent + Bandit | 无 | 用户自己看 dashboard 决定下一步 |
| Knowledge Forest | 无 | 历史 run 是平铺列表，无聚类 / 嵌入索引 |
| Run Logging | `fr_*_runs` MySQL 表 + payload_json | 已有结构化日志（这是亮点）；缺 hypothesis / feedback 维度 |
| 因子注册表 | `FactorRegistry` + `BaseFactor` 子类 | 这是本项目优势——RD-Agent 没强 typed 注册表 |
| 实盘订阅 | `subscription_service` + worker | RD-Agent 不做实盘，这是本项目独有 |

---

## 5. 借鉴点（按 ROI 分级）

### L1 ⭐⭐⭐ 高 ROI、低成本（建议立即做）

#### L1.1 给 `factor_assistant` 加 IC 去重 + 自动评估闭环

现状：`factor_assistant` 生成代码 → 落盘 → **结束**。用户得自己点"评估"才能知道好坏。
借鉴：RD-Agent 的 Validation Agent 在生成后**自动跑 IC 验证 + 0.99 阈值去重**。

**具体可做**：
- 生成因子保存后，自动派发一个 `eval` run（小池子、短窗口，比如沪深 300 × 60 天），快速给个"健康度分数"
- 在 `fr_factor` 表加 `auto_eval_run_id` + `auto_eval_summary`，列表页直接显示"IC 0.04 / 健康"或"IC -0.001 / 噪音"
- 如果 IC > 0.99 与已有因子相关，前端打 ⚠️"与 X 高度同源"提示

成本：1-2 天工作量（已有 `eval_service.evaluate_factor_panel` 可直接调），收益：因子助手不再是"黑箱代码生成器"。

#### L1.2 引入 `task_id` + 状态机 + 反馈字段（统一 RunRecord 抽象）

现状：`fr_eval_runs` / `fr_backtest_runs` / `fr_signal_runs` / `fr_composition_runs` 各有自己的 status 字段，结构相似但分散。
借鉴：RD-Agent 用统一的 `(task_description, code, feedback)` 三元组，跨场景一致。

**具体可做**：
- 新建 MySQL 视图或公共 `fr_run_log` 跨表汇总（task_type, run_id, hypothesis, status, feedback, created_at）
- 给所有 service `_update_status` 增加 `feedback: str | None` 参数：失败时必填 error_message + LLM 友好的"问题诊断"
- 不动现有表结构，纯加字段，向后兼容

成本：1 天，收益：将来想接 LLM 反馈闭环（L2.1）有公共数据基底。

#### L1.3 历史因子的"假设字段"

现状：`fr_factor` 只有 `display_name` + `description`，没有"为什么我建这个因子"的 hypothesis 字段。
借鉴：RD-Agent 把 `hypothesis` 当一等公民，每个 task 都带。

**具体可做**：
- `fr_factor` 加 `hypothesis: TEXT` 列（迁移：未填的老因子留空）
- 因子助手 LLM 生成时强制输出 `hypothesis`（在 system prompt 里加要求 + JSON schema 约束）
- 列表页"鼠标悬停 / 详情面板"显示 hypothesis

成本：半天，收益：以后用户回头看为什么建这个因子，不至于全靠记忆。

### L2 ⭐⭐ 中 ROI、中等成本（建议 1-2 个迭代后做）

#### L2.1 因子助手的"反馈循环"（轻量 Co-STEER）

现状：用户输入"我想要个 RSI 改良版"→ LLM 出代码 → 用户拿到。如果代码跑不出来，用户得自己在前端报错信息和 LLM 之间来回粘贴。
借鉴：Co-STEER 的失败 → 复杂度 +δ → 重试机制。

**具体可做**：
- factor_assistant 改成两阶段：generate → auto_test（导入 + 跑一次小 panel）
- 失败时把 traceback + factor 代码再丢回 LLM 修，最多 3 轮
- 知识库（最简版）：在每次生成时把"3 个最相似的已有因子代码"塞进 prompt 作 few-shot

成本：3-5 天，收益：因子助手成功率从"能跑就好"提升到"能跑且 IC 不为零"。

#### L2.2 IC 0.99 去重检查器

现状：用户可能建 5 个因子其实彼此 IC > 0.95，浪费算力。
借鉴：RD-Agent Validation 阶段强制去重。

**具体可做**：
- 新建 `fr_factor_corr` 缓存表（symbol_id × factor_id × date 简化为汇总指标）
- 创建因子时跑一次"与所有已有因子的 IC 相关性"，> 0.99 的提示用户确认
- 前端因子列表加"显示与本因子高相关的因子" tab

成本：3 天，收益：避免冗余因子库膨胀。

### L3 ⭐ 高 ROI 但重大改造（建议先观察 + 后期评估）

#### L3.1 多 Agent 自演化模式

把 factor_assistant 升级成 RD-Agent 那样的"自动跑"模式：用户给一个目标（"找一个对消费板块有效的反转因子"）→ 系统自动迭代提假设 / 写代码 / 跑评估 / 选下一步。

**为什么先不做**：
- 工程量极大（5 个 Agent + bandit + 知识森林）
- LLM token 消耗 / 时间成本不可控
- 个人量化研究通常想要"AI 提建议、我拍板"，不是"AI 全自动"——本项目的"人在回路"定位本身可能更合理
- RD-Agent 论文也承认在变化市场下 online adaptation 是 future work

**评估时机**：等 L1 + L2 做完，且确认有"批量探索因子空间"的真实需求时再考虑。

#### L3.2 Bandit 调度器（单独引入也可）

如果将来用户做了**多个 active 订阅** + **多组合参数扫描**，可以用 bandit 决定"下一轮把算力给谁刷"。但当前项目订阅数量级是个位数，bandit 收益不显著。

#### L3.3 知识森林 / Sentence-BERT 嵌入索引

将来因子库 > 500 个时，用嵌入聚类做"相似因子探索 / 推荐"。当前规模不需要。

---

## 6. 不建议借鉴的部分（YAGNI）

| RD-Agent 特性 | 为什么不抄 |
|---|---|
| 完整的 5 Agent 角色拆分 | 本项目是单用户研究平台，不需要把决策切成多个 LLM 调用——一个 prompt 干完省 token |
| MLE-bench 式 benchmark 跑分 | 本项目业务定位是 A 股因子研究，不是通用 ML 工程 agent |
| Co-STEER 全部 DAG 调度 | 本项目因子之间没真依赖关系（单因子独立计算），DAG 退化成 list |
| 完整的"开题 → 假设 → 代码 → 跑 → 学" LLM 自驱动闭环 | 个人量化的痛点是"想法多、时间少"，AI 应该当 copilot 而不是 cofounder |

---

## 7. 落地建议（最短路径）

如果准备动手，按这个顺序最划算：

1. **本周**：L1.3（hypothesis 字段，半天）→ 让历史因子有可解释性
2. **下周**：L1.1（factor_assistant 自动评估，1-2 天）→ AI 生成的因子立刻有"健康度"标签
3. **下下周**：L1.2（统一 RunRecord 反馈字段，1 天）→ 给将来的反馈闭环铺数据基底
4. **观察期**（1-2 个迭代）：评估 L1 三件事的真实使用频率
5. **如果 L1 证明 AI 路径价值**：上 L2.1 反馈循环（最像 RD-Agent 但本项目化的形态）
6. **L2.2 去重检查**：等因子库 > 30 个再做

---

## 8. 参考资料

- [microsoft/RD-Agent GitHub](https://github.com/microsoft/RD-Agent)
- [microsoft/qlib GitHub](https://github.com/microsoft/qlib)
- [R&D-Agent-Quant 论文 arXiv:2505.15155v2](https://arxiv.org/html/2505.15155v2)
- [Qlib: An AI-oriented Quantitative Investment Platform (Microsoft Research)](https://www.microsoft.com/en-us/research/publication/qlib-an-ai-oriented-quantitative-investment-platform/)
- Co-STEER 算法：arXiv:2407.18690
- 技术报告别名：aka.ms/RD-Agent-Tech-Report
