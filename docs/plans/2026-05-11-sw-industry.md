# 申万行业分类接入 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `fr_industry_current` 表新增 `sw_l1`/`sw_l2`/`sw_l3` 字段，通过 Akshare 申万行业接口填充全市场股票的申万三级行业归属。

**Architecture:** 新建 `backend/adapters/akshare_industry.py` 适配器，从 Akshare 拉取申万行业层级关系和成分股映射，upsert 到现有 `fr_industry_current` 表。通过 admin API 端点触发后台同步。

**Tech Stack:** Python, Akshare (`sw_index_first_info`, `sw_index_third_info`, `index_component_sw`), PyMySQL, FastAPI BackgroundTasks

---

### Task 1: 迁移脚本 — 新增 sw_l1/sw_l2/sw_l3 字段

**Files:**
- Create: `backend/scripts/migrations/016_add_sw_industry_fields.sql`

**Step 1: 编写迁移 SQL**

```sql
-- 016: 为 fr_industry_current 新增申万行业三级字段。
-- 数据源：Akshare 申万行业接口（sw_index_*_info + index_component_sw）。
ALTER TABLE `fr_industry_current`
  ADD COLUMN `sw_l1` varchar(32) NOT NULL DEFAULT '' AFTER `industry_classification`,
  ADD COLUMN `sw_l2` varchar(32) NOT NULL DEFAULT '' AFTER `sw_l1`,
  ADD COLUMN `sw_l3` varchar(64) NOT NULL DEFAULT '' AFTER `sw_l2`,
  ADD KEY `idx_sw_l1` (`sw_l1`);
```

**Step 2: Commit**

```bash
git add backend/scripts/migrations/016_add_sw_industry_fields.sql
git commit -m "feat(schema): 新增 sw_l1/sw_l2/sw_l3 申万行业字段"
```

---

### Task 2: 申万行业适配器 — 层级映射 + 成分股拉取

**Files:**
- Create: `backend/adapters/akshare_industry.py`

**Step 1: 编写适配器**

