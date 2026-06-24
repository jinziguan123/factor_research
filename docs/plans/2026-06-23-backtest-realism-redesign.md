# 回测真实性改造设计文档

- 日期：2026-06-23
- 状态：设计已定稿，进入实现
- 范围：`backend/services/backtest_service.py` / `cost_sensitivity_service.py` / `api/schemas.py`，新增 `backend/services/execution.py`
- 定位前提：系统最终走向**实盘交易**。回测必须从"理想化指标"升级为"可实现收益"的诚实模拟。

---

## 1. 背景与目标

当前系统有两套评价体系：**评估（eval）** 与 **回测（backtest）**。评估侧用 `close.shift(-k)/close` 度量因子预测力，是学术界通用的理想化口径，可接受。问题出在回测侧——它**沿用了评估侧的理想化假设**，没有完成"现实化"这一本职工作：

1. **成交价用信号日当天 close** → 隐含前视偏差（因子用到了 T 日 close，又假设在 T 日 close 成交）。
2. **没有滑点** → `from_orders` 只传了 `fees`，未传 `slippage`，成交价就是 close 本身。
3. **成本对称且偏低** → 单一 `cost_bps=3`（买卖各万 3），而 A 股卖出含印花税，真实往返成本约万 10+，低估近一半。
4. **涨跌停默认不过滤** → `filter_price_limit=False`。
5. **无成交量容量约束** → 可无限量成交，小市值因子回测严重失真。

### 目标

把回测引擎改造为贴近 A 股实盘的执行模拟器：

- 成交价：**T+1 open（默认）/ T+1 VWAP**，可配置，彻底消除前视。
- 成本：**A 股不对称成本拆分**（佣金双边 + 印花税仅卖出 + 过户费双边）。
- 滑点：**固定 bp + 平方根市场冲击模型**（量相关）。
- 涨跌停：默认开启过滤。
- 容量：**单日成交额 ≤ k% × 当日成交额** 的裁剪。

---

## 2. 现状诊断（精确到代码）

| 环节 | 现状代码 | 问题 |
|------|---------|------|
| 成交价 | `backtest_service.py:475` `size = W * init_cash / close`；`:541` `from_orders(close=close, size=size, fees=cost_bps/1e4)` | 用 T 日 close 成交，前视 |
| 滑点 | 无 `slippage` 参数 | 完全缺失 |
| 成本 | `cost_bps: float = 3.0`（对称） | 低估、未体现卖出更贵 |
| 涨跌停 | `filter_price_limit: bool = False` | 默认关闭；且只过滤选股侧，不处理"卖不掉" |
| 容量 | 无 | 无限量成交 |
| 权重 | `_build_weights` 调仓日 qcut，`rebalance_dates = F.index[::rebalance]` | 逻辑正确，复用 |

数据层能力（已确认可用）：`DataService.load_panel(field=...)` 支持 `open/high/low/close/volume/amount_k`；`amount_k` 单位千元，不受 qfq 复权影响。

---

## 3. 设计原则：评估 vs 回测的边界

| 维度 | 评估侧（eval_service） | 回测侧（backtest_service） |
|------|----------------------|---------------------------|
| 回答的问题 | 因子**有没有预测力** | 拿这个因子**真能赚到钱吗** |
| 成交价 | `close→close`（理想化，**保留不动**） | `T+1 open/vwap`（现实化） |
| 成本 | 无 | A 股不对称成本 + 滑点 + 冲击 |
| 约束 | 无 | 涨跌停 + 容量 |

**本次改造只动回测侧**，评估侧的 close→close forward return 保持不变——它度量的是 alpha 信号本身，理想化是恰当的。两套体系定位不同，不应统一。

---

## 4. 详细设计

### 4.1 成交价口径（T+1 执行）

**核心建模：T 日盘后定目标组合，T+1 成交。**

- 权重 `W` 由 `_build_weights` 在调仓日 T 算出（基于 `F.loc[T]`，即 T 日及之前数据）。
- 执行延迟：`W_exec = W.shift(1)`（首行 NaN → 0）。语义：T 日的目标持仓在 T+1 这一行生效。
- 成交价宽表 `exec_price`：
  - `exec_price = "open"`（默认）：`open` 宽表。
  - `exec_price = "vwap"`：首期用**复权典型价** `(high + low + close) / 3`（全复权价、量纲自洽、无需处理 amount/volume 复权）。真实成交额 VWAP（`amount/volume` 复权）列入 roadmap。
