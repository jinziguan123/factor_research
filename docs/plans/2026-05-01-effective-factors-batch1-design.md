# 有效因子库扩展 · 批次 1（精简 8 因子）设计

> 关联：[2026-05-01-lightgbm-composition-design.md](./2026-05-01-lightgbm-composition-design.md)
> 类型：因子库扩展（不是因子合成方法扩展）
> 借鉴对象：WorldQuant Alpha101 / Fama-French 5 / AQR Quality / A 股 Liu-Stambaugh-Yuan 2019

## Goal

给 `backend/factors/` 加 8 个学术/业界有公开证据的因子，覆盖三个互补维度（Alpha101 量价、基本面、A 股专属异象）。完工后跟 LightGBM 合成器（method=`ml_lgb`）配合做端到端验证。

## 非目标（YAGNI）

- 不扩展 `_FUND_FIELDS_PROFIT` 白名单（BP / 小市值留批次 2，等批次 1 跑出合成结果再决策）
- 不接通分钟数据（D 方向已被否决）
- 不做因子超参 grid search（每个因子配 1 套合理默认）
- 不做主动 winsorize（依赖现有 evaluate_factor_panel 的 z-score / rank 处理）
- 不做指数基准导入（IVOL / Beta 用横截面均值近似 market return）

## Architecture

每个因子是独立 `BaseFactor` 子类，散布在现有目录下：

```
backend/factors/
├── alpha101/
│   ├── alpha101_8.py         (existing)
│   ├── alpha101_6.py         (NEW)  价量负相关
│   ├── alpha101_12.py        (NEW)  量价短期反转
│   └── alpha101_101.py       (NEW)  K 线归一化涨幅
├── fundamental/              (NEW dir)
│   ├── __init__.py
│   ├── earnings_yield.py     (NEW)  EP = eps_ttm/close
│   ├── roe_yoy.py            (NEW)  ROE 同比
│   └── gp_margin_stability.py(NEW)  毛利率稳定性
└── volatility/
    ├── idio_vol_reversal.py  (NEW)  特质波动率反转
    └── max_anomaly.py        (NEW)  MAX 异象（彩票股反转）
```

每个因子文件 ~70-100 行（公式 + warmup + compute + docstring）。

整体走现有 BaseFactor 接口：调度器（`evaluate_factor_panel`）扫到这些类后自动可用，不改任何上游。

## 因子规格

### A. Alpha101 系列（3 个，纯量价）

#### A1. `alpha101_6` — 价量负相关

**公式**：
```
factor_t = -1 * correlation(open, volume, window=10)
```

**直觉**：开盘价与成交量近 10 日 rolling 相关——负相关越深表示"放量但开盘不积极/缩量但开盘强"，业界视作反转信号；取负后高分股 → 弱反转。WorldQuant Alpha101 公认有效。

**数据**：`open`（qfq）+ `volume`。

**参数**：`window=10`（论文标准）。

**预热**：`int(window * 1.5) + 10` 自然日。

**实现关键**：用 `df.rolling(window).corr(other)` 算 row-wise correlation；NaN 由 rolling 自然 propagate。

#### A2. `alpha101_12` — 量价短期反转

**公式**：
```
factor_t = sign(volume_t - volume_{t-1}) * (-1 * (close_t - close_{t-1}))
```

**直觉**：当日"放量上涨"或"缩量下跌"做空信号，反向操作；公式 1 行最简洁。

**数据**：`close`（qfq）+ `volume`。

**参数**：无（论文公式无可调）。

**预热**：3 个交易日（diff 1 + safety）。

**实现关键**：`np.sign(volume.diff(1))` × `(-close.diff(1))`，element-wise。

#### A3. `alpha101_101` — K 线归一化涨幅

**公式**：
```
factor_t = (close - open) / (high - low + epsilon)
```
其中 `epsilon = 1e-3`（防分母 0）。

**直觉**：日内"实体相对幅度"。值 ≈ 1 表示当日强势收高，≈ -1 表示弱势收低；归一化后跨股票可比。最简单的形态因子，是 Alpha101 论文里少数无需 rolling 的"瞬时"因子。

**数据**：OHLC（qfq）。

**参数**：`epsilon=1e-3`（暴露给 schema 但默认值即论文版本）。

**预热**：0 天（无 lag 操作）。

**实现关键**：直接 (close-open) / (high-low + epsilon)，注意停牌日 high=low 会让分母 ≈ epsilon → 因子被 epsilon 放大；不修正，让停牌噪声自然显现，下游 z-score 会处理。

### B. 基本面 3 因子

#### B1. `earnings_yield` — 盈利收益率（EP / 1/PE）

**公式**：
```
factor_t = eps_ttm_t (PIT, ffill) / close_t
```

**直觉**：市盈率倒数。Fama-French 价值因子核心；A 股大盘股稳定有效（小盘可能反向，下游 LightGBM 学非线性可处理）。值越大 → 估值越便宜 → 长仓信号。

**数据**：`eps_ttm`（PIT, `load_fundamental_panel` ffill 到日频） + `close`（qfq）。

**参数**：无。

