# 量化交易因子工程基础

> 面向本项目 `factor_research` 的因子研究者速查手册。
> 风格：**第一性原理 + 本项目代码定位**，不讲空话。
> 每一节都告诉你：这东西是什么、为什么需要、代码在哪、踩过什么坑。

---

## 0. 为什么要做因子研究

量化交易归根到底就一件事：**对未来收益做有正期望的预测**。

因子（Factor）就是把"预测信号"数字化的一列数：每个交易日、每只股票给一个数值，代表"我认为它接下来会涨/跌多少"的相对强弱。

因子研究的终极问题只有一个：

> **这列数值，对下一期（T+1、T+5、T+10）的横截面收益，有没有稳定的预测力？**

所有指标（IC、分组净值、多空收益）都是在回答这个问题的不同侧面。

---

## 1. 基本数据结构与术语

### 1.1 宽表（wide）vs 长表（long）

本项目一律用**宽表**：

```
                600000.SH  600036.SH  000001.SZ  ...
2025-01-02      0.012      -0.003     0.021
2025-01-03      0.015      -0.001     0.019
...
```

- **index**：交易日 `trade_date`
- **columns**：股票代码 `symbol`
- **value**：当天的因子值 / 收益 / 价格 / ...

所有 `metrics.py` 里的函数都吃这种宽表，原因：
- 横截面（同一天跨所有股票）= 取一行
- 时序（同一股票跨时间）= 取一列
- pandas 对齐 `align(join="inner")` 一句话解决两张表对齐

### 1.2 横截面 vs 时序

- **横截面（cross-section）**：锁定某一天，看不同股票之间的差异。因子评估里 IC、分组、换手率都是横截面概念。
- **时序（time-series）**：锁定某只股票，看它自己随时间的变化。回测的净值曲线、Sharpe 是时序概念。

好因子的核心要求是**横截面可比**：同一天里，因子值高的股票接下来确实比因子值低的股票涨得多。

### 1.3 股票池（universe）

**股票池 = 参与横截面计算的股票集合**。本项目在 [backend/storage/data_service.py](../backend/storage/data_service.py) 的 `resolve_pool` 里按 `pool_id` 解析成 symbol 列表。

常见池：
- **全市场**：所有 A 股（本次你选的"全量股票"就是这个），约 5000+ 只。
- **指数成分**：沪深 300、中证 500、中证 1000。流动性好，回测干净。
- **行业池 / 自定义池**：按行业、市值、主题定义。

选池的核心权衡：
- 池越大 → 横截面分散度越好，但噪声股多（ST、低流动性、新股）会污染排名。
- 池越小 → 池内同质化高，部分因子（尤其是规模、流动性类）失去区分度。

### 1.4 频率与预热期（warmup）

- **频率（freq）**：本项目当前只做 `1d`（日频）。基本所有基本面因子都是日频及以上。
- **预热期（warmup_days）**：因子计算需要历史窗口。比如 20 日动量需要至少 20 个交易日的历史，否则前 20 天的因子值是 NaN。
- 本项目在 `base_factor.py` 里每个因子实现 `required_warmup(params)`，由 `EvalService` 自动往前多取数据。见 [neg_return_argmax_rank.py:50-54](../backend/factors/custom/neg_return_argmax_rank.py:50)。

---

## 2. 前复权（qfq）与数据准备

### 2.1 为什么要前复权

A 股有**除权除息**：分红、送股、配股会让股价"凭空跳水"。
- 不复权：2024-06-15 分红后股价从 100 跳到 97，pct_change 给你一个 -3% 的假跌幅。
- **前复权（qfq）**：把历史所有价格按复权因子调整，让今天价格保持不变，历史价格往下压。
- 后复权（hfq）：反过来，历史不变、今天往上拔。

**研究用 qfq**：因为我们要让"今天的价格"保持真实（策略下单用的就是今天的价格），历史价格只是为了算收益率正确。

本项目 [backend/storage/data_service.py](../backend/storage/data_service.py) 的 `load_panel(..., adjust="qfq")` 已经把复权因子算好，所有收益率都是 qfq 收益率。

### 2.2 未来函数（look-ahead bias）

研究里最致命的坑，没有之一。

