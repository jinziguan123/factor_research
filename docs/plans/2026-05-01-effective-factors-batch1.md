# 有效因子库扩展批次 1（精简 8 因子）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `backend/factors/` 下加 8 个学术/业界有公开证据的因子，覆盖 Alpha101 量价（3）+ 基本面（3）+ A 股专属异象（2），每个因子配 3 个测试。

**Architecture:** 每个因子是独立 `BaseFactor` 子类，自动被 `FactorRegistry.scan_and_register()` 发现。测试用项目已建立的 `@dataclass FakeDataService` 模式（见 `backend/tests/test_factors_alpha101_8.py`）。无后端 API / 前端改动。

**Tech Stack:** Python 3.10 + pandas + numpy + pytest（与现有项目一致）

**Design 源**: `docs/plans/2026-05-01-effective-factors-batch1-design.md`

---

## 共享约定

**测试文件命名**：`backend/tests/test_factors_<factor_id>.py`（与 `test_factors_alpha101_8.py` 保持一致）

**测试 helper 模板**（每个测试文件顶部都有，**3 行 import 之外不要 DRY 抽公共模块**——项目现有因子测试就是各自带本地 fake，便于 grep + 隔离）：

```python
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
# from backend.factors.<dir>.<file> import <FactorClass>


@dataclass
class FakeDataService:
    """只实现 load_panel / load_fundamental_panel 的最小替身。"""
    panels: dict[str, pd.DataFrame]
    fund_panel: pd.DataFrame | None = None  # 仅 B 类基本面因子用

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        if self.fund_panel is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)
```

**通用三类测试**（每个因子都有这 3 类，命名一致）：

| 测试名 | 验证什么 | 失败信号 |
|---|---|---|
| `test_<factor>_happy_path` | 5 票 × 30 天，与 docstring 同口径手算对齐 | 公式实现错 |
| `test_<factor>_nan_robust` | 某段 close/volume = NaN（模拟停牌），因子不崩 + 该段 NaN | 隐式 fillna 引入未来信息 |
| `test_<factor>_col_order_invariance` | 打乱 columns 顺序 → 各 column 因子值与原顺序对应位置一致 | 内部依赖 columns 顺序 |

**运行测试通用命令**（所有 task 都用，路径已绝对）：

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_<factor_id>.py -v
```

---

## Task 1: alpha101_6（价量负相关）

**Files:**
- Create: `backend/factors/alpha101/alpha101_6.py`
- Test: `backend/tests/test_factors_alpha101_6.py`

**因子公式**：`-1 * correlation(open, volume, 10)`

**Step 1: Write the failing test**

创建 `backend/tests/test_factors_alpha101_6.py`：

```python
"""Alpha101 #6 因子单测（-corr(open, volume, 10)）。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_6 import Alpha101_6


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def _make_panels(n: int, symbols: list[str], seed: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = _biz_index(n)
    open_data = {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}
    vol_data = {s: rng.uniform(1e6, 1e7, n) for s in symbols}
    return {
        "open": pd.DataFrame(open_data, index=idx),
        "volume": pd.DataFrame(vol_data, index=idx),
    }


def test_alpha101_6_happy_path():
    """30 天 × 5 票，因子 = -rolling(10).corr(open, volume) 与手算对齐。"""
    n = 30
    symbols = ["A", "B", "C", "D", "E"]
    panels = _make_panels(n, symbols, seed=0)
    ctx = FactorContext(
        data=FakeDataService(panels=panels),
        symbols=symbols,
        start_date=panels["open"].index[15],
        end_date=panels["open"].index[-1],
        warmup_days=15,
    )
    factor = Alpha101_6().compute(ctx, {})

    # 手算同口径
    open_df = panels["open"]
    vol_df = panels["volume"]
    expected = -open_df.rolling(10).corr(vol_df)
    target = factor.index[5]
    pd.testing.assert_series_equal(
        factor.loc[target].sort_index(),
        expected.loc[target].sort_index(),
        check_names=False,
    )


def test_alpha101_6_nan_robust():
    """某只票某段 NaN（模拟停牌），因子不崩 + 该段输出 NaN。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=1)
    panels["open"].iloc[10:15, panels["open"].columns.get_loc("A")] = np.nan
    panels["volume"].iloc[10:15, panels["volume"].columns.get_loc("A")] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels=panels),
        symbols=symbols,
        start_date=panels["open"].index[15],
        end_date=panels["open"].index[-1],
        warmup_days=15,
    )
    factor = Alpha101_6().compute(ctx, {})
    # 不抛异常 + B/C 列在末段非 NaN
    assert not factor.empty
    assert factor[["B", "C"]].iloc[-5:].notna().any().all()


def test_alpha101_6_col_order_invariance():
    """打乱 columns 顺序，因子值在对应 column 上一致。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=2)
    ctx_a = FactorContext(
        data=FakeDataService(panels=panels),
        symbols=symbols,
        start_date=panels["open"].index[15],
        end_date=panels["open"].index[-1],
        warmup_days=15,
    )
    factor_a = Alpha101_6().compute(ctx_a, {})

    # 打乱
    shuffled = ["C", "A", "B"]
    panels_s = {k: v[shuffled] for k, v in panels.items()}
    ctx_s = FactorContext(
        data=FakeDataService(panels=panels_s),
        symbols=shuffled,
        start_date=panels_s["open"].index[15],
        end_date=panels_s["open"].index[-1],
        warmup_days=15,
    )
    factor_s = Alpha101_6().compute(ctx_s, {})

    # 同 column 上 NaN-aware 等值
    target = factor_a.index[5]
    for c in symbols:
        a_v = factor_a.loc[target, c]
        s_v = factor_s.loc[target, c]
        if pd.isna(a_v):
            assert pd.isna(s_v)
        else:
            assert abs(a_v - s_v) < 1e-12
```

**Step 2: Run test to verify it fails**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_alpha101_6.py -v
```

Expected: `ImportError: cannot import name 'Alpha101_6' from 'backend.factors.alpha101.alpha101_6'`（文件还没建）

**Step 3: Write minimal implementation**

创建 `backend/factors/alpha101/alpha101_6.py`：

```python
"""WorldQuant Alpha 101 #6（价量负相关，反转信号）。

