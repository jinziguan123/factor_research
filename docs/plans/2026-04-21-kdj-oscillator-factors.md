# KDJ Oscillator Factors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `backend/factors/oscillator/` 下新增 5 个 KDJ 衍生横截面因子（`kdj_j_oversold` / `kdj_cross` / `kdj_oversold_hinge` / `kdj_k_pct_rev` / `kdj_divergence`），共享一个 `_kdj.py` helper，通过 TDD 保证每个公式正确。

**Architecture:** 新目录 `backend/factors/oscillator/`，5 因子各一文件 + 共享 helper `_kdj.py`（`compute_kdj(high, low, close, n) -> (K, D, J)`，用 `DataFrame.ewm(alpha=1/3, adjust=False)` 向量化 EMA）。每个因子从 `ctx.data.load_panel(..., field)` 取 high/low/close、调 helper 拿三线、做一次转换、`.loc[start_date:]` 切回。所有因子 `category="oscillator"`，n 暴露可扫，alpha 固定 1/3。

**Tech Stack:** Python 3.10+ / pandas 2.x / numpy / pytest 8.3 / FastAPI。参考 `backend/factors/reversal/reversal_n.py` 的风格。设计文档：`docs/plans/2026-04-21-kdj-oscillator-factors-design.md`。

**测试策略：** 每个因子用 `backend/tests/test_factors_math.py` 里的 `FakeDataService` 模式写 1-2 个**极端构造**的数学单测，验证公式正确性（不依赖 DB / ClickHouse）。最后跑一次 `FactorRegistry.scan_and_register()` 确认 5 个因子被注册 + API `/factors` 能列出。

**运行测试的命令：** `cd backend && uv run pytest backend/tests/test_factors_kdj.py -v`

---

## Task 0：创建 oscillator 包骨架 + `_kdj.py` helper（TDD）

**Files:**
- Create: `backend/factors/oscillator/__init__.py`（空）
- Create: `backend/factors/oscillator/_kdj.py`
- Create: `backend/tests/test_factors_kdj.py`

### Step 0.1：写失败测试 `test_compute_kdj_basic_shape_and_range`

在 `backend/tests/test_factors_kdj.py` 写入完整文件骨架：

```python
"""KDJ oscillator 因子的纯计算单测。

通过 FakeDataService 喂预置 high / low / close panel，验证：
- helper compute_kdj 的数学正确性（形状、值域、边界）；
- 5 个因子在极端构造序列下的输出符合预期方向与公式。

测试**不加** integration mark，和 test_factors_math.py 同属常规单测。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.factors.oscillator._kdj import compute_kdj


@dataclass
class FakeDataService:
    """只实现 load_panel 的最小替身（同 test_factors_math.FakeDataService）。"""
    panels: dict[str, pd.DataFrame]

    def load_panel(
        self,
        symbols,
        start,
        end,
        freq: str = "1d",
        field: str = "close",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


# ====================== helper compute_kdj ======================

def test_compute_kdj_basic_shape_and_range() -> None:
    """单调上涨序列上：RSV 接近 100 → K/D 从首值 50 单调逼近 100；
    J = 3K - 2D 在 K/D 同向时也应为正。"""
    idx = _biz_index(30)
    # 从 10 涨到 30，连续单调
    close = np.linspace(10.0, 30.0, num=30)
    # high / low 相对 close 做小幅扰动，保证 (max - min) > 0
    high = close + 0.2
    low = close - 0.2
    h = pd.DataFrame({"A": high}, index=idx)
    l = pd.DataFrame({"A": low}, index=idx)
    c = pd.DataFrame({"A": close}, index=idx)

    K, D, J = compute_kdj(h, l, c, n=9)

    # 形状一致、列一致
    assert K.shape == c.shape
    assert list(K.columns) == list(c.columns)
    # 窗口就位后（index >= n-1），K 值应在 [0, 100] 之间
    tail_k = K["A"].iloc[15:].dropna()
    assert (tail_k >= 0 - 1e-9).all() and (tail_k <= 100 + 1e-9).all()
    # J = 3K - 2D 的定义性断言
    assert np.allclose(J.values, 3 * K.values - 2 * D.values, equal_nan=True)
    # 单调上涨下，末尾 K 应显著 > 50（RSV ≈ 100，EMA 收敛向 100）
    assert K["A"].iloc[-1] > 80
```