**定义**：计算 T 日的因子值时，用到了 T+k（k≥0）才能知道的信息。

典型犯罪现场：
1. 用当天收盘价算因子，然后用当天收盘买入 —— 但真实世界你下单的时候不知道收盘价。
2. 用当天的 VWAP 排序选股，再假设你用当天 VWAP 买入。
3. 归一化时用了全样本均值 / 标准差，但"全样本"包含未来。
4. 股票池本身用了未来信息筛选（例如"市值 > X 的票"—— X 来自未来）。

本项目的约定（必看）：
- **因子 T 日计算 → 用 T+1 日收益评估**。`eval_service.py` 里 `fwd_rets = {k: close.shift(-k) / close - 1}`，shift(-k) 相当于把未来挪到今天，在对应位置 eval，没有未来函数。
- 因子如果依赖当天数据，约定用收盘后数据，但**下单发生在 T+1**。
- 禁止在因子内部做全样本标准化。要做也得做 rolling / expanding。

---

## 3. 评估框架：怎么证明一个因子有效

本节对应代码：[backend/services/metrics.py](../backend/services/metrics.py) + [backend/services/eval_service.py](../backend/services/eval_service.py)。

### 3.1 IC（信息系数，Information Coefficient）

**定义**：每日横截面里，因子值与未来收益的 **Pearson 相关系数**。

```python
# metrics.py 里的 cross_sectional_ic
# 对每一天 dt：
IC_dt = corrcoef( factor[dt, :], forward_return[dt, :] )
```

- 值域 [-1, 1]。每天一个值，得到一条 IC 序列。
- **IC > 0**：因子值越高的股票，下一期收益越高（正向因子）。
- **IC < 0**：因子值越高的股票，下一期收益越低（反向因子，取负号即可使用）。
- **|IC| 越大越强**。业内经验：
  - |IC_mean| > 0.03 已经算不错
  - |IC_mean| > 0.05 是优秀因子
  - |IC_mean| > 0.1 基本是数据穿越或者未来函数了（怀疑人生一下）

### 3.2 Rank IC（Spearman）

**定义**：同上，但用**排名**算相关系数。

为什么要 Rank IC？
- Pearson IC 被极端值主导。某天一只票涨停（+10%），因子值也极高，IC 可能被这一条数据强行拉到 0.3。
- Rank IC 只关心"相对排名"，抗极端值。
- 实盘选股时我们也只关心"top 组 vs bot 组"的排序，排名更贴近策略语义。

**IC 和 Rank IC 不一致怎么办**：
- 两者同号但 Rank IC 更大 → 因子对大部分股票排序稳定，少数极端票拖了 Pearson。**正常，优先信 Rank IC**。
- 两者异号 → 高度怀疑因子只对极端股票有预测力，对主体分布反而错。这是你这次 `NegReturnArgmaxRank` 评估里看到的现象（IC = -0.003，Rank IC = +0.006）。
- |IC| 都接近 0 → 因子无效。

### 3.3 IC 的派生指标（ic_summary）

对应 [metrics.py:76 ic_summary](../backend/services/metrics.py:76)。

| 指标 | 公式 | 含义 | 经验阈值 |
|---|---|---|---|
| `ic_mean` | mean(IC_t) | 平均预测力 | \|·\| > 0.02 及格 |
| `ic_std` | std(IC_t) | IC 波动，越小越稳 | - |
| `ic_ir` | mean / std | **IC 信息比率**，最重要的单指标 | \|·\| > 0.3 及格，>0.5 优秀 |
| `ic_win_rate` | P(IC_t > 0) | IC 为正的日期占比 | > 55% 及格 |
| `ic_t_stat` | mean / (std/√n) | IC 是否显著偏离 0 | \|·\| > 2 算显著 |

**IC IR 为什么最重要**：单看均值容易被"少数爆发日 + 长期噪声"蒙骗。IR 把稳定性考虑进去——一个每天都稳稳产生 0.02 IC 的因子，远好于一个 50% 天数 +0.1、50% 天数 -0.1 的因子。

### 3.4 分组回测（quantile portfolio）

代码位置：[metrics.py:111 group_returns](../backend/services/metrics.py:111)。

**做法**：每天把股票按因子值从小到大分成 N 组（通常 5 或 10），计算每组的等权平均收益。

