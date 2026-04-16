# 因子研究平台 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 从零构建因子研究平台 factor_research，支持因子定义、评估（IC/分组/换手）、回测（VectorBT），后端 FastAPI，前端 Vue3，对接现有 ClickHouse/MySQL

**Architecture:** 见 `docs/plans/2026-04-16-factor-research-design.md`。核心：FastAPI + ProcessPool 异步任务；因子 Python 类注册 + watchdog 热加载；因子值永久入库 ClickHouse `factor_value_1d`；前端 Vue3 + Naive UI + ECharts 轮询进度。

**Tech Stack:** Python 3.10 / FastAPI / VectorBT / pandas / numpy / clickhouse-driver / pymysql / watchdog / Vue3 / TypeScript / Vite / Naive UI / ECharts / @tanstack/vue-query / Pinia

---

## Task 1：后端脚手架 + 配置体系

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/.env.example`
- Create: `backend/.gitignore`
- Create: `backend/config.py`
- Create: `backend/api/__init__.py`
- Create: `backend/api/main.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_config.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/pyproject.toml`

**Step 1: 写 requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
pydantic==2.9.2
pydantic-settings==2.6.1
python-dotenv==1.0.1
pymysql==1.1.1
sqlalchemy==2.0.35
clickhouse-driver==0.2.9
pandas==2.2.3
numpy==1.26.4
numba==0.60.0
pyarrow==17.0.0
vectorbt==0.26.2
watchdog==5.0.3
httpx==0.27.2
pytest==8.3.3
pytest-asyncio==0.24.0
```

**Step 2: 写 .env.example**

```
CLICKHOUSE_HOST=172.30.26.12
CLICKHOUSE_PORT=9000
CLICKHOUSE_DATABASE=quant_data
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
MYSQL_HOST=172.30.26.12
MYSQL_PORT=3306
MYSQL_USER=myuser
MYSQL_PASSWORD=mypassword
MYSQL_DATABASE=quant_data
QFQ_FACTOR_PATH=./data/merged_adjust_factors.parquet
FR_TASK_WORKERS=2
FR_LOG_LEVEL=INFO
FR_HOT_RELOAD=true
FR_OWNER_KEY=default
FR_FACTORS_DIR=./backend/factors
```