### Step 0.2：运行测试确认失败

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py::test_compute_kdj_basic_shape_and_range -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.factors.oscillator'`

### Step 0.3：实现 `_kdj.py`

Create `backend/factors/oscillator/__init__.py` as empty file.

Create `backend/factors/oscillator/_kdj.py`：

```python
"""KDJ 三线计算 helper：给定 high/low/close 宽表返回 K/D/J 宽表。

抽出来的原因：kdj_* 5 个因子都要先算 K/D/J 再做不同转换，helper 让 KDJ 定义
只写一次、变一处生效。下划线前缀 `_kdj` 防止被人误当成因子模块 import——
实际 FactorRegistry.scan_and_register 靠识别 BaseFactor 子类，不看文件名，
但下划线前缀仍是"这是包内私有 helper"的 Python 惯用信号。

公式：
- RSV_t = (close_t - min_n(low)) / (max_n(high) - min_n(low)) * 100
- K_t = (2/3) * K_{t-1} + (1/3) * RSV_t       (EMA with alpha=1/3)
- D_t = (2/3) * D_{t-1} + (1/3) * K_t         (EMA with alpha=1/3)
- J_t = 3 * K_t - 2 * D_t

向量化策略：``DataFrame.rolling(n).min() / .max()`` 跨列同步算 RSV，然后
``.ewm(alpha=1/3, adjust=False)`` 跨列同步算 K、D——全程没有 Python 层 for。
"""
from __future__ import annotations

import pandas as pd


def compute_kdj(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    n: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """计算 K / D / J 宽表。

    Args:
        high / low / close: 行=日期、列=标的的宽表。三者必须同 index 同 columns。
        n: RSV 窗口（交易日）。

    Returns:
        (K, D, J) 三个宽表，shape 与输入一致。前 n-1 行是 NaN（窗口未就绪）。
    """
    # rolling 跨列一起算，min / max 自动忽略 NaN；但如果窗口内**全 NaN** 会返 NaN，
    # 这正是我们想要的（停牌段因子输出应为 NaN）。
    low_min = low.rolling(n, min_periods=n).min()
    high_max = high.rolling(n, min_periods=n).max()
    rng = high_max - low_min
    # range=0 时（极端横盘）用 NaN 替代，避免 inf；下游 ewm 遇 NaN 自然跳过。
    rng = rng.where(rng > 0)
    rsv = (close - low_min) / rng * 100

    # alpha=1/3 <=> K_t = (2/3) K_{t-1} + (1/3) RSV_t，adjust=False 保 recurrence
    # 和公式一致（adjust=True 会用整个历史做分母加权，不是经典 KDJ）。
    # ignore_na=False 让 NaN 参与但不破坏 EMA（pandas 将 NaN 视为 0 与前值的加权一致）。
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j
```

### Step 0.4：运行测试确认通过

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py::test_compute_kdj_basic_shape_and_range -v
```

Expected: PASS

### Step 0.5：commit

```bash
git add backend/factors/oscillator/__init__.py backend/factors/oscillator/_kdj.py backend/tests/test_factors_kdj.py
git commit -m "feat(oscillator): 新增 KDJ 三线计算 helper + 基础单测

compute_kdj(high, low, close, n) 返回 K/D/J 三个宽表，向量化
DataFrame.rolling + ewm(alpha=1/3, adjust=False) 实现经典 EMA 平滑。
为后续 5 个 KDJ 衍生横截面因子共享。

设计文档：docs/plans/2026-04-21-kdj-oscillator-factors-design.md"
```

---

## Task 1：`kdj_j_oversold`（J 超卖深度）

**Files:**
- Create: `backend/factors/oscillator/kdj_j_oversold.py`
- Modify: `backend/tests/test_factors_kdj.py`（追加测试）

### Step 1.1：追加失败测试

在 `backend/tests/test_factors_kdj.py` 末尾追加：

```python
from backend.factors.oscillator.kdj_j_oversold import KdjJOversold