- 分 5 组（五分位，quintile）是默认。
- 期望：`组1 < 组2 < 组3 < 组4 < 组5`（正向因子，下一期收益单调递增），叫 **单调性（monotonicity）**。
- 画图：每组累计收益曲线，好因子是"扇形张开"，差因子是"五条线绞在一起"。

**为什么分组而不是直接用 IC**：
- IC 只告诉你"有相关性"，没告诉你"能不能赚钱"。非线性因子 IC 可能不高但 top 组爆涨、bot 组爆跌。
- 实盘下单是"买入 top 组、卖空 bot 组"，分组更贴近策略。

### 3.5 多空组合（long-short）

代码位置：[metrics.py:197 long_short_series](../backend/services/metrics.py:197)。

**定义**：每日多空收益 = top 组收益 - bot 组收益。

- 如果因子是反向的（IC < 0），top 跌 bot 涨，多空收益为负 → 取负号使用。
- **多空累计净值**：`(1 + 日多空收益).cumprod()`。这就是你之前那张空白的"多空净值"图本应显示的东西。
- **多空 Sharpe**：`mean / std * sqrt(252)`。好因子 > 1.0，优秀 > 2.0。
- **多空年化**：`mean * 252`。

**本项目里多空还会做 dropna**（`long_short_series` 末尾），这就引出了下一节的坑。

### 3.6 换手率（turnover）

代码位置：[metrics.py:154 turnover_series](../backend/services/metrics.py:154)。

**定义（本项目约定单边）**：
```
turnover_t = | top_set_t \ top_set_{t-1} | / | top_set_t |
```
即：今天 top 组里有多少只是昨天没有的。值域 [0, 1]。

- 0 = 组成员完全不变（因子几天都没动过，策略不用换仓）。
- 1 = 每天全换（极高频，手续费吃掉收益）。

**为什么重要**：
- 换手率 ×（单边手续费 + 滑点） ≈ 策略的交易成本拖累。
- 学术因子在**纸面上** Sharpe 很高，但日均换手 80% 乘以 5bp 成本 = 年化 100% 的成本拖累 → 实盘负收益。
- 通常能容忍的日均换手 < 30%。

### 3.7 本项目在 eval 里还额外算了什么

- **因子值直方图** `value_histogram`：一眼看因子值分布。正态 / 偏态 / 离散 / 有 fat tail。
- **long_short_n_effective**：多空序列 dropna 后的样本数。**这是你这次遇到告警的原因**（见下一节）。

---

## 4. 踩坑录（重点）

### 4.1 rank / argmax 类因子与 qcut 退化（你这次踩的坑）

**症状**：
- `多空有效样本数 = 1 天`
- `多空 Sharpe = 2.5e10`（离谱的天文数字，std=1e-12 兜底除出来的）
- `换手率 = 0.00%`，图全空
- 分组累计净值图上"有数据"但告警说多空只有 1 天

**根因**（你的 `NegReturnArgmaxRank`）：

1. `Ts_ArgMax(signed_sq, window=5)` 每天每只股票只能吐 {0,1,2,3,4} 这 5 个整数。
2. `rank(axis=1, method='average', pct=True)` 做横截面百分位排名。虽然有几千只股票，但 ArgMax 源值只有 5 档，绝大多数股票排名相同，拿到的 pct rank 也最多只有 5 个不同的值（实际可能更少，因为 ties 的平均排名还可能重复）。
3. `pd.qcut(values, n_groups=5, duplicates='drop')` 本来要切 5 段。当 5 个分位点落在只有 ~5 个不同值的数据上，边界几乎全重合，`duplicates='drop'` 把它们合并成 2~4 段。
4. `group_returns` 里 `reindex(range(5))` 把缺失的组（往往是最顶 / 最底）填成 NaN。
5. `long_short_series` 做 `dropna()` → 几乎所有日期的多空都是 NaN → 只剩 1 天侥幸完整。
6. `turnover_series` 同理，top 组要么全空要么恒定 → 换手率几乎无可用日期。

**本项目的告警就是为这个场景加的**，见 `long_short_metrics` 的 `long_short_n_effective`。