公式：``-1 * correlation(open, volume, window)``，window 默认 10。

直觉：开盘价与成交量近 N 日 rolling 相关——负相关越深表示"放量低开/缩量
高开"，业界视作反转信号；取负后高分股 → 弱反转预期。

预热 = ``int(window * 1.5) + 5`` 自然日。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_6(BaseFactor):
    factor_id = "alpha101_6"
    display_name = "Alpha101 #6（价量负相关）"
    category = "alpha101"
    description = "-1 * correlation(open, volume, 10)；价量负相关反转信号。"
    hypothesis = "近 10 日 open 与 volume 负相关越深 → 反转预期越强；取负使高分股偏多头。"
    params_schema: dict = {
        "window": {"type": "int", "default": 10, "min": 3, "max": 60,
                   "desc": "rolling correlation 窗口"},
    }
    default_params: dict = {"window": 10}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return int(window * 1.5) + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        open_ = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="open", adjust="qfq",
        )
        volume = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="volume", adjust="none",
        )
        if open_.empty or volume.empty:
            return pd.DataFrame()
        # outer align 防 column 漂移
        open_, volume = open_.align(volume, join="outer")
        # rolling.corr 是 element-wise；axis 默认 row-wise NaN-aware
        corr = open_.rolling(window).corr(volume)
        return (-corr).loc[ctx.start_date :]
```

**Step 4: Run test to verify it passes**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_alpha101_6.py -v
```

Expected: 3 passed

**Step 5: Commit**

```bash
git add backend/factors/alpha101/alpha101_6.py backend/tests/test_factors_alpha101_6.py
git commit -m "feat(factor): alpha101_6 价量负相关（-corr(open,vol,10)）"
```

---

## Task 2: alpha101_12（量价短期反转）

**Files:**
- Create: `backend/factors/alpha101/alpha101_12.py`
- Test: `backend/tests/test_factors_alpha101_12.py`

**因子公式**：`sign(volume_t - volume_{t-1}) * (-1 * (close_t - close_{t-1}))`

**Step 1: Write the failing test**

创建 `backend/tests/test_factors_alpha101_12.py`：

```python
"""Alpha101 #12 因子单测：sign(Δvol) * (-Δclose)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_12 import Alpha101_12


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def _make_panels(n, symbols, seed):
    rng = np.random.default_rng(seed)
    idx = _biz_index(n)
    return {
        "close": pd.DataFrame(
            {s: 10 + rng.normal(0, 0.3, n).cumsum() for s in symbols}, index=idx,
        ),
        "volume": pd.DataFrame(
            {s: rng.uniform(1e6, 1e7, n) for s in symbols}, index=idx,
        ),
    }


def test_alpha101_12_happy_path():
    """因子 = sign(Δvol) * (-Δclose)，与手算对齐。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=0)
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    factor = Alpha101_12().compute(ctx, {})
    expected = np.sign(panels["volume"].diff(1)) * (-panels["close"].diff(1))
    target = factor.index[5]
    pd.testing.assert_series_equal(
        factor.loc[target].sort_index(),
        expected.loc[target].sort_index(),
        check_names=False,
    )


def test_alpha101_12_nan_robust():
    n = 30
    symbols = ["A", "B"]
    panels = _make_panels(n, symbols, seed=1)
    panels["close"].iloc[10:13, 0] = np.nan
    panels["volume"].iloc[10:13, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    factor = Alpha101_12().compute(ctx, {})
    assert not factor.empty
    assert factor["B"].iloc[-5:].notna().all()


def test_alpha101_12_col_order_invariance():
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=2)
    ctx_a = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    fa = Alpha101_12().compute(ctx_a, {})

    shuffled = ["C", "A", "B"]
    panels_s = {k: v[shuffled] for k, v in panels.items()}
    ctx_s = FactorContext(
        data=FakeDataService(panels=panels_s), symbols=shuffled,
        start_date=panels_s["close"].index[3], end_date=panels_s["close"].index[-1],
        warmup_days=3,
    )
    fs = Alpha101_12().compute(ctx_s, {})

    target = fa.index[5]
    for c in symbols:
        a_v, s_v = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a_v): assert pd.isna(s_v)
        else: assert abs(a_v - s_v) < 1e-12
```