- 目标股数：`size = W_exec * init_cash / exec_price`（`size_type='targetamount'`）。
- `from_orders(close=close, size=size, price=exec_price, ...)`：
  - `price=exec_price` → 在 T+1 行以 open/vwap 成交；
  - `close` 仍用于逐日 mark-to-market 估值。

**前视消除验证（测试要点）**：构造一个"仅在 T 日因子值极高、T+1 价格暴跌"的样本，T 日 close 成交口径会捕获不到亏损，T+1 口径会吃到亏损。详见 §8。

### 4.2 A 股不对称成本模型（2026 现行费率）

| 费项 | 买入 | 卖出 | 默认值（bp） |
|------|------|------|-------------|
| 佣金 commission | ✓ | ✓ | 2.5（双边，最低 5 元先不建模） |
| 印花税 stamp_tax | — | ✓ | 5.0（**仅卖出**，0.05%） |
| 过户费 transfer_fee | ✓ | ✓ | 0.1（双边） |
| **单边合计** | **≈2.6** | **≈7.6** | |

**VectorBT `fees` 是对称的**（买卖同费率），无法直接表达"卖出额外印花税"。解决方案：**构造方向相关的 fees 数组**。

- 方向判定：`trade_dir = W_exec.diff()`；`>0` 买入，`<0` 卖出，首行（建仓）视为买入。
- `buy_fee = (commission_bps + transfer_fee_bps) / 1e4`
- `sell_fee = (commission_bps + stamp_tax_bps + transfer_fee_bps) / 1e4`
- `fees_arr[t,j] = sell_fee if ΔW[t,j] < 0 else buy_fee`，shape 同 close。
- 传 `from_orders(..., fees=fees_arr)`。

**已知近似**：`targetamount + cash_sharing` 下，VectorBT 实际成交方向受可用现金约束，可能与 `W_exec.diff()` 符号不完全一致（如现金不足导致少买）。绝大多数调仓场景一致，作为一阶近似可接受，标注于代码。

### 4.3 滑点 + 平方根市场冲击

VectorBT 的 `slippage` 参数按比例调整成交价，且**自动按成交方向施加**（买入价上浮、卖出价下浮）。我们把固定滑点与量相关冲击合成一个 `slippage` 数组传入：

- 固定滑点：`base_slip = slippage_bps / 1e4`（按板块/流动性可分档，首期统一）。
- 平方根冲击（Almgren 简化）：
  ```
  order_value[t,j]  = |W_exec.diff()[t,j]| * init_cash      # 当期成交额（元）
  daily_amount[t,j] = amount_k[t,j] * 1000                  # 当日成交额（元）
  impact[t,j]       = impact_coef * sqrt(order_value / daily_amount)
  ```
- `slip_arr[t,j] = base_slip + impact[t,j]`，`daily_amount=0` 时冲击置 0（停牌/无成交，配合容量约束兜底）。
- 传 `from_orders(..., slippage=slip_arr)`。

**已知近似**：`order_value` 用复权价×权重估算，`daily_amount` 用原始成交额，比值含 qfq 二阶偏差（qfq factor 近期接近 1）。首期接受，标注。

### 4.4 涨跌停处理

- 选股侧（已实现，复用）：`_compute_price_limit_mask` 按板块阈值（主板 10% / 创业板·科创 20% / 北交所 30% / ST 5%）剔除当日触板票。`filter_price_limit` 默认改为 **True**。
- 卖出侧"想卖却跌停卖不掉"的滞留：在 `from_orders` 框架下需 `order_func_nb` 精确建模，工作量大，**列入 roadmap**。首期在文档与前端提示中明确这一已知限制。

### 4.5 成交量容量约束

单票单日成交额不超过当日成交额的 `max_volume_pct`（默认 0.10）。采用**逐行路径裁剪**（因为实际持仓依赖前一行裁剪结果）：

```
actual = zeros_like(target)
for t in range(n_bars):
    for j in range(n_assets):
        prev      = actual[t-1, j] if t > 0 else 0
        delta     = target[t, j] - prev                 # 目标成交股数
        cap_value = max_volume_pct * daily_amount[t, j]  # 元
        order_value = abs(delta) * exec_price[t, j]
        if order_value > cap_value and order_value > 0:
            delta *= cap_value / order_value             # 按额裁剪
        actual[t, j] = prev + delta
return actual
```