**解决路径**：
- **首选**：换连续因子。动量、反转、波动率、换手率类都是连续值。
- **其次**：同一因子，把 `n_groups` 从 5 改成 3。3 组比 5 组更宽容于 ties。
- **再次**：修改因子本身——不要做 `rank`，直接暴露 `Ts_ArgMax` 的整数位置，再配合更大的窗口（window 越大，整数取值范围越大）。
- **不要**：把 `duplicates='drop'` 改成 `'raise'` —— 那只会让评估直接报错，治标不治本。

### 4.2 幸存者偏差（survivorship bias）

- **症状**：回测时股票池只包含"活到今天还在交易"的股票 → 死掉的、退市的、ST 的被自动剔除 → 回测收益系统性高估。
- **解法**：股票池要**按日期重建**，用"当天存在的股票集合"，而不是"今天存在的股票集合"。
- 本项目目前 `resolve_pool` 返回的是静态 symbol 列表，**这是潜在的偏差源**。后续接入指数成分历史数据后要改成按日期查询。

### 4.3 停牌、涨跌停、新股

- **停牌**：当天没有成交，因子值用昨日数据、或直接 NaN。不能让它参与下单（买不到 / 卖不掉）。
- **涨停**：想买买不到。
- **跌停**：想卖卖不了。
- **新股**：上市前 N 天（60~120）价格严重失真（炒新、换手率异常），通常从池中剔除。

本项目当前 MVP 阶段对这些处理**较粗**：`load_panel` 对停牌日返回 NaN，IC / 分组里会被 notna mask 掉。涨跌停限制在 Task 10 回测层处理。

### 4.4 n_groups 与池大小的匹配

经验法则：**每组至少 30 只股票**。
- 分 5 组 → 池至少 150 只。
- 分 10 组 → 池至少 300 只。
- 不到这个量就把 n_groups 降下来（3 组），否则组内收益估计噪声巨大。

本项目做了硬拦截：`eval_service.py` 里 `if len(symbols) < n_groups_req: raise ValueError`，池小于 n_groups 会直接拒绝。

### 4.5 样本外稳定性

因子在 2015-2020 很好 ≠ 2021-2025 也好。最基本的检查：
- **分段查看 IC**：按年 / 按牛熊切片，看 IC 是否有大幅漂移。
- **本项目现在没做这个**，IC 是整个评估期的均值。作为研究者，画 IC 滚动均值（rolling 60 日）自己看一下。

### 4.6 交易成本

学术因子基本不算手续费。实盘至少要减：
- 印花税（卖 0.05%）
- 券商佣金（双边约 0.03%~0.1%）
- 滑点（视流动性，A 股主板约 0.1%~0.3%）

一个粗略估算：日均换手 30% 的策略，年化成本 ≈ 30% × 252 × 0.15% ≈ 11% 年化拖累。

### 4.7 因子间相关性（多因子建模的前置）

单因子研究完了你会想组合多个。坑：**两个相关性 0.9 的因子合起来就是一个因子**。
- 算因子之间的相关性矩阵，高相关的（|ρ| > 0.7）留一个或做正交化。
- 本项目当前是单因子评估，多因子合成是后续路线（不在 MVP 内）。

---

## 5. 因子分类速览

| 类别 | 直觉 | 代表 | 本项目示例 |
|---|---|---|---|
| **动量（momentum）** | 过去涨得多的继续涨（中长期） | 12-1 月动量、52 周高点 | [momentum_n.py](../backend/factors/momentum/momentum_n.py) |
| **反转（reversal）** | 短期涨得多的回落 | 5 日反转、20 日反转 | [reversal_n.py](../backend/factors/reversal/reversal_n.py) |
| **波动率（volatility）** | 低波动跑赢高波动（A 股反着也成立过） | 年化波动、GARCH | [volatility/](../backend/factors/volatility) |
| **成交量 / 流动性** | 换手率、Amihud 非流动性 | amihud_illiq、volume_ratio | [volume/](../backend/factors/volume) |
| **价值（value）** | 便宜的涨，贵的跌 | BP、EP、SP | 本项目暂未实现（需财务数据） |
| **质量（quality）** | 盈利好的涨 | ROE、毛利率 | 本项目暂未实现 |
| **情绪 / 技术** | 资金面 / 市场情绪 | 融资融券、北向、换手 | [custom/](../backend/factors/custom) |
| **Alpha 101 等公式因子** | WorldQuant 公开的 101 个量价因子 | ArgMax、ts_rank、correlation | `neg_return_argmax_rank.py` 就是改编 |

