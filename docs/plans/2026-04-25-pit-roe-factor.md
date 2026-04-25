# PIT ROE 因子实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 新增 `roe_pit` 因子（基于 baostock 财报 PIT 数据），跑通"因子扫描 → 计算 → 评估 IC → 落库"完整链路，验证 PIT join 语义正确性。

**Architecture:**
- 在 `DataService` 增加 `load_fundamental_panel`：从 `fr_fundamental_profit` 按 `announcement_date` 拉财报值，ffill 到日频交易日，返回 (date × symbol) 宽表（与 `load_panel` 同形态）。
- 新增 `backend/factors/custom/roe_pit.py`：继承 `BaseFactor`，`compute()` 调用上述方法返回 ROE 宽表。无 warmup、无参数。
- 复用现有 `FactorRegistry.scan_and_register` + `services/eval_service.run_eval` + `metrics.cross_sectional_ic`，不动评估管线。

**Tech Stack:** Python 3.10、pandas、PyMySQL（fr_fundamental_profit / fr_trade_calendar）、pytest（单测，FakeDataService 模式参考 [test_factors_math.py](backend/tests/test_factors_math.py)）。

**关键 PIT 语义：**
- 财报值在 `announcement_date` 当天**收盘后**才公开（baostock 披露日已是公告日，安全可用）。本因子约定：**`announcement_date` 当天起至下个 announcement 之前**，因子值都是该期 ROE。
- 即"披露 T 日，T+1 日开盘可用"——T+0 即可用是激进近似，但与现有量价因子（基于 close 的 T+0 信号）的时序粒度对齐，先做这一档；后续若需保守，再把 ROE 整体 shift(1) 一日。
- ffill 范围 = 评估窗口内交易日（用 fr_trade_calendar）；没有任何披露时该 symbol 全 NaN 列。

---

## Task 1: 给 DataService 加 load_fundamental_panel 方法

**Files:**
- Modify: `backend/storage/data_service.py`（在 `load_panel` 后、`save_factor_values` 前插入新方法）
- Test: `backend/tests/test_data_service_fundamentals.py`（新建）

**Step 1: 写失败测试**

```python
# backend/tests/test_data_service_fundamentals.py
"""DataService.load_fundamental_panel：PIT 财报展平到日频。

不依赖真实 DB；通过 monkeypatch 替换 mysql_conn 注入 mock 行。验证：
- 仅 announcement_date <= 当日的财报会被使用（PIT 不前视）
- 同 symbol 多期数据按 announcement_date 排序后 ffill
- 交易日历缺口被填上、非交易日不进 index
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.storage.data_service import DataService


class _FakeCursor:
    def __init__(self, payloads: list[list[dict]]):
        self._payloads = list(payloads)
        self._current: list[dict] = []

    def execute(self, sql, params=None):
        self._current = self._payloads.pop(0) if self._payloads else []

    def fetchall(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, payloads):
        self._payloads = payloads

    def cursor(self):
        return _FakeCursor(self._payloads)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextmanager
def _fake_mysql_conn(payloads):
    yield _FakeConn(payloads)


def test_load_fundamental_panel_ffills_announcement_date_to_trading_days(monkeypatch):
    # 第 1 个 payload：交易日 2026-01-05 ~ 2026-01-09（5 天工作日）
    cal_rows = [{"trade_date": dt.date(2026, 1, d)} for d in (5, 6, 7, 8, 9)]
    # 第 2 个 payload：profit 表，A 在 1-06 披露 ROE=0.1，B 在 1-08 披露 ROE=0.2
    profit_rows = [
        {"symbol": "000001.SZ", "announcement_date": dt.date(2026, 1, 6), "roe_avg": 0.1},
        {"symbol": "600000.SH", "announcement_date": dt.date(2026, 1, 8), "roe_avg": 0.2},
    ]
    monkeypatch.setattr(
        "backend.storage.data_service.mysql_conn",
        lambda: _fake_mysql_conn([cal_rows, profit_rows]),
    )

    svc = DataService()
    panel = svc.load_fundamental_panel(
        symbols=["000001.SZ", "600000.SH"],
        start=dt.date(2026, 1, 5),
        end=dt.date(2026, 1, 9),
        field="roe_avg",
    )

    # index = 5 个交易日，columns = 2 个 symbol
    assert list(panel.index) == [pd.Timestamp(2026, 1, d) for d in (5, 6, 7, 8, 9)]
    assert set(panel.columns) == {"000001.SZ", "600000.SH"}

    # 000001.SZ：1-05 NaN（披露前），1-06 起 = 0.1
    a = panel["000001.SZ"]
    assert pd.isna(a.loc["2026-01-05"])
    assert a.loc["2026-01-06"] == pytest.approx(0.1)
    assert a.loc["2026-01-09"] == pytest.approx(0.1)

    # 600000.SH：1-05~1-07 NaN，1-08 起 = 0.2
    b = panel["600000.SH"]
    assert pd.isna(b.loc["2026-01-07"])
    assert b.loc["2026-01-08"] == pytest.approx(0.2)


def test_load_fundamental_panel_empty_when_no_disclosures(monkeypatch):
    cal_rows = [{"trade_date": dt.date(2026, 1, 5)}]
    monkeypatch.setattr(
        "backend.storage.data_service.mysql_conn",
        lambda: _fake_mysql_conn([cal_rows, []]),
    )
    svc = DataService()
    panel = svc.load_fundamental_panel(
        symbols=["000001.SZ"], start=dt.date(2026, 1, 5), end=dt.date(2026, 1, 5),
    )
    assert panel.empty
```

**Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/test_data_service_fundamentals.py -v`
Expected: FAIL — `AttributeError: 'DataService' object has no attribute 'load_fundamental_panel'`

**Step 3: 写最小实现**

在 `backend/storage/data_service.py` 的 `load_panel` 后追加：

```python
    def load_fundamental_panel(
        self,
        symbols: list[str],
        start: date,
        end: date,
        field: str = "roe_avg",
        table: str = "fr_fundamental_profit",
    ) -> pd.DataFrame:
        """PIT 财报值按 announcement_date ffill 到日频交易日，返回 (date × symbol) 宽表。

        语义：``announcement_date`` 当天起、到下个 announcement 之前，因子值保持不变。
        非交易日不进 index；披露之前的交易日为 NaN。

        Args:
            symbols: 标准 symbol 列表（如 ``["000001.SZ"]``）。
            start / end: 评估窗口（闭区间）。
            field: 要拉的财报字段，必须是 ``_FUND_FIELDS_PROFIT`` 白名单内之一。
            table: 当前只支持 ``fr_fundamental_profit``；预留扩展位（balance / growth）。

        Returns:
            ``index=DatetimeIndex(交易日, 升序)``, ``columns=symbol``, 值为 float。
            没有任何披露时返回空 DataFrame。
        """
        if table != "fr_fundamental_profit":
            raise NotImplementedError(
                f"load_fundamental_panel: 当前仅支持 fr_fundamental_profit，收到 {table!r}"
            )
        if field not in _FUND_FIELDS_PROFIT:
            raise ValueError(
                f"field {field!r} 不在白名单 {sorted(_FUND_FIELDS_PROFIT)} 内"
            )
        if not symbols:
            return pd.DataFrame()

        # 1) 拉交易日历（end 之内，start 之上）。fr_trade_calendar 已 sync。
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT trade_date FROM fr_trade_calendar "
                    "WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
                    (start, end),
                )
                cal_rows = cur.fetchall()
                if not cal_rows:
                    return pd.DataFrame()
                cal_index = pd.DatetimeIndex(
                    [pd.Timestamp(r["trade_date"]) for r in cal_rows]
                )

                # 2) 拉财报：announcement_date <= end 的全部记录（左侧不限，方便 ffill 跨窗口起点）。
                placeholders = ",".join(["%s"] * len(symbols))
                cur.execute(
                    f"SELECT symbol, announcement_date, {field} AS v "
                    f"FROM fr_fundamental_profit "
                    f"WHERE symbol IN ({placeholders}) "
                    f"  AND announcement_date <= %s "
                    f"  AND {field} IS NOT NULL "
                    f"ORDER BY symbol, announcement_date",
                    (*symbols, end),
                )
                profit_rows = cur.fetchall()

        if not profit_rows:
            return pd.DataFrame()

        # 3) 透视成 (announcement_date × symbol)，再 reindex 到 cal_index 并 ffill
        df = pd.DataFrame(profit_rows)
        df["announcement_date"] = pd.to_datetime(df["announcement_date"])
        df["v"] = df["v"].astype("float64")
        wide = (
            df.pivot_table(
                index="announcement_date", columns="symbol", values="v", aggfunc="last"
            )
            .sort_index()
        )
        # 把窗口左侧（earliest disclosure 之前）也保留下来，再 reindex 到交易日 ffill
        # 用 union 防止 cal_index 与 wide.index 错位时丢失左 seed
        full_idx = wide.index.union(cal_index).sort_values()
        out = wide.reindex(full_idx).ffill().reindex(cal_index)
        out.columns.name = None
        return out