def test_kdj_j_oversold_monotonic_up_gives_negative_factor() -> None:
    """单调上涨下 RSV≈100 → K,D 收敛到 100 → J≈100 → factor = -J ≈ -100。

    即"上涨到顶部"时因子强烈看空（负值大），符合反转语义。
    """
    idx = _biz_index(40)
    close = np.linspace(10.0, 30.0, num=40)
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[20],
        end_date=idx[-1],
        warmup_days=20,
    )
    factor = KdjJOversold().compute(ctx, {"n": 9})
    tail = factor.dropna()
    assert not tail.empty
    # factor = -J；顶部时 J 应 >> 0，因子值应 << 0
    assert (tail.values < -50).all()


def test_kdj_j_oversold_required_warmup() -> None:
    """warmup 公式：int(n * 3 * 1.5) + 10。n=9 → int(40.5)+10 = 50。"""
    assert KdjJOversold().required_warmup({"n": 9}) == 50
    # n=20 → int(90)+10 = 100
    assert KdjJOversold().required_warmup({"n": 20}) == 100
```

### Step 1.2：运行测试确认失败

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_j_oversold
```

Expected: FAIL with `ModuleNotFoundError: backend.factors.oscillator.kdj_j_oversold`

### Step 1.3：实现 `kdj_j_oversold.py`

Create `backend/factors/oscillator/kdj_j_oversold.py`：

```python
"""KDJ J 超卖深度因子。

定义：``factor = -J``；J = 3K - 2D 是 KDJ 最灵敏的衍生线，可以跑出 0-100 之外
的尖锐值，J<<0 表示极端超卖 / J>>100 表示极端超买。取负号后"因子越大越看多"，
与平台其它 reversal 类因子约定对齐。

直觉：
- 顶部 RSV→100，K、D 收敛到 100，J = 3·100 - 2·100 = 100，因子 = -100（强空）；
- 底部 RSV→0，K、D 收敛到 0，J = 0，因子 = 0；
- 深度超卖段（RSV 刚从极低 rebound），K 先于 D 上升，J 能短暂跌破 0，因子取正的
  大数字 → 超卖反弹买点。

预期方向：反转（与 reversal_n 同向，但驱动量是"相对 N 日高低点位置"而非收益率）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj


class KdjJOversold(BaseFactor):
    factor_id = "kdj_j_oversold"
    display_name = "J 超卖深度"
    category = "oscillator"
    description = "factor = -J；J 是 KDJ 最灵敏线，因子值越高表示越超卖，越看多。"
    params_schema = {
        "n": {
            "type": "int",
            "default": 9,
            "min": 3,
            "max": 60,
            "desc": "RSV 窗口（交易日）",
        }
    }
    default_params = {"n": 9}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        # K/D 是 alpha=1/3 的 EMA，衰减系数 2/3，3n 样本后残余 ~5%（(2/3)^27≈5%）；
        # 1.5× 交易日→自然日折算，+10 兜春节 / 国庆长假。
        return int(n * 3 * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        high = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="high", adjust="qfq",
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="low", adjust="qfq",
        )
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if high.empty or low.empty or close.empty:
            return pd.DataFrame()
        _, _, j = compute_kdj(high, low, close, n=n)
        factor = -j
        return factor.loc[ctx.start_date:]
```

### Step 1.4：运行测试确认通过

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_j_oversold
```

Expected: PASS (2 tests)

### Step 1.5：commit

```bash
git add backend/factors/oscillator/kdj_j_oversold.py backend/tests/test_factors_kdj.py
git commit -m "feat(oscillator): 新增 kdj_j_oversold 因子（J 超卖深度）

factor = -J；J=3K-2D 是 KDJ 最灵敏线，取负号后因子值越大越看多，
和 reversal_n 方向对齐但驱动量是 N 日高低点相对位置。"
```

---

## Task 2：`kdj_cross`（K-D 金叉强度，趋势向）

**Files:**
- Create: `backend/factors/oscillator/kdj_cross.py`
- Modify: `backend/tests/test_factors_kdj.py`

### Step 2.1：追加失败测试

```python
from backend.factors.oscillator.kdj_cross import KdjCross


def test_kdj_cross_rebound_gives_positive() -> None:
    """V 型反转末段：价格刚从低点反弹，RSV 快速从低位抬起，K 先于 D 抬升
    （K 是对 RSV 的 EMA，D 是对 K 的 EMA），K - D 应为正（金叉强度）。
    """
    idx = _biz_index(50)
    # 前 25 天 30→10 单调下跌，后 25 天 10→25 单调上涨
    close = np.concatenate([
        np.linspace(30.0, 10.0, num=25),
        np.linspace(10.0, 25.0, num=25),
    ])
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[30],  # 反弹段开始后几天
        end_date=idx[-1],
        warmup_days=30,
    )
    factor = KdjCross().compute(ctx, {"n": 9})
    # 反弹段后期 K > D（金叉已发生），因子值应 > 0
    assert factor["A"].iloc[-1] > 0