裁剪后被限制的买入会留存现金（符合真实"买不到那么多"）。统一用成交额（元）口径，避开 `volume` 的"股 vs 手"单位歧义。

### 4.6 成本敏感性联动

`cost_sensitivity_service` 复用 `BacktestInputs`。`cost_bps` 语义升级：扫描时把单点 `cost_bps` 作为**滑点+佣金的总等效单边费率**叠加在新成本模型之上，或保留为 `slippage_bps` 维度扫描（二选一，实现时取后者更直观——扫"滑点假设"对收益的侵蚀）。

---

## 5. 接口变更（schema）

`CreateBacktestIn` 新增字段（全部带默认值，向后兼容；旧 `cost_bps` 保留为 deprecated 兼容位，若传入则映射为 `commission_bps*2` 的等效对称费率）：

```python
exec_price: str = "open"            # "open" | "vwap"
commission_bps: float = 2.5         # 佣金，双边
stamp_tax_bps: float = 5.0          # 印花税，仅卖出
transfer_fee_bps: float = 0.1       # 过户费，双边
slippage_bps: float = 5.0           # 固定滑点，双边
impact_coef: float = 0.1            # 平方根冲击系数，0 关闭
max_volume_pct: float = 0.10        # 单日成交额占比上限，0 关闭
filter_price_limit: bool = True     # 默认开启
# cost_bps: float = 3.0             # deprecated，保留兼容
```

`CreateCostSensitivityIn` 同步新增上述字段；扫描维度从 `cost_bps_list` 调整为 `slippage_bps_list`（语义更清晰），保留 `cost_bps_list` 兼容。

---

## 6. 新模块：`backend/services/execution.py`

**纯函数、无 DB 依赖、pickle 友好、可单测**（与 `metrics.py` 同风格）。

```python
def build_exec_price(open_, high, low, close, mode: str) -> DataFrame:
    """T+1 成交价宽表。mode='open' 用 open；'vwap' 用 (high+low+close)/3。"""

def build_fee_array(w_exec: DataFrame, commission_bps, stamp_tax_bps,
                    transfer_fee_bps) -> ndarray:
    """方向相关不对称费用数组（买=佣金+过户，卖=佣金+印花+过户）。"""

def build_slippage_array(w_exec, init_cash, daily_amount, exec_price,
                         slippage_bps, impact_coef) -> ndarray:
    """固定滑点 + 平方根冲击合成 slippage 数组。"""

def apply_volume_cap(target_size, daily_amount, exec_price,
                     max_volume_pct) -> ndarray:
    """逐行路径裁剪：单日成交额 ≤ max_volume_pct × 当日成交额。"""

def shift_for_t1(w: DataFrame) -> DataFrame:
    """W.shift(1) 实现 T+1 执行，首行补 0。"""
```

所有矩阵入参在调用前已按 `(date × symbol)` 对齐（与 close 同 index/columns）。NaN/inf 处理沿用 `metrics.py` 约定：非有限值兜底为 0 或跳过。

---

## 7. 数据流改造（backtest_service）

`_prepare_backtest_inputs` 调整：

1. 现状取 `close`（qfq）不变，**新增** `open/high/low`（vwap 模式需要）与 `amount_k`（滑点/容量需要）panel，按 `common_index × common_cols` 对齐。
2. `W = _build_weights(...)`（不变） → `W_exec = shift_for_t1(W)`。
3. `exec_price = build_exec_price(open, high, low, close, mode)`。
4. `size = W_exec * init_cash / exec_price`。
5. `size = apply_volume_cap(size, daily_amount, exec_price, max_volume_pct)`。
6. 产出 `fees_arr = build_fee_array(...)`、`slip_arr = build_slippage_array(...)`，随 `BacktestInputs` 一起返回。

`run_backtest` 的 `from_orders` 调用：

```python
pf = vbt.Portfolio.from_orders(
    close=inputs.close,
    size=inputs.size,
    price=inputs.exec_price,
    size_type="targetamount",
    fees=inputs.fees_arr,
    slippage=inputs.slip_arr,
    freq="1D",
    init_cash=init_cash,
    cash_sharing=True,
    group_by=True,
)
```

`BacktestInputs` dataclass 新增字段：`exec_price: DataFrame`、`fees_arr: ndarray`、`slip_arr: ndarray`。

---

## 8. 测试计划

`backend/tests/test_execution.py`（纯函数，无需 DB）：