```

并在文件顶部 `_PRICE_COLS` 之后加一个白名单常量：

```python
# 财报 profit 表里允许暴露给因子的数值字段；防 SQL 注入兜底
_FUND_FIELDS_PROFIT: frozenset[str] = frozenset({
    "roe_avg", "np_margin", "gp_margin",
    "net_profit", "eps_ttm", "mb_revenue",
})
```

**Step 4: 验证测试通过**

Run: `cd backend && uv run pytest tests/test_data_service_fundamentals.py -v`
Expected: 2 passed

**Step 5: 提交**

```bash
git add backend/storage/data_service.py backend/tests/test_data_service_fundamentals.py
git commit -m "feat(data_service): 新增 load_fundamental_panel（PIT 财报 ffill 到日频）"
```

---

## Task 2: 实现 RoePit 因子类 + 单测

**Files:**
- Create: `backend/factors/custom/roe_pit.py`
- Test: `backend/tests/test_factors_pit.py`（新建）

**Step 1: 写失败测试**

```python
# backend/tests/test_factors_pit.py
"""PIT ROE 因子单测：直接喂 fundamental panel，验证 compute 透传 + 切片。"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from backend.engine.base_factor import FactorContext
from backend.factors.custom.roe_pit import RoePit


@dataclass
class FakeFundService:
    """只实现 load_fundamental_panel 的 DataService 替身。"""
    panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.panel.columns]
        return self.panel[cols].copy()


def test_roe_pit_returns_panel_sliced_to_window():
    idx = pd.bdate_range("2025-12-01", periods=10)
    panel = pd.DataFrame(
        {"000001.SZ": [None]*3 + [0.1]*7, "600000.SH": [None]*5 + [0.2]*5},
        index=idx,
    )
    ctx = FactorContext(
        data=FakeFundService(panel=panel),
        symbols=["000001.SZ", "600000.SH"],
        start_date=idx[5],
        end_date=idx[-1],
        warmup_days=0,
    )
    factor = RoePit().compute(ctx, params={})

    # 起点切到 idx[5] 之后；列保留两只
    assert factor.index[0] == idx[5]
    assert factor.index[-1] == idx[-1]
    assert set(factor.columns) == {"000001.SZ", "600000.SH"}
    # 600000.SH 在 idx[5] 当天首次有值
    assert factor["600000.SH"].iloc[0] == pytest.approx(0.2)
    assert factor["000001.SZ"].iloc[0] == pytest.approx(0.1)


def test_roe_pit_required_warmup_is_zero():
    assert RoePit().required_warmup({}) == 0
```

**Step 2: 验证测试失败**

Run: `cd backend && uv run pytest tests/test_factors_pit.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.factors.custom.roe_pit`

**Step 3: 写最小实现**

```python
# backend/factors/custom/roe_pit.py
"""RoePit：基于 baostock 财报 PIT 的 ROE 因子。