def test_kdj_cross_required_warmup() -> None:
    assert KdjCross().required_warmup({"n": 9}) == 50
```

### Step 2.2：运行测试确认失败

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_cross
```

Expected: FAIL（模块不存在）

### Step 2.3：实现 `kdj_cross.py`

```python
"""KDJ 金叉强度因子。

定义：``factor = K - D``。

直觉：
- K 是 RSV 的 1 阶 EMA（alpha=1/3），D 是 K 的 2 阶 EMA——D 比 K 滞后；
- 价格触底反弹时 RSV 先抬升 → K 先涨 → K-D 转正（金叉强度）；
- 价格见顶回落时 K 先跌 → K-D 转负（死叉）。

预期方向：趋势（与 kdj_j_oversold 反向——这是故意的，用来横比 KDJ 在目标
universe 上到底是反转信号有效还是趋势跟随有效）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj


class KdjCross(BaseFactor):
    factor_id = "kdj_cross"
    display_name = "KDJ 金叉强度"
    category = "oscillator"
    description = "factor = K - D；金叉越强（K 高于 D）看多，趋势跟随信号。"
    params_schema = {
        "n": {
            "type": "int",
            "default": 9,
            "min": 3,
            "max": 60,
            "desc": "RSV 窗口（交易日）",
        }
    }
    default_params = {"n": 9}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        return int(n * 3 * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        high = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="high", adjust="qfq",
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="low", adjust="qfq",
        )
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if high.empty or low.empty or close.empty:
            return pd.DataFrame()
        k, d, _ = compute_kdj(high, low, close, n=n)
        factor = k - d
        return factor.loc[ctx.start_date:]
```

### Step 2.4：运行测试确认通过

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_cross
```

Expected: PASS (2 tests)

### Step 2.5：commit

```bash
git add backend/factors/oscillator/kdj_cross.py backend/tests/test_factors_kdj.py
git commit -m "feat(oscillator): 新增 kdj_cross 因子（K-D 金叉强度）

factor = K - D；K 领先 D 抬升时 > 0（金叉看多，趋势跟随）。
和 kdj_j_oversold 反方向，作为反转 vs 趋势的横比对照。"
```

---

## Task 3：`kdj_oversold_hinge`（K 超卖阈值 hinge）

**Files:**
- Create: `backend/factors/oscillator/kdj_oversold_hinge.py`
- Modify: `backend/tests/test_factors_kdj.py`

### Step 3.1：追加失败测试

```python
from backend.factors.oscillator.kdj_oversold_hinge import KdjOversoldHinge


def test_kdj_oversold_hinge_zero_above_threshold() -> None:
    """上涨段 K 应很快升到 threshold=20 以上，factor = max(0, 20-K) = 0。"""
    idx = _biz_index(40)
    close = np.linspace(10.0, 30.0, num=40)
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[20],
        end_date=idx[-1],
        warmup_days=20,
    )
    factor = KdjOversoldHinge().compute(ctx, {"n": 9, "threshold": 20})
    tail = factor.dropna()
    assert not tail.empty
    # 上涨到顶部 K→100，全都超过阈值，因子恒为 0
    assert (tail.values == 0).all()


def test_kdj_oversold_hinge_positive_below_threshold() -> None:
    """下跌到底 K→0 时，factor = 20 - K ≈ 20 > 0。"""
    idx = _biz_index(40)
    close = np.linspace(30.0, 10.0, num=40)
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[20],
        end_date=idx[-1],
        warmup_days=20,
    )
    factor = KdjOversoldHinge().compute(ctx, {"n": 9, "threshold": 20})
    # 末尾（底部）K≈0，因子值应接近 threshold=20
    assert factor["A"].iloc[-1] > 15


def test_kdj_oversold_hinge_required_warmup() -> None:
    assert KdjOversoldHinge().required_warmup({"n": 9}) == 50