- `build_exec_price`：open / vwap 两模式数值正确；vwap = (h+l+c)/3。
- `build_fee_array`：买入位 = 佣金+过户；卖出位 = 佣金+印花+过户；建仓首行视为买入。
- `build_slippage_array`：冲击 ∝ √(order/amount)；amount=0 时冲击=0；base_slip 叠加正确。
- `apply_volume_cap`：超额票被按比例裁剪；裁剪后单日成交额 ≤ 上限；不超额票不动；路径依赖（连续买入累积持仓）正确。
- `shift_for_t1`：整体后移一行，首行为 0。

`backend/tests/test_backtest_lookahead.py`（回测侧，可 mock DataService）：

- **前视检验**：构造"T 日因子高、T+1 暴跌"样本，验证 T+1 口径净值吃到下跌（旧 close 口径不会）。
- **不对称成本检验**：纯卖出 vs 纯买入同等金额，卖出扣费显著更高。

跑测试：`backend/.venv` 环境（项目约定），`uv run pytest backend/tests/test_execution.py backend/tests/test_backtest_lookahead.py -v`。

---

## 9. 实施里程碑

1. ✅ 设计文档（本文件）。
2. `execution.py` 纯函数 + `test_execution.py`（TDD）。
3. `schemas.py` 参数扩展。
4. `backtest_service.py` 集成（`_prepare_backtest_inputs` + `run_backtest` + `BacktestInputs`）。
5. `cost_sensitivity_service.py` 同步。
6. 回测前视/不对称成本集成测试。
7. 全量相关测试通过 → 提交。

---

## 10. 后续 Roadmap —— 已全部落地（2026-06-23）

按"走向实盘"的优先级，回测真实性之后的下一批已全部实现并测试通过：

- ✅ **样本外验证框架**：`services/validation.py` —— Walk-Forward 滚动 + Purged K-Fold（embargo 防泄露），`oos_validation_report` 用 IC 衰减比量化过拟合。测试 13。
- ✅ **组合级风控**：`services/risk_control.py` —— 集中度（个股水填充 + 行业缩减）、目标波动率缩放、回撤熔断；`apply_portfolio_risk` 接入回测权重链路。测试 11。
- ✅ **卖出侧涨跌停滞留**：`execution.apply_trading_constraints` —— 在 size 层逐行路径裁剪建模封板滞留（等价 `order_func_nb` 且更简洁），与容量约束共用 prev 路径，`lock_price_limit` 默认开启。
- ✅ **真实成交额 VWAP**：`data_service` 支持 vwap 字段（`amount/volume` 复权），替换复权典型价近似（典型价仅作回退）。
- ✅ **执行层对接**：`execution_layer/` —— 统一 `Broker` 抽象 + 内存 `SimulatedBroker`（A 股不对称费用/资金/持仓约束）。测试 8。实盘 QMT/CTP 按接口扩展（需外部 SDK + 账户，本仓库不内置）。
- ✅ **组合优化器**：`services/optimizer.py` —— 均值-方差 / 风险平价 / 逆波动率 / IC 加权合成 + 换手预算；`reweight_intragroup` 接入回测组内加权（`weighting` 参数）。测试 15。
- ✅ **可观测性**：`observability/` + `deploy/observability/` —— 零依赖 Prometheus 导出 + `/metrics` 端点 + Grafana 监控栈样例。测试 6。

> 仍依赖外部环境、本仓库不内置的部分：QMT/CTP 实盘适配器的真实对接（券商 SDK + 资金账户）、Prometheus/Grafana 生产部署（持久化 / 告警 / 抓取鉴权）。这些已留好 `Broker` 接口与 `deploy/observability/` 配置样例，就绪后即可接。

---

## 附：关键近似与边界（诚实声明）

| 近似 | 影响 | 接受理由 |
|------|------|---------|
| 成交方向用 `W_exec.diff()` 符号 | 现金约束下少买时方向可能不符 | 一阶近似，调仓场景绝大多数一致 |
| ~~VWAP 用复权典型价~~ | — | ✅ 已升级为真实成交额 VWAP（§10），典型价仅作回退 |
| 容量/冲击用成交额比值 | qfq 复权二阶偏差 | factor 近 1；避开股/手单位歧义 |
| ~~卖出涨跌停不滞留~~ | — | ✅ 已实现封板滞留（§10，size 层路径裁剪），`lock_price_limit` 默认开启 |