**Step 2: Run failing test**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_alpha101_12.py -v
```
Expected: ImportError

**Step 3: Implement**

创建 `backend/factors/alpha101/alpha101_12.py`：

```python
"""WorldQuant Alpha 101 #12（量价短期反转）。

公式：``sign(volume_t - volume_{t-1}) * (-1 * (close_t - close_{t-1}))``。

直觉：当日"放量上涨"或"缩量下跌"被视作反转信号——sign(Δvol) 给量方向，
(-Δclose) 给反向涨跌。两者乘积 = 短期反转预期。最简单的 1 行 Alpha101 因子。

预热 = 3 自然日（diff 1 + safety）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_12(BaseFactor):
    factor_id = "alpha101_12"
    display_name = "Alpha101 #12（量价短期反转）"
    category = "alpha101"
    description = "sign(Δvolume) * (-Δclose)；放量上涨视作反转信号。"
    hypothesis = "Δvolume 与 Δclose 同向时反转概率高；取负后高分 → 多头预期。"
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 3

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        volume = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="volume", adjust="none",
        )
        if close.empty or volume.empty:
            return pd.DataFrame()
        close, volume = close.align(volume, join="outer")
        factor = np.sign(volume.diff(1)) * (-close.diff(1))
        return factor.loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_alpha101_12.py -v
```
Expected: 3 passed

**Step 5: Commit**
```bash
git add backend/factors/alpha101/alpha101_12.py backend/tests/test_factors_alpha101_12.py
git commit -m "feat(factor): alpha101_12 量价短期反转（sign(Δvol)·(-Δclose)）"
```

---

## Task 3: alpha101_101（K 线归一化涨幅）

**Files:**
- Create: `backend/factors/alpha101/alpha101_101.py`
- Test: `backend/tests/test_factors_alpha101_101.py`

**公式**：`(close - open) / (high - low + 1e-3)`

**Step 1: Write failing test**

创建 `backend/tests/test_factors_alpha101_101.py`：

```python
"""Alpha101 #101 因子单测：(close - open) / (high - low + epsilon)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_101 import Alpha101_101


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def _biz_index(n, start="2024-01-02"):
    return pd.bdate_range(start=start, periods=n)


def _make_ohlc(n, symbols, seed):
    rng = np.random.default_rng(seed)
    idx = _biz_index(n)
    open_ = pd.DataFrame({s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx)
    close = open_ + rng.normal(0, 0.2, (n, len(symbols)))
    high = pd.concat([open_, close]).groupby(level=0).max() + rng.uniform(0.05, 0.2, (n, len(symbols)))
    low = pd.concat([open_, close]).groupby(level=0).min() - rng.uniform(0.05, 0.2, (n, len(symbols)))
    return {"open": open_, "close": close, "high": high, "low": low}


def test_alpha101_101_happy_path():
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_ohlc(n, symbols, seed=0)
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[0], end_date=panels["close"].index[-1],
        warmup_days=0,
    )
    factor = Alpha101_101().compute(ctx, {})
    eps = 1e-3
    expected = (panels["close"] - panels["open"]) / (panels["high"] - panels["low"] + eps)
    target = factor.index[10]
    pd.testing.assert_series_equal(
        factor.loc[target].sort_index(),
        expected.loc[target].sort_index(),
        check_names=False,
    )


def test_alpha101_101_nan_robust():
    n = 30
    symbols = ["A", "B"]
    panels = _make_ohlc(n, symbols, seed=1)
    for k in panels:
        panels[k].iloc[5:8, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[0], end_date=panels["close"].index[-1],
        warmup_days=0,
    )
    factor = Alpha101_101().compute(ctx, {})
    assert not factor.empty
    assert factor["A"].iloc[5:8].isna().all()
    assert factor["B"].notna().all()


def test_alpha101_101_col_order_invariance():
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_ohlc(n, symbols, seed=2)
    ctx_a = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[0], end_date=panels["close"].index[-1],
        warmup_days=0,
    )
    fa = Alpha101_101().compute(ctx_a, {})

    shuffled = ["B", "C", "A"]
    panels_s = {k: v[shuffled] for k, v in panels.items()}
    ctx_s = FactorContext(
        data=FakeDataService(panels=panels_s), symbols=shuffled,
        start_date=panels_s["close"].index[0], end_date=panels_s["close"].index[-1],
        warmup_days=0,
    )
    fs = Alpha101_101().compute(ctx_s, {})

    target = fa.index[10]
    for c in symbols:
        a_v, s_v = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a_v): assert pd.isna(s_v)
        else: assert abs(a_v - s_v) < 1e-12
```

**Step 2: Run failing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_alpha101_101.py -v
```

**Step 3: Implement**

创建 `backend/factors/alpha101/alpha101_101.py`：

```python
"""WorldQuant Alpha 101 #101（K 线归一化涨幅）。

公式：``(close - open) / (high - low + epsilon)``，epsilon=1e-3 防分母 0。

直觉：日内"实体相对幅度"。≈ 1 表示当日强势收高，≈ -1 表示弱势收低；
归一化后跨股票可比。最简单的瞬时形态因子，无 rolling / lag。

预热 = 0（无 lag）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_101(BaseFactor):
    factor_id = "alpha101_101"
    display_name = "Alpha101 #101（K 线归一化涨幅）"
    category = "alpha101"
    description = "(close-open) / (high-low+epsilon)；日内归一化涨幅。"
    hypothesis = "当日实体相对全幅度的占比反映多空力道；高分 → 强势收高。"
    params_schema: dict = {
        "epsilon": {"type": "float", "default": 1e-3, "min": 1e-6, "max": 1.0,
                    "desc": "防分母 0 的常数项"},
    }
    default_params: dict = {"epsilon": 1e-3}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        eps = float(params.get("epsilon", self.default_params["epsilon"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        open_ = ctx.data.load_panel(ctx.symbols, s, e, field="open", adjust="qfq")
        close = ctx.data.load_panel(ctx.symbols, s, e, field="close", adjust="qfq")
        high = ctx.data.load_panel(ctx.symbols, s, e, field="high", adjust="qfq")
        low = ctx.data.load_panel(ctx.symbols, s, e, field="low", adjust="qfq")
        if any(p.empty for p in [open_, close, high, low]):
            return pd.DataFrame()
        # 4 路 outer align（防 column 漂移）
        open_, close = open_.align(close, join="outer")
        high, low = high.align(low, join="outer")
        open_, high = open_.align(high, join="outer")
        close, low = close.align(low, join="outer")
        factor = (close - open_) / (high - low + eps)
        return factor.loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_alpha101_101.py -v
```