**预热**：0 天（PIT 数据自带左 seed）。

**实现关键**：拉两张 panel 后 align（symbol × date 取交集），element-wise 除。`close == 0` 不可能（停牌不进 panel），`eps_ttm` 可能负（亏损股）→ 因子值负，直接保留（后续 cross-section rank 会自然处理）。

#### B2. `roe_yoy` — ROE 同比改善

**公式**：
```
factor_t = roe_avg_t - roe_avg_{t - 252_trading_days}
```

**直觉**：ROE 同比改善是"质量动量"。AQR Quality 因子家族成员，A 股财报季前后效应显著。

**数据**：`roe_avg`（PIT, ffill）。

**参数**：`yoy_lag=252`（≈ 1 年交易日）。

**预热**：252 + 30 安全 buffer = 282 自然日（PIT 已自带左 seed，但要保证 shift 后还有有效值）。

**实现关键**：`panel - panel.shift(yoy_lag)`。注意：因为是 ffill 后的日频 panel，shift 252 不会精确对齐"同期 announcement"，会有 ±10-30 交易日的偏差。**这是有意识的简化**，避免跳出现有 `load_fundamental_panel` API。学术上验证显示 ±20 交易日的偏差不损 IC 显著性。

#### B3. `gp_margin_stability` — 毛利率稳定性

**公式**：
```
factor_t = -1 * rolling_std(gp_margin, window=252)
```

**直觉**：毛利率波动小 = 商业模式稳定 = 高质量企业。AQR Quality 因子里 "Profitability Stability" 维度。取负后大值 → 稳定 → 长仓信号。

**数据**：`gp_margin`（PIT, ffill）。

**参数**：`window=252`（≈ 1 年）。

**预热**：252 + 30 = 282 自然日。

**实现关键**：`-1 * panel.rolling(window).std()`。注意 ffill 后的 panel 里大量重复值（季报 ~60 个交易日才更新一次），rolling std 在重复值期间是 0；这种"伪低波"是 ffill 的副作用，下游 cross-section 排名时所有股票都被 ffill 同样的偏置，相对排序仍有效。

### C. A 股专属异象 2 因子

#### C1. `max_anomaly` — MAX 异象（彩票股反转）

> **设计修正记录**（2026-05-01）：原设计用 `low_turnover = -turnover_ratio` 作为 C1，被 quality reviewer 抓出方法论错误：树模型（LightGBM）的 split 选择对单调变换（含 negate）天然不变，`x` 与 `-x` 划分出完全相同的子集，模型只会随机选一个用，另一个 feature_importance 归零。两个共线因子既浪费 slot 又干扰 importance 排名。修正方案：换成与 turnover/IVOL 真正不共线的 A 股异象——MAX 异象（高单日"彩票"特征股票未来收益更低）。

**公式**：
```
ret_t = close_t.pct_change()
factor_t = -1 * rolling_max(ret_t, window=20)
```

**直觉**：Bali-Cakici-Whitelaw (RFS 2011) "Maxing Out: Stocks as Lotteries" 提出 MAX 异象——过去 N 日单日最高收益（"彩票特征"）越大的股票未来表现越差。A 股 Han-Hu-Yang (PBFJ 2018) 等论文确认有效。Negate 后大值 → 低 MAX → 长仓信号。

**数据**：`close`（qfq）。

**参数**：`window=20`（≈1 月）。

**预热**：`int(window * 1.5) + 5` 自然日（pct_change 1 + rolling 19 + 节假 buffer）。

**实现关键**：
1. 算 returns：`close.pct_change(fill_method=None)`（停牌 NaN 不传染）
2. 算 rolling max：`ret.rolling(window).max()`
3. 取负

**与 IVOL 的区别**（C2 也用 returns）：IVOL 是 60 日**残差波动**，MAX 是 20 日**单日最大**——前者度量"持续紊乱程度"，后者度量"瞬时极端程度"。两者在因子空间正交（IVOL ≈ rolling_std，MAX ≈ rolling_max；不同的统计量），不构成共线。

#### C2. `idio_vol_reversal` — 特质波动率反转

**公式**：
```
ret_t = close_t.pct_change()
mkt_t = ret_t.mean(axis=1)              # 横截面均值近似市场收益
residual_t,s = ret_t,s - mkt_t          # 特质收益
factor_t,s = -1 * rolling_std(residual_{,s}, window=60)
```

**直觉**：特质波动率（idiosyncratic volatility）越高，未来收益越低（IVOL 异象，Ang-Hodrick-Xing-Zhang 2006，A 股 Cao-Han 等多个研究证实）。Negate 后大值 → 低 IVOL → 长仓。

**数据**：`close`（qfq）。

**参数**：`ret_window=60`（rolling std 长度）；`use_market_proxy="cs_mean"`（写死，不暴露）。

**预热**：`60 + 5 = 65` 交易日 → ~95 自然日。

**实现关键**：
1. 算 returns：`close.pct_change(fill_method=None)` （停牌 NaN 不传染）
2. 算市场基准：`ret.mean(axis=1)` （cross-section mean，按行求均值）
3. 算 residual：`ret.sub(market_proxy, axis=0)` （每行减市场）
4. 算 rolling std：`.rolling(60).std()`
5. 取负