```

### Step 3.2：运行测试确认失败

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_oversold_hinge
```

Expected: FAIL（模块不存在）

### Step 3.3：实现 `kdj_oversold_hinge.py`

```python
"""KDJ 超卖阈值 hinge 因子。

定义：``factor = max(0, threshold - K)``；K < threshold（典型 20）时给正值，
否则 0。和 kdj_j_oversold 的连续版本相比，hinge 让"不在超卖区的股票"一视同仁（都
给 0），只在超卖段激活——更接近技术分析手册的离散规则，用来测"非连续信号"比
"连续水平信号"在横截面上是否更有效。

预期方向：反转。结构特点：分五组回测时可能有大量 0 挤在低分组，qcut 可能报
"bins 不足"警告，属正常现象（因子本身就是稀疏的）。

预期方向：反转（只在 K < threshold 时激活）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj


class KdjOversoldHinge(BaseFactor):
    factor_id = "kdj_oversold_hinge"
    display_name = "KDJ 超卖 hinge"
    category = "oscillator"
    description = "factor = max(0, threshold - K)；仅在 K 低于阈值（超卖区）给正分。"
    params_schema = {
        "n": {
            "type": "int", "default": 9, "min": 3, "max": 60,
            "desc": "RSV 窗口（交易日）",
        },
        "threshold": {
            "type": "int", "default": 20, "min": 5, "max": 40,
            "desc": "K 超卖阈值（低于此值才激活）",
        },
    }
    default_params = {"n": 9, "threshold": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        return int(n * 3 * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        threshold = float(params.get("threshold", self.default_params["threshold"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        high = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="high", adjust="qfq",
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="low", adjust="qfq",
        )
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if high.empty or low.empty or close.empty:
            return pd.DataFrame()
        k, _, _ = compute_kdj(high, low, close, n=n)
        # np.maximum 处理 NaN 时结果仍 NaN（symmetry），正是我们想要的。
        factor = pd.DataFrame(
            np.maximum(0.0, threshold - k.values),
            index=k.index, columns=k.columns,
        )
        # maximum 会把 NaN 吃掉变 0，需手工恢复 K 的 NaN 位。
        factor = factor.where(~k.isna())
        return factor.loc[ctx.start_date:]
```

### Step 3.4：运行测试确认通过

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_oversold_hinge
```

Expected: PASS (3 tests)

### Step 3.5：commit

```bash
git add backend/factors/oscillator/kdj_oversold_hinge.py backend/tests/test_factors_kdj.py
git commit -m "feat(oscillator): 新增 kdj_oversold_hinge 因子（K 超卖阈值 hinge）

factor = max(0, threshold - K)；只在 K < threshold 的超卖区激活，
非连续信号（大量零）用来横比连续 vs 稀疏的有效性差异。"
```

---

## Task 4：`kdj_k_pct_rev`（K 自身分位反转）

**Files:**
- Create: `backend/factors/oscillator/kdj_k_pct_rev.py`
- Modify: `backend/tests/test_factors_kdj.py`

### Step 4.1：追加失败测试

```python
from backend.factors.oscillator.kdj_k_pct_rev import KdjKPctRev


def test_kdj_k_pct_rev_monotonic_up_gives_negative() -> None:
    """单调上涨下 K 也单调上涨，K 在自身 lookback 历史里总处于最高分位→1.0，
    factor = -rolling_pct_rank(K) ≈ -1.0。"""
    idx = _biz_index(100)
    close = np.linspace(10.0, 50.0, num=100)
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[70],
        end_date=idx[-1],
        warmup_days=70,
    )
    factor = KdjKPctRev().compute(ctx, {"n": 9, "lookback": 30})
    # K 一直创新高 → pct_rank 逼近 1.0 → factor 逼近 -1.0
    assert factor["A"].iloc[-1] < -0.8


def test_kdj_k_pct_rev_required_warmup() -> None:
    """warmup = int((n*3 + lookback) * 1.5) + 10
    n=9, lookback=60 → int((27+60)*1.5)+10 = int(130.5)+10 = 140。"""
    assert KdjKPctRev().required_warmup({"n": 9, "lookback": 60}) == 140
```

### Step 4.2：运行测试确认失败

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_k_pct_rev
```

Expected: FAIL（模块不存在）

### Step 4.3：实现 `kdj_k_pct_rev.py`