**Step 5: Commit**
```bash
git add backend/factors/alpha101/alpha101_101.py backend/tests/test_factors_alpha101_101.py
git commit -m "feat(factor): alpha101_101 K 线归一化涨幅 ((close-open)/(high-low+ε))"
```

---

## Task 4: 创建 fundamental 目录 + earnings_yield（EP）

**Files:**
- Create: `backend/factors/fundamental/__init__.py` （空文件）
- Create: `backend/factors/fundamental/earnings_yield.py`
- Test: `backend/tests/test_factors_earnings_yield.py`

**公式**：`eps_ttm (PIT, ffill) / close (qfq)`

**Step 1: Write failing test**

创建 `backend/tests/test_factors_earnings_yield.py`：

```python
"""Earnings Yield (EP) 因子单测：eps_ttm / close。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
import pytest
from backend.engine.base_factor import FactorContext
from backend.factors.fundamental.earnings_yield import EarningsYield


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]
    fund_panel: pd.DataFrame

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None: return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_earnings_yield_happy_path():
    """因子 = eps_ttm / close，逐元素对齐。"""
    n = 30
    idx = pd.bdate_range("2024-01-02", periods=n)
    symbols = ["A", "B"]
    close = pd.DataFrame({"A": np.linspace(10, 20, n), "B": np.linspace(50, 100, n)}, index=idx)
    eps = pd.DataFrame({"A": [0.5] * n, "B": [2.0] * n}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}, fund_panel=eps),
        symbols=symbols, start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = EarningsYield().compute(ctx, {})
    expected = eps / close
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1), expected.sort_index(axis=1), check_names=False,
    )


def test_earnings_yield_nan_robust():
    """eps_ttm 在某段为 NaN（披露前），因子也是 NaN，不崩。"""
    n = 30
    idx = pd.bdate_range("2024-01-02", periods=n)
    close = pd.DataFrame({"A": np.linspace(10, 20, n)}, index=idx)
    eps = pd.DataFrame({"A": [np.nan]*10 + [0.5]*20}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}, fund_panel=eps),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = EarningsYield().compute(ctx, {})
    assert factor["A"].iloc[:10].isna().all()
    assert factor["A"].iloc[10:].notna().all()


def test_earnings_yield_col_order_invariance():
    n = 20
    idx = pd.bdate_range("2024-01-02", periods=n)
    symbols = ["A", "B", "C"]
    close = pd.DataFrame({s: np.linspace(10, 20, n) + i for i, s in enumerate(symbols)}, index=idx)
    eps = pd.DataFrame({s: [0.5 + i*0.1]*n for i, s in enumerate(symbols)}, index=idx)

    ctx_a = FactorContext(
        data=FakeDataService(panels={"close": close}, fund_panel=eps),
        symbols=symbols, start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fa = EarningsYield().compute(ctx_a, {})

    shuffled = ["B", "C", "A"]
    close_s = close[shuffled]
    eps_s = eps[shuffled]
    ctx_s = FactorContext(
        data=FakeDataService(panels={"close": close_s}, fund_panel=eps_s),
        symbols=shuffled, start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fs = EarningsYield().compute(ctx_s, {})

    for c in symbols:
        assert (fa[c] - fs[c]).abs().max() < 1e-12
```

**Step 2: Run failing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_earnings_yield.py -v
```
Expected: ImportError（fundamental 目录不存在）

**Step 3: Implement**

创建 `backend/factors/fundamental/__init__.py` （空文件）

```python
"""基本面因子目录。"""
```

创建 `backend/factors/fundamental/earnings_yield.py`：

```python
"""Earnings Yield (EP)：盈利收益率，市盈率倒数。

公式：``factor_t = eps_ttm_t (PIT, ffill) / close_t (qfq)``。

直觉：Fama-French 价值因子核心。值越大 → 估值越便宜 → 长仓信号。
A 股大盘股稳定有效；小盘可能反向，下游 LightGBM 学非线性可处理。

预热 = 0（PIT 数据自带左 seed）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class EarningsYield(BaseFactor):
    factor_id = "earnings_yield"
    display_name = "盈利收益率 EP（eps_ttm/close）"
    category = "fundamental"
    description = "eps_ttm（PIT, ffill 到日频） / close（qfq），即 1/PE。"
    hypothesis = "估值越便宜（EP 越高）长期超额收益越高（价值溢价）。"
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        s, e = ctx.start_date.date(), ctx.end_date.date()
        eps = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="eps_ttm")
        close = ctx.data.load_panel(ctx.symbols, s, e, field="close", adjust="qfq")
        if eps.empty or close.empty:
            return pd.DataFrame()
        eps, close = eps.align(close, join="inner")
        return (eps / close).loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_earnings_yield.py -v
```

**Step 5: Commit**
```bash
git add backend/factors/fundamental/__init__.py backend/factors/fundamental/earnings_yield.py backend/tests/test_factors_earnings_yield.py
git commit -m "feat(factor): earnings_yield EP 因子（eps_ttm/close，PIT ffill）"
```

---

## Task 5: roe_yoy（ROE 同比改善）

**Files:**
- Create: `backend/factors/fundamental/roe_yoy.py`
- Test: `backend/tests/test_factors_roe_yoy.py`

**公式**：`roe_avg_t - roe_avg_{t - 252_trading_days}`

**Step 1: Write failing test**

创建 `backend/tests/test_factors_roe_yoy.py`：

```python
"""ROE YoY 因子单测：roe_avg - shift(252)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.fundamental.roe_yoy import RoeYoy