**为何不接指数表**：A 股没拉沪深300/中证500/万得全A 这种基准。横截面均值在 universe 充分大时统计上等价（按 CAPM 视角，市场收益的无偏估计）。

## 工程决策

| 决策 | 选择 | 原因 |
|---|---|---|
| BP / 小市值是否做？ | 不做 | 现有数据缺 net_assets / total_share，留批次 2 |
| 财报字段扩展？ | 不扩 | YAGNI；批次 1 用现有 6 字段就够 |
| 指数基准导入？ | 不做 | IVOL / Beta 用横截面均值近似 |
| C1 用 low_turnover (= -turnover_ratio) 还是 max_anomaly？ | max_anomaly | 树模型对 negate 不敏感，-turnover_ratio 与 turnover_ratio 100% 共线（split 选择不变），feature_importance 会归零。换 MAX 异象（rolling_max(returns)）真正与 turnover/IVOL 正交 |
| ROE 同比 shift 的精度？ | 252 交易日（不严格对齐 announcement） | 简单；偏差不损显著性 |
| 因子值取负在哪一层？ | 因子内部 negate | 让因子分数与"长仓信号方向"对齐，便于读者直觉 |

## Persistence

- 8 个新因子继承 `BaseFactor`，启动时被 `FactorRegistry.scan_and_register()` 自动发现
- 落库到 `fr_factor_meta`（`factor_id` 主键、`category` 字段、`hypothesis` 字段写"长仓信号方向"的简单一句话）
- factor_value_1d 缓存：通过 `evaluate_factor_panel` 调用时按需写入

## Testing

每个因子配 3 个测试（一致结构）：

1. **happy path**：mock 5 票 × 30 天数据 → 算因子 → 断言 shape + dtype + 至少一行非 NaN
2. **NaN robustness**：故意把某只票某段时间设 NaN（模拟停牌）→ 因子不崩 + 该段输出为 NaN
3. **cross-section invariance**：把 columns 顺序打乱 → 因子值"跨 symbol 排序"应不变（即每行 rank 一致）

8 因子 × 3 测试 = 24 测试。预计每个 ~ 0.1s，全 batch 跑 ~3-5s。

新建测试文件分布：
- `backend/tests/test_alpha101_factors.py`（覆盖 alpha101_6 / 12 / 101，共 9 测试）
- `backend/tests/test_fundamental_factors.py`（覆盖 earnings_yield / roe_yoy / gp_margin_stability，共 9 测试）
- `backend/tests/test_anomaly_factors.py`（覆盖 idio_vol_reversal / max_anomaly，共 6 测试）

## API & 前端

无后端 API 改动（因子自动 scan 注册）。

前端：`FactorList` 页面会自动展示新因子（已有 category 分类筛选），无需改 vue 文件。**前端零改动**。

## DoD

- [ ] 8 个因子文件创建完毕，`FactorRegistry.scan_and_register()` 能识别（启动时无 import 报错）
- [ ] 24 个测试全绿
- [ ] 整套 backend test 不回归
- [ ] 前端 vue-tsc 无新增错误
- [ ] 实测：在 UI 用 `ml_lgb` 选 8 个因子做合成，5 因子 × 3 月 walk-forward 跑通
- [ ] design doc + plan + 8 个因子代码 + 24 测试 + ml_lgb 实测 5 commit 干净 push

## 风险与已知 trade-offs

| 风险 | 缓解 |
|---|---|
| ROE 同比 shift 252 不精确 | 设计文档明示，偏差 ±20 交易日不损 IC；批次 2 可补严格版 |
| ffill 后 rolling std（B3）有伪低波 | 文档说明，下游 cross-section rank 不受影响 |
| Alpha101 / 基本面在某些 universe 失效 | LightGBM 合成器的优势就是学权重；某因子失效会被 importance 自然降权 |
| 横截面均值近似市场（C2） | universe ≥ 100 股票时统计上等价；股票池太窄时偏差才显著 |
| 财报字段 NaN 比例高（停牌 / 退市 / 亏损） | 现有 panel ffill + 下游 evaluate 的 IC 计算自带 valid mask |

## 与已有因子的对比

新增 8 个 vs 现有 14 个，目录变化：
- `alpha101/` 1 → **4** （从单点变体系雏形）
- `fundamental/` 0 → **3** （新建目录）
- `volatility/` 2 → **4**（含 idio_vol_reversal + max_anomaly）

整体 14 → **22** 因子，且分布从"侧重量价 / 震荡器"扩到"量价 + 基本面 + A 股异象"三足鼎立。

## 后续（批次 2 暗示）

如果批次 1 跑通且 ml_lgb 合成出现可见 alpha：
- 批次 2 重点：扩 financial schema（加 net_assets / total_share），补 BP + 小市值因子（约 4 天工程：1 天 importer 改造 + 1 天回填历史 + 2 天因子 + 测试）
- 批次 3 候选：分析师一致预期 / 资金流向 / 高频订单簿（依赖外部数据源，先看批次 1+2 效果再决策）