```python
"""KDJ K 自身分位反转因子。

定义：``factor = -rolling_pct_rank(K, lookback)``；K 值在过去 lookback 日内的
百分位（0-1），取负号后分位越低因子越大（越看多）。

设计动机：直接用 K 做横截面因子有一个根本问题——A 股的 K=30 可能是超卖反弹
机会，B 股的 K=30 可能只是下跌刚开始。K 的绝对值跨股票不可比。改成"K 在
自身过去 lookback 日的分位"后，每只股票的分位值都被归一化到 [0,1]，横截面
比较才站得住脚。

为什么用 pct_rank 不用 z-score：K 是 bounded 量（0-100 附近），分布偏态且尾部
被 clip，z-score 对这种分布敏感；pct_rank 只看排序，对分布形状不敏感。

预期方向：反转（分位低越看多）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj


class KdjKPctRev(BaseFactor):
    factor_id = "kdj_k_pct_rev"
    display_name = "K 自身分位反转"
    category = "oscillator"
    description = (
        "factor = -rolling_pct_rank(K, lookback)；K 在自身过去 lookback 日分位的"
        "相反数，分位越低越看多，消除 K 绝对值跨股不可比。"
    )
    params_schema = {
        "n": {
            "type": "int", "default": 9, "min": 3, "max": 60,
            "desc": "RSV 窗口（交易日）",
        },
        "lookback": {
            "type": "int", "default": 60, "min": 10, "max": 252,
            "desc": "K 自身分位回看窗口（交易日）",
        },
    }
    default_params = {"n": 9, "lookback": 60}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        # n*3 给 K/D EMA 收敛，+ lookback 给 pct_rank 窗口就位，再 1.5× + 10 兜假。
        return int((n * 3 + lookback) * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        high = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="high", adjust="qfq",
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="low", adjust="qfq",
        )
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if high.empty or low.empty or close.empty:
            return pd.DataFrame()
        k, _, _ = compute_kdj(high, low, close, n=n)

        # rolling.apply 的 pct_rank：窗口内"当前值 > 过去值"的比例。
        # raw=True 传 ndarray，numpy 向量化比 Series.rank 快一个量级。
        def _pct_rank_last(window: np.ndarray) -> float:
            if np.isnan(window[-1]):
                return np.nan
            valid = window[~np.isnan(window)]
            if valid.size < 2:
                return np.nan
            # 当前值（window[-1]）在有效样本中的严格大于 + 等于一半的比例。
            # 这是 pandas rank(method='average', pct=True) 的近似，够用且快。
            last = window[-1]
            gt = (valid < last).sum()
            eq = (valid == last).sum()
            return (gt + 0.5 * eq) / valid.size

        pct_rank = k.rolling(lookback, min_periods=lookback).apply(
            _pct_rank_last, raw=True
        )
        factor = -pct_rank
        return factor.loc[ctx.start_date:]
```

### Step 4.4：运行测试确认通过

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_k_pct_rev
```

Expected: PASS (2 tests)

### Step 4.5：commit

```bash
git add backend/factors/oscillator/kdj_k_pct_rev.py backend/tests/test_factors_kdj.py
git commit -m "feat(oscillator): 新增 kdj_k_pct_rev 因子（K 自身分位反转）

factor = -rolling_pct_rank(K, lookback)；把 K 换成"自身 lookback 分位"，
消除 K 绝对值跨股不可比的根本问题。rolling.apply + raw=True 向量化实现。"
```

---

## Task 5：`kdj_divergence`（价-J 底背离强度）

**Files:**
- Create: `backend/factors/oscillator/kdj_divergence.py`
- Modify: `backend/tests/test_factors_kdj.py`

### Step 5.1：追加失败测试

```python
from backend.factors.oscillator.kdj_divergence import KdjDivergence


def test_kdj_divergence_required_warmup() -> None:
    """n=9, lookback=20 → int((27+20)*1.5)+10 = int(70.5)+10 = 80。"""
    assert KdjDivergence().required_warmup({"n": 9, "lookback": 20}) == 80