@dataclass
class FakeDataService:
    fund_panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_roe_yoy_happy_path():
    """构造 280 天 ROE 序列：前 252 天 0.10，之后 0.15 → 因子在 t=252 起 = 0.05。"""
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({"A": [0.10]*252 + [0.15]*(n-252)}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = RoeYoy().compute(ctx, {})
    # 前 252 天因 shift(252) NaN → 因子 NaN
    assert factor["A"].iloc[:252].isna().all()
    # 第 252+ 天 = 0.15 - 0.10 = 0.05
    assert abs(factor["A"].iloc[252] - 0.05) < 1e-9


def test_roe_yoy_nan_robust():
    """披露前 NaN 段 + shift 后段都应是 NaN，不崩。"""
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({"A": [np.nan]*100 + [0.10]*180}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = RoeYoy().compute(ctx, {})
    # 至少不抛异常 + 末段 = 0（0.10 - 0.10）
    assert not factor.empty
    assert factor["A"].iloc[-1] == 0.0


def test_roe_yoy_col_order_invariance():
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({
        "A": [0.10]*252 + [0.15]*(n-252),
        "B": [0.05]*252 + [0.20]*(n-252),
        "C": [0.12]*252 + [0.10]*(n-252),
    }, index=idx)
    ctx_a = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A","B","C"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fa = RoeYoy().compute(ctx_a, {})

    panel_s = panel[["C", "A", "B"]]
    ctx_s = FactorContext(
        data=FakeDataService(fund_panel=panel_s),
        symbols=["C","A","B"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fs = RoeYoy().compute(ctx_s, {})
    for c in ["A","B","C"]:
        assert (fa[c] - fs[c]).abs().fillna(0).max() < 1e-12
```

**Step 2: Run failing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_roe_yoy.py -v
```

**Step 3: Implement**

创建 `backend/factors/fundamental/roe_yoy.py`：

```python
"""ROE YoY：ROE 同比改善（质量动量）。

公式：``factor_t = roe_avg_t - roe_avg_{t - lag}``，lag 默认 252 交易日（≈ 1 年）。

直觉：AQR Quality 因子家族的 "Quality Momentum"——ROE 同比改善的公司
未来超额收益高。A 股财报季前后效应显著。

注意：因 ``load_fundamental_panel`` 返回 ffill 后的日频 panel，shift(252) 不
精确对齐"同期 announcement"，会有 ±10-30 交易日的偏差。学术上 ±20 交易日的
偏差不损 IC 显著性。批次 2 可补严格按 announcement_date 对齐的版本。

预热 = 0（PIT 自带左 seed；shift 后前 lag 天 NaN，下游会过滤）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RoeYoy(BaseFactor):
    factor_id = "roe_yoy"
    display_name = "ROE 同比改善（roe_avg - shift(252)）"
    category = "fundamental"
    description = "PIT roe_avg 减去 252 交易日前的同字段（同比变化）。"
    hypothesis = "ROE 同比改善 → 质量动量 → 长期超额。"
    params_schema: dict = {
        "yoy_lag": {"type": "int", "default": 252, "min": 200, "max": 365,
                    "desc": "同比 lag（交易日，252 ≈ 1 年）"},
    }
    default_params: dict = {"yoy_lag": 252}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        lag = int(params.get("yoy_lag", self.default_params["yoy_lag"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        panel = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="roe_avg")
        if panel.empty:
            return pd.DataFrame()
        return (panel - panel.shift(lag)).loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_roe_yoy.py -v
```

**Step 5: Commit**
```bash
git add backend/factors/fundamental/roe_yoy.py backend/tests/test_factors_roe_yoy.py
git commit -m "feat(factor): roe_yoy ROE 同比改善（质量动量，shift 252 简化版）"
```

---

## Task 6: gp_margin_stability（毛利率稳定性）

**Files:**
- Create: `backend/factors/fundamental/gp_margin_stability.py`
- Test: `backend/tests/test_factors_gp_margin_stability.py`

**公式**：`-1 * rolling_std(gp_margin, 252)`

**Step 1: Write failing test**

创建 `backend/tests/test_factors_gp_margin_stability.py`：

```python
"""毛利率稳定性单测：-rolling_std(gp_margin, 252)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.fundamental.gp_margin_stability import GpMarginStability


@dataclass
class FakeDataService:
    fund_panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="gp_margin",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_gp_margin_stability_happy_path():
    """A 序列恒定 0.30 → std=0 → 因子=-0；B 序列波动 → 因子<0 且 < A。"""
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(0)
    panel = pd.DataFrame({
        "A": [0.30] * n,                                # 恒定
        "B": 0.30 + rng.normal(0, 0.05, n),             # 随机扰动
    }, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A","B"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = GpMarginStability().compute(ctx, {})
    # 末段：A = -0（稳定），B < 0（有波动）；A > B（A 更稳定）
    last = factor.iloc[-1]
    assert abs(last["A"]) < 1e-9   # std 全 0
    assert last["B"] < -1e-3       # 显著波动
    assert last["A"] > last["B"]


def test_gp_margin_stability_nan_robust():
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({"A": [np.nan]*100 + [0.30]*180}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = GpMarginStability().compute(ctx, {})
    assert not factor.empty
    # 末段：rolling 252 全部落在 0.30 段后期 → std=0 → 因子=0
    assert abs(factor["A"].iloc[-1]) < 1e-9


def test_gp_margin_stability_col_order_invariance():
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(7)
    panel = pd.DataFrame({
        "A": [0.30]*n,
        "B": 0.30 + rng.normal(0, 0.05, n),
        "C": 0.30 + rng.normal(0, 0.10, n),
    }, index=idx)
    ctx_a = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A","B","C"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fa = GpMarginStability().compute(ctx_a, {})

    panel_s = panel[["C", "A", "B"]]
    ctx_s = FactorContext(
        data=FakeDataService(fund_panel=panel_s),
        symbols=["C","A","B"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fs = GpMarginStability().compute(ctx_s, {})

    for c in ["A","B","C"]:
        assert (fa[c] - fs[c]).abs().fillna(0).max() < 1e-12
```

**Step 2: Run failing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_gp_margin_stability.py -v
```

**Step 3: Implement**

创建 `backend/factors/fundamental/gp_margin_stability.py`：

```python
"""GpMarginStability：毛利率稳定性（AQR Quality）。

公式：``factor_t = -1 * rolling_std(gp_margin, window=252)``。

直觉：毛利率波动小 = 商业模式稳定 = 高质量企业。AQR Quality 因子里
"Profitability Stability" 维度。取负后大值 → 稳定 → 长仓信号。

注意：``load_fundamental_panel`` 返回 ffill 后的日频 panel，含大量重复值
（季报 ~60 个交易日才更新一次），rolling std 在重复值期间是 0；这种"伪低波"
是 ffill 的副作用，所有股票同样被偏置，下游 cross-section 排序不受影响。

预热 = 0（PIT 自带左 seed；rolling 前 window-1 天 NaN）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class GpMarginStability(BaseFactor):
    factor_id = "gp_margin_stability"
    display_name = "毛利率稳定性（-rolling_std(gp_margin, 252)）"
    category = "fundamental"
    description = "PIT gp_margin 的 252 交易日 rolling std 取负——稳定 → 长仓。"
    hypothesis = "毛利率稳定 → 商业模式可预测 → 长期超额（AQR Quality）。"
    params_schema: dict = {
        "window": {"type": "int", "default": 252, "min": 60, "max": 504,
                   "desc": "rolling std 窗口（交易日）"},
    }
    default_params: dict = {"window": 252}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        panel = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="gp_margin")
        if panel.empty:
            return pd.DataFrame()
        return (-1.0 * panel.rolling(window).std()).loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_gp_margin_stability.py -v
```

**Step 5: Commit**
```bash
git add backend/factors/fundamental/gp_margin_stability.py backend/tests/test_factors_gp_margin_stability.py
git commit -m "feat(factor): gp_margin_stability 毛利率稳定性（-rolling_std, AQR Quality）"
```

---

## Task 7: max_anomaly（MAX 异象 / 彩票股反转）

> **设计修正**：原 Task 7 是 `low_turnover = -turnover_ratio`，被发现与现有 turnover_ratio 100% 共线（树模型对 negate 不敏感，feature_importance 会归零）。改为 MAX 异象——与 turnover/IVOL 真正不共线的 A 股异象。详见 design doc §C1 修正记录。

**Files:**
- Create: `backend/factors/volatility/max_anomaly.py`
- Test: `backend/tests/test_factors_max_anomaly.py`

**公式**：`factor = -1 * rolling_max(close.pct_change(), 20)` —— 取过去 N 日单日最高收益的负值（高 MAX = 彩票股 → 未来收益更低 → 取负后高分长仓）。

**Step 1: Write failing test**

创建 `backend/tests/test_factors_max_anomaly.py`：

```python
"""MAX 异象因子单测：-rolling_max(returns, 20)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.volatility.max_anomaly import MaxAnomaly


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None: return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_max_anomaly_happy_path():
    """因子 = -rolling_max(close.pct_change(), 20)，全表与手算对齐。"""
    n = 40
    idx = pd.bdate_range("2024-01-02", periods=n)
    symbols = ["A", "B", "C"]
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    factor = MaxAnomaly().compute(ctx, {})
    # 手算同口径
    ret = close.pct_change(fill_method=None)
    expected = -ret.rolling(20).max()
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_names=False,
    )


def test_max_anomaly_nan_robust():
    n = 40
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(1)
    symbols = ["A", "B"]
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    close.iloc[15:18, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    factor = MaxAnomaly().compute(ctx, {})
    assert not factor.empty
    # B 列末段非 NaN
    assert factor["B"].iloc[-5:].notna().all()


def test_max_anomaly_col_order_invariance():
    n = 40
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(2)
    symbols = ["A", "B", "C"]
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx_a = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    fa = MaxAnomaly().compute(ctx_a, {})

    shuffled = ["C", "A", "B"]
    ctx_s = FactorContext(
        data=FakeDataService(panels={"close": close[shuffled]}), symbols=shuffled,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    fs = MaxAnomaly().compute(ctx_s, {})

    target = fa.index[5]
    for c in symbols:
        a, s = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a):
            assert pd.isna(s)
        else:
            assert abs(a - s) < 1e-12
```

**Step 2: Run failing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_max_anomaly.py -v
```

**Step 3: Implement**

创建 `backend/factors/volatility/max_anomaly.py`：

```python
"""MaxAnomaly：MAX 异象（彩票股反转）。

公式：``factor_t = -1 * rolling_max(close.pct_change(), window)``。

直觉：Bali-Cakici-Whitelaw (RFS 2011) "Maxing Out: Stocks as Lotteries" 提出
MAX 异象——过去 N 日单日最高收益（"彩票特征"）越大的股票未来表现越差。
A 股 Han-Hu-Yang (PBFJ 2018) 等多篇论文确认有效。Negate 后大值 → 低 MAX → 长仓信号。

与 IVOL 的区别（同样基于 returns）：IVOL 是 60 日**残差波动**度量"持续紊乱程度"，
MAX 是 20 日**单日最大**度量"瞬时极端程度"，两者在因子空间正交。

预热 = ``int(window * 1.5) + 10`` 自然日（pct_change 1 + rolling window-1 + 节假 buffer）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class MaxAnomaly(BaseFactor):
    factor_id = "max_anomaly"
    display_name = "MAX 异象（-rolling_max(returns, 20)）"
    category = "volatility"
    description = "过去 20 日单日最高收益取负——高 MAX 股票（彩票特征）未来收益更低。"
    hypothesis = "Bali-Cakici-Whitelaw 2011 / Han-Hu-Yang 2018：高 MAX 股未来跑输；取负使高分→长仓。"
    params_schema: dict = {
        "window": {"type": "int", "default": 20, "min": 5, "max": 60,
                   "desc": "rolling max 窗口（交易日，20 ≈ 1 月）"},
    }
    default_params: dict = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return int(window * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        factor = -ret.rolling(window).max()
        return factor.loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_max_anomaly.py -v
```

**Step 5: Commit**
```bash
git add backend/factors/volatility/max_anomaly.py backend/tests/test_factors_max_anomaly.py
git commit -m "feat(factor): max_anomaly MAX 异象（-rolling_max(returns, 20)，A 股彩票股反转）"
```

---

## Task 8: idio_vol_reversal（特质波动率反转）

**Files:**
- Create: `backend/factors/volatility/idio_vol_reversal.py`
- Test: `backend/tests/test_factors_idio_vol_reversal.py`

**公式**：
```
ret = close.pct_change()
mkt = ret.mean(axis=1)         # 横截面均值近似市场
residual = ret.sub(mkt, axis=0)
factor = -rolling_std(residual, 60)
```

**Step 1: Write failing test**

创建 `backend/tests/test_factors_idio_vol_reversal.py`：

```python
"""特质波动率反转单测：-std(returns - cs_mean(returns), 60)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.volatility.idio_vol_reversal import IdioVolReversal


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None: return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_idio_vol_reversal_happy_path():
    """构造 5 票 80 天 close → 算特质 std → 与手算对齐。"""
    n = 80
    symbols = ["A","B","C","D","E"]
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    factor = IdioVolReversal().compute(ctx, {})

    # 手算同口径
    ret = close.pct_change(fill_method=None)
    mkt = ret.mean(axis=1)
    residual = ret.sub(mkt, axis=0)
    expected = -residual.rolling(60).std()

    target = factor.index[5]
    pd.testing.assert_series_equal(
        factor.loc[target].sort_index(),
        expected.loc[target].sort_index(),
        check_names=False,
    )


def test_idio_vol_reversal_nan_robust():
    n = 80
    symbols = ["A","B","C"]
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(1)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    close.iloc[20:30, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    factor = IdioVolReversal().compute(ctx, {})
    assert not factor.empty
    # B/C 末段非 NaN
    assert factor[["B","C"]].iloc[-3:].notna().all().all()


def test_idio_vol_reversal_col_order_invariance():
    n = 80
    symbols = ["A","B","C"]
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(2)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx_a = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    fa = IdioVolReversal().compute(ctx_a, {})

    shuffled = ["C","A","B"]
    ctx_s = FactorContext(
        data=FakeDataService(panels={"close": close[shuffled]}), symbols=shuffled,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    fs = IdioVolReversal().compute(ctx_s, {})

    target = fa.index[5]
    for c in symbols:
        a, s = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a): assert pd.isna(s)
        else: assert abs(a - s) < 1e-12
```

**Step 2: Run failing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_idio_vol_reversal.py -v
```

**Step 3: Implement**

创建 `backend/factors/volatility/idio_vol_reversal.py`：

```python
"""IdioVolReversal：特质波动率反转（IVOL 异象）。

公式：
  ret      = close.pct_change()
  mkt      = ret.mean(axis=1)           # 横截面均值近似市场收益
  residual = ret - mkt
  factor   = -1 * rolling_std(residual, window=60)

直觉：Ang-Hodrick-Xing-Zhang 2006 IVOL 异象——特质波动越高，未来收益越低。
A 股 Cao-Han 等多个研究确认。取负后高分股 → 低 IVOL → 长仓预期。

为何用横截面均值代替指数：A 股没拉沪深300/中证500 这种基准。横截面均值
在 universe 充分大（≥ 100 票）时统计上等价于市场收益的无偏估计（CAPM 视角）。

预热 = ret_window + 5 个交易日折算自然日 ≈ ``int(ret_window * 1.5) + 10``。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class IdioVolReversal(BaseFactor):
    factor_id = "idio_vol_reversal"
    display_name = "特质波动率反转（-std(ret - cs_mean, 60)）"
    category = "volatility"
    description = (
        "对 close.pct_change 减去横截面均值得残差，再取 60 日 rolling std 取负。"
    )
    hypothesis = "高 IVOL 未来收益更低（IVOL 异象）；取负使高分 → 长仓。"
    params_schema: dict = {
        "ret_window": {"type": "int", "default": 60, "min": 20, "max": 252,
                       "desc": "rolling std 窗口（交易日）"},
    }
    default_params: dict = {"ret_window": 60}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        w = int(params.get("ret_window", self.default_params["ret_window"]))
        return int(w * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        w = int(params.get("ret_window", self.default_params["ret_window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        mkt = ret.mean(axis=1)                  # cross-section mean
        residual = ret.sub(mkt, axis=0)         # 每行减市场
        factor = -residual.rolling(w).std()
        return factor.loc[ctx.start_date :]
```

**Step 4: Run passing**
```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_idio_vol_reversal.py -v
```

**Step 5: Commit**
```bash
git add backend/factors/volatility/idio_vol_reversal.py backend/tests/test_factors_idio_vol_reversal.py
git commit -m "feat(factor): idio_vol_reversal 特质波动率反转（IVOL 异象，cs_mean 近似市场）"
```

---

## Task 9: 端到端集成验证

**Files:**
- Test: `backend/tests/test_factors_batch1_registry.py`

**Step 1: Write integration test**

创建 `backend/tests/test_factors_batch1_registry.py`：

```python
"""批次 1 因子的注册集成验证。

验证：
1. 8 个新因子文件能被 import 不抛错（FactorRegistry.scan_and_register 内部 import 各
   factor 模块，import 失败会让整个 registry 启动失败）。
2. 8 个 factor_id 都在 registry 里（保证 UI 下拉能看到）。
3. 每个因子的 BaseFactor 必填 ClassVar（factor_id / display_name / category）非空。
"""
from __future__ import annotations
import pytest
from backend.engine.base_factor import BaseFactor


_BATCH1_FACTOR_IDS = {
    "alpha101_6", "alpha101_12", "alpha101_101",
    "earnings_yield", "roe_yoy", "gp_margin_stability",
    "idio_vol_reversal", "max_anomaly",
}


def test_all_batch1_factor_modules_import_cleanly():
    """8 个因子模块全部成功 import（不抛异常）。"""
    from backend.factors.alpha101 import alpha101_6, alpha101_12, alpha101_101
    from backend.factors.fundamental import earnings_yield, roe_yoy, gp_margin_stability
    from backend.factors.volatility import idio_vol_reversal, max_anomaly


def test_factor_registry_finds_all_batch1_factors():
    """FactorRegistry 扫到全部 8 个新 factor_id。"""
    from backend.engine.factor_registry import FactorRegistry
    reg = FactorRegistry()
    reg.scan_and_register()
    ids = {f.factor_id for f in reg.list_factors()}
    missing = _BATCH1_FACTOR_IDS - ids
    assert not missing, f"批次 1 因子未注册：{missing}"


def test_each_batch1_factor_has_required_classvars():
    """每个新因子的 factor_id / display_name / category 都非空。"""
    from backend.factors.alpha101.alpha101_6 import Alpha101_6
    from backend.factors.alpha101.alpha101_12 import Alpha101_12
    from backend.factors.alpha101.alpha101_101 import Alpha101_101
    from backend.factors.fundamental.earnings_yield import EarningsYield
    from backend.factors.fundamental.roe_yoy import RoeYoy
    from backend.factors.fundamental.gp_margin_stability import GpMarginStability
    from backend.factors.volatility.idio_vol_reversal import IdioVolReversal
    from backend.factors.volatility.max_anomaly import MaxAnomaly

    cls_list = [Alpha101_6, Alpha101_12, Alpha101_101,
                EarningsYield, RoeYoy, GpMarginStability,
                IdioVolReversal, MaxAnomaly]
    for cls in cls_list:
        assert isinstance(cls.factor_id, str) and cls.factor_id
        assert isinstance(cls.display_name, str) and cls.display_name
        assert isinstance(cls.category, str) and cls.category
```

**Step 2: Run integration test**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_batch1_registry.py -v
```

Expected: 3 passed

**Step 3: Final regression on all factor + composition tests**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/backend/.venv/bin/python -m pytest backend/tests/test_factors_*.py backend/tests/test_factor_registry.py backend/tests/test_lightgbm_composition.py backend/tests/test_composition_ic_contribution.py -q
```

Expected: 全绿（含 8 × 3 = 24 新增 + 3 集成 + 现有 ~30+）

**Step 4: vue-tsc 兜底（前端零改动应当无新错误）**

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/factors-batch1/frontend && npx vue-tsc --noEmit 2>&1 | tail -3
```

Expected: 仅 pre-existing `vite/client` / `baseUrl deprecation` 两条，无指向新文件的错误。

**Step 5: Commit + push**

```bash
git add backend/tests/test_factors_batch1_registry.py
git commit -m "test(factor): 批次 1 因子注册集成验证（8 个 factor_id 全注册）"
git push -u origin claude/factors-batch1
```

Expected: push 成功，分支可见 PR 链接。

---

## DoD（Definition of Done）

- [ ] 8 个因子文件创建并通过 import（Task 1-8）
- [ ] 8 × 3 = 24 个单元测试全绿（Task 1-8）
- [ ] 集成测试 3/3 通过（Task 9）
- [ ] backend test 整套不回归（Task 9 Step 3）
- [ ] 前端 vue-tsc 无新增错误（Task 9 Step 4）
- [ ] 分支 push 到 origin（Task 9 Step 5）

## 风险与回滚

- **某个因子手算与公式对不上**：参考 plan 里的"手算同口径"代码段，确保测试的 expected 用同样的 pandas API 计算
- **fundamental 子目录 import 失败**：`__init__.py` 必须存在（即使空）
- **LightGBM 实测失败**：批次 1 不强制端到端跑 `ml_lgb` composition（DB 依赖太重），由用户 UI 验证

## Backout Plan

如果某个因子的 IC / 测试存在不可调和问题：
- 单独 `git revert <task_N_commit>` 回滚那个因子
- 其它因子互相独立，不会相互影响
