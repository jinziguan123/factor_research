# 行业/市值中性化 + 轻量风格归因 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在因子评估 pipeline 中增加行业/市值中性化步骤和 5 个轻量 Barra 风格因子，前端评估详情页展示中性化对比和风格暴露。

**Architecture:** NeutralizationService（截面回归残差）+ 5 个 BaseFactor 子类（riskmodel/）+ AttributionService（暴露度分解）。风格因子复用现有注册/缓存/评估基础设施。DataService 扩展 3 个数据加载方法。

**Tech Stack:** Python/FastAPI + pandas/numpy + akshare + Vue 3/Naive UI/ECharts + TypeScript

---

### Task 1: DDL 迁移

**Files:**
- Create: `backend/scripts/migrations/016_add_market_cap_pb.sql`
- Create: `backend/scripts/migrations/017_add_industry_history.sql`

- [ ] **Step 1: 创建 fr_daily_market_cap + fr_daily_pb 表 DDL**

```sql
-- 016: fr_daily_market_cap + fr_daily_pb（日频市值和市净率）
-- fr_daily_market_cap: 日频总市值 / 流通市值，symbol_id 与 stock_bar_1d 对齐
-- fr_daily_pb: 日频市净率，从 akshare spot 快照拉取

CREATE TABLE IF NOT EXISTS `fr_daily_market_cap` (
  `symbol_id`  int unsigned NOT NULL,
  `trade_date` date         NOT NULL,
  `total_mv`   decimal(18,2) DEFAULT NULL COMMENT '总市值（元）',
  `float_mv`   decimal(18,2) DEFAULT NULL COMMENT '流通市值（元）',
  PRIMARY KEY (`symbol_id`, `trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `fr_daily_pb` (
  `symbol_id`  int unsigned NOT NULL,
  `trade_date` date         NOT NULL,
  `pb`         decimal(10,4) DEFAULT NULL COMMENT '市净率',
  PRIMARY KEY (`symbol_id`, `trade_date`),
  KEY `idx_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 创建 fr_industry_history 表 DDL**

```sql
-- 017: fr_industry_history（行业分类历史快照）
-- 每日拉取申万一级行业分类，仅写入新增/变化的行，天然形成历史快照
-- snapshot_date=快照日期，查询时取 as_of_date 之前最近的快照即为该日行业归属

CREATE TABLE IF NOT EXISTS `fr_industry_history` (
  `symbol`         varchar(16)  NOT NULL,
  `snapshot_date`  date         NOT NULL,
  `industry_l1`    varchar(64)  DEFAULT NULL COMMENT '申万一级行业',
  `industry_l2`    varchar(64)  DEFAULT NULL COMMENT '申万二级行业',
  `classification` varchar(32)  NOT NULL DEFAULT 'sw' COMMENT '分类标准：sw/csrc',
  PRIMARY KEY (`symbol`, `snapshot_date`),
  KEY `idx_snapshot` (`snapshot_date`),
  KEY `idx_symbol_date` (`symbol`, `snapshot_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 3: 提交**

```bash
git add backend/scripts/migrations/016_add_market_cap_pb.sql backend/scripts/migrations/017_add_industry_history.sql
git commit -m "feat(db): add fr_daily_market_cap, fr_daily_pb, fr_industry_history tables"
```

---

### Task 2: akshare 市值 + PB 适配器

**Files:**
- Create: `backend/adapters/akshare/market_data.py`
- Create: `backend/adapters/akshare/__init__.py`

- [ ] **Step 1: 创建 akshare 子包 `__init__.py`**

```python
"""akshare data adapters (market cap, PB, industry)."""
```

- [ ] **Step 2: 创建 `market_data.py`**

```python
"""Fetch market cap + PB from akshare spot snapshot, write to MySQL."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver

log = logging.getLogger(__name__)

# akshare spot_em columns we need (Chinese → English)
_RENAME = {
    "代码": "_raw_code",
    "总市值": "total_mv",
    "流通市值": "float_mv",
    "市净率": "pb",
}


def fetch_and_save_market_data(
    trade_date: date | None = None,
    spot_fetcher: Callable[[], pd.DataFrame] | None = None,
) -> int:
    """Pull akshare spot snapshot, write market cap + PB to MySQL.

    Returns number of rows written.
    """
    if spot_fetcher is None:
        import akshare as ak  # noqa: PLC0415
        spot_fetcher = ak.stock_zh_a_spot_em

    raw = spot_fetcher()
    if raw.empty:
        log.warning("akshare spot snapshot returned empty")
        return 0

    df = raw[list(_RENAME.keys())].rename(columns=_RENAME).copy()
    df["symbol"] = df["_raw_code"].apply(_safe_normalize)
    df = df[df["symbol"].notna() & (df["symbol"] != "")]

    resolver = SymbolResolver()
    sid_map = resolver.resolve_many(df["symbol"].tolist())
    df["symbol_id"] = df["symbol"].map(sid_map)
    df = df[df["symbol_id"].notna()]

    if trade_date is None:
        trade_date = date.today()

    # market cap
    mv_rows = []
    pb_rows = []
    for _, row in df.iterrows():
        sid = int(row["symbol_id"])
        mv_rows.append({
            "symbol_id": sid,
            "trade_date": trade_date,
            "total_mv": _safe_decimal(row.get("total_mv")),
            "float_mv": _safe_decimal(row.get("float_mv")),
        })
        pb_rows.append({
            "symbol_id": sid,
            "trade_date": trade_date,
            "pb": _safe_decimal(row.get("pb")),
        })

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.executemany(
                "REPLACE INTO fr_daily_market_cap (symbol_id, trade_date, total_mv, float_mv) "
                "VALUES (%(symbol_id)s, %(trade_date)s, %(total_mv)s, %(float_mv)s)",
                mv_rows,
            )
            cur.executemany(
                "REPLACE INTO fr_daily_pb (symbol_id, trade_date, pb) "
                "VALUES (%(symbol_id)s, %(trade_date)s, %(pb)s)",
                pb_rows,
            )
        c.commit()

    log.info("Saved %d market cap + %d PB rows for %s", len(mv_rows), len(pb_rows), trade_date)
    return len(mv_rows)


def _safe_normalize(raw_code: str) -> str | None:
    try:
        return normalize_symbol(str(raw_code))
    except (ValueError, TypeError):
        return None


def _safe_decimal(val) -> float | None:
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 3: 提交**

```bash
git add backend/adapters/akshare/__init__.py backend/adapters/akshare/market_data.py
git commit -m "feat(adapter): add akshare market_cap + PB data fetcher"
```

---

### Task 3: akshare 行业分类适配器

**Files:**
- Create: `backend/adapters/akshare/industry.py`

- [ ] **Step 1: 创建 `industry.py`**

```python
"""Fetch industry classification from akshare, write to MySQL."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import pandas as pd

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def fetch_and_save_industry(
    snapshot_date: date | None = None,
    fetcher: Callable[[], pd.DataFrame] | None = None,
) -> int:
    """Pull akshare Shenwan industry classification, write changed rows to
    fr_industry_history. Returns number of new/changed rows written.
    """
    if fetcher is None:
        import akshare as ak  # noqa: PLC0415
        fetcher = ak.stock_board_industry_name_em

    raw = fetcher()
    if raw.empty:
        log.warning("akshare industry returned empty")
        return 0

    if snapshot_date is None:
        snapshot_date = date.today()

    rows: list[dict] = []
    for _, r in raw.iterrows():
        code = str(r.get("代码", ""))
        try:
            symbol = normalize_symbol(code)
        except (ValueError, TypeError):
            continue
        rows.append({
            "symbol": symbol,
            "snapshot_date": snapshot_date,
            "industry_l1": str(r.get("板块名称", "")).strip() or None,
            "industry_l2": str(r.get("板块名称", "")).strip() or None,
            "classification": "sw",
        })

    if not rows:
        return 0

    with mysql_conn() as c:
        with c.cursor() as cur:
            written = 0
            for row in rows:
                cur.execute(
                    "SELECT industry_l1 FROM fr_industry_history "
                    "WHERE symbol=%s ORDER BY snapshot_date DESC LIMIT 1",
                    (row["symbol"],),
                )
                prev = cur.fetchone()
                if prev is None or prev.get("industry_l1") != row["industry_l1"]:
                    cur.execute(
                        "INSERT INTO fr_industry_history "
                        "(symbol, snapshot_date, industry_l1, industry_l2, classification) "
                        "VALUES (%(symbol)s, %(snapshot_date)s, %(industry_l1)s, "
                        "%(industry_l2)s, %(classification)s)",
                        row,
                    )
                    written += 1
        c.commit()

    log.info("Industry: %d new/changed rows for %s", written, snapshot_date)
    return written
```

- [ ] **Step 2: 提交**

```bash
git add backend/adapters/akshare/industry.py
git commit -m "feat(adapter): add akshare industry classification fetcher"
```

---

### Task 4: DataService 扩展

**Files:**
- Modify: `backend/storage/data_service.py`

- [ ] **Step 1: 新增 3 个方法**

在 `DataService` 类中，现有 `load_fundamental_panel` 方法之后追加：

```python
def load_market_cap(
    self,
    symbols: list[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    """返回总市值宽表：index=trade_date, columns=symbol, values=total_mv。"""
    sid_map = self.resolver.resolve_many(symbols)
    if not sid_map:
        return pd.DataFrame()
    inv = {sid: sym for sym, sid in sid_map.items()}
    sid_list = sorted(set(sid_map.values()))

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT symbol_id, trade_date, total_mv "
                "FROM fr_daily_market_cap "
                "WHERE symbol_id IN %(sids)s AND trade_date BETWEEN %(s)s AND %(e)s "
                "ORDER BY symbol_id, trade_date",
                {"sids": sid_list, "s": start, "e": end},
            )
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["symbol_id", "trade_date", "total_mv"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol_id"].map(inv)
    df = df.dropna(subset=["symbol"])
    panel = df.pivot_table(
        index="trade_date", columns="symbol", values="total_mv", aggfunc="last"
    ).sort_index()
    panel.columns.name = None
    return panel


def load_pb(
    self,
    symbols: list[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    """返回 PB 宽表：index=trade_date, columns=symbol, values=pb。"""
    sid_map = self.resolver.resolve_many(symbols)
    if not sid_map:
        return pd.DataFrame()
    inv = {sid: sym for sym, sid in sid_map.items()}
    sid_list = sorted(set(sid_map.values()))

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT symbol_id, trade_date, pb "
                "FROM fr_daily_pb "
                "WHERE symbol_id IN %(sids)s AND trade_date BETWEEN %(s)s AND %(e)s "
                "ORDER BY symbol_id, trade_date",
                {"sids": sid_list, "s": start, "e": end},
            )
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["symbol_id", "trade_date", "pb"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol_id"].map(inv)
    df = df.dropna(subset=["symbol"])
    panel = df.pivot_table(
        index="trade_date", columns="symbol", values="pb", aggfunc="last"
    ).sort_index()
    panel.columns.name = None
    return panel


def load_industry(
    self,
    symbols: list[str],
    as_of_date: date,
) -> pd.Series:
    """返回行业分类 Series：index=symbol, values=industry_l1。

    取 as_of_date 之前最近的行业快照；无记录的返回 None。
    """
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT h.symbol, h.industry_l1 "
                "FROM fr_industry_history h "
                "INNER JOIN ("
                "  SELECT symbol, MAX(snapshot_date) AS max_date "
                "  FROM fr_industry_history "
                "  WHERE snapshot_date <= %(as_of)s "
                "  GROUP BY symbol"
                ") latest ON h.symbol = latest.symbol AND h.snapshot_date = latest.max_date",
                {"as_of": as_of_date},
            )
            rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=str)
    result = pd.Series(
        {r["symbol"]: r["industry_l1"] for r in rows},
        name="industry_l1",
    )
    # 只返回请求的 symbols
    return result.reindex([s.strip().upper() for s in symbols])
```

- [ ] **Step 2: 提交**

```bash
git add backend/storage/data_service.py
git commit -m "feat(data): add load_market_cap, load_pb, load_industry to DataService"
```

---

### Task 5: 历史数据回填脚本

**Files:**
- Create: `backend/scripts/backfill_market_data.py`

- [ ] **Step 1: 创建回填脚本**

```python
"""一次性回填历史市值、PB、行业数据。

用法: python -m backend.scripts.backfill_market_data --start 2015-01-01 --end 2026-05-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def backfill(start: date, end: date) -> None:
    from backend.adapters.akshare.market_data import fetch_and_save_market_data
    from backend.adapters.akshare.industry import fetch_and_save_industry
    from backend.storage.mysql_client import mysql_conn

    # 先拉一次行业（当前快照）
    log.info("Fetching current industry classification...")
    n = fetch_and_save_industry(date.today())
    log.info("Industry: %d rows written", n)

    # 逐日回填市值和 PB
    from backend.storage.symbol_resolver import SymbolResolver
    resolver = SymbolResolver()

    cursor_date = start
    while cursor_date <= end:
        try:
            n = fetch_and_save_market_data(cursor_date)
            log.info("Market data for %s: %d rows", cursor_date, n)
        except Exception as e:
            log.error("Failed for %s: %s", cursor_date, e)
        cursor_date += timedelta(days=1)

    log.info("Backfill complete: %s → %s", start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    backfill(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
```

- [ ] **Step 2: 提交**

```bash
git add backend/scripts/backfill_market_data.py
git commit -m "feat(scripts): add historical market data backfill script"
```

---

### Task 6: NeutralizationService

**Files:**
- Create: `backend/services/neutralization.py`
- Create: `backend/tests/test_neutralization.py`

- [ ] **Step 1: 写测试**

```python
"""NeutralizationService 单元测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.neutralization import NeutralizationService


def make_test_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """构造 3 stocks × 5 days 的假数据。"""
    dates = pd.date_range("2025-01-02", periods=5, freq="B")
    symbols = ["A.SZ", "B.SH", "C.SZ"]
    factor = pd.DataFrame(
        np.random.randn(5, 3), index=dates, columns=symbols
    )
    mktcap = pd.DataFrame(
        [[1e10, 5e10, 2e10]] * 5, index=dates, columns=symbols, dtype=float
    )
    industry = pd.Series(
        {"A.SZ": "银行", "B.SH": "电子", "C.SZ": "银行"}
    )
    return factor, mktcap, industry


def test_neutralize_returns_same_shape():
    factor, mktcap, industry = make_test_data()
    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry)
    assert result.shape == factor.shape
    assert list(result.index) == list(factor.index)
    assert list(result.columns) == list(factor.columns)


def test_neutralize_reduces_industry_bias():
    """中性化后同行业内均值应接近 0。"""
    factor, mktcap, industry = make_test_data()
    factor.iloc[:, :] = 0.0
    # 人为注入行业偏差：银行股 +0.1
    bank_cols = [c for c in factor.columns if industry[c] == "银行"]
    factor[bank_cols] += 0.1

    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry)
    # 中性化后银行股均值应接近 0
    bank_residual = result[bank_cols].values.mean()
    assert abs(bank_residual) < 0.02


def test_neutralize_handles_nan():
    """NaN 因子值应保留为 NaN。"""
    factor, mktcap, industry = make_test_data()
    factor.iloc[2, 0] = np.nan  # 第 3 天 A.SZ 的因子值 = NaN
    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry)
    assert np.isnan(result.iloc[2, 0])


def test_neutralize_small_industry_merged():
    """单行业 < 3 只股票 → 合并为"其他"。"""
    factor, mktcap, industry = make_test_data()
    # C.SZ 单独一个行业 → 应被合并
    industry["C.SZ"] = "稀有行业"
    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry, min_industry_size=3)
    # 不应崩溃，应正常返回
    assert result.shape == factor.shape


def test_neutralize_market_cap_only():
    factor, mktcap, industry = make_test_data()
    svc = NeutralizationService()
    result = svc.neutralize_with_market_cap_only(factor, mktcap)
    assert result.shape == factor.shape


def test_neutralize_industry_only():
    factor, mktcap, industry = make_test_data()
    svc = NeutralizationService()
    result = svc.neutralize_with_industry_only(factor, industry)
    assert result.shape == factor.shape
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_neutralization.py -v
```

Expected: ModuleNotFoundError（NeutralizationService 尚未创建）

- [ ] **Step 3: 实现 NeutralizationService**

```python
"""行业 + 市值截面中性化服务。"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class NeutralizationService:
    """截面 OLS 回归取残差，剥离行业和市值暴露。"""

    def neutralize(
        self,
        factor_panel: pd.DataFrame,
        market_cap: pd.DataFrame,
        industry: pd.Series,
        min_industry_size: int = 3,
    ) -> pd.DataFrame:
        """行业 + 市值中性化。每交易日截面回归，返回残差面板。"""
        return self._neutralize_core(
            factor_panel, market_cap, industry, min_industry_size,
            use_industry=True, use_mktcap=True,
        )

    def neutralize_with_industry_only(
        self,
        factor_panel: pd.DataFrame,
        industry: pd.Series,
        min_industry_size: int = 3,
    ) -> pd.DataFrame:
        """仅行业中性化。"""
        return self._neutralize_core(
            factor_panel, None, industry, min_industry_size,
            use_industry=True, use_mktcap=False,
        )

    def neutralize_with_market_cap_only(
        self,
        factor_panel: pd.DataFrame,
        market_cap: pd.DataFrame,
    ) -> pd.DataFrame:
        """仅市值中性化。"""
        return self._neutralize_core(
            factor_panel, market_cap, None, 3,
            use_industry=False, use_mktcap=True,
        )

    def _neutralize_core(
        self,
        factor_panel: pd.DataFrame,
        market_cap: pd.DataFrame | None,
        industry: pd.Series | None,
        min_industry_size: int,
        use_industry: bool,
        use_mktcap: bool,
    ) -> pd.DataFrame:
        result = factor_panel.copy()

        for d in factor_panel.index:
            y = factor_panel.loc[d]
            valid = ~y.isna()

            X_parts: list[np.ndarray] = []

            if use_mktcap and market_cap is not None:
                mc = market_cap.reindex(index=factor_panel.index, columns=factor_panel.columns)
                if d in mc.index:
                    mc_row = mc.loc[d]
                    log_mc = np.log(mc_row.replace(0, np.nan))
                    valid = valid & log_mc.notna()
                    X_parts.append(log_mc.values.reshape(-1, 1))

            if use_industry and industry is not None:
                ind = industry.reindex(factor_panel.columns)
                valid = valid & ind.notna()

                # 合并小行业
                counts = ind[valid].value_counts()
                small = counts[counts < min_industry_size].index.tolist()
                ind_merged = ind.where(~ind.isin(small), "其他")

                dummies = pd.get_dummies(ind_merged[valid], dtype=float)
                # 去掉一列防共线（drop_first）
                if dummies.shape[1] > 1:
                    dummies = dummies.iloc[:, 1:]
                X_parts.append(dummies.values)

            if not X_parts:
                # 无有效 X → 该日全 NaN
                result.loc[d] = np.nan
                continue

            X = np.hstack(X_parts)
            y_valid = y[valid].values.astype(float)

            if X.shape[0] < 10:
                result.loc[d] = np.nan
                continue

            try:
                beta, _, _, _ = np.linalg.lstsq(X, y_valid, rcond=None)
            except np.linalg.LinAlgError:
                result.loc[d] = np.nan
                continue

            residual = np.full(len(y), np.nan)
            residual[valid.values] = y_valid - X @ beta
            result.loc[d] = residual

        return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_neutralization.py -v
```

Expected: 6 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/neutralization.py backend/tests/test_neutralization.py
git commit -m "feat(service): add NeutralizationService with industry/market-cap regression"
```

---

### Task 7: Eval Pipeline 集成

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/routers/evals.py`
- Modify: `backend/services/eval_service.py`

- [ ] **Step 1: 扩展 `CreateEvalIn` schema**

在 `backend/api/schemas.py` 的 `CreateEvalIn` 类中添加字段：

```python
neutralize: bool = Field(default=True, description="是否做行业+市值中性化")
```

（加在 `split_date` 之前或之后均可）

- [ ] **Step 2: 扩展 eval_service.run_eval()**

修改 `backend/services/eval_service.py` 的 `run_eval` 函数：

在加载 close 数据之后、调用 `evaluate_factor_panel` 之前，插入：

```python
neutralize = bool(body.get("neutralize", True))
neut_payload = None
if neutralize:
    try:
        from backend.services.neutralization import NeutralizationService
        svc2 = DataService()
        mktcap = svc2.load_market_cap(symbols, start.date(), end.date())
        industry = svc2.load_industry(symbols, end.date())
        if not mktcap.empty and not industry.empty:
            neut_svc = NeutralizationService()
            F_neut = neut_svc.neutralize(F, mktcap, industry)
            neut_payload, neut_structured = evaluate_factor_panel(
                F_neut, close,
                forward_periods=forward_periods,
                n_groups=n_groups,
                split_date=split_date,
            )
    except Exception as e:
        log.warning("Neutralization failed for run_id=%s: %s", run_id, e)
```

在写入 MySQL 的 `REPLACE INTO fr_factor_eval_metrics` 中追加字段：

```python
# 在 VALUES 中增加：
neut_ic_mean = neut_structured.get("ic_mean") if neut_structured else None
neut_ic_ir = neut_structured.get("ic_ir") if neut_structured else None
neut_rank_ic_mean = neut_structured.get("rank_ic_mean") if neut_structured else None
neut_rank_ic_ir = neut_structured.get("rank_ic_ir") if neut_structured else None
neut_long_short_annret = neut_structured.get("long_short_annret") if neut_structured else None
neut_payload_json = json.dumps(neut_payload, default=str) if neut_payload else None
```

SQL 需要扩展为包含新列：

```sql
REPLACE INTO fr_factor_eval_metrics
(run_id, ic_mean, ic_std, ic_ir, ic_win_rate, ic_t_stat,
 rank_ic_mean, rank_ic_std, rank_ic_ir,
 turnover_mean, long_short_sharpe, long_short_annret,
 neut_ic_mean, neut_ic_ir, neut_rank_ic_mean, neut_rank_ic_ir,
 neut_long_short_annret, neut_payload_json,
 payload_json)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
```

但首先需要确认 neut_* 列在 MySQL 表中存在（需要先执行 migration 添加列）：

```sql
-- 加在 016 迁移中或单独迁移
ALTER TABLE fr_factor_eval_metrics
    ADD COLUMN neut_ic_mean            double DEFAULT NULL,
    ADD COLUMN neut_ic_ir              double DEFAULT NULL,
    ADD COLUMN neut_rank_ic_mean       double DEFAULT NULL,
    ADD COLUMN neut_rank_ic_ir         double DEFAULT NULL,
    ADD COLUMN neut_long_short_annret  double DEFAULT NULL,
    ADD COLUMN neut_payload_json       longtext;
```

- [ ] **Step 3: 更新 evals router**

在 `backend/api/routers/evals.py` 中，读取 eval 详情时返回 neut_* 字段（已在 metrics 行中，无需额外改动 — 但确认 SQL SELECT 包含新列）。如果详情页用的是 `SELECT * FROM fr_factor_eval_metrics`，则自动包含。

- [ ] **Step 4: 提交**

```bash
git add backend/api/schemas.py backend/api/routers/evals.py backend/services/eval_service.py
git commit -m "feat(eval): integrate neutralization into eval pipeline"
```

---

### Task 8: 5 个风格因子

**Files:**
- Create: `backend/factors/riskmodel/__init__.py`
- Create: `backend/factors/riskmodel/size.py`
- Create: `backend/factors/riskmodel/value.py`
- Create: `backend/factors/riskmodel/momentum_12m1m.py`
- Create: `backend/factors/riskmodel/volatility.py`
- Create: `backend/factors/riskmodel/liquidity.py`

- [ ] **Step 1: `__init__.py`**

```python
"""Lightweight Barra-style risk model factors."""
```

- [ ] **Step 2: Size 因子 (`size.py`)**

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class SizeFactor(BaseFactor):
    factor_id = "size_mv"
    display_name = "规模因子"
    category = "riskmodel"
    description = "Barra-style Size: log(total market cap)"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 1

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        mktcap = ctx.data.load_market_cap(
            ctx.symbols, ctx.start_date.date(), ctx.end_date.date()
        )
        if mktcap.empty:
            return pd.DataFrame()
        mktcap = mktcap.reindex(index=pd.DatetimeIndex(sorted(mktcap.index)))
        result = np.log(mktcap.replace(0.0, np.nan))
        return result.loc[ctx.start_date:]
```

- [ ] **Step 3: Value 因子 (`value.py`)**

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class ValueFactor(BaseFactor):
    factor_id = "value_ep"
    display_name = "价值因子"
    category = "riskmodel"
    description = "Barra-style Value: 1 / PB"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 1

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        pb = ctx.data.load_pb(
            ctx.symbols, ctx.start_date.date(), ctx.end_date.date()
        )
        if pb.empty:
            return pd.DataFrame()
        pb = pb.reindex(index=pd.DatetimeIndex(sorted(pb.index)))
        result = 1.0 / pb.replace(0.0, np.nan)
        return result.loc[ctx.start_date:]
```

- [ ] **Step 4: Momentum 因子 (`momentum_12m1m.py`)**

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Momentum12m1m(BaseFactor):
    factor_id = "momentum_12m1m"
    display_name = "动量因子"
    category = "riskmodel"
    description = "Barra-style Momentum: cumulative return over past 12 months, skipping the most recent month"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return self._calc_warmup(250 + 21)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        close = close.astype(float).sort_index()
        # 12-month return skipping 1 month: p(t-21) / p(t-252) - 1
        result = close.shift(21) / close.shift(252) - 1.0
        return result.loc[ctx.start_date:]
```

- [ ] **Step 5: Volatility 因子 (`volatility.py`)**

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Volatility60d(BaseFactor):
    factor_id = "volatility_60d"
    display_name = "波动因子"
    category = "riskmodel"
    description = "Barra-style Volatility: std of daily returns over trailing 60 days"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return self._calc_warmup(60 + 1)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        close = close.astype(float).sort_index()
        ret = close.pct_change()
        result = ret.rolling(window=60, min_periods=20).std()
        return result.loc[ctx.start_date:]
```

- [ ] **Step 6: Liquidity 因子 (`liquidity.py`)**

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Liquidity20d(BaseFactor):
    factor_id = "liquidity_20d"
    display_name = "流动性因子"
    category = "riskmodel"
    description = "Barra-style Liquidity: average daily turnover over trailing 20 days"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return self._calc_warmup(20 + 1)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()

        volume = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="volume", adjust="none",
        )
        mktcap = ctx.data.load_market_cap(
            ctx.symbols, data_start, ctx.end_date.date(),
        )
        if volume.empty or mktcap.empty:
            return pd.DataFrame()

        volume = volume.astype(float).sort_index()
        mktcap = mktcap.reindex(index=volume.index, columns=volume.columns)
        turnover = volume / mktcap.replace(0.0, np.nan)
        result = turnover.rolling(window=20, min_periods=10).mean()
        return result.loc[ctx.start_date:]
```

- [ ] **Step 7: 提交**

```bash
git add backend/factors/riskmodel/
git commit -m "feat(factors): add 5 Barra-style risk model factors"
```

---

### Task 9: AttributionService

**Files:**
- Create: `backend/services/attribution.py`
- Create: `backend/tests/test_attribution.py`

- [ ] **Step 1: 写测试**

```python
"""AttributionService 单元测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.services.attribution import AttributionService


def make_test_data():
    dates = pd.date_range("2025-01-02", periods=10, freq="B")
    symbols = ["A.SZ", "B.SH", "C.SZ", "D.SH", "E.SZ"]
    np.random.seed(42)
    size = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    value = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    momentum = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    volatility = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    liquidity = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    # 构造因子 = 0.5*Size + 0.3*Value + noise
    alpha = 0.5 * size + 0.3 * value + 0.1 * np.random.randn(10, 5)
    style_panels = {
        "Size": size, "Value": value, "Momentum": momentum,
        "Volatility": volatility, "Liquidity": liquidity,
    }
    return alpha, style_panels


def test_decompose_returns_exposures():
    alpha, style_panels = make_test_data()
    svc = AttributionService()
    result = svc.decompose(alpha, style_panels)
    assert set(result.exposures.keys()) == set(style_panels.keys())
    for name, series in result.exposures.items():
        assert len(series) == len(alpha.index)


def test_decompose_r_squared_between_0_and_1():
    alpha, style_panels = make_test_data()
    svc = AttributionService()
    result = svc.decompose(alpha, style_panels)
    assert (result.r_squared >= 0).all()
    assert (result.r_squared <= 1).all()


def test_decompose_residual_shape():
    alpha, style_panels = make_test_data()
    svc = AttributionService()
    result = svc.decompose(alpha, style_panels)
    assert result.residual.shape == alpha.shape
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_attribution.py -v
```

- [ ] **Step 3: 实现 AttributionService**

```python
"""因子风格暴露度归因服务。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class AttributionResult:
    exposures: dict[str, pd.Series]  # {"Size": β_series, ...}
    r_squared: pd.Series              # 每日 R²
    residual: pd.DataFrame            # 风格中性化残差


class AttributionService:
    """每日截面回归 alpha_factor ~ Σ β_i × style_i + ε。"""

    def decompose(
        self,
        factor_panel: pd.DataFrame,
        style_panels: dict[str, pd.DataFrame],
    ) -> AttributionResult:
        common_dates = factor_panel.index
        common_symbols = factor_panel.columns

        exposures: dict[str, list[float]] = {name: [] for name in style_panels}
        r2_list: list[float] = []
        residual = factor_panel.copy()

        for d in common_dates:
            y = factor_panel.loc[d]

            X_cols = []
            for name, panel in style_panels.items():
                aligned = panel.reindex(index=common_dates, columns=common_symbols)
                if d in aligned.index:
                    X_cols.append(aligned.loc[d].values)

            if not X_cols:
                for name in style_panels:
                    exposures[name].append(np.nan)
                r2_list.append(np.nan)
                residual.loc[d] = np.nan
                continue

            X = np.column_stack(X_cols)
            valid = ~y.isna() & ~np.isnan(X).any(axis=1)

            if valid.sum() < 10:
                for name in style_panels:
                    exposures[name].append(np.nan)
                r2_list.append(np.nan)
                residual.loc[d] = np.nan
                continue

            y_valid = y[valid].values.astype(float)
            X_valid = X[valid]

            try:
                beta, residuals_ss, rank, _ = np.linalg.lstsq(
                    X_valid, y_valid, rcond=None
                )
            except np.linalg.LinAlgError:
                for name in style_panels:
                    exposures[name].append(np.nan)
                r2_list.append(np.nan)
                residual.loc[d] = np.nan
                continue

            for i, name in enumerate(style_panels):
                exposures[name].append(float(beta[i]) if i < len(beta) else np.nan)

            y_hat = X_valid @ beta
            ss_res = np.sum((y_valid - y_hat) ** 2)
            ss_tot = np.sum((y_valid - y_valid.mean()) ** 2)
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
            r2_list.append(r2)

            res = np.full(len(y), np.nan)
            res[valid.values] = y_valid - y_hat
            residual.loc[d] = res

        return AttributionResult(
            exposures={name: pd.Series(vals, index=common_dates, name=name)
                       for name, vals in exposures.items()},
            r_squared=pd.Series(r2_list, index=common_dates, name="r2"),
            residual=residual,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Expected: 3 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/attribution.py backend/tests/test_attribution.py
git commit -m "feat(service): add AttributionService for style factor exposure decomposition"
```

---

### Task 10: 风格归因集成到 Eval Pipeline

**Files:**
- Modify: `backend/services/eval_service.py`

- [ ] **Step 1: 在 eval 中添加归因计算**

在 `run_eval` 函数中，中性化之后、写 MySQL 之前，插入归因计算：

```python
# --- style attribution ---
attribution = None
try:
    from backend.runtime.factor_registry import FactorRegistry
    from backend.services.attribution import AttributionService
    reg = FactorRegistry()
    reg.scan_and_register()
    style_ids = ["size_mv", "value_ep", "momentum_12m1m", "volatility_60d", "liquidity_20d"]
    style_panels = {}
    for sid in style_ids:
        try:
            sf = reg.get(sid)
            sctx = FactorContext(
                data=DataService(),
                symbols=symbols,
                start_date=start,
                end_date=end,
                warmup_days=sf.required_warmup(sf.default_params),
            )
            style_panels[sid] = sf.compute(sctx, sf.default_params)
        except Exception:
            log.debug("Style factor %s compute failed, skipping", sid)

    if len(style_panels) >= 3:
        # 转换为 display_name 为 key
        renamed = {}
        for sid, panel in style_panels.items():
            try:
                display = reg.get(sid).display_name
            except Exception:
                display = sid
            renamed[display] = panel
        attr_svc = AttributionService()
        attribution = attr_svc.decompose(F, renamed)
except Exception as e:
    log.warning("Attribution failed for run_id=%s: %s", run_id, e)
```

将 `attribution` 结果存入 `payload_json` 或单独字段：

```python
if attribution is not None:
    payload["attribution"] = {
        "exposures": {
            name: series.dropna().tolist()
            for name, series in attribution.exposures.items()
        },
        "r_squared": attribution.r_squared.dropna().tolist(),
        "dates": [d.strftime("%Y-%m-%d") for d in attribution.r_squared.index],
    }
```

- [ ] **Step 2: 提交**

```bash
git add backend/services/eval_service.py
git commit -m "feat(eval): integrate style factor attribution into eval pipeline"
```

---

### Task 11: 前端 — API 类型 + EvalCreate 开关

**Files:**
- Modify: `frontend/src/api/evals.ts`
- Modify: `frontend/src/pages/evals/EvalCreate.vue`

- [ ] **Step 1: 扩展 evals.ts 类型**

在 `frontend/src/api/evals.ts` 中，`EvalRun` 接口追加字段：

```typescript
export interface EvalRun {
  // ... existing fields ...
  neut_ic_mean?: number
  neut_ic_ir?: number
  neut_rank_ic_mean?: number
  neut_rank_ic_ir?: number
  neut_long_short_annret?: number
  neut_payload_json?: any
  // attribution (inside payload)
}
```

- [ ] **Step 2: EvalCreate.vue 新增 checkbox**

在 `EvalCreate.vue` 的 `<script setup>` 中添加：

```typescript
const neutralize = ref(true)
```

在 body 构造中加入：

```typescript
const body: Record<string, any> = {
  // ... existing fields ...
  neutralize: neutralize.value,
}
```

在 `<template>` 中表单末尾（提交按钮之前）添加：

```html
<n-form-item label="中性化" style="margin-top: 8px">
  <n-checkbox v-model:checked="neutralize">
    行业+市值中性化（默认开启，评估时同时输出原始和中性化后指标）
  </n-checkbox>
</n-form-item>
```

- [ ] **Step 3: 验证 TypeScript**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | grep -c "error TS"
```

Expected: 仅预存 3 个错误，无新增

- [ ] **Step 4: 提交**

```bash
git add frontend/src/api/evals.ts frontend/src/pages/evals/EvalCreate.vue
git commit -m "feat(ui): add neutralize checkbox to eval create form"
```

---

### Task 12: 前端 — EvalDetail 中性化对比 + 风格暴露

**Files:**
- Modify: `frontend/src/pages/evals/EvalDetail.vue`

- [ ] **Step 1: 新增中性化对比卡片**

在现有 structured metrics 表格（约 line 622-650）之后，新增：

```html
<!-- 中性化对比 -->
<n-card v-if="metrics.neut_ic_mean != null" title="📊 中性化效果对比" style="margin-top: 16px" size="small">
  <n-descriptions bordered :column="3" label-placement="left" size="small">
    <n-descriptions-item label="IC Mean">
      {{ fmtPct(metrics.ic_mean) }} → <span style="color: #5dade2">{{ fmtPct(metrics.neut_ic_mean) }}</span>
    </n-descriptions-item>
    <n-descriptions-item label="IC IR">
      {{ fmtNum(metrics.ic_ir) }} → <span style="color: #5dade2">{{ fmtNum(metrics.neut_ic_ir) }}</span>
    </n-descriptions-item>
    <n-descriptions-item label="Rank IC Mean">
      {{ fmtPct(metrics.rank_ic_mean) }} → <span style="color: #5dade2">{{ fmtPct(metrics.neut_rank_ic_mean) }}</span>
    </n-descriptions-item>
    <n-descriptions-item label="Rank IC IR">
      {{ fmtNum(metrics.rank_ic_ir) }} → <span style="color: #5dade2">{{ fmtNum(metrics.neut_rank_ic_ir) }}</span>
    </n-descriptions-item>
    <n-descriptions-item label="Long-Short 年化收益">
      {{ fmtPct(metrics.long_short_annret) }} → <span style="color: #5dade2">{{ fmtPct(metrics.neut_long_short_annret) }}</span>
    </n-descriptions-item>
  </n-descriptions>
  <p style="color: #848E9C; font-size: 12px; margin-top: 8px">
    IC 小幅下降但更纯——中性化剥离行业/市值暴露后的真实 alpha。若中性化后 IC 接近 0，说明因子超额主要来自行业/市值暴露。
  </p>
</n-card>
```

- [ ] **Step 2: 新增风格暴露图表**

在中性化卡片之后，当 `evalRun.payload?.attribution` 存在时：

```html
<!-- 风格暴露度 -->
<n-card v-if="evalRun.payload?.attribution" title="🎯 风格暴露度" style="margin-top: 16px" size="small">
  <StyleExposureChart :attribution="evalRun.payload.attribution" />
</n-card>
```

需要创建 `frontend/src/components/charts/StyleExposureChart.vue`：

- 柱状图：5 个风格因子的平均暴露度（`exposures` 各序列均值）
- 折线图：各风格暴露度时序

使用 ECharts（与 CandlestickChart 一致的 vue-echarts 模式）：

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent])

const props = defineProps<{
  attribution: {
    exposures: Record<string, number[]>
    r_squared: number[]
    dates: string[]
  }
}>()

const barOption = computed(() => ({
  animation: false,
  tooltip: { trigger: 'axis' as const },
  xAxis: {
    type: 'category' as const,
    data: Object.keys(props.attribution.exposures),
  },
  yAxis: {
    type: 'value' as const,
    axisLabel: { formatter: (v: number) => v.toFixed(3) },
  },
  series: [{
    type: 'bar' as const,
    data: Object.values(props.attribution.exposures).map(
      arr => arr.reduce((a, b) => a + b, 0) / arr.length
    ),
    itemStyle: { color: '#5dade2' },
  }],
}))

const lineOption = computed(() => ({
  animation: false,
  tooltip: { trigger: 'axis' as const },
  legend: { data: Object.keys(props.attribution.exposures), bottom: 0 },
  xAxis: { type: 'category' as const, data: props.attribution.dates },
  yAxis: {
    type: 'value' as const,
    axisLabel: { formatter: (v: number) => v.toFixed(3) },
  },
  series: Object.entries(props.attribution.exposures).map(([name, vals]) => ({
    name, type: 'line' as const, data: vals, symbol: 'none' as const,
  })),
}))
</script>

<template>
  <div style="display: flex; gap: 16px">
    <v-chart :option="barOption" autoresize style="flex: 1; height: 200px" />
    <v-chart :option="lineOption" autoresize style="flex: 2; height: 200px" />
  </div>
</template>
```

- [ ] **Step 3: 验证 TypeScript**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | grep -c "error TS"
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/evals/EvalDetail.vue frontend/src/components/charts/StyleExposureChart.vue
git commit -m "feat(ui): add neutralization comparison and style exposure charts to eval detail"
```

---

### Task 13: 集成验证

**Files:** None (manual verification)

- [ ] **Step 1: 运行后端中性化测试**

```bash
cd backend && python -m pytest tests/test_neutralization.py tests/test_attribution.py -v
```

Expected: 9 PASS

- [ ] **Step 2: 前端类型检查**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1
```

Expected: 仅预存 3 个错误

- [ ] **Step 3: 手动验证清单**

1. 创建新评估 → 确认 "行业+市值中性化" checkbox 默认选中
2. 运行评估 → 评估详情页显示「中性化效果对比」卡片
3. 确认 IC Mean / IC IR / Rank IC / Long-Short 对比值合理
4. 确认「风格暴露度」柱状图 + 折线图正常渲染
5. 取消选中中性化重新评估 → 确认不出现中性化区块

- [ ] **Step 4: 提交（如有修复）**

```bash
git add -A && git commit -m "chore: integration verification fixes"
```

---

## Self-Review

**1. Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| fr_daily_market_cap + fr_daily_pb DDL | Task 1 |
| fr_industry_history DDL | Task 1 |
| akshare market_data adapter | Task 2 |
| akshare industry adapter | Task 3 |
| DataService.load_market_cap/pb/industry | Task 4 |
| Historical backfill script | Task 5 |
| NeutralizationService (3 methods) | Task 6 |
| Eval pipeline neutralize param | Task 7 |
| EvalCreate schema neutralize field | Task 7 |
| fr_factor_eval_metrics neut_* columns | Task 7 |
| 5 style factors (riskmodel/) | Task 8 |
| AttributionService.decompose() | Task 9 |
| Attribution integrated into eval | Task 10 |
| EvalCreate checkbox | Task 11 |
| EvalDetail neutralization comparison | Task 12 |
| EvalDetail style exposure chart | Task 12 |
| Integration verification | Task 13 |

**2. Placeholder scan:** No TBD/TODO. All code blocks are complete.

**3. Type consistency:** NeutralizationService.neutralize() returns pd.DataFrame in Task 6, consumed by eval_service in Task 7. AttributionResult.exposures is dict[str, pd.Series] in Task 9, serialized to JSON in Task 10, rendered by StyleExposureChart in Task 12. All interfaces aligned.