def test_kdj_divergence_output_shape_and_finite() -> None:
    """烟雾测试：公式很复杂，做形状+有限性断言，数学性质靠实盘 IC 验证。

    底背离的精确构造很难，我们这里只确保：
    - 正常上涨序列上公式能跑通、输出非空、至少部分值非 NaN；
    - 没有 inf（rolling_std 除零兜底生效）。
    """
    idx = _biz_index(100)
    rng = np.random.default_rng(42)
    # 带噪声的上涨趋势（避免完全单调导致 std=0）
    close = np.linspace(10.0, 30.0, num=100) + rng.normal(0, 0.3, 100)
    high = close + 0.5
    low = close - 0.5
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[60],
        end_date=idx[-1],
        warmup_days=60,
    )
    factor = KdjDivergence().compute(ctx, {"n": 9, "lookback": 20})
    assert not factor.empty
    tail = factor.dropna()
    assert not tail.empty
    # 所有非 NaN 值有限
    assert np.all(np.isfinite(tail.values))
```

### Step 5.2：运行测试确认失败

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_divergence
```

Expected: FAIL（模块不存在）

### Step 5.3：实现 `kdj_divergence.py`

```python
"""KDJ 价-J 底背离强度因子。

定义：
    j_rebound = J - rolling_min(J, lookback)           # J 已从近期最低反弹的距离
    p_rebound = close - rolling_min(close, lookback)   # 价格已从近期最低反弹的距离
    scale     = rolling_std(J, lookback) /             # 两边量级归一
                rolling_std(close, lookback)
    factor    = j_rebound - scale * p_rebound

直觉：
- 底背离 = "J 先反弹 + 价格滞后"，此时 j_rebound 大而 p_rebound 小 → factor > 0；
- 顶背离 = "J 先下跌 + 价格惯性新高"，反过来 → factor < 0；
- 如果两者同步（无背离），factor ≈ 0。

为什么用 rolling_min 近似而不找 local extrema：
- 真找局部极值要判 window 内的"反转点"，实现又脏又慢，收敛也敏感；
- rolling_min 虽然在"一路下跌（min 永远是最新那根）"时给出 0、不算强信号，
  但在 V 型底 / W 型底时能正确捕捉——这正是我们关心的场景。

为什么要 scale：
- close 的量级是价格（几元~几百元）、J 的量级是 0-100，直接相减会被 p_rebound
  主导；用两边 rolling_std 的比值做横截面归一，让两者在同一量纲下可比。
- 当 rolling_std(close) 极小（新股 / 停牌后），用 scale=1 兜底避免除零爆 inf。

预期方向：反转（底背离看多）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj


class KdjDivergence(BaseFactor):
    factor_id = "kdj_divergence"
    display_name = "价-J 底背离强度"
    category = "oscillator"
    description = (
        "(J - rolling_min(J, lb)) - scale * (close - rolling_min(close, lb))；"
        "J 已反弹距离 - 价格已反弹距离（归一化后），正值=底背离看多。"
    )
    params_schema = {
        "n": {
            "type": "int", "default": 9, "min": 3, "max": 60,
            "desc": "RSV 窗口（交易日）",
        },
        "lookback": {
            "type": "int", "default": 20, "min": 10, "max": 60,
            "desc": "背离回看窗口（交易日）",
        },
    }
    default_params = {"n": 9, "lookback": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        return int((n * 3 + lookback) * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        high = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="high", adjust="qfq",
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="low", adjust="qfq",
        )
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if high.empty or low.empty or close.empty:
            return pd.DataFrame()
        _, _, j = compute_kdj(high, low, close, n=n)

        j_rebound = j - j.rolling(lookback, min_periods=lookback).min()
        p_rebound = close - close.rolling(lookback, min_periods=lookback).min()
        j_std = j.rolling(lookback, min_periods=lookback).std()
        p_std = close.rolling(lookback, min_periods=lookback).std()
        # p_std 极小时（< 1e-9 ≈ 完全横盘）用 scale=1 兜底，避免 inf / 无穷小 * inf。
        scale = (j_std / p_std.where(p_std > 1e-9)).fillna(1.0)
        factor = j_rebound - scale * p_rebound
        return factor.loc[ctx.start_date:]
```

### Step 5.4：运行测试确认通过

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v -k kdj_divergence
```

Expected: PASS (2 tests)

### Step 5.5：commit

```bash
git add backend/factors/oscillator/kdj_divergence.py backend/tests/test_factors_kdj.py
git commit -m "feat(oscillator): 新增 kdj_divergence 因子（价-J 底背离强度）