**Step 3: 写 .gitignore**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
data/*.parquet
backend/cache/
*.egg-info/
```

**Step 4: 写失败测试 tests/test_config.py**

```python
from backend.config import Settings

def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "1.2.3.4")
    monkeypatch.setenv("CLICKHOUSE_HOST", "5.6.7.8")
    s = Settings()
    assert s.mysql_host == "1.2.3.4"
    assert s.clickhouse_host == "5.6.7.8"
    assert s.task_workers >= 1
```

Run: `cd backend && pytest tests/test_config.py -v` → Expected FAIL (module missing)

**Step 5: 实现 backend/config.py**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    clickhouse_host: str = "127.0.0.1"
    clickhouse_port: int = 9000
    clickhouse_database: str = "quant_data"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "quant_data"

    qfq_factor_path: str = "./data/merged_adjust_factors.parquet"
    task_workers: int = 2
    log_level: str = "INFO"
    hot_reload: bool = True
    owner_key: str = "default"
    factors_dir: str = "./backend/factors"

    class Config:
        env_prefix = "FR_"

settings = Settings()
```

注意 `FR_` 前缀只作用于非数据库字段；数据库字段名直接对应环境变量。修正方案：把所有环境变量都改为 `FR_` 前缀，或者使用字段别名。推荐字段别名方式：

```python
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    clickhouse_host: str = Field("127.0.0.1", alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(9000, alias="CLICKHOUSE_PORT")
    clickhouse_database: str = Field("quant_data", alias="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field("default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field("", alias="CLICKHOUSE_PASSWORD")
    mysql_host: str = Field("127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(3306, alias="MYSQL_PORT")
    mysql_user: str = Field("root", alias="MYSQL_USER")
    mysql_password: str = Field("", alias="MYSQL_PASSWORD")
    mysql_database: str = Field("quant_data", alias="MYSQL_DATABASE")
    qfq_factor_path: str = Field("./data/merged_adjust_factors.parquet", alias="QFQ_FACTOR_PATH")
    task_workers: int = Field(2, alias="FR_TASK_WORKERS")
    log_level: str = Field("INFO", alias="FR_LOG_LEVEL")
    hot_reload: bool = Field(True, alias="FR_HOT_RELOAD")
    owner_key: str = Field("default", alias="FR_OWNER_KEY")
    factors_dir: str = Field("./backend/factors", alias="FR_FACTORS_DIR")

settings = Settings()
```

Run: `cd backend && pytest tests/test_config.py -v` → Expected PASS

**Step 6: 实现 api/main.py（最小 FastAPI 应用）**

```python
from fastapi import FastAPI
from backend.config import settings

app = FastAPI(title="Factor Research Platform", version="0.1.0")

@app.get("/api/health")
def health():
    return {"code": 0, "data": {"status": "ok"}}
```

**Step 7: 运行冒烟测试**

Run: `cd backend && uvicorn api.main:app --port 8000 &` then `curl localhost:8000/api/health`
Expected: `{"code":0,"data":{"status":"ok"}}`

**Step 8: Commit**

```bash
git add backend/
git commit -m "feat(backend): 初始化 FastAPI 脚手架与配置"
```

---

## Task 2：数据库 Schema 初始化脚本

**Files:**
- Create: `backend/scripts/init_mysql.sql`
- Create: `backend/scripts/init_clickhouse.sql`
- Create: `backend/scripts/run_init.py`
- Create: `backend/tests/test_schema_sql_valid.py`

**Step 1: 写 init_mysql.sql**

完整包含 §3.1 里的三张新表（`factor_meta`、`factor_eval_runs`、`factor_eval_metrics`）+ `ALTER TABLE backtest_runs`。对已有表（stock_pool / stock_pool_symbol / stock_bar_import_job / backtest_runs / backtest_metrics / backtest_artifacts）用 `CREATE TABLE IF NOT EXISTS`。

```sql
-- stock_basic（新增，记录 symbol_id ↔ symbol 映射）
CREATE TABLE IF NOT EXISTS `stock_basic` (
  `symbol_id`  int unsigned NOT NULL AUTO_INCREMENT,
  `symbol`     varchar(16) NOT NULL,
  `name`       varchar(64) DEFAULT NULL,
  `exchange`   varchar(8)  DEFAULT NULL,
  `list_date`  date        DEFAULT NULL,
  `delist_date` date       DEFAULT NULL,
  `is_active`  tinyint(1) NOT NULL DEFAULT 1,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol_id`),
  UNIQUE KEY `uk_symbol` (`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- qfq_factor（复用 timing_driven 表结构）
CREATE TABLE IF NOT EXISTS `qfq_factor` (
  `symbol_id`    int unsigned NOT NULL,
  `trade_date`   date NOT NULL,
  `factor`       double NOT NULL,
  `source_mtime` bigint unsigned NOT NULL,
  PRIMARY KEY (`symbol_id`, `trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】factor_meta
CREATE TABLE IF NOT EXISTS `factor_meta` (...);   -- 完整 DDL 见设计文档 §3.1

-- 【新增】factor_eval_runs / factor_eval_metrics
CREATE TABLE IF NOT EXISTS `factor_eval_runs` (...);
CREATE TABLE IF NOT EXISTS `factor_eval_metrics` (...);

-- 既有 stock_pool / stock_pool_symbol / stock_bar_import_job / backtest_runs / backtest_metrics / backtest_artifacts 保持用户提供的 DDL

-- 扩 backtest_runs
ALTER TABLE `backtest_runs`
  ADD COLUMN IF NOT EXISTS `factor_id`      varchar(64) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `factor_version` int unsigned DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `pool_id`        bigint unsigned DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `params_hash`    char(40) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS `freq`           varchar(8) NOT NULL DEFAULT '1d';
```

**Step 2: 写 init_clickhouse.sql**

```sql
CREATE DATABASE IF NOT EXISTS quant_data;

CREATE TABLE IF NOT EXISTS quant_data.stock_bar_1d
(
    `symbol_id` UInt32,
    `trade_date` Date,
    `open` Float32, `high` Float32, `low` Float32, `close` Float32,
    `volume` UInt64, `amount_k` UInt32,
    `version` UInt64,
    `updated_at` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYear(trade_date)
ORDER BY (symbol_id, trade_date)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS quant_data.factor_value_1d
(
    `factor_id` LowCardinality(String),
    `factor_version` UInt32,
    `params_hash` FixedString(40),
    `symbol_id` UInt32,
    `trade_date` Date,
    `value` Float64,
    `version` UInt64,
    `updated_at` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (factor_id, toYear(trade_date))
ORDER BY (factor_id, factor_version, params_hash, symbol_id, trade_date);
```

**Step 3: 写 scripts/run_init.py**

```python
"""初始化 MySQL 和 ClickHouse schema。幂等（用 CREATE IF NOT EXISTS）。"""
from pathlib import Path
import pymysql
from clickhouse_driver import Client
from backend.config import settings

SCRIPTS_DIR = Path(__file__).parent

def _run_mysql():
    sql = (SCRIPTS_DIR / "init_mysql.sql").read_text(encoding="utf-8")
    conn = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password,
        database=settings.mysql_database, charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            for stmt in _split_sql(sql):
                if stmt.strip():
                    cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()

def _run_clickhouse():
    sql = (SCRIPTS_DIR / "init_clickhouse.sql").read_text(encoding="utf-8")
    ch = Client(
        host=settings.clickhouse_host, port=settings.clickhouse_port,
        user=settings.clickhouse_user, password=settings.clickhouse_password,
    )
    for stmt in _split_sql(sql):
        if stmt.strip():
            ch.execute(stmt)

def _split_sql(sql: str) -> list[str]:
    # 简易拆分：按 ';' 且不在引号中
    stmts = []
    buf = []
    in_quote = False
    for ch in sql:
        if ch == "'" and (not buf or buf[-1] != '\\'):
            in_quote = not in_quote
        if ch == ';' and not in_quote:
            stmts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        stmts.append(''.join(buf))
    return stmts

if __name__ == "__main__":
    print("Initializing MySQL ...")
    _run_mysql()
    print("Initializing ClickHouse ...")
    _run_clickhouse()
    print("Done.")
```

**Step 4: 写 tests/test_schema_sql_valid.py**（不连真库，只校验 SQL 可解析）

```python
from pathlib import Path
import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"

def test_mysql_sql_exists():
    sql = (SCRIPTS / "init_mysql.sql").read_text(encoding="utf-8")
    assert "factor_meta" in sql
    assert "factor_eval_runs" in sql
    assert "factor_eval_metrics" in sql
    assert "stock_basic" in sql
    assert "qfq_factor" in sql

def test_clickhouse_sql_exists():
    sql = (SCRIPTS / "init_clickhouse.sql").read_text(encoding="utf-8")
    assert "stock_bar_1d" in sql
    assert "factor_value_1d" in sql
    assert "ReplacingMergeTree" in sql
```

Run: `cd backend && pytest tests/test_schema_sql_valid.py -v` → PASS

**Step 5: 手动在测试数据库上执行初始化**

Run: `cd backend && python -m scripts.run_init`（需要 .env 配置可达的测试库）

**Step 6: Commit**

```bash
git add backend/scripts/ backend/tests/test_schema_sql_valid.py
git commit -m "feat(backend): 新增 MySQL/ClickHouse schema 初始化脚本"
```

---

## Task 3：数据读取层（ClickHouse + MySQL client + DataService）

**Files:**
- Create: `backend/storage/__init__.py`
- Create: `backend/storage/clickhouse_client.py`
- Create: `backend/storage/mysql_client.py`
- Create: `backend/storage/symbol_resolver.py`
- Create: `backend/storage/data_service.py`
- Create: `backend/tests/test_symbol_resolver.py`
- Create: `backend/tests/test_data_service.py`

**Step 1: clickhouse_client.py**

```python
from contextlib import contextmanager
from clickhouse_driver import Client
from backend.config import settings

def get_client() -> Client:
    return Client(
        host=settings.clickhouse_host, port=settings.clickhouse_port,
        user=settings.clickhouse_user, password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        settings={"use_numpy": True},
    )

@contextmanager
def ch_client():
    cli = get_client()
    try:
        yield cli
    finally:
        cli.disconnect()
```

**Step 2: mysql_client.py**

```python
from contextlib import contextmanager
import pymysql
from pymysql.cursors import DictCursor
from backend.config import settings

def get_connection():
    return pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4", cursorclass=DictCursor, autocommit=False,
    )

@contextmanager
def mysql_conn():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
```

**Step 3: 写测试 test_symbol_resolver.py**

```python
def test_resolve_symbol_roundtrip(stock_basic_seeded):
    from backend.storage.symbol_resolver import SymbolResolver
    r = SymbolResolver()
    sid = r.resolve_symbol_id("000001.SZ")
    assert sid > 0
    assert r.resolve_symbol(sid) == "000001.SZ"

def test_resolve_unknown_returns_none():
    from backend.storage.symbol_resolver import SymbolResolver
    r = SymbolResolver()
    assert r.resolve_symbol_id("999999.XX") is None
```

Run: → FAIL

**Step 4: 实现 symbol_resolver.py**

```python
from functools import lru_cache
from backend.storage.mysql_client import mysql_conn

class SymbolResolver:
    """symbol ↔ symbol_id 互转；进程内缓存。"""

    @lru_cache(maxsize=8192)
    def resolve_symbol_id(self, symbol: str) -> int | None:
        symbol = symbol.strip().upper()
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT symbol_id FROM stock_basic WHERE symbol=%s", (symbol,))
                row = cur.fetchone()
        return int(row["symbol_id"]) if row else None

    @lru_cache(maxsize=8192)
    def resolve_symbol(self, symbol_id: int) -> str | None:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT symbol FROM stock_basic WHERE symbol_id=%s", (symbol_id,))
                row = cur.fetchone()
        return row["symbol"] if row else None

    def resolve_many(self, symbols: list[str]) -> dict[str, int]:
        return {s: self.resolve_symbol_id(s) for s in symbols if self.resolve_symbol_id(s)}
```

Run → PASS（前置：先在 stock_basic 表插入测试数据；fixture 在 conftest.py）

**Step 5: data_service.py 核心**

```python
from __future__ import annotations
from datetime import date
from typing import Literal
import pandas as pd
import numpy as np
from backend.storage.clickhouse_client import ch_client
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver

_DAILY_FIELDS = ("open","high","low","close","volume","amount_k")

class DataService:
    def __init__(self):
        self.resolver = SymbolResolver()

    def load_bars(
        self, symbols: list[str], start: date, end: date,
        freq: Literal["1d","1m"] = "1d",
        adjust: Literal["none","qfq"] = "qfq",
        fields: tuple = _DAILY_FIELDS,
    ) -> dict[str, pd.DataFrame]:
        if freq != "1d":
            raise NotImplementedError(f"freq={freq} 尚未实现")

        sid_map = self.resolver.resolve_many(symbols)
        if not sid_map:
            return {}
        sid_list = list(sid_map.values())

        with ch_client() as ch:
            rows = ch.execute(
                """
                SELECT symbol_id, trade_date, open, high, low, close, volume, amount_k
                FROM quant_data.stock_bar_1d FINAL
                WHERE symbol_id IN %(sids)s
                  AND trade_date BETWEEN %(s)s AND %(e)s
                ORDER BY symbol_id, trade_date
                """,
                {"sids": sid_list, "s": start, "e": end},
            )
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=["symbol_id","trade_date","open","high","low","close","volume","amount_k"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        if adjust == "qfq":
            factor_map = self._load_qfq_factors(sid_list, start, end)
            df = self._apply_qfq(df, factor_map)

        inv = {v: k for k, v in sid_map.items()}
        out: dict[str, pd.DataFrame] = {}
        for sid, g in df.groupby("symbol_id"):
            sym = inv.get(int(sid))
            if not sym:
                continue
            frame = g.drop(columns=["symbol_id"]).set_index("trade_date").sort_index()
            out[sym] = frame[list(fields)]
        return out

    def load_panel(
        self, symbols: list[str], start: date, end: date,
        freq="1d", field="close", adjust="qfq",
    ) -> pd.DataFrame:
        bars = self.load_bars(symbols, start, end, freq=freq, adjust=adjust, fields=(field,))
        if not bars:
            return pd.DataFrame()
        panel = pd.concat({k: v[field] for k, v in bars.items()}, axis=1).sort_index()
        return panel

    def resolve_pool(self, pool_id: int) -> list[str]:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    SELECT b.symbol FROM stock_pool_symbol s
                    JOIN stock_basic b ON b.symbol_id = s.symbol_id
                    WHERE s.pool_id=%s ORDER BY s.sort_order
                    """, (pool_id,),
                )
                return [r["symbol"] for r in cur.fetchall()]

    def _load_qfq_factors(self, sid_list: list[int], start: date, end: date) -> dict[int, pd.Series]:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    SELECT symbol_id, trade_date, factor FROM qfq_factor
                    WHERE symbol_id IN %s AND trade_date BETWEEN %s AND %s
                    """,
                    (tuple(sid_list), start, end),
                )
                rows = cur.fetchall()
        out: dict[int, pd.Series] = {}
        for r in rows:
            sid = int(r["symbol_id"])
            out.setdefault(sid, []).append((pd.to_datetime(r["trade_date"]), float(r["factor"])))
        return {
            sid: pd.Series(dict(vals)).sort_index()
            for sid, vals in out.items()
        }

    def _apply_qfq(self, df: pd.DataFrame, factor_map: dict[int, pd.Series]) -> pd.DataFrame:
        df = df.copy()
        price_cols = ["open","high","low","close"]
        for sid in df["symbol_id"].unique():
            series = factor_map.get(int(sid))
            if series is None or series.empty:
                continue
            mask = df["symbol_id"] == sid
            idx = df.loc[mask, "trade_date"]
            factors = series.reindex(idx, method="ffill").values
            for col in price_cols:
                df.loc[mask, col] = df.loc[mask, col].values * factors
        return df
```

**Step 6: 写 test_data_service.py（集成测试，需本地测试 CH/MySQL）**

```python
import pytest, pandas as pd
from datetime import date
from backend.storage.data_service import DataService

@pytest.mark.integration
def test_load_panel_shape(data_seeded):
    svc = DataService()
    panel = svc.load_panel(["000001.SZ","000002.SZ"], date(2024,1,1), date(2024,2,1), field="close")
    assert isinstance(panel, pd.DataFrame)
    assert set(panel.columns).issuperset({"000001.SZ","000002.SZ"})
    assert panel.index.is_monotonic_increasing

@pytest.mark.integration
def test_load_panel_qfq_applied(data_seeded):
    svc = DataService()
    raw = svc.load_panel(["000001.SZ"], date(2024,1,1), date(2024,2,1), field="close", adjust="none")
    adj = svc.load_panel(["000001.SZ"], date(2024,1,1), date(2024,2,1), field="close", adjust="qfq")
    # 至少在某天复权价与原价不同（测试数据需要制造一次除权）
    assert not raw.equals(adj)
```

Run: `pytest -m integration` 在本地测试库上执行。

**Step 7: Commit**

```bash
git add backend/storage/ backend/tests/test_symbol_resolver.py backend/tests/test_data_service.py
git commit -m "feat(backend): 数据读取层 (ClickHouse/MySQL/SymbolResolver/DataService)"
```

---

## Task 4：复权因子导入 + 日频聚合脚本

**Files:**
- Create: `backend/scripts/import_qfq.py`（移植 timing_driven 的 `adjust_factor_importer.py`，改为 factor_research 自己的 mysql_client）
- Create: `backend/scripts/aggregate_bar_1d.py`
- Create: `backend/tests/test_aggregate_bar_1d.py`

**Step 1: import_qfq.py**

从 `/Users/jinziguan/Desktop/quantitativeTradeProject/timing_driven_backtest/backend/adjust_factor_importer.py` 拷贝代码，改 import 为：
```python
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver
```
并把 `MysqlBarStorage.upsert_symbol` / `upsert_qfq_factor_rows` 改写为直接 SQL：

```python
def upsert_symbol(conn, symbol: str) -> int:
    symbol = symbol.strip().upper()
    with conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO stock_basic (symbol) VALUES (%s)", (symbol,))
        cur.execute("SELECT symbol_id FROM stock_basic WHERE symbol=%s", (symbol,))
        return int(cur.fetchone()["symbol_id"])

def upsert_qfq_rows(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO qfq_factor (symbol_id, trade_date, factor, source_mtime)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE factor=VALUES(factor), source_mtime=VALUES(source_mtime)
            """, rows,
        )
        return cur.rowcount
```

入口保留 argparse 签名兼容，默认从 `settings.qfq_factor_path` 读取。

**Step 2: aggregate_bar_1d.py**

```python
"""从 stock_bar_1m 聚合 stock_bar_1d；支持全量和增量。"""
import argparse, time
from datetime import date, timedelta
from backend.storage.clickhouse_client import ch_client

AGG_SQL = """
INSERT INTO quant_data.stock_bar_1d
SELECT
    symbol_id,
    trade_date,
    argMin(open, minute_slot)   AS open,
    max(high)                   AS high,
    min(low)                    AS low,
    argMax(close, minute_slot)  AS close,
    sum(volume)                 AS volume,
    sum(amount_k)               AS amount_k,
    toUInt64(now64()) * 1000    AS version,
    now()                       AS updated_at
FROM quant_data.stock_bar_1m FINAL
WHERE trade_date >= %(s)s AND trade_date <= %(e)s
GROUP BY symbol_id, trade_date
"""

def aggregate(start: date, end: date) -> int:
    with ch_client() as ch:
        ch.execute(AGG_SQL, {"s": start, "e": end})
        ch.execute("OPTIMIZE TABLE quant_data.stock_bar_1d FINAL")
        rows = ch.execute(
            "SELECT count() FROM quant_data.stock_bar_1d WHERE trade_date BETWEEN %(s)s AND %(e)s",
            {"s": start, "e": end},
        )
    return int(rows[0][0])

def get_latest_aggregated_date() -> date | None:
    with ch_client() as ch:
        r = ch.execute("SELECT max(trade_date) FROM quant_data.stock_bar_1d")
    return r[0][0] if r and r[0][0] else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["full","incremental"], default="incremental", nargs="?")
    ap.add_argument("--start", type=str)
    ap.add_argument("--end", type=str)
    args = ap.parse_args()

    if args.mode == "full":
        start = date(2010, 1, 1) if not args.start else date.fromisoformat(args.start)
        end   = date.today()      if not args.end   else date.fromisoformat(args.end)
    else:
        last = get_latest_aggregated_date()
        start = (last + timedelta(days=1)) if last else date(2010, 1, 1)
        end   = date.today() if not args.end else date.fromisoformat(args.end)
        if start > end:
            print("Up-to-date"); return

    t0 = time.time()
    count = aggregate(start, end)
    print(f"Aggregated {start} → {end}: {count} rows, {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
```

**Step 3: 测试（集成，用测试 CH 上的少量分钟数据）**

```python
@pytest.mark.integration
def test_aggregate_bar_1d(bar_1m_seeded):
    from backend.scripts.aggregate_bar_1d import aggregate
    from datetime import date
    count = aggregate(date(2024,1,1), date(2024,1,5))
    assert count > 0
```

**Step 4: 手动跑一次全量聚合**

```bash
cd backend && python -m scripts.aggregate_bar_1d full --start 2020-01-01 --end 2026-04-15
```

**Step 5: Commit**

```bash
git add backend/scripts/import_qfq.py backend/scripts/aggregate_bar_1d.py backend/tests/test_aggregate_bar_1d.py
git commit -m "feat(backend): 前复权因子导入 + 日频聚合脚本"
```

---

## Task 5：因子基类 + 注册 + 热加载 + 示例因子

**Files:**
- Create: `backend/engine/__init__.py`
- Create: `backend/engine/base_factor.py`
- Create: `backend/runtime/__init__.py`
- Create: `backend/runtime/factor_registry.py`
- Create: `backend/runtime/hot_reload.py`
- Create: `backend/factors/__init__.py`
- Create: `backend/factors/base.py`（re-export）
- Create: `backend/factors/reversal/__init__.py`
- Create: `backend/factors/reversal/reversal_n.py`
- Create: `backend/factors/momentum/__init__.py`
- Create: `backend/factors/momentum/momentum_n.py`
- Create: `backend/factors/volatility/__init__.py`
- Create: `backend/factors/volatility/realized_vol.py`
- Create: `backend/factors/volume/__init__.py`
- Create: `backend/factors/volume/turnover_ratio.py`
- Create: `backend/tests/test_factor_registry.py`
- Create: `backend/tests/test_factors_math.py`

**Step 1: engine/base_factor.py**

完整代码见 §4.1 设计文档。

**Step 2: runtime/factor_registry.py**

```python
from __future__ import annotations
import hashlib, importlib, inspect, json, pkgutil, threading
from pathlib import Path
from typing import Type
from backend.engine.base_factor import BaseFactor
from backend.storage.mysql_client import mysql_conn
from backend.config import settings

class FactorRegistry:
    _instance: "FactorRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._factors = {}
                cls._instance._hashes = {}
                cls._instance._modules = {}
        return cls._instance

    def scan_and_register(self, root_pkg: str = "backend.factors") -> list[str]:
        updated: list[str] = []
        pkg = importlib.import_module(root_pkg)
        pkg_path = Path(pkg.__file__).parent if pkg.__file__ else None
        if pkg_path is None:
            return updated

        for mod_info in pkgutil.walk_packages(pkg.__path__, prefix=f"{root_pkg}."):
            if mod_info.ispkg:
                continue
            if mod_info.name.endswith(".base"):
                continue
            module = importlib.import_module(mod_info.name)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj is BaseFactor or not issubclass(obj, BaseFactor):
                    continue
                if obj.__module__ != module.__name__:
                    continue
                factor_id = getattr(obj, "factor_id", None)
                if not factor_id:
                    continue
                code_hash = self._compute_hash(obj)
                if self._hashes.get(factor_id) != code_hash:
                    self._factors[factor_id] = obj
                    self._hashes[factor_id] = code_hash
                    self._modules[factor_id] = module.__name__
                    self._persist_meta(obj, code_hash)
                    updated.append(factor_id)
        return updated

    def reload_module(self, module_name: str) -> list[str]:
        if module_name in self._modules.values():
            mod = importlib.import_module(module_name)
            importlib.reload(mod)
        return self.scan_and_register()

    def get(self, factor_id: str) -> BaseFactor:
        cls = self._factors.get(factor_id)
        if not cls:
            raise KeyError(f"factor not found: {factor_id}")
        return cls()

    def list(self) -> list[dict]:
        return [
            {
                "factor_id": fid,
                "display_name": cls.display_name,
                "category": cls.category,
                "description": cls.description,
                "params_schema": cls.params_schema,
                "default_params": cls.default_params,
                "supported_freqs": list(cls.supported_freqs),
            }
            for fid, cls in self._factors.items()
        ]

    def _compute_hash(self, cls: type) -> str:
        src = inspect.getsource(cls)
        return hashlib.sha1(src.encode("utf-8")).hexdigest()

    def _persist_meta(self, cls: type, code_hash: str) -> None:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT version, code_hash FROM factor_meta WHERE factor_id=%s", (cls.factor_id,))
                row = cur.fetchone()
                if row is None:
                    cur.execute("""
                        INSERT INTO factor_meta
                          (factor_id, display_name, category, description,
                           params_schema, default_params, supported_freqs,
                           code_hash, version, is_active)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,1)
                    """, (
                        cls.factor_id, cls.display_name, cls.category, cls.description,
                        json.dumps(cls.params_schema, ensure_ascii=False),
                        json.dumps(cls.default_params, ensure_ascii=False),
                        ",".join(cls.supported_freqs),
                        code_hash,
                    ))
                elif row["code_hash"] != code_hash:
                    cur.execute("""
                        UPDATE factor_meta SET
                          display_name=%s, category=%s, description=%s,
                          params_schema=%s, default_params=%s, supported_freqs=%s,
                          code_hash=%s, version=version+1
                        WHERE factor_id=%s
                    """, (
                        cls.display_name, cls.category, cls.description,
                        json.dumps(cls.params_schema, ensure_ascii=False),
                        json.dumps(cls.default_params, ensure_ascii=False),
                        ",".join(cls.supported_freqs),
                        code_hash, cls.factor_id,
                    ))
            c.commit()

    def current_version(self, factor_id: str) -> int:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT version FROM factor_meta WHERE factor_id=%s", (factor_id,))
                r = cur.fetchone()
                return int(r["version"]) if r else 1
```

**Step 3: runtime/hot_reload.py**

```python
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging, time
from backend.runtime.factor_registry import FactorRegistry

log = logging.getLogger(__name__)

class _Handler(FileSystemEventHandler):
    def __init__(self, factors_dir: Path):
        self.factors_dir = factors_dir
        self._last = 0.0

    def on_any_event(self, event):
        if event.is_directory or not event.src_path.endswith(".py"):
            return
        now = time.time()
        if now - self._last < 0.5:  # debounce
            return
        self._last = now
        try:
            updated = FactorRegistry().scan_and_register()
            if updated:
                log.info("factor hot-reloaded: %s", updated)
        except Exception:
            log.exception("hot-reload failed")

def start_hot_reload(factors_dir: Path) -> Observer:
    obs = Observer()
    obs.schedule(_Handler(factors_dir), str(factors_dir), recursive=True)
    obs.start()
    return obs
```

**Step 4: 四个内置因子**

`reversal/reversal_n.py` 完整代码见设计文档 §4.2。其余三个：

```python
# momentum/momentum_n.py
class MomentumN(BaseFactor):
    factor_id="momentum_n"; display_name="N日动量"; category="momentum"
    description="过去 N 日收益率（排除最近 5 日，避免反转效应）"
    params_schema={"window":{"type":"int","default":120,"min":20,"max":252},
                   "skip":{"type":"int","default":5,"min":0,"max":20}}
    default_params={"window":120,"skip":5}
    def required_warmup(self, p): return int(p["window"])+int(p["skip"])+5
    def compute(self, ctx, p):
        w, k = int(p["window"]), int(p["skip"])
        close = ctx.data.load_panel(ctx.symbols,
            (ctx.start_date - pd.Timedelta(days=(w+k)*2+10)).date(),
            ctx.end_date.date(), field="close", adjust="qfq")
        return (close.shift(k) / close.shift(w+k) - 1).loc[ctx.start_date:]

# volatility/realized_vol.py
class RealizedVol(BaseFactor):
    factor_id="realized_vol"; display_name="N日已实现波动率"; category="volatility"
    description="过去 N 日日收益率的标准差（年化）"
    params_schema={"window":{"type":"int","default":20,"min":5,"max":252}}
    default_params={"window":20}
    def required_warmup(self, p): return int(p["window"])+5
    def compute(self, ctx, p):
        w = int(p["window"])
        close = ctx.data.load_panel(ctx.symbols,
            (ctx.start_date - pd.Timedelta(days=w*2+10)).date(),
            ctx.end_date.date(), field="close", adjust="qfq")
        ret = close.pct_change()
        return (ret.rolling(w).std() * (252**0.5)).loc[ctx.start_date:]

# volume/turnover_ratio.py  —— 用 amount_k 近似（需真实流通市值时可扩展）
class TurnoverRatio(BaseFactor):
    factor_id="turnover_ratio"; display_name="N日平均换手代理"; category="volume"
    description="过去 N 日成交金额 / 过去 N 日收盘价均值（近似换手率）"
    params_schema={"window":{"type":"int","default":20,"min":5,"max":120}}
    default_params={"window":20}
    def required_warmup(self, p): return int(p["window"])+5
    def compute(self, ctx, p):
        w = int(p["window"])
        start = (ctx.start_date - pd.Timedelta(days=w*2+10)).date()
        amt = ctx.data.load_panel(ctx.symbols, start, ctx.end_date.date(), field="amount_k", adjust="none")
        close = ctx.data.load_panel(ctx.symbols, start, ctx.end_date.date(), field="close", adjust="qfq")
        return (amt.rolling(w).mean() / close.rolling(w).mean()).loc[ctx.start_date:]
```

**Step 5: 测试 test_factor_registry.py + test_factors_math.py**

```python
# test_factor_registry.py
def test_scan_registers_builtins():
    from backend.runtime.factor_registry import FactorRegistry
    reg = FactorRegistry()
    reg.scan_and_register()
    ids = {f["factor_id"] for f in reg.list()}
    assert {"reversal_n","momentum_n","realized_vol","turnover_ratio"}.issubset(ids)

def test_get_instance():
    from backend.runtime.factor_registry import FactorRegistry
    FactorRegistry().scan_and_register()
    inst = FactorRegistry().get("reversal_n")
    assert inst.factor_id == "reversal_n"
    assert inst.required_warmup({"window": 20}) == 25

# test_factors_math.py —— 用 mock DataService 构造已知 close
def test_reversal_n_math():
    from backend.factors.reversal.reversal_n import ReversalN
    import pandas as pd, numpy as np
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    close = pd.DataFrame({"A": np.linspace(100, 130, 30)}, index=dates)  # 单调上升
    class FakeData:
        def load_panel(self, *a, **kw): return close
    ctx = type("Ctx", (), {})()
    ctx.data = FakeData(); ctx.symbols=["A"]
    ctx.start_date = dates[10]; ctx.end_date = dates[-1]; ctx.warmup_days = 5
    out = ReversalN().compute(ctx, {"window": 5})
    assert (out < 0).all().all()  # 单调上升时 reversal 应全为负
```

Run: `pytest tests/test_factor_registry.py tests/test_factors_math.py -v` → PASS

**Step 6: Commit**

```bash
git add backend/engine backend/runtime backend/factors backend/tests/test_factor_registry.py backend/tests/test_factors_math.py
git commit -m "feat(backend): 因子基类 + 注册 + 热加载 + 4 个内置因子"
```

---

## Task 6：评估引擎（含数学单测）

**Files:**
- Create: `backend/services/__init__.py`
- Create: `backend/services/eval_service.py`
- Create: `backend/services/metrics.py`
- Create: `backend/services/params_hash.py`
- Create: `backend/tests/test_metrics.py`

**Step 1: services/params_hash.py**

```python
import hashlib, json

def params_hash(params: dict) -> str:
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
```

**Step 2: services/metrics.py** —— 纯函数，可单测

```python
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

def cross_sectional_ic(factor: pd.DataFrame, forward_ret: pd.DataFrame) -> pd.Series:
    """每日横截面 Pearson corr。factor 和 forward_ret index/columns 应对齐。"""
    aligned_f, aligned_r = factor.align(forward_ret, join="inner")
    out = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < 3:
            continue
        out[dt] = float(np.corrcoef(f_row[mask], r_row[mask])[0, 1])
    return pd.Series(out).sort_index()

def cross_sectional_rank_ic(factor: pd.DataFrame, forward_ret: pd.DataFrame) -> pd.Series:
    aligned_f, aligned_r = factor.align(forward_ret, join="inner")
    out = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < 3:
            continue
        rho, _ = spearmanr(f_row[mask], r_row[mask])
        out[dt] = float(rho)
    return pd.Series(out).sort_index()

def ic_summary(ic_series: pd.Series) -> dict:
    if ic_series.empty:
        return {"ic_mean":0,"ic_std":0,"ic_ir":0,"ic_win_rate":0,"ic_t_stat":0}
    n = len(ic_series); mean = float(ic_series.mean()); std = float(ic_series.std(ddof=1) or 1e-12)
    return {
        "ic_mean": mean,
        "ic_std":  std,
        "ic_ir":   mean / std,
        "ic_win_rate": float((ic_series > 0).mean()),
        "ic_t_stat":   mean / (std / np.sqrt(n)),
    }

def group_returns(factor: pd.DataFrame, forward_ret_1d: pd.DataFrame, n_groups: int = 5) -> pd.DataFrame:
    """每日按因子值分 n 组（每日独立 qcut），返回各组日收益。"""
    aligned_f, aligned_r = factor.align(forward_ret_1d, join="inner")
    rows = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < n_groups:
            continue
        try:
            q = pd.qcut(f_row[mask], n_groups, labels=False, duplicates="drop")
        except ValueError:
            continue
        df = pd.DataFrame({"q": q, "r": r_row[mask]})
        rows[dt] = df.groupby("q")["r"].mean().reindex(range(n_groups))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).T.sort_index()

def turnover_series(factor: pd.DataFrame, n_groups: int = 5, which: str = "top") -> pd.Series:
    """每日顶组/底组与前一期相比的 symmetric diff / 组大小。"""
    prev: set[str] | None = None
    out = {}
    for dt, f_row in factor.iterrows():
        valid = f_row.dropna()
        if len(valid) < n_groups:
            continue
        q = pd.qcut(valid, n_groups, labels=False, duplicates="drop")
        target_label = n_groups - 1 if which == "top" else 0
        current = set(valid.index[q == target_label])
        if prev is not None and current and prev:
            out[dt] = len(current ^ prev) / max(len(current), 1)
        prev = current
    return pd.Series(out).sort_index()

def long_short_series(group_rets: pd.DataFrame) -> pd.Series:
    if group_rets.empty: return pd.Series(dtype=float)
    top = group_rets.iloc[:, -1]; bot = group_rets.iloc[:, 0]
    return (top - bot).rename("long_short")

def long_short_metrics(ls: pd.Series, trading_days: int = 252) -> dict:
    if ls.empty: return {"long_short_annret":0,"long_short_sharpe":0}
    ann = float(ls.mean() * trading_days)
    sharpe = float(ls.mean() / (ls.std(ddof=1) or 1e-12) * np.sqrt(trading_days))
    return {"long_short_annret": ann, "long_short_sharpe": sharpe}

def value_histogram(values: pd.DataFrame, bins: int = 50) -> dict:
    arr = values.values.ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return {"bins": [], "counts": []}
    counts, edges = np.histogram(arr, bins=bins)
    return {"bins": edges.tolist(), "counts": counts.tolist()}
```

**Step 3: 测试 test_metrics.py**

```python
import numpy as np, pandas as pd
from backend.services import metrics

def _mk_panel(n_dates=60, n_syms=20, seed=0):
    np.random.seed(seed)
    idx = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    cols = [f"S{i:02d}" for i in range(n_syms)]
    return pd.DataFrame(np.random.randn(n_dates, n_syms), index=idx, columns=cols)

def test_ic_perfect_positive_relationship():
    f = _mk_panel()
    r = f * 0.1 + np.random.randn(*f.shape) * 1e-6
    ic = metrics.cross_sectional_ic(f, r)
    assert ic.mean() > 0.99

def test_ic_perfect_negative_relationship():
    f = _mk_panel()
    r = -f * 0.1 + np.random.randn(*f.shape) * 1e-6
    ic = metrics.cross_sectional_ic(f, r)
    assert ic.mean() < -0.99

def test_group_returns_monotonic_when_factor_predicts_return():
    # 因子直接 = 未来收益：分组平均收益应严格单调递增
    f = _mk_panel(n_dates=80, n_syms=50)
    r = f.copy()
    g = metrics.group_returns(f, r, n_groups=5)
    means = g.mean().values
    assert all(means[i] <= means[i+1] for i in range(len(means)-1))

def test_turnover_zero_when_factor_constant_rank():
    # 若因子秩不变，top 组股票每日一致 → 换手 = 0
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(10)]
    vals = np.tile(np.arange(10), (10,1))
    f = pd.DataFrame(vals, index=idx, columns=cols)
    to = metrics.turnover_series(f, n_groups=5, which="top")
    assert (to == 0).all()

def test_ic_summary_basic():
    ic = pd.Series([0.1, 0.05, -0.02, 0.08, 0.06])
    s = metrics.ic_summary(ic)
    assert s["ic_mean"] > 0
    assert 0 <= s["ic_win_rate"] <= 1
```

Run: `pytest tests/test_metrics.py -v` → PASS

**Step 4: services/eval_service.py**

```python
from __future__ import annotations
import json, logging, traceback
from datetime import datetime
from dataclasses import asdict
import pandas as pd
from backend.storage.mysql_client import mysql_conn
from backend.storage.data_service import DataService
from backend.runtime.factor_registry import FactorRegistry
from backend.engine.base_factor import FactorContext
from backend.services import metrics
from backend.services.params_hash import params_hash as _hash

log = logging.getLogger(__name__)

def _set_status(run_id, *, status=None, progress=None, error=None, started=False, finished=False):
    sets, vals = [], []
    if status is not None: sets.append("status=%s"); vals.append(status)
    if progress is not None: sets.append("progress=%s"); vals.append(progress)
    if error is not None: sets.append("error_message=%s"); vals.append(error)
    if started: sets.append("started_at=%s"); vals.append(datetime.utcnow())
    if finished: sets.append("finished_at=%s"); vals.append(datetime.utcnow())
    if not sets: return
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(f"UPDATE factor_eval_runs SET {','.join(sets)} WHERE run_id=%s", vals)
        c.commit()

def run_eval(run_id: str, body: dict) -> None:
    try:
        _set_status(run_id, status="running", started=True, progress=5)
        reg = FactorRegistry()
        reg.scan_and_register()
        factor = reg.get(body["factor_id"])
        version = reg.current_version(body["factor_id"])
        params = body.get("params") or factor.default_params
        phash = _hash(params)

        data = DataService()
        symbols = data.resolve_pool(int(body["pool_id"]))
        start = pd.to_datetime(body["start_date"])
        end   = pd.to_datetime(body["end_date"])
        warmup = factor.required_warmup(params)
        ctx = FactorContext(data=data, symbols=symbols,
                            start_date=start, end_date=end, warmup_days=warmup)

        _set_status(run_id, progress=15)
        F = factor.compute(ctx, params)
        _set_status(run_id, progress=40)
        data.save_factor_values(body["factor_id"], version, phash, F)  # 见 Task 7 的补充实现

        close = data.load_panel(symbols, start.date(), end.date(), field="close", adjust="qfq")
        fwd_periods = [int(x) for x in body.get("forward_periods", [1,5,10])]
        fwd_rets = {k: close.shift(-k) / close - 1 for k in fwd_periods}

        _set_status(run_id, progress=55)
        ic = {k: metrics.cross_sectional_ic(F, fwd_rets[k]) for k in fwd_periods}
        rank_ic = {k: metrics.cross_sectional_rank_ic(F, fwd_rets[k]) for k in fwd_periods}

        _set_status(run_id, progress=75)
        n_groups = int(body.get("n_groups", 5))
        g_rets = metrics.group_returns(F, fwd_rets[1], n_groups=n_groups)
        ls = metrics.long_short_series(g_rets)

        _set_status(run_id, progress=85)
        to = metrics.turnover_series(F, n_groups=n_groups, which="top")
        hist = metrics.value_histogram(F)

        ic_sum = metrics.ic_summary(ic[1])
        rank_ic_sum = metrics.ic_summary(rank_ic[1])
        rank_ic_sum = {f"rank_{k}": v for k, v in rank_ic_sum.items()}
        ls_stats = metrics.long_short_metrics(ls)

        payload = {
            "ic":       {str(k): _series_to_obj(ic[k]) for k in fwd_periods},
            "rank_ic":  {str(k): _series_to_obj(rank_ic[k]) for k in fwd_periods},
            "group_returns":     _df_to_obj(g_rets.rename(columns=lambda c: f"g{int(c)+1}")),
            "long_short_equity": _series_to_obj((1 + ls).cumprod()),
            "turnover_series":   _series_to_obj(to),
            "value_hist":        hist,
        }

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("""
                    REPLACE INTO factor_eval_metrics
                    (run_id, ic_mean, ic_std, ic_ir, ic_win_rate, ic_t_stat,
                     rank_ic_mean, rank_ic_std, rank_ic_ir,
                     turnover_mean, long_short_sharpe, long_short_annret, payload_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    run_id,
                    ic_sum["ic_mean"], ic_sum["ic_std"], ic_sum["ic_ir"],
                    ic_sum["ic_win_rate"], ic_sum["ic_t_stat"],
                    rank_ic_sum["rank_ic_mean"], rank_ic_sum["rank_ic_std"], rank_ic_sum["rank_ic_ir"],
                    float(to.mean()) if not to.empty else 0.0,
                    ls_stats["long_short_sharpe"], ls_stats["long_short_annret"],
                    json.dumps(payload, default=str),
                ))
            c.commit()

        _set_status(run_id, status="success", progress=100, finished=True)
    except Exception as e:
        log.exception("eval failed: %s", run_id)
        _set_status(run_id, status="failed", error=traceback.format_exc()[:4000], finished=True)

def _series_to_obj(s: pd.Series) -> dict:
    return {"dates": [d.strftime("%Y-%m-%d") for d in s.index], "values": [float(x) if pd.notna(x) else None for x in s.values]}

def _df_to_obj(df: pd.DataFrame) -> dict:
    if df.empty: return {"dates": [], **{}}
    obj = {"dates": [d.strftime("%Y-%m-%d") for d in df.index]}
    for col in df.columns:
        obj[col] = [float(x) if pd.notna(x) else None for x in df[col].values]
    return obj
```

**Step 5: Commit**

```bash
git add backend/services/ backend/tests/test_metrics.py
git commit -m "feat(backend): 评估引擎 + 指标数学库 + 单测"
```

---

## Task 7：factor_value_1d 读写 + 回测引擎

**Files:**
- Modify: `backend/storage/data_service.py`（增加 load_factor_values / save_factor_values）
- Create: `backend/services/backtest_service.py`
- Create: `backend/tests/test_factor_value_io.py`

**Step 1: 扩展 data_service.py**

```python
def save_factor_values(self, factor_id, factor_version, params_hash, frame: pd.DataFrame) -> int:
    if frame is None or frame.empty: return 0
    sid_map = self.resolver.resolve_many(list(frame.columns))
    long = frame.stack(dropna=True).rename("value").reset_index()
    long.columns = ["trade_date", "symbol", "value"]
    long["symbol_id"] = long["symbol"].map(sid_map).astype("Int64")
    long = long.dropna(subset=["symbol_id"])
    version = int(pd.Timestamp.utcnow().value // 10**6)  # ms

    rows = [
        (factor_id, int(factor_version), params_hash, int(r.symbol_id),
         pd.Timestamp(r.trade_date).date(), float(r.value), version)
        for r in long.itertuples()
    ]
    if not rows: return 0
    with ch_client() as ch:
        ch.execute(
            """INSERT INTO quant_data.factor_value_1d
               (factor_id, factor_version, params_hash, symbol_id, trade_date, value, version)
               VALUES""", rows,
        )
    return len(rows)

def load_factor_values(self, factor_id, factor_version, params_hash,
                       symbols, start, end) -> pd.DataFrame:
    sid_map = self.resolver.resolve_many(symbols)
    if not sid_map: return pd.DataFrame()
    with ch_client() as ch:
        rows = ch.execute(
            """SELECT symbol_id, trade_date, value
               FROM quant_data.factor_value_1d FINAL
               WHERE factor_id=%(fid)s AND factor_version=%(fv)s AND params_hash=%(ph)s
                 AND symbol_id IN %(sids)s AND trade_date BETWEEN %(s)s AND %(e)s
               ORDER BY trade_date, symbol_id""",
            {"fid":factor_id,"fv":factor_version,"ph":params_hash,
             "sids":list(sid_map.values()),"s":start,"e":end},
        )
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["symbol_id","trade_date","value"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    inv = {v:k for k,v in sid_map.items()}
    df["symbol"] = df["symbol_id"].map(inv)
    return df.pivot(index="trade_date", columns="symbol", values="value").sort_index()
```

**Step 2: services/backtest_service.py**

```python
from __future__ import annotations
import json, logging, traceback, uuid
from datetime import datetime
from pathlib import Path
import pandas as pd, numpy as np, vectorbt as vbt
from backend.storage.mysql_client import mysql_conn
from backend.storage.data_service import DataService
from backend.runtime.factor_registry import FactorRegistry
from backend.engine.base_factor import FactorContext
from backend.services.params_hash import params_hash as _hash
from backend.config import settings

log = logging.getLogger(__name__)

ARTIFACT_DIR = Path("./data/artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def _update(run_id, **kw):
    sets, vals = [], []
    for k, v in kw.items():
        sets.append(f"{k}=%s"); vals.append(v)
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(f"UPDATE backtest_runs SET {','.join(sets)} WHERE run_id=%s", vals)
        c.commit()

def _load_or_compute_factor(data, reg, body, params, phash):
    factor = reg.get(body["factor_id"])
    version = reg.current_version(body["factor_id"])
    start = pd.to_datetime(body["start_date"]); end = pd.to_datetime(body["end_date"])
    symbols = data.resolve_pool(int(body["pool_id"]))
    F = data.load_factor_values(body["factor_id"], version, phash, symbols, start.date(), end.date())
    if F.empty or F.index.min() > start or F.index.max() < end:
        warmup = factor.required_warmup(params)
        ctx = FactorContext(data=data, symbols=symbols,
                            start_date=start, end_date=end, warmup_days=warmup)
        F = factor.compute(ctx, params)
        data.save_factor_values(body["factor_id"], version, phash, F)
    return F, version, symbols, start, end

def _build_weights(F: pd.DataFrame, n_groups: int, rebalance: int,
                   position: str) -> pd.DataFrame:
    W = pd.DataFrame(0.0, index=F.index, columns=F.columns)
    rebal_idx = F.index[::rebalance]
    for dt in rebal_idx:
        row = F.loc[dt].dropna()
        if len(row) < n_groups: continue
        q = pd.qcut(row, n_groups, labels=False, duplicates="drop")
        top = row.index[q == n_groups - 1]
        if position == "top":
            W.loc[dt, top] = 1.0 / len(top)
        else:  # long_short
            bot = row.index[q == 0]
            W.loc[dt, top] =  1.0 / len(top)
            W.loc[dt, bot] = -1.0 / len(bot)
    W = W.replace(0, np.nan).ffill().fillna(0)
    return W

def run_backtest(run_id: str, body: dict) -> None:
    try:
        _update(run_id, status="running", started_at=datetime.utcnow())
        data = DataService(); reg = FactorRegistry(); reg.scan_and_register()
        params = body.get("params") or reg.get(body["factor_id"]).default_params
        phash = _hash(params)
        F, version, symbols, start, end = _load_or_compute_factor(data, reg, body, params, phash)

        close = data.load_panel(symbols, start.date(), end.date(), field="close", adjust="qfq")
        close, F = close.align(F, join="inner", axis=None)

        n_groups  = int(body.get("n_groups", 5))
        rebalance = int(body.get("rebalance_period", 1))
        position  = body.get("position", "top")
        cost_bps  = float(body.get("cost_bps", 3))
        init_cash = float(body.get("init_cash", 1e7))

        W = _build_weights(F, n_groups, rebalance, position)
        size = W.mul(init_cash).div(close).replace([np.inf, -np.inf], 0).fillna(0)
        pf = vbt.Portfolio.from_orders(
            close=close, size=size, size_type="targetamount",
            fees=cost_bps/1e4, freq="1D", init_cash=init_cash,
            cash_sharing=True, group_by=True,
        )

        stats = pf.stats()
        equity = pf.value()
        orders = pf.orders.records_readable
        trades = pf.trades.records_readable

        art_dir = ARTIFACT_DIR / run_id; art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "equity.parquet").write_bytes(equity.to_frame("equity").to_parquet())
        (art_dir / "orders.parquet").write_bytes(orders.to_parquet())
        (art_dir / "trades.parquet").write_bytes(trades.to_parquet())

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("""REPLACE INTO backtest_metrics
                  (run_id,total_return,annual_return,sharpe_ratio,max_drawdown,win_rate,trade_count,payload_json)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (
                    run_id,
                    float(stats.get("Total Return [%]", 0))/100,
                    float(stats.get("Total Return [%]", 0))/100 * 252 / max(len(equity),1),
                    float(stats.get("Sharpe Ratio", 0)),
                    float(stats.get("Max Drawdown [%]", 0))/100,
                    float(stats.get("Win Rate [%]", 0))/100,
                    int(stats.get("Total Trades", 0)),
                    json.dumps({k: (float(v) if isinstance(v,(int,float)) else str(v)) for k,v in stats.items()}),
                ))
                for art_type, fname in [("equity","equity.parquet"),("orders","orders.parquet"),("trades","trades.parquet")]:
                    cur.execute("""REPLACE INTO backtest_artifacts
                      (run_id, artifact_type, artifact_path) VALUES (%s,%s,%s)""",
                      (run_id, art_type, str(art_dir/fname)))
            c.commit()

        _update(run_id, status="success", finished_at=datetime.utcnow())
    except Exception:
        log.exception("backtest failed: %s", run_id)
        _update(run_id, status="failed",
                error_message=traceback.format_exc()[:4000],
                finished_at=datetime.utcnow())
```

**Step 3: 写 test_factor_value_io.py（集成测试）**

```python
@pytest.mark.integration
def test_factor_value_roundtrip(data_seeded):
    import pandas as pd
    from backend.storage.data_service import DataService
    from datetime import date
    svc = DataService()
    idx = pd.date_range("2024-01-02", periods=5, freq="B")
    df = pd.DataFrame({"000001.SZ":[1,2,3,4,5],"000002.SZ":[5,4,3,2,1]}, index=idx)
    n = svc.save_factor_values("test_fac", 1, "a"*40, df)
    assert n == 10
    got = svc.load_factor_values("test_fac",1,"a"*40,["000001.SZ","000002.SZ"],
                                 date(2024,1,1), date(2024,1,31))
    assert got.shape == (5, 2)
```

**Step 4: Commit**

```bash
git add backend/storage/data_service.py backend/services/backtest_service.py backend/tests/test_factor_value_io.py
git commit -m "feat(backend): factor_value_1d 读写 + VectorBT 回测引擎"
```

---

## Task 8：任务运行时（ProcessPool + 入口）

**Files:**
- Create: `backend/runtime/task_pool.py`
- Create: `backend/runtime/entries.py`（worker 进程执行入口，必须可 pickle）
- Create: `backend/tests/test_task_pool.py`

**Step 1: runtime/entries.py**

```python
"""Worker 进程入口。必须是模块级函数以便 pickle。"""
def eval_entry(run_id: str, body: dict):
    from backend.services.eval_service import run_eval
    run_eval(run_id, body)

def backtest_entry(run_id: str, body: dict):
    from backend.services.backtest_service import run_backtest
    run_backtest(run_id, body)
```

**Step 2: runtime/task_pool.py**

```python
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor
from typing import Callable
from backend.config import settings

_pool: ProcessPoolExecutor | None = None

def get_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(
            max_workers=settings.task_workers,
            max_tasks_per_child=5,
        )
    return _pool

def submit(fn: Callable, *args, **kw):
    return get_pool().submit(fn, *args, **kw)

def reset_pool():
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=False)
        _pool = None
    return get_pool()
```

**Step 3: test_task_pool.py**

```python
def test_submit_runs_in_worker():
    from backend.runtime.task_pool import submit
    import os
    def _echo_pid():
        return os.getpid()
    f = submit(_echo_pid)
    assert f.result(timeout=10) != os.getpid()
```

Run: `pytest tests/test_task_pool.py -v` → PASS

**Step 4: Commit**

```bash
git add backend/runtime/task_pool.py backend/runtime/entries.py backend/tests/test_task_pool.py
git commit -m "feat(backend): ProcessPoolExecutor 任务运行时"
```

---

## Task 9：FastAPI 路由层

**Files:**
- Create: `backend/api/schemas.py`（Pydantic 模型）
- Create: `backend/api/deps.py`（依赖注入）
- Create: `backend/api/routers/__init__.py`
- Create: `backend/api/routers/factors.py`
- Create: `backend/api/routers/pools.py`
- Create: `backend/api/routers/evals.py`
- Create: `backend/api/routers/backtests.py`
- Create: `backend/api/routers/bars.py`
- Create: `backend/api/routers/admin.py`
- Modify: `backend/api/main.py`（挂载路由、启动 watchdog）
- Create: `backend/tests/test_api_health.py`
- Create: `backend/tests/test_api_factors.py`
- Create: `backend/tests/test_api_pools.py`

**Step 1: api/schemas.py** —— 列出所有请求/响应 DTO

```python
from pydantic import BaseModel
from datetime import date

class CreateEvalIn(BaseModel):
    factor_id: str
    params: dict | None = None
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    forward_periods: list[int] = [1, 5, 10]
    n_groups: int = 5

class CreateBacktestIn(BaseModel):
    factor_id: str
    params: dict | None = None
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    n_groups: int = 5
    rebalance_period: int = 1
    position: str = "top"    # "top" | "long_short"
    cost_bps: float = 3.0
    init_cash: float = 1e7

class PoolIn(BaseModel):
    name: str
    description: str | None = None
    symbols: list[str] = []

class PoolImportIn(BaseModel):
    text: str    # 支持换行/空格/逗号分隔

# 通用响应包装
def ok(data): return {"code": 0, "data": data}
def fail(code: int, msg: str, detail=None):
    return {"code": code, "message": msg, "detail": detail}
```

**Step 2: routers/factors.py**

```python
from fastapi import APIRouter, HTTPException
from backend.runtime.factor_registry import FactorRegistry
from backend.api.schemas import ok

router = APIRouter(prefix="/api/factors", tags=["factors"])

@router.get("")
def list_factors():
    reg = FactorRegistry()
    reg.scan_and_register()  # 确保初始扫描
    return ok(reg.list())

@router.get("/{factor_id}")
def get_factor(factor_id: str):
    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        inst = reg.get(factor_id)
    except KeyError:
        raise HTTPException(404, "factor not found")
    return ok({
        "factor_id": inst.factor_id,
        "display_name": inst.display_name,
        "category": inst.category,
        "description": inst.description,
        "params_schema": inst.params_schema,
        "default_params": inst.default_params,
        "supported_freqs": list(inst.supported_freqs),
        "version": reg.current_version(factor_id),
    })

@router.post("/reload")
def reload_factors():
    reg = FactorRegistry()
    updated = reg.scan_and_register()
    return ok({"updated": updated})
```

**Step 3: routers/pools.py** —— CRUD + 批量导入

```python
from fastapi import APIRouter, HTTPException
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver
from backend.config import settings
from backend.api.schemas import PoolIn, PoolImportIn, ok
import re

router = APIRouter(prefix="/api/pools", tags=["pools"])

@router.get("")
def list_pools():
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT pool_id, pool_name, description, created_at FROM stock_pool WHERE owner_key=%s AND is_active=1 ORDER BY pool_id DESC",
                        (settings.owner_key,))
            return ok(cur.fetchall())

@router.post("")
def create_pool(body: PoolIn):
    r = SymbolResolver()
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("INSERT INTO stock_pool (owner_key, pool_name, description) VALUES (%s,%s,%s)",
                        (settings.owner_key, body.name, body.description))
            pid = cur.lastrowid
            for i, s in enumerate(body.symbols):
                sid = r.resolve_symbol_id(s)
                if sid:
                    cur.execute("INSERT IGNORE INTO stock_pool_symbol (pool_id, symbol_id, sort_order) VALUES (%s,%s,%s)",
                                (pid, sid, i))
        c.commit()
    return ok({"pool_id": pid})

@router.get("/{pool_id}")
def get_pool(pool_id: int):
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM stock_pool WHERE pool_id=%s AND owner_key=%s",
                        (pool_id, settings.owner_key))
            p = cur.fetchone()
            if not p: raise HTTPException(404, "pool not found")
            cur.execute("""SELECT b.symbol, b.name FROM stock_pool_symbol s
                           JOIN stock_basic b ON b.symbol_id = s.symbol_id
                           WHERE s.pool_id=%s ORDER BY s.sort_order""", (pool_id,))
            p["symbols"] = cur.fetchall()
    return ok(p)

@router.put("/{pool_id}")
def update_pool(pool_id: int, body: PoolIn):
    r = SymbolResolver()
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("UPDATE stock_pool SET pool_name=%s, description=%s WHERE pool_id=%s AND owner_key=%s",
                        (body.name, body.description, pool_id, settings.owner_key))
            cur.execute("DELETE FROM stock_pool_symbol WHERE pool_id=%s", (pool_id,))
            for i, s in enumerate(body.symbols):
                sid = r.resolve_symbol_id(s)
                if sid:
                    cur.execute("INSERT INTO stock_pool_symbol (pool_id, symbol_id, sort_order) VALUES (%s,%s,%s)",
                                (pool_id, sid, i))
        c.commit()
    return ok({"pool_id": pool_id})

@router.delete("/{pool_id}")
def delete_pool(pool_id: int):
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("UPDATE stock_pool SET is_active=0 WHERE pool_id=%s AND owner_key=%s",
                        (pool_id, settings.owner_key))
        c.commit()
    return ok({"pool_id": pool_id})

@router.post("/{pool_id}:import")
def import_symbols(pool_id: int, body: PoolImportIn):
    tokens = [t for t in re.split(r"[\s,;]+", body.text) if t]
    r = SymbolResolver()
    inserted = 0
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM stock_pool_symbol WHERE pool_id=%s", (pool_id,))
            base = cur.fetchone()["m"] + 1
            for i, s in enumerate(tokens):
                sid = r.resolve_symbol_id(s)
                if sid:
                    cur.execute("INSERT IGNORE INTO stock_pool_symbol (pool_id, symbol_id, sort_order) VALUES (%s,%s,%s)",
                                (pool_id, sid, base + i))
                    inserted += cur.rowcount
        c.commit()
    return ok({"inserted": inserted, "total_input": len(tokens)})
```

**Step 4: routers/evals.py**

```python
import uuid, json
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, HTTPException
from backend.storage.mysql_client import mysql_conn
from backend.runtime.task_pool import submit
from backend.runtime.entries import eval_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.services.params_hash import params_hash
from backend.api.schemas import CreateEvalIn, ok

router = APIRouter(prefix="/api/evals", tags=["evals"])

@router.post("")
def create_eval(body: CreateEvalIn, bt: BackgroundTasks):
    reg = FactorRegistry(); reg.scan_and_register()
    try:
        reg.get(body.factor_id)
    except KeyError:
        raise HTTPException(400, "factor not found")
    version = reg.current_version(body.factor_id)
    params = body.params or reg.get(body.factor_id).default_params
    run_id = uuid.uuid4().hex
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("""INSERT INTO factor_eval_runs
              (run_id, factor_id, factor_version, params_hash, params_json,
               pool_id, freq, start_date, end_date, forward_periods, n_groups,
               status, progress, created_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)""",
              (run_id, body.factor_id, version, params_hash(params),
               json.dumps(params), body.pool_id, body.freq,
               body.start_date, body.end_date,
               ",".join(str(x) for x in body.forward_periods),
               body.n_groups, datetime.utcnow()))
        c.commit()
    bt.add_task(submit, eval_entry, run_id, body.model_dump(mode="json"))
    return ok({"run_id": run_id, "status": "pending"})

@router.get("")
def list_evals(factor_id: str | None = None, status: str | None = None, limit: int = 50):
    sql = "SELECT * FROM factor_eval_runs WHERE 1=1"
    params = []
    if factor_id: sql += " AND factor_id=%s"; params.append(factor_id)
    if status:    sql += " AND status=%s";    params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"; params.append(limit)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params); return ok(cur.fetchall())

@router.get("/{run_id}")
def get_eval(run_id: str):
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT * FROM factor_eval_runs WHERE run_id=%s", (run_id,))
            run = cur.fetchone()
            if not run: raise HTTPException(404, "not found")
            cur.execute("SELECT * FROM factor_eval_metrics WHERE run_id=%s", (run_id,))
            m = cur.fetchone()
    if m and m.get("payload_json"):
        m["payload"] = json.loads(m.pop("payload_json"))
    run["metrics"] = m
    return ok(run)

@router.get("/{run_id}/status")
def get_eval_status(run_id: str):
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT run_id, status, progress, error_message, started_at, finished_at FROM factor_eval_runs WHERE run_id=%s",
                        (run_id,))
            r = cur.fetchone()
    if not r: raise HTTPException(404, "not found")
    return ok(r)
```

**Step 5: routers/backtests.py** —— 结构同 evals.py，只是换用 backtest_runs 表 + backtest_entry

（省略样板，参照 evals.py）

**Step 6: routers/bars.py + admin.py**

```python
# bars.py
@router.get("/api/bars/daily")
def get_daily_bars(symbol: str, start: date, end: date, adjust: str = "qfq"):
    svc = DataService()
    bars = svc.load_bars([symbol], start, end, freq="1d", adjust=adjust)
    if symbol not in bars:
        raise HTTPException(404, "no data")
    return ok(bars[symbol].reset_index().assign(
        trade_date=lambda d: d["trade_date"].dt.strftime("%Y-%m-%d")
    ).to_dict(orient="records"))

# admin.py  ——  触发 bar_1d 聚合 / qfq 导入，异步
@router.post("/api/admin/bar_1d:aggregate")
def trigger_aggregate(body: dict, bt: BackgroundTasks):
    from backend.scripts.aggregate_bar_1d import aggregate
    ...
```

**Step 7: 修改 api/main.py**

```python
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config import settings
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.hot_reload import start_hot_reload
from backend.api.routers import factors, pools, evals, backtests, bars, admin
from backend.api.schemas import ok

logging.basicConfig(level=settings.log_level)
app = FastAPI(title="Factor Research", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup():
    FactorRegistry().scan_and_register()
    if settings.hot_reload:
        app.state.observer = start_hot_reload(Path(settings.factors_dir))

@app.on_event("shutdown")
def _shutdown():
    obs = getattr(app.state, "observer", None)
    if obs: obs.stop(); obs.join(timeout=2)

@app.get("/api/health")
def health():
    return ok({"status": "ok"})

app.include_router(factors.router)
app.include_router(pools.router)
app.include_router(evals.router)
app.include_router(backtests.router)
app.include_router(bars.router)
app.include_router(admin.router)
```

**Step 8: 测试（用 fastapi.testclient）**

```python
def test_api_health():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    r = TestClient(app).get("/api/health")
    assert r.json()["code"] == 0

def test_api_list_factors():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    r = TestClient(app).get("/api/factors")
    ids = [x["factor_id"] for x in r.json()["data"]]
    assert "reversal_n" in ids
```

**Step 9: Commit**

```bash
git add backend/api
git commit -m "feat(backend): FastAPI 路由层（factors/pools/evals/backtests/bars/admin）"
```

---

## Task 10：前端脚手架

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/.gitignore`
- Create: `frontend/src/main.ts`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/router/index.ts`
- Create: `frontend/src/styles/theme.ts`
- Create: `frontend/src/styles/global.scss`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/components/layout/AppSidebar.vue`
- Create: `frontend/src/components/layout/AppHeader.vue`
- Create: `frontend/src/pages/dashboard/DashboardPage.vue`

**Step 1: package.json**

```json
{
  "name": "factor-research-frontend",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest"
  },
  "dependencies": {
    "vue": "^3.5.0",
    "vue-router": "^4.4.5",
    "pinia": "^2.2.4",
    "naive-ui": "^2.40.1",
    "@tanstack/vue-query": "^5.59.0",
    "axios": "^1.7.7",
    "echarts": "^5.5.1",
    "vue-echarts": "^7.0.3",
    "date-fns": "^4.1.0"
  },
  "devDependencies": {
    "vite": "^5.4.9",
    "@vitejs/plugin-vue": "^5.1.4",
    "typescript": "^5.6.3",
    "vue-tsc": "^2.1.6",
    "sass": "^1.80.3",
    "vitest": "^2.1.3",
    "@vue/test-utils": "^2.4.6"
  }
}
```

**Step 2: vite.config.ts**

```ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
```

**Step 3: tsconfig.json, index.html, .gitignore** —— 标准 Vite Vue3 TS 模板

**Step 4: src/main.ts**

```ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import { VueQueryPlugin } from '@tanstack/vue-query'
import naive from 'naive-ui'
import App from './App.vue'
import { routes } from './router'
import './styles/global.scss'

const router = createRouter({ history: createWebHistory(), routes })

createApp(App)
  .use(createPinia())
  .use(router)
  .use(VueQueryPlugin)
  .use(naive)
  .mount('#app')
```

**Step 5: src/styles/theme.ts** —— 完整见设计文档 §8.4

**Step 6: App.vue**

```vue
<script setup lang="ts">
import { NConfigProvider, NLayout, NLayoutSider, NLayoutContent } from 'naive-ui'
import { binanceThemeOverrides } from './styles/theme'
import AppSidebar from './components/layout/AppSidebar.vue'
import AppHeader from './components/layout/AppHeader.vue'
</script>

<template>
  <n-config-provider :theme-overrides="binanceThemeOverrides">
    <n-layout has-sider class="app-root">
      <n-layout-sider :width="220" bordered><app-sidebar /></n-layout-sider>
      <n-layout>
        <app-header />
        <n-layout-content content-style="padding:20px" class="app-main">
          <router-view />
        </n-layout-content>
      </n-layout>
    </n-layout>
  </n-config-provider>
</template>

<style>
.app-root { height: 100vh; }
.app-main { background: #FAFAFA; }
</style>
```

**Step 7: src/router/index.ts**

```ts
import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  { path: '/', component: () => import('@/pages/dashboard/DashboardPage.vue') },
  { path: '/factors', component: () => import('@/pages/factors/FactorList.vue') },
  { path: '/factors/:factorId', component: () => import('@/pages/factors/FactorDetail.vue') },
  { path: '/pools', component: () => import('@/pages/pools/PoolList.vue') },
  { path: '/pools/new', component: () => import('@/pages/pools/PoolEditor.vue') },
  { path: '/pools/:poolId', component: () => import('@/pages/pools/PoolEditor.vue') },
  { path: '/evals/new', component: () => import('@/pages/evals/EvalCreate.vue') },
  { path: '/evals/:runId', component: () => import('@/pages/evals/EvalDetail.vue') },
  { path: '/backtests/new', component: () => import('@/pages/backtests/BacktestCreate.vue') },
  { path: '/backtests/:runId', component: () => import('@/pages/backtests/BacktestDetail.vue') },
  { path: '/admin', component: () => import('@/pages/admin/DataOps.vue') },
]
```

**Step 8: src/api/client.ts**

```ts
import axios from 'axios'

export const client = axios.create({ baseURL: '/api', timeout: 30_000 })

client.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body?.code === 0) return { ...resp, data: body.data }
    const err: any = new Error(body?.message || 'API error')
    err.code = body?.code; err.detail = body?.detail
    throw err
  },
  (err) => { throw err }
)
```

**Step 9: 骨架组件**

```vue
<!-- AppSidebar.vue -->
<script setup lang="ts">
import { NMenu } from 'naive-ui'
import { h } from 'vue'
import { RouterLink } from 'vue-router'
const menuOptions = [
  { label: () => h(RouterLink, { to: '/' }, { default: () => 'Dashboard' }), key: 'dash' },
  { label: () => h(RouterLink, { to: '/factors' }, { default: () => '因子库' }), key: 'factors' },
  { label: () => h(RouterLink, { to: '/pools' }, { default: () => '股票池' }), key: 'pools' },
  { label: () => h(RouterLink, { to: '/evals/new' }, { default: () => '新评估' }), key: 'evals' },
  { label: () => h(RouterLink, { to: '/backtests/new' }, { default: () => '新回测' }), key: 'backtests' },
  { label: () => h(RouterLink, { to: '/admin' }, { default: () => '数据维护' }), key: 'admin' },
]
</script>
<template><n-menu :options="menuOptions" default-value="dash" /></template>
```

**Step 10: 冒烟**

```bash
cd frontend && npm install && npm run dev
# 访问 http://localhost:5173 看到侧栏 + 空 Dashboard
```

**Step 11: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): Vite + Vue3 + Naive UI 脚手架与骨架"
```

---

## Task 11：前端核心页面（股票池 / 因子列表 / 评估创建与详情）

**Files:**
- Create: `frontend/src/api/factors.ts, pools.ts, evals.ts, backtests.ts`
- Create: `frontend/src/pages/pools/PoolList.vue`
- Create: `frontend/src/pages/pools/PoolEditor.vue`
- Create: `frontend/src/pages/factors/FactorList.vue`
- Create: `frontend/src/pages/factors/FactorDetail.vue`
- Create: `frontend/src/pages/evals/EvalCreate.vue`
- Create: `frontend/src/pages/evals/EvalDetail.vue`
- Create: `frontend/src/components/forms/ParamsFormRenderer.vue`
- Create: `frontend/src/components/forms/PoolSelector.vue`
- Create: `frontend/src/components/charts/IcSeriesChart.vue`
- Create: `frontend/src/components/charts/GroupReturnsChart.vue`
- Create: `frontend/src/components/charts/TurnoverChart.vue`
- Create: `frontend/src/components/charts/ValueHistogram.vue`
- Create: `frontend/src/components/layout/StatusBadge.vue`

**Step 1: api/factors.ts**

```ts
import { useQuery } from '@tanstack/vue-query'
import { client } from './client'
import type { Ref } from 'vue'

export interface Factor {
  factor_id: string; display_name: string; category: string
  description: string; params_schema: Record<string, any>
  default_params: Record<string, any>; supported_freqs: string[]; version?: number
}

export function useFactors() {
  return useQuery<Factor[]>({
    queryKey: ['factors'],
    queryFn: () => client.get('/factors').then(r => r.data),
  })
}

export function useFactor(factorId: Ref<string>) {
  return useQuery<Factor>({
    queryKey: ['factor', factorId],
    queryFn: () => client.get(`/factors/${factorId.value}`).then(r => r.data),
    enabled: () => !!factorId.value,
  })
}
```

**Step 2: api/evals.ts**

```ts
import { useQuery, useMutation } from '@tanstack/vue-query'
import { client } from './client'
import type { Ref } from 'vue'

export function useCreateEval() {
  return useMutation({
    mutationFn: (body: any) => client.post('/evals', body).then(r => r.data),
  })
}

export function useEvalStatus(runId: Ref<string>) {
  return useQuery({
    queryKey: ['eval-status', runId],
    queryFn: () => client.get(`/evals/${runId.value}/status`).then(r => r.data),
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
    enabled: () => !!runId.value,
  })
}

export function useEval(runId: Ref<string>) {
  return useQuery({
    queryKey: ['eval', runId],
    queryFn: () => client.get(`/evals/${runId.value}`).then(r => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
  })
}
```

**Step 3: ParamsFormRenderer.vue** —— 基于 schema 渲染

```vue
<script setup lang="ts">
import { NForm, NFormItem, NInputNumber, NInput } from 'naive-ui'
import { watch } from 'vue'

const props = defineProps<{ schema: Record<string, any>; defaults: Record<string, any> }>()
const emit = defineEmits<{ (e:'update:params', v: Record<string,any>): void }>()
const model = reactive<Record<string,any>>({ ...(props.defaults || {}) })
watch(model, v => emit('update:params', { ...v }), { deep: true, immediate: true })
</script>

<template>
  <n-form label-placement="left" label-width="120">
    <n-form-item v-for="(def, name) in schema" :key="name" :label="name">
      <n-input-number v-if="def.type==='int' || def.type==='float'"
        v-model:value="model[name]" :min="def.min" :max="def.max" :step="def.type==='int'?1:0.01" />
      <n-input v-else v-model:value="model[name]" />
    </n-form-item>
  </n-form>
</template>
```

**Step 4: EvalCreate.vue**

```vue
<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NSelect, NButton, NDatePicker, NInputNumber, NDynamicTags, NCard, NSpace } from 'naive-ui'
import { useFactors } from '@/api/factors'
import { useCreateEval } from '@/api/evals'
import ParamsFormRenderer from '@/components/forms/ParamsFormRenderer.vue'
import PoolSelector from '@/components/forms/PoolSelector.vue'

const router = useRouter(); const route = useRoute()
const { data: factors } = useFactors()
const factorId = ref<string>((route.query.factor_id as string) || '')
const selectedFactor = computed(() => factors.value?.find(f => f.factor_id === factorId.value))
const params = ref<Record<string, any>>({})
watch(selectedFactor, f => { if (f) params.value = { ...f.default_params } }, { immediate: true })

const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const forwardPeriods = ref<string[]>(['1','5','10'])
const nGroups = ref(5)

const create = useCreateEval()
async function submit() {
  if (!factorId.value || !poolId.value || !dateRange.value) return
  const [s, e] = dateRange.value
  const body = {
    factor_id: factorId.value, params: params.value,
    pool_id: poolId.value,
    start_date: new Date(s).toISOString().slice(0,10),
    end_date:   new Date(e).toISOString().slice(0,10),
    freq: '1d',
    forward_periods: forwardPeriods.value.map(Number),
    n_groups: nGroups.value,
  }
  const res: any = await create.mutateAsync(body)
  router.push(`/evals/${res.run_id}`)
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card title="新建因子评估">
      <n-form label-placement="left" label-width="120">
        <n-form-item label="因子">
          <n-select v-model:value="factorId"
            :options="(factors||[]).map(f=>({label:f.display_name+' ('+f.category+')', value:f.factor_id}))" />
        </n-form-item>
        <n-form-item label="参数" v-if="selectedFactor">
          <params-form-renderer :schema="selectedFactor.params_schema" :defaults="selectedFactor.default_params"
                                @update:params="v=>params=v" />
        </n-form-item>
        <n-form-item label="股票池"><pool-selector v-model:value="poolId" /></n-form-item>
        <n-form-item label="日期区间"><n-date-picker v-model:value="dateRange" type="daterange" /></n-form-item>
        <n-form-item label="前瞻期（日）"><n-dynamic-tags v-model:value="forwardPeriods" /></n-form-item>
        <n-form-item label="分组数"><n-input-number v-model:value="nGroups" :min="2" :max="20" /></n-form-item>
      </n-form>
      <n-button type="primary" round @click="submit" :loading="create.isPending.value">开始评估</n-button>
    </n-card>
  </n-space>
</template>
```

**Step 5: EvalDetail.vue + 四个图表组件（ECharts 封装）**

```vue
<!-- IcSeriesChart.vue -->
<script setup lang="ts">
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent, DataZoomComponent } from 'echarts/components'
use([CanvasRenderer, LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent, DataZoomComponent])

const props = defineProps<{ series: { dates:string[], values:(number|null)[] }, title?:string }>()
const option = computed(() => ({
  title: { text: props.title || 'IC 序列', left: 'center', textStyle:{fontSize:14} },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: props.series.dates, axisLabel:{fontSize:10} },
  yAxis: { type: 'value' },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16 }],
  series: [
    { name:'IC', type:'bar', data: props.series.values, itemStyle:{color:'#F0B90B'} },
    { name:'累计 IC', type:'line', data: cumsum(props.series.values), smooth: true, yAxisIndex: 0 }
  ],
}))
function cumsum(xs:(number|null)[]) { let s=0; return xs.map(x => (s += (x??0))) }
</script>
<template><v-chart :option="option" autoresize style="height:280px" /></template>
```

类似实现 `GroupReturnsChart`（多条线，各组累计净值）、`TurnoverChart`（单线）、`ValueHistogram`（bar）。

```vue
<!-- EvalDetail.vue 骨架 -->
<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed } from 'vue'
import { NCard, NProgress, NGrid, NGi, NSpace, NButton, NDescriptions, NDescriptionsItem } from 'naive-ui'
import { useEval } from '@/api/evals'
import IcSeriesChart from '@/components/charts/IcSeriesChart.vue'
import GroupReturnsChart from '@/components/charts/GroupReturnsChart.vue'
import TurnoverChart from '@/components/charts/TurnoverChart.vue'
import ValueHistogram from '@/components/charts/ValueHistogram.vue'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const route = useRoute(); const router = useRouter()
const runId = computed(() => route.params.runId as string)
const { data: run } = useEval(runId)
const payload = computed(() => run.value?.metrics?.payload)
</script>

<template>
  <n-space vertical :size="16">
    <n-card>
      <n-space align="center">
        <h2 style="margin:0">评估 {{ runId.slice(0,8) }}</h2>
        <status-badge :status="run?.status || 'pending'" />
      </n-space>
      <n-progress v-if="run && (run.status==='pending'||run.status==='running')"
                  :percentage="run.progress" status="warning" :show-indicator="true" />
    </n-card>

    <n-grid :x-gap="16" :y-gap="16" :cols="3" responsive="screen" v-if="payload">
      <n-gi><ic-series-chart :series="payload.ic['1']" title="IC (1d)" /></n-gi>
      <n-gi><ic-series-chart :series="payload.rank_ic['1']" title="Rank IC (1d)" /></n-gi>
      <n-gi><turnover-chart :series="payload.turnover_series" /></n-gi>
      <n-gi><group-returns-chart :data="payload.group_returns" /></n-gi>
      <n-gi><ic-series-chart :series="payload.long_short_equity" title="多空净值" /></n-gi>
      <n-gi><value-histogram :data="payload.value_hist" /></n-gi>
    </n-grid>

    <n-card title="结构化指标" v-if="run?.metrics">
      <n-descriptions :column="4" bordered>
        <n-descriptions-item label="IC 均值">{{ run.metrics.ic_mean?.toFixed(4) }}</n-descriptions-item>
        <n-descriptions-item label="IC IR">{{ run.metrics.ic_ir?.toFixed(3) }}</n-descriptions-item>
        <n-descriptions-item label="IC 胜率">{{ (run.metrics.ic_win_rate*100)?.toFixed(1) }}%</n-descriptions-item>
        <n-descriptions-item label="Rank IC 均值">{{ run.metrics.rank_ic_mean?.toFixed(4) }}</n-descriptions-item>
        <n-descriptions-item label="多空 Sharpe">{{ run.metrics.long_short_sharpe?.toFixed(2) }}</n-descriptions-item>
        <n-descriptions-item label="多空年化">{{ (run.metrics.long_short_annret*100)?.toFixed(2) }}%</n-descriptions-item>
        <n-descriptions-item label="平均换手">{{ (run.metrics.turnover_mean*100)?.toFixed(2) }}%</n-descriptions-item>
      </n-descriptions>
    </n-card>

    <n-space>
      <n-button round type="primary"
        @click="router.push(`/backtests/new?factor_id=${run?.factor_id}&prefill_eval=${runId}`)"
        :disabled="run?.status!=='success'">
        拿这套参数去回测
      </n-button>
    </n-space>
  </n-space>
</template>
```

**Step 6: 股票池页面（PoolList / PoolEditor）**

```vue
<!-- PoolEditor.vue 骨架 -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NInput, NButton, NInputGroup, NList, NListItem, NSpace } from 'naive-ui'
import { client } from '@/api/client'

const route = useRoute(); const router = useRouter()
const poolId = route.params.poolId as string | undefined
const name = ref(''); const description = ref(''); const symbols = ref<string[]>([])
const pasteText = ref('')

async function load() {
  if (!poolId) return
  const { data } = await client.get(`/pools/${poolId}`)
  name.value = data.pool_name; description.value = data.description || ''
  symbols.value = (data.symbols || []).map((s:any)=>s.symbol)
}
load()

async function save() {
  const body = { name: name.value, description: description.value, symbols: symbols.value }
  if (poolId) await client.put(`/pools/${poolId}`, body)
  else        await client.post('/pools', body)
  router.push('/pools')
}

async function importText() {
  if (!poolId || !pasteText.value.trim()) return
  await client.post(`/pools/${poolId}:import`, { text: pasteText.value })
  pasteText.value = ''; await load()
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="poolId ? '编辑股票池' : '新建股票池'">
      <n-space vertical>
        <n-input v-model:value="name" placeholder="名称" />
        <n-input v-model:value="description" placeholder="描述" type="textarea" :rows="2"/>
        <div v-if="poolId">
          <n-input v-model:value="pasteText" type="textarea" :rows="5"
                   placeholder="批量粘贴：每行或以空格/逗号分隔的股票代码（如 000001.SZ）" />
          <n-button @click="importText" style="margin-top:8px">批量导入</n-button>
        </div>
        <n-list bordered v-if="symbols.length" style="max-height:300px;overflow:auto">
          <n-list-item v-for="s in symbols" :key="s">{{ s }}</n-list-item>
        </n-list>
        <n-button type="primary" round @click="save">保存</n-button>
      </n-space>
    </n-card>
  </n-space>
</template>
```

**Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): 因子列表/评估创建+详情/股票池管理页"
```

---

## Task 12：回测前端 + Dashboard + DataOps + Docker Compose + 冒烟

**Files:**
- Create: `frontend/src/pages/backtests/BacktestCreate.vue`
- Create: `frontend/src/pages/backtests/BacktestDetail.vue`
- Create: `frontend/src/components/charts/EquityCurveChart.vue`
- Create: `frontend/src/pages/dashboard/DashboardPage.vue`（完善）
- Create: `frontend/src/pages/admin/DataOps.vue`
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Create: `docker-compose.yml`（项目根目录）
- Create: `README.md`
- Create: `backend/tests/test_e2e_smoke.py`

**Step 1: 回测前端**（结构与 EvalCreate/EvalDetail 类似，增加 `position / rebalance_period / cost_bps / init_cash` 字段）

**Step 2: EquityCurveChart.vue** —— 净值曲线 + drawdown 叠加

```vue
<script setup lang="ts">
import VChart from 'vue-echarts'
const props = defineProps<{ equity: {dates:string[], values:(number|null)[]} }>()
// 画 equity line + 右轴 drawdown area
const option = computed(() => {
  const dd = computeDD(props.equity.values)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['净值','回撤'], top: 0 },
    grid: { top: 30 },
    xAxis: { type:'category', data: props.equity.dates },
    yAxis: [{ type:'value', name:'净值' },
            { type:'value', name:'回撤', max:0, inverse:true, axisLabel:{formatter:'{value}%'} }],
    series: [
      { name:'净值', type:'line', data: props.equity.values, itemStyle:{color:'#F0B90B'}, smooth:true },
      { name:'回撤', type:'line', data: dd, yAxisIndex:1, areaStyle:{color:'#F6465D', opacity:0.15},
        lineStyle:{color:'#F6465D'} },
    ],
  }
})
function computeDD(xs:(number|null)[]):number[]{
  let peak=-Infinity; const out:number[]=[]
  for(const x of xs){ const v=x??peak; peak=Math.max(peak,v); out.push(peak>0?((v-peak)/peak*100):0) }
  return out
}
</script>
```

**Step 3: Dashboard 完善** —— 最近 5 个评估 + 最近 5 个回测 + 关键数字卡（已有因子数/股票池数等）

**Step 4: DataOps 页面** —— 按钮触发 `POST /api/admin/bar_1d:aggregate` 和 `/api/admin/qfq:import`

**Step 5: backend/Dockerfile**

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Step 6: frontend/Dockerfile (multi-stage)**

```dockerfile
FROM node:20 AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**Step 7: frontend/nginx.conf**

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://backend:8000;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

**Step 8: docker-compose.yml（根目录）**

```yaml
services:
  backend:
    build: ./backend
    env_file: ./backend/.env
    ports: ["8000:8000"]
    volumes:
      - ./data:/app/data
  frontend:
    build: ./frontend
    ports: ["80:80"]
    depends_on: [backend]
```

**Step 9: README.md** —— 一页上手指南

**Step 10: E2E 冒烟测试 test_e2e_smoke.py**

```python
@pytest.mark.e2e
def test_minimal_eval_pipeline(bar_1d_seeded, pool_seeded):
    """构造 30 天数据 + 一个股票池 + 跑 reversal_n 评估，确认 run 能进入 success"""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    import time
    c = TestClient(app)
    r = c.post("/api/evals", json={
        "factor_id": "reversal_n", "params": {"window": 5},
        "pool_id": 1, "start_date":"2024-01-15","end_date":"2024-01-30",
        "freq":"1d", "forward_periods":[1,3], "n_groups":3,
    })
    rid = r.json()["data"]["run_id"]
    for _ in range(60):
        s = c.get(f"/api/evals/{rid}/status").json()["data"]["status"]
        if s in ("success","failed"): break
        time.sleep(1)
    assert s == "success"
    detail = c.get(f"/api/evals/{rid}").json()["data"]
    assert detail["metrics"]["payload"]["ic"]["1"]["values"]
```

**Step 11: 手动端到端冒烟**

```bash
# 1. 初始化
cd backend && python -m scripts.run_init
python -m scripts.import_qfq --file-path ../data/merged_adjust_factors.parquet
python -m scripts.aggregate_bar_1d full --start 2023-01-01 --end 2026-04-15

# 2. 启动
uvicorn api.main:app --reload --port 8000 &
cd ../frontend && npm run dev

# 3. 浏览器：
#   - 建股票池
#   - 新评估 (reversal_n)
#   - 观察 EvalDetail 页面进度 + 图表
#   - 从评估跳回测
```

**Step 12: Commit**

```bash
git add frontend/ backend/Dockerfile docker-compose.yml README.md backend/tests/test_e2e_smoke.py
git commit -m "feat: 回测前端 + Dashboard + DataOps + docker-compose + E2E 冒烟"
```

---

## 收尾检查清单

完成所有 Task 后，逐项核对：

- [ ] 所有 12 个 Task 各自独立 commit
- [ ] `pytest backend/tests -m 'not integration and not e2e'` 全过
- [ ] 本地测试库上 `pytest -m integration` 通过
- [ ] `npm run build` 前端构建无错
- [ ] 浏览器走通：建池 → 评估 → 详情 → 回测 → 详情 → 导出
- [ ] `docker-compose up -d` 能正常启动
- [ ] `docs/plans/2026-04-16-factor-research-design.md` 与实现一致，不一致处更新文档并 commit

## 参考资料

- 设计文档：`docs/plans/2026-04-16-factor-research-design.md`
- 可复用代码来源：
  - `timing_driven_backtest/backend/adjust_factor_importer.py`
  - `timing_driven_backtest/backend/clickhouse_bar_reader.py`
  - `timing_driven_backtest/backend/stock_pool_manager.py`
  - `timing_driven_backtest/backend/qfq_factor_reader.py`
- VectorBT 文档：https://vectorbt.dev/
- Naive UI：https://www.naiveui.com/
- @tanstack/vue-query：https://tanstack.com/query/latest
