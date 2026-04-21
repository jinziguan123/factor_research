# KDJ 系横截面因子（oscillator 组）设计文档

> 日期：2026-04-21
> 状态：已与用户确认，等待进入实施
> 作者：Claude（与用户 jinziguan 共同设计）

## 0. 背景与动机

KDJ 是经典的**时间序列择时震荡指标**，强调同一只股票自身的"超买 / 超卖 / 金叉"。直接把 K 值拿来做横截面打分，存在一个根本疑问：**跨股票时 K 值含义并不天然可比**——A 股的 K=30 可能是超卖反弹机会，B 股的 K=30 可能只是下跌刚开始。

因此"KDJ 因子"不是一个自明对象，**如何把 KDJ 翻译成横截面打分**本身就是研究的核心问题。本设计实现 5 个具有不同语义的 KDJ 衍生横截面因子，让用户用平台的 `/evals` + `/param-sensitivity` 独立评估、横比，筛出在目标 universe 上真正有预测力的变体。

用户意图（确认路径 B）：系统性探索 KDJ 系因子的横截面有效性，注册 5 个衍生变体，自测时段 / 池子由用户自行决定。

## 1. 架构决策

### 1.1 文件组织

新目录 `backend/factors/oscillator/`，5 个因子各一文件（和现有 `reversal/` / `momentum/` 同结构）：

```
backend/factors/oscillator/
├── __init__.py
├── _kdj.py                  # 共享 helper：compute_kdj(high, low, close, n) -> (K, D, J)
├── kdj_j_oversold.py        # ① J 超卖深度
├── kdj_k_pct_rev.py         # ② K 分位反转（标准化版）
├── kdj_cross.py             # ③ K-D 金叉强度
├── kdj_oversold_hinge.py    # ④ 超卖阈值 hinge
└── kdj_divergence.py        # ⑤ 价-J 底背离强度
```

- 下划线前缀的 `_kdj.py` 是纯 helper，不继承 `BaseFactor`，不会被 `FactorRegistry.scan_and_register` 当因子。
- 5 个因子都调 `_kdj.compute_kdj(...)`，KDJ 定义只写一次。

**未选择的替代方案**：单个 `kdj_multi.py` + `mode` 字符串参数。不采用的原因：`mode` 是枚举，参数敏感性扫不了；且单因子名丢失语义，横比表可读性差。

### 1.2 基础参数暴露策略

| 参数 | 语义 | 策略 | 理由 |
|---|---|---|---|
| `n` | RSV 窗口 | **暴露为 params**（可扫） | KDJ 唯一的结构参数，不同 universe 最优 n 可能不同 |
| `alpha` | K / D 的 EMA 平滑系数 | **固定 1/3** | 市面约定俗成，暴露只会让参数面板变长 |
| `J = 3K - 2D` 的系数 | KDJ 定义本身 | **固定** | 改了就不是 KDJ 了 |

## 2. 五个因子定义

### ① `kdj_j_oversold`（J 超卖深度）

- **factor_id**：`kdj_j_oversold`
- **display_name**：`J 超卖深度`
- **公式**：`factor = -J`
- **params_schema**：`{"n": {"type": "int", "default": 9, "min": 3, "max": 60, "desc": "RSV 窗口（交易日）"}}`
- **required_warmup**：`int(n * 3 * 1.5) + 10`
- **方向**：反转（J 越低越看多）

### ② `kdj_k_pct_rev`（K 分位反转）

- **factor_id**：`kdj_k_pct_rev`
- **display_name**：`K 自身分位反转`
- **公式**：`factor = -rolling_pct_rank(K, lookback)`（K 在过去 lookback 日的百分位，取负号）
- **params_schema**：
  - `{"n": 9, min=3, max=60}`
  - `{"lookback": 60, min=10, max=252}`
- **required_warmup**：`int((n * 3 + lookback) * 1.5) + 10`
- **方向**：反转，用"K 在自身历史的位置"消除跨股不可比
- **实现要点**：`K.rolling(lookback).apply(lambda x: (x[-1] > x).mean(), raw=True)`，纯 numpy，不要一行一行 Python

### ③ `kdj_cross`（K-D 金叉强度）

- **factor_id**：`kdj_cross`
- **display_name**：`KDJ 金叉强度`
- **公式**：`factor = K - D`
- **params_schema**：`{"n": 9, min=3, max=60}`
- **required_warmup**：`int(n * 3 * 1.5) + 10`
- **方向**：趋势（金叉看多，不反号）

### ④ `kdj_oversold_hinge`（超卖阈值 hinge）

- **factor_id**：`kdj_oversold_hinge`
- **display_name**：`KDJ 超卖 hinge`
- **公式**：`factor = max(0, threshold - K)`
- **params_schema**：
  - `{"n": 9, min=3, max=60}`
  - `{"threshold": 20, min=5, max=40, desc="K 超卖阈值"}`