factor = (J - rolling_min(J, lb)) - scale * (close - rolling_min(close, lb))；
scale=std(J)/std(close) 做横截面归一，rolling_min 近似局部极值（V 型底有效，
单边下跌时给 0）。rolling_std(close) 极小时 scale=1 兜底避免除零。"
```

---

## Task 6：端到端验证（FactorRegistry + API）

**Files:** 不创建新代码；只做验证。

### Step 6.1：跑全套单测

```bash
cd backend && uv run pytest backend/tests/test_factors_kdj.py -v
```

Expected: 11 tests pass（helper 1 + 5 因子各 2-3 个 = 12 附近）

### Step 6.2：确认 FactorRegistry 能扫到新因子

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research && backend/.venv/bin/python -c "
from backend.runtime.factor_registry import FactorRegistry
reg = FactorRegistry()
reg.scan_and_register()
print('oscillator factors:')
for fid, cls in reg._registry.items():
    if getattr(cls, 'category', None) == 'oscillator':
        print(f'  {fid} | {cls.display_name}')
"
```

Expected: 输出 5 条 oscillator 因子（kdj_j_oversold / kdj_cross / kdj_oversold_hinge / kdj_k_pct_rev / kdj_divergence）。

### Step 6.3：确认 API 能列出

```bash
curl -s http://localhost:8000/api/factors | python3 -c "
import json, sys
d = json.load(sys.stdin)['data']
osc = [f for f in d if f.get('category') == 'oscillator']
print(f'{len(osc)} oscillator factors:')
for f in osc:
    print(f\"  {f['factor_id']} | {f['display_name']} | schema={list((f.get('params_schema') or {}).keys())}\")
"
```

Expected: 5 条 oscillator 因子列出，`params_schema` 的 key 正确。

### Step 6.4：浏览器抽查

在浏览器打开 `http://localhost:5173/factors`，确认侧边分组有新的 `oscillator`（或中文分类名，取决于前端映射），里面有 5 个因子卡片。

不需要单独 commit（没有代码改动）。

---

## Task 7：单因子快速冒烟评估（可选，用户可跳）

**目的**：单跑一次评估验证因子在真实数据上能跑通（不求 IC 结果好坏，只求不崩、输出有数字）。

### Step 7.1：创建一次小窗口评估

```bash
curl -s -X POST http://localhost:8000/api/evals -H 'Content-Type: application/json' -d '{
  "factor_id": "kdj_j_oversold",
  "pool_id": 4,
  "start_date": "2024-01-01",
  "end_date": "2024-03-31",
  "n_groups": 5,
  "forward_periods": [1, 5]
}'
```

Expected: `{"code": 0, "data": {"run_id": "...", "status": "pending"}}`

### Step 7.2：轮询直到 success

```bash
RUN_ID=<from-step-7.1>
for i in 1 2 3 4 5; do
  curl -s "http://localhost:8000/api/evals/${RUN_ID}/status" | head -c 200
  echo
  sleep 20
done
```

Expected: 一分钟内 status 变为 `success`。

### Step 7.3：如果失败，看 error_message 排查

若 status=failed，`error_message` 里会有 traceback。常见问题：
- 某个 `load_panel` 字段返回空 → 检查 ClickHouse 是否有该字段数据；
- `compute_kdj` 返回全 NaN → 检查 `n` 是否大于可用样本数。

不需要为此 commit（代码应该是对的，冒烟只是"不崩"验证）。

---

## 完工清单

- [ ] Task 0：`_kdj.py` helper + 基础单测
- [ ] Task 1：`kdj_j_oversold`
- [ ] Task 2：`kdj_cross`
- [ ] Task 3：`kdj_oversold_hinge`
- [ ] Task 4：`kdj_k_pct_rev`
- [ ] Task 5：`kdj_divergence`
- [ ] Task 6：FactorRegistry + API + 浏览器 3 处验证
- [ ] Task 7（可选）：单因子冒烟评估

**总计 commit 数**：6（Task 0-5 各一个；Task 6-7 验证不 commit）。

**预计总时长**：每个 factor ≈ 15-20 分钟（写测试 + 实现 + 过测试 + commit），5 个因子约 1.5-2 小时；helper + 验证再 30 分钟。合计约 **2-2.5 小时**。