```python
"""从 Akshare 同步申万行业归属到 fr_industry_current.sw_l1/sw_l2/sw_l3。

策略：
1. sw_index_third_info() + sw_index_second_info() 建立 L3→L2→L1 层级映射
2. 遍历 31 个 L1 行业 index_component_sw(code) 获取 stock→L1 映射（兜底）
3. 遍历 336 个 L3 行业 index_component_sw(code) 获取 stock→L3 映射
4. 通过层级表反推 L2/L1

每次 API 调用间隔 0.5s 防限流。总耗时 ~8-10 分钟，适合后台任务。
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

CALL_INTERVAL = 0.5  # 秒，防限流


def _safe_normalize(raw_code: str) -> str | None:
    """尝试将申万接口返回的证券代码规范化为 QMT 格式，失败返回 None。"""
    s = str(raw_code).strip()
    if len(s) == 6:
        try:
            return normalize_symbol(s)
        except ValueError:
            return None
    return None


def fetch_sw_hierarchy() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """构建申万三级行业层级映射。

    Returns:
        (l3_code_to_name, l3_name_to_l2, l2_name_to_l1):
        - l3_code_to_name: {"850111": "集成电路设计", ...}
        - l3_name_to_l2: {"集成电路设计": "半导体", ...}
        - l2_name_to_l1: {"半导体": "电子", ...}
    """
    import akshare as ak

    df_l2 = ak.sw_index_second_info()
    df_l3 = ak.sw_index_third_info()

    # L2_name → L1_name
    l2_name_to_l1: dict[str, str] = {}
    for _, row in df_l2.iterrows():
        l2_name = str(row["行业名称"]).strip()
        l1_name = str(row["上级行业"]).strip()
        if l2_name and l1_name:
            l2_name_to_l1[l2_name] = l1_name

    # L3_code → L3_name, L3_name → L2_name
    l3_code_to_name: dict[str, str] = {}
    l3_name_to_l2: dict[str, str] = {}
    for _, row in df_l3.iterrows():
        code = str(row["行业代码"]).replace(".SI", "").strip()
        l3_name = str(row["行业名称"]).strip()
        l2_name = str(row["上级行业"]).strip()
        if code and l3_name:
            l3_code_to_name[code] = l3_name
        if l3_name and l2_name:
            l3_name_to_l2[l3_name] = l2_name

    log.info(
        "sw hierarchy: %d L3 industries, %d L2, %d L1 mappings",
        len(l3_code_to_name), len(l3_name_to_l2), len(l2_name_to_l1),
    )
    return l3_code_to_name, l3_name_to_l2, l2_name_to_l1


def fetch_sw_industry_all() -> dict[str, dict[str, str]]:
    """遍历申万行业获取全市场 stock→{sw_l1, sw_l2, sw_l3} 映射。

    Returns:
        {symbol: {"sw_l1": "电子", "sw_l2": "半导体", "sw_l3": "集成电路设计"}, ...}
    """
    import akshare as ak

    l3_code_to_name, l3_name_to_l2, l2_name_to_l1 = fetch_sw_hierarchy()

    result: dict[str, dict[str, str]] = {}

    # Phase 1: 遍历 L1 行业，建立兜底映射（stock → sw_l1）
    df_l1 = ak.sw_index_first_info()
    l1_codes: list[tuple[str, str]] = []
    for _, row in df_l1.iterrows():
        code = str(row["行业代码"]).replace(".SI", "").strip()
        name = str(row["行业名称"]).strip()
        if code and name:
            l1_codes.append((code, name))

    log.info("fetching L1 constituents for %d industries...", len(l1_codes))
    for code, l1_name in l1_codes:
        try:
            df_cons = ak.index_component_sw(symbol=code)
            time.sleep(CALL_INTERVAL)
        except Exception as e:
            log.warning("index_component_sw(%s) failed: %s", code, e)
            time.sleep(CALL_INTERVAL)
            continue

        if df_cons is None or df_cons.empty:
            continue

        for _, row in df_cons.iterrows():
            raw = str(row.get("证券代码", "")).strip()
            symbol = _safe_normalize(raw)
            if symbol and symbol not in result:
                result[symbol] = {"sw_l1": l1_name, "sw_l2": "", "sw_l3": ""}

    log.info("L1 pass done: %d stocks mapped", len(result))

    # Phase 2: 遍历 L3 行业，覆盖写入完整三级
    l3_codes = list(l3_code_to_name.items())
    log.info("fetching L3 constituents for %d industries...", len(l3_codes))
    for i, (code, l3_name) in enumerate(l3_codes):
        try:
            df_cons = ak.index_component_sw(symbol=code)
            time.sleep(CALL_INTERVAL)
        except Exception as e:
            log.warning("index_component_sw(%s/%s) failed: %s", code, l3_name, e)
            time.sleep(CALL_INTERVAL)
            continue

        if df_cons is None or df_cons.empty:
            continue

        l2_name = l3_name_to_l2.get(l3_name, "")
        l1_name = l2_name_to_l1.get(l2_name, "")

        for _, row in df_cons.iterrows():
            raw = str(row.get("证券代码", "")).strip()
            symbol = _safe_normalize(raw)
            if symbol:
                result[symbol] = {"sw_l1": l1_name, "sw_l2": l2_name, "sw_l3": l3_name}

        if (i + 1) % 50 == 0:
            log.info("L3 progress: %d/%d", i + 1, len(l3_codes))

    log.info("L3 pass done: %d stocks total", len(result))
    return result


def upsert_sw_industry(mapping: dict[str, dict[str, str]]) -> dict[str, int]:
    """将申万行业映射写入 fr_industry_current。

    对已有 symbol: UPDATE sw_l1/sw_l2/sw_l3。
    对不存在的 symbol: INSERT（industry_l1 留空，仅填申万字段）。
    """
    update_sql = (
        "UPDATE fr_industry_current "
        "SET sw_l1=%s, sw_l2=%s, sw_l3=%s "
        "WHERE symbol=%s"
    )
    insert_sql = (
        "INSERT IGNORE INTO fr_industry_current "
        "(symbol, sw_l1, sw_l2, sw_l3, industry_l1, industry_classification, "
        " snapshot_date, data_source) "
        "VALUES (%s, %s, %s, %s, '', '', CURDATE(), 'akshare')"
    )

    updated = 0
    inserted = 0

    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for symbol, info in mapping.items():
                cur.execute(update_sql, (info["sw_l1"], info["sw_l2"], info["sw_l3"], symbol))
                if cur.rowcount == 0:
                    cur.execute(insert_sql, (symbol, info["sw_l1"], info["sw_l2"], info["sw_l3"]))
                    inserted += 1
                else:
                    updated += 1
        conn.commit()

    log.info("sw industry upsert: updated=%d, inserted=%d", updated, inserted)
    return {"updated": updated, "inserted": inserted, "total": updated + inserted}


def sync_sw_industry() -> dict[str, int]:
    """入口：拉取全市场申万行业归属并 upsert。"""
    mapping = fetch_sw_industry_all()
    return upsert_sw_industry(mapping)
```