定义：每个交易日 t 的因子值 = symbol 在 ``announcement_date <= t`` 的最近一期
``fr_fundamental_profit.roe_avg``。披露之前的交易日为 NaN。

实现要点：
- ``announcement_date`` 当日就视为可用（与现有量价因子的 T+0 信号粒度对齐）。
  若需保守口径，未来可在出口加 ``shift(1)`` 一档。
- 财报数据稀疏（季频），ffill 在 DataService.load_fundamental_panel 内统一做。
- 无需 warmup：``announcement_date <= start_date`` 的最近一条已被 panel 携带。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RoePit(BaseFactor):
    factor_id = "roe_pit"
    display_name = "ROE (PIT, 季度披露 ffill 到日频)"
    category = "custom"
    description = (
        "baostock fr_fundamental_profit.roe_avg，按 announcement_date 在交易日维度 ffill。"
    )
    params_schema = {}
    default_params = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        panel = ctx.data.load_fundamental_panel(
            ctx.symbols,
            ctx.start_date.date(),
            ctx.end_date.date(),
            field="roe_avg",
        )
        if panel.empty:
            return pd.DataFrame()
        return panel.loc[ctx.start_date :]
```

**Step 4: 验证测试通过**

Run: `cd backend && uv run pytest tests/test_factors_pit.py -v`
Expected: 2 passed

**Step 5: 提交**

```bash
git add backend/factors/custom/roe_pit.py backend/tests/test_factors_pit.py
git commit -m "feat(factor): 新增 roe_pit 因子（PIT 财报 ROE）"
```

---

## Task 3: 触发因子扫描，确认 fr_factor_meta 中有 roe_pit

**Files:** 无修改；操作验证。

**Step 1: 重启后端 / 触发扫描**

Run:
```bash
curl -sX POST http://localhost:8000/api/factors:rescan | jq .
```

如果没有 `:rescan` 端点，重启 backend：`./stop.sh && ./start.sh`，启动钩子会跑 `FactorRegistry.scan_and_register`。

Expected: HTTP 200，返回 `{code: 0, data: {...}}`，且日志里能看到 `roe_pit` 注册成功（grep `.run/backend.log`）。

**Step 2: 直接查 DB 确认**

Run:
```bash
uv run python -c "
import sys; sys.path.insert(0, '..')
from backend.storage.mysql_client import mysql_conn
with mysql_conn() as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT factor_id, display_name, category, version, is_active FROM fr_factor_meta WHERE factor_id = 'roe_pit'\")
        print(cur.fetchone())
"
```

Expected: 输出 `{'factor_id': 'roe_pit', 'display_name': 'ROE (PIT...', 'category': 'custom', 'version': 1, 'is_active': 1}`。

**Step 3: 前端因子库页面验证**

打开 [http://localhost:5173/factors](http://localhost:5173/factors)，搜索 "roe_pit"，应该能看到行。

无需提交（无文件变更）。

---

## Task 4: 跑一次评估，确认 IC 出值

**Files:** 无修改；操作验证。

**前置条件：** 需要一个股票池。如果没有蓝筹池，先建一个：

**Step 1: 建测试股票池（HS300 当前成分子集）**

```bash
# 取 HS300 当前成分前 30 只大流通市值股票作为测试池
uv run python -c "
import sys; sys.path.insert(0, '..')
from backend.storage.mysql_client import mysql_conn
with mysql_conn() as c:
    with c.cursor() as cur:
        cur.execute(
            \"SELECT symbol FROM fr_index_constituent WHERE index_code='000300.SH' AND end_date IS NULL ORDER BY symbol LIMIT 30\"
        )
        symbols = [r['symbol'] for r in cur.fetchall()]
        print(symbols)