- **required_warmup**：`int(n * 3 * 1.5) + 10`
- **方向**：反转，K < threshold 才激活

### ⑤ `kdj_divergence`（价-J 底背离强度）

- **factor_id**：`kdj_divergence`
- **display_name**：`价-J 底背离强度`
- **公式**：
  ```
  j_rebound = J - rolling_min(J, lookback)
  p_rebound = close - rolling_min(close, lookback)
  scale = rolling_std(J, lookback) / rolling_std(close, lookback)
  factor = j_rebound - scale * p_rebound
  ```
  直觉：J 已反弹距离 - 价格已反弹距离（归一化后）。J 先反弹、价格滞后 = 底背离，正值看多。
- **params_schema**：
  - `{"n": 9, min=3, max=60}`
  - `{"lookback": 20, min=10, max=60}`
- **required_warmup**：`int((n * 3 + lookback) * 1.5) + 10`
- **方向**：反转（底背离看多）
- **实现要点**：用 `rolling_min` 近似局部极值，避免真找 local extrema（实现复杂且收敛敏感）。`scale` 用 rolling_std 比值做横截面归一，让两边量级可比；当 `rolling_std(close)` 极小时（比如新股停牌后）用 `scale = 1.0` 兜底避免除零。

## 3. 共享 helper：`_kdj.py`

`compute_kdj(high, low, close, n)` 返回 `(K, D, J)` 三个宽表。约定：

- 输入均为行=日期、列=标的的宽表（`load_panel` 返回的格式）。
- **RSV**：`rsv = (close - low_min_n) / (high_max_n - low_min_n) * 100`；`high_max_n - low_min_n == 0` 时（N 日 K 线完全横盘）返回 NaN，避免除零。
- **K / D**：`K_t = (2/3) * K_{t-1} + (1/3) * RSV_t`；`D_t = (2/3) * D_{t-1} + (1/3) * K_t`。用 `pandas.DataFrame.ewm(alpha=1/3, adjust=False)` 实现，跨列向量化。
- **J**：`3 * K - 2 * D`。J 可以 < 0 也可以 > 100（这是特征、不是 bug）。
- **warm-up / 前 n 行**：前 n-1 行 `low_min_n` / `high_max_n` 还没有完整窗口，RSV 会 NaN，`ewm` 在遇 NaN 时按默认行为跳过——5 个因子 compute 里都会用 `.loc[ctx.start_date:]` 切回评估区间，所以 warmup 前的 NaN 不暴露给评估层。

## 4. 共同工程约定

- 所有因子 `category = "oscillator"`（新分类，前端会自动多一组）。
- 所有因子从 `ctx.data.load_panel(..., field, adjust='qfq')` 取 high / low / close 三个宽表。
- warmup 向左多取 `required_warmup` 天，防 rolling / EMA 在 `start_date` 当天就吐 NaN。
- `fill_method=None`（停牌不填，直接 NaN），和 `reversal_n` 保持一致。
- 返回 `factor.loc[ctx.start_date:]` 切回评估区间。
- 每个因子的模块 docstring 写清楚公式、直觉、预期方向；和现有 `reversal_n.py` 风格对齐，注释写"为什么这么做"而非"这行做什么"。

## 5. 自测交付边界

本设计交付范围：**5 个因子的实现代码 + helper**，跑通 `FactorRegistry.scan_and_register()` 能列出这 5 个因子、前端因子库页能看到 `oscillator` 分组。

**不交付**：任何评估 / 参数敏感性 / 横比分析——这些由用户自己按自己选定的 universe、split_date、时间段去跑。

## 6. 风险与已知局限

- KDJ 的横截面 IC 可能**整体很低**：震荡指标在单边趋势市会一直超买 / 超卖失效。五个变体一起给用户一个结论"KDJ 在横截面上到底有没有信息量"，即使全是 IC ≈ 0 也是有价值的研究结论。
- ⑤ 背离因子的实现是一阶近似（用 rolling_min 替代局部极值）。如果这个因子表现出意外强的 IC，建议用户手工复核一下公式在极端段的输出是否合理——`rolling_min` 近似在 lookback 内价格一直下跌（min 永远是最新那根）时会给出 0，不算强信号；在 V 型底时能捕捉到。
- `kdj_oversold_hinge` 在 K 普遍高于 threshold 时大量标的得 0 分，分组回测分五组时可能第五组只有 0（因为超过一半标的都是 0）导致分组重叠。评估层会打印 qcut 警告，属正常现象，是因子本身结构决定的。

## 7. 后续扩展方向（不在本次交付中）

- KDJ 与经典动量 / 反转因子的正交化版本（`compose` 里做 Gram-Schmidt）
- 分频率 KDJ（周频、月频）
- MACD / RSI / ATR 等其他震荡指标的类似翻译