**Step 2: Commit**

```bash
git add backend/adapters/akshare_industry.py
git commit -m "feat(adapter): 申万行业适配器（层级映射 + 成分股遍历）"
```

---

### Task 3: Admin API 端点

**Files:**
- Modify: `backend/api/routers/admin.py`

**Step 1: 添加同步端点**

在 `_run_sync_baostock_industry_safely` 函数之后添加：

```python
def _run_sync_sw_industry_safely() -> None:
    """BackgroundTasks 回调：Akshare → fr_industry_current.sw_l1/l2/l3。"""
    try:
        from backend.adapters.akshare_industry import sync_sw_industry

        result = sync_sw_industry()
        log.info("sync_sw_industry ok: %s", result)
    except Exception:  # noqa: BLE001
        log.exception("sync_sw_industry failed")
```

在 `/industry:sync_baostock` 端点之后添加新端点：

```python
@router.post("/industry:sync_sw")
def trigger_sync_sw_industry(bt: BackgroundTasks) -> dict:
    """从 Akshare 同步申万行业归属到 fr_industry_current.sw_l1/l2/l3。

    耗时约 8-10 分钟（遍历 367 个行业指数成分）。
    """
    bt.add_task(_run_sync_sw_industry_safely)
    return ok({"message": "sw industry sync submitted (~8-10 min); see server logs"})
```

**Step 2: Commit**

```bash
git add backend/api/routers/admin.py
git commit -m "feat(api): 新增 POST /api/admin/industry:sync_sw 端点"
```

---

### Task 4: 单元测试 — 适配器逻辑

**Files:**
- Create: `backend/tests/test_akshare_industry.py`

**Step 1: 编写测试（mock Akshare 调用）**