"
```

通过前端 [http://localhost:5173/pools/new](http://localhost:5173/pools/new) 建池，名称 "HS300 测试池-30"，把上面 30 个 symbol 粘进去保存。记下生成的 pool_id（前端 URL `/pools/<id>`）。

**Step 2: 通过前端发起评估**

打开 [http://localhost:5173/evals/new](http://localhost:5173/evals/new)：
- 因子选 `roe_pit`（无参数）
- 股票池选刚建的 "HS300 测试池-30"
- 时间窗口：`2024-01-01` ~ `2025-12-31`（profit 表已覆盖到 2026-03-31，留 buffer）
- forward returns：1d / 5d / 10d 默认

提交后跳转到 `/evals/<run_id>`，等 status 从 pending → running → completed（财报+30 只票，应当 < 30 秒）。

**Step 3: 验证结果**

页面应能看到：
- IC 时序图（每日 IC，绿/红柱）
- 累计 IC 曲线（递增 = 因子方向稳定 / 来回打架 = 噪声大）
- ic_mean / ic_ir / rank_ic_mean 等汇总数字

**预期 sanity check：**
- |ic_mean| 应该是个 small number（0.01 ~ 0.10），不是 0 也不是 0.99（如果 0.99 大概率泄露，要排查 PIT join）
- ic 时序的非空覆盖率应该 > 50%（至少有一半交易日 ROE 已披露）
- 如果 IC 是常数（每天一样），说明 ffill 链路没断，但截面无差异 — 检查是不是只有一只票有数据

**Step 4: 失败排查清单（仅在 IC 异常时执行）**

- IC=NaN 全场：检查 `fr_factor_eval_metrics.payload_json` 看 factor 宽表是不是空 → 大概率 `load_fundamental_panel` 没匹配上交易日
- IC > 0.5：检查是不是用了 `report_date` 而非 `announcement_date`（=未来数据泄露）
- IC = 0 常数：检查 ROE 在该窗口是不是全部 stocks 同值（不太可能，但 sanity）

无需提交（无文件变更）。

---

## Task 5: 把 RoePit 因子手册条目补进 docs

**Files:**
- Modify: `frontend/src/pages/docs/FactorGuide.vue`（在术语表/红线表里加一行 PIT 因子说明，可选）

**判断**：如果 FactorGuide 已经有"PIT 因子"分类，加一行；否则跳过此步，避免范围蔓延。先 `git status` 确认要不要改这个文件。

Run:
```bash
grep -n "PIT\|announcement_date\|财报" frontend/src/pages/docs/FactorGuide.vue | head -10
```

如果能匹配，按现有风格加一行；否则 **skip task 5**。

提交（如有）：

```bash
git add frontend/src/pages/docs/FactorGuide.vue
git commit -m "docs(factor-guide): 补充 PIT 因子说明（roe_pit 样例）"
```

---

## 完成判定（DoD）

- [x] Task 1 单测 2 passed（load_fundamental_panel）
- [x] Task 2 单测 2 passed（RoePit）
- [x] Task 3 fr_factor_meta 有 roe_pit 行 + 前端因子库可见
- [x] Task 4 评估 run 状态 = completed，IC 时序非全 NaN，ic_mean 在 [-0.2, 0.2] 范围
- [x] Task 5 (可选) 因子手册更新

---

## 已知风险 / 后续

1. **披露日 = T+0 假设**：保守起见可加 shift(1)，但当前不做。等链路全跑通后再说。
2. **行业中性化未做**：roe_pit 在不同行业之间不可直接比（金融股 ROE 普遍高）。后续可加 `roe_pit_neutral` 因子做行业 demean。
3. **profit 之外字段**：`load_fundamental_panel` 已留 `field` 参数，下次扩 `gp_margin` / `np_margin` 因子直接复用。
4. **PIT 因子缓存**：当前每次 compute 都查 DB；如果 60+ 只票 + 长窗口慢，再考虑 `save_factor_values` 落 ClickHouse 缓存。MVP 不做。
