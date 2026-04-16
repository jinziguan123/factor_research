"""``backend.scripts.import_qfq`` 的集成测试。

测试意图：
- **不触碰** 真实的大 parquet（``data/merged_adjust_factors.parquet``）；用 fixture 造一个
  只含 5 列 × 3 行的 tiny parquet，确保跑得快、数据可预测。
- 验证：
  1. 未知 symbol（``999999.XX`` / ``888888.YY``）被跳过，``skipped_count == 2``；
  2. 已知 symbol 写入 MySQL，行数 = 3 只 × 3 日 = 9；
  3. 重复跑一次（``ON DUPLICATE KEY UPDATE``）后行数仍然 9，幂等。
"""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def tiny_qfq_parquet(tmp_path):
    """构造 tiny 宽表 parquet：行=日期索引，列=股票代码，值=因子。

    5 列中 3 只在 stock_symbol seed 里（sid=1,2,3），另 2 只是未知代码，
    用来校验 run_import 的 skip 分支。
    """
    df = pd.DataFrame(
        {
            "000001.SZ": [1.0, 1.0, 0.5],
            "000002.SZ": [1.0, 1.0, 1.0],
            "600000.SH": [2.0, 2.0, 1.0],
            "999999.XX": [1.0, 1.0, 1.0],  # 未知 → 跳过
            "888888.YY": [1.0, 1.0, 1.0],  # 未知 → 跳过
        },
        index=pd.to_datetime(["2024-02-01", "2024-02-02", "2024-02-05"]),
    )
    df.index.name = "trade_date"
    path = tmp_path / "tiny.parquet"
    df.to_parquet(path)
    return path


@pytest.fixture
def clean_fr_qfq_for_test():
    """用例前后清理 fr_qfq_factor 中测试窗口的数据，避免污染其它用例。"""
    from backend.storage.mysql_client import mysql_conn

    sql = (
        "DELETE FROM fr_qfq_factor WHERE symbol_id IN (1,2,3) "
        "AND trade_date BETWEEN '2024-02-01' AND '2024-02-05'"
    )
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql)
        c.commit()
    yield
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql)
        c.commit()


@pytest.mark.integration
def test_import_qfq_skips_unknown_symbols(tiny_qfq_parquet, clean_fr_qfq_for_test):
    from backend.scripts.import_qfq import run_import

    result = run_import(tiny_qfq_parquet, chunk_size=2)
    assert result["skipped_count"] == 2
    assert result["symbol_count"] == 3
    assert result["row_count"] == 9

    from backend.storage.mysql_client import mysql_conn

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM fr_qfq_factor WHERE symbol_id IN (1,2,3) "
                "AND trade_date BETWEEN '2024-02-01' AND '2024-02-05'"
            )
            assert cur.fetchone()["n"] == 9


@pytest.mark.integration
def test_import_qfq_idempotent(tiny_qfq_parquet, clean_fr_qfq_for_test):
    from backend.scripts.import_qfq import run_import

    run_import(tiny_qfq_parquet, chunk_size=2)
    run_import(tiny_qfq_parquet, chunk_size=2)

    from backend.storage.mysql_client import mysql_conn

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM fr_qfq_factor WHERE symbol_id IN (1,2,3) "
                "AND trade_date BETWEEN '2024-02-01' AND '2024-02-05'"
            )
            assert cur.fetchone()["n"] == 9