```python
"""申万行业适配器单测——通过 mock 避免真实网络调用。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _fake_l1_info():
    return pd.DataFrame({
        "行业代码": ["801010.SI", "801080.SI"],
        "行业名称": ["农林牧渔", "电子"],
        "成份个数": [50, 200],
        "静态市盈率": [30.0, 45.0],
        "TTM(滚动)市盈率": [28.0, 42.0],
        "市净率": [3.0, 5.0],
        "静态股息率": [1.5, 0.8],
    })


def _fake_l2_info():
    return pd.DataFrame({
        "行业代码": ["801011.SI", "801081.SI"],
        "行业名称": ["种植业", "半导体"],
        "上级行业": ["农林牧渔", "电子"],
        "成份个数": [20, 80],
        "静态市盈率": [25.0, 50.0],
        "TTM(滚动)市盈率": [23.0, 48.0],
        "市净率": [2.5, 6.0],
        "静态股息率": [2.0, 0.5],
    })


def _fake_l3_info():
    return pd.DataFrame({
        "行业代码": ["850111.SI", "850811.SI"],
        "行业名称": ["粮食种植", "集成电路设计"],
        "上级行业": ["种植业", "半导体"],
        "成份个数": [10, 40],
        "静态市盈率": [20.0, 55.0],
        "TTM(滚动)市盈率": [18.0, 52.0],
        "市净率": [2.0, 7.0],
        "静态股息率": [2.5, 0.3],
    })


def _fake_component(symbol: str):
    data = {
        "801010": pd.DataFrame({"证券代码": ["600598", "002714"], "证券名称": ["A", "B"], "最新权重": [1.0, 1.0], "计入日期": ["2024-01-01", "2024-01-01"]}),
        "801080": pd.DataFrame({"证券代码": ["002049", "603501"], "证券名称": ["C", "D"], "最新权重": [1.0, 1.0], "计入日期": ["2024-01-01", "2024-01-01"]}),
        "850111": pd.DataFrame({"证券代码": ["600598"], "证券名称": ["A"], "最新权重": [1.0], "计入日期": ["2024-01-01"]}),
        "850811": pd.DataFrame({"证券代码": ["002049", "603501"], "证券名称": ["C", "D"], "最新权重": [1.0, 1.0], "计入日期": ["2024-01-01", "2024-01-01"]}),
    }
    return data.get(symbol, pd.DataFrame())


@patch("backend.adapters.akshare_industry.CALL_INTERVAL", 0)
@patch("backend.adapters.akshare_industry.ak")
def test_fetch_sw_hierarchy(mock_ak):
    mock_ak.sw_index_second_info.return_value = _fake_l2_info()
    mock_ak.sw_index_third_info.return_value = _fake_l3_info()

    from backend.adapters.akshare_industry import fetch_sw_hierarchy
    l3_code_to_name, l3_name_to_l2, l2_name_to_l1 = fetch_sw_hierarchy()

    assert l3_code_to_name["850111"] == "粮食种植"
    assert l3_code_to_name["850811"] == "集成电路设计"
    assert l3_name_to_l2["集成电路设计"] == "半导体"
    assert l2_name_to_l1["半导体"] == "电子"


@patch("backend.adapters.akshare_industry.CALL_INTERVAL", 0)
@patch("backend.adapters.akshare_industry.ak")
def test_fetch_sw_industry_all(mock_ak):
    mock_ak.sw_index_first_info.return_value = _fake_l1_info()
    mock_ak.sw_index_second_info.return_value = _fake_l2_info()
    mock_ak.sw_index_third_info.return_value = _fake_l3_info()
    mock_ak.index_component_sw.side_effect = _fake_component

    from backend.adapters.akshare_industry import fetch_sw_industry_all
    result = fetch_sw_industry_all()

    # 002049 应属于 L3=集成电路设计, L2=半导体, L1=电子
    assert result["002049.SZ"]["sw_l1"] == "电子"
    assert result["002049.SZ"]["sw_l2"] == "半导体"
    assert result["002049.SZ"]["sw_l3"] == "集成电路设计"

    # 600598 通过 L3 覆盖：L3=粮食种植, L2=种植业, L1=农林牧渔
    assert result["600598.SH"]["sw_l1"] == "农林牧渔"
    assert result["600598.SH"]["sw_l2"] == "种植业"
    assert result["600598.SH"]["sw_l3"] == "粮食种植"
```

**Step 2: 运行测试**

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/zealous-elbakyan-bdb9f4
python -m pytest backend/tests/test_akshare_industry.py -v
```

**Step 3: Commit**

```bash
git add backend/tests/test_akshare_industry.py
git commit -m "test: 申万行业适配器单元测试"
```

---

### Task 5: 执行迁移 + 端到端验证

**Step 1: 在本地 MySQL 执行迁移**

```bash
mysql -u root factor_research < backend/scripts/migrations/016_add_sw_industry_fields.sql
```

**Step 2: 启动后端，调用同步端点**

```bash
curl -X POST http://localhost:8000/api/admin/industry:sync_sw
```

**Step 3: 等待完成后验证数据**

```sql
SELECT symbol, sw_l1, sw_l2, sw_l3 FROM fr_industry_current WHERE sw_l1 != '' LIMIT 10;
SELECT sw_l1, COUNT(*) cnt FROM fr_industry_current WHERE sw_l1 != '' GROUP BY sw_l1 ORDER BY cnt DESC;
```

预期：~5000 行有 sw_l1 值，31 个不同的一级行业。

**Step 4: Commit（如有修复）**