A 股当前（2020~2025）经验：
- **反转** 在中短期（5~20 日）有效，动量在中期（60~120 日）有效，长期（12-1）反而弱（跟美股相反）。
- **小市值** 长期溢价明显，但波动极大。
- **低波动** 跑赢高波动是稳定现象。
- **价值因子** 近 10 年效果减弱（全球共识）。

---

## 6. 本项目代码地图（因子研究的每一步在哪）

| 步骤 | 代码位置 | 作用 |
|---|---|---|
| 因子定义 | [backend/factors/](../backend/factors) 下各子目录 | 继承 `BaseFactor`，实现 `compute` 和 `required_warmup` |
| 因子注册与加载 | [backend/runtime/factor_registry.py](../backend/runtime/factor_registry.py) | 扫描并注册所有因子类 |
| 因子值计算入口 | [backend/engine/base_factor.py](../backend/engine/base_factor.py) | 定义 `FactorContext`、执行上下文 |
| 数据读取 | [backend/storage/data_service.py](../backend/storage/data_service.py) | qfq 价格、股票池、因子值缓存 |
| 评估主流程 | [backend/services/eval_service.py](../backend/services/eval_service.py) | 串联：取池 → 算因子 → 算 fwd return → 算指标 → 落库 |
| 评估数学库 | [backend/services/metrics.py](../backend/services/metrics.py) | IC / Rank IC / 分组 / 多空 / 换手 / 直方图，全部纯函数 |
| 回测服务 | [backend/services/backtest_service.py](../backend/services/backtest_service.py) | 基于因子值做分组或 top-K 策略回测 |
| 前端展示 | [frontend/src/pages/](../frontend/src/pages) | 评估详情页、回测详情页 |

---

## 7. 推荐阅读 / 继续学习

论文 / 经典：
- Grinold & Kahn, *Active Portfolio Management*（IC、IR、Fundamental Law 的出处）
- Fama & French, 1992/1993 三因子、2015 五因子
- Kakushadze, "101 Formulaic Alphas"（WorldQuant 的 101 个因子）

国内社区：
- 聚宽、米筐、优矿的研报专栏
- 东方证券、天风、国盛、广发的金工研究报告（免费公开）

自查清单（每次开发新因子过一遍）：
- [ ] 因子公式有没有用到 T+k 数据？
- [ ] 因子值是连续的还是离散的？离散值 < 10 档时 n_groups 要慎重选。
- [ ] 预热期够不够？前 warmup 天的值是不是应该 NaN？
- [ ] IC 和 Rank IC 同号吗？
- [ ] IC IR > 0.3 吗？
- [ ] 分组净值五条线张开了吗？
- [ ] 换手率是不是离谱地高（>50%/日）或离谱地低（<2%/日）？
- [ ] 样本外（out-of-sample）上因子还稳定吗？

---

## 附：今天遇到那张评估图的完整解读

你跑 `NegReturnArgmaxRank` 全量池评估后看到的：

| 指标 | 值 | 解读 |
|---|---|---|
| IC 均值 | -0.0031 | 几乎为 0，Pearson 意义上无预测力 |
| Rank IC 均值 | +0.0060 | 同样接近 0，但方向与 IC 相反，说明极端票和主体票预测方向不一致 → 因子不稳 |
| IC IR | -0.0408 | 远 < 0.3，无显著性 |
| IC 胜率 | 49.32% | ≈ 50%，等同抛硬币 |
| 多空 Sharpe | 2.5e10 | **警告**：std=1e-12 兜底产出的伪值，样本只剩 1 天 |
| 多空年化 | 39.90% | 同上，被单日主导，不可信 |
| 多空有效样本 | 1 天 | qcut 退化，top/bot 常缺 |
| 平均换手率 | 0.00% | top 组常空，无可算样本 |

**结论**：这个因子在全量池 + 5 分组下不适合评估。换成连续因子（比如反转、动量），或把分组数降到 3 重跑一次即可。
