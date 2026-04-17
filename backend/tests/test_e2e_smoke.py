"""端到端 smoke 测试：一条用例跑通 eval → backtest 完整链路。

目的：作为部署 / 合并前的"管道断没断"哨兵，不追求因子指标数值的合理性。
断言只覆盖流程能走完 + 关键字段存在，避免随 seed 数据变化而频繁 flake。

依赖：
- 本地 docker-compose-test 已启动（MySQL + ClickHouse）；
- ``stock_symbol`` 表已预种 symbol_id 1..5（由部署脚本负责，与 conftest.py 约定一致）；
- ``seed_bar_1d`` 会往 ClickHouse 灌 5 只股票 × 22 个交易日（2024-01-02 ~ 2024-02-01）。

因子选择：``reversal_n`` with ``window=3``，预热短，能在 seed 的 22 天内给出有效值。
n_groups=3：与 5 只股票的池搭配，qcut 不会轻易退化成空组。
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest


# ---------------------------- 本文件专用 fixture ----------------------------


@pytest.fixture
def smoke_pool_id():
    """建一个包含 symbol_id 1..5 的临时 stock_pool，用完删除。

    pool_name 用 UUID 前缀保证并行跑测试也不互相冲突。
    """
    from backend.storage.mysql_client import mysql_conn

    pool_name = f"__fr_smoke_{uuid.uuid4().hex[:8]}__"
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO stock_pool (owner_key, pool_name, description) "
                "VALUES (%s, %s, %s)",
                ("factor_research", pool_name, "e2e smoke test pool"),
            )
            pool_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO stock_pool_symbol (pool_id, symbol_id, sort_order) "
                "VALUES (%s, %s, %s)",
                [(pool_id, sid, idx) for idx, sid in enumerate([1, 2, 3, 4, 5])],
            )
        c.commit()
    try:
        yield pool_id
    finally:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "DELETE FROM stock_pool_symbol WHERE pool_id=%s", (pool_id,)
                )
                cur.execute("DELETE FROM stock_pool WHERE pool_id=%s", (pool_id,))
            c.commit()


@pytest.fixture
def smoke_eval_run_id():
    """给 eval smoke 测试准备一个 run_id，并预先 INSERT 一条 pending 记录。

    真实 API 层在创建 run 时会先写一条 pending 记录，run_eval / run_backtest 只 UPDATE。
    测试里必须复现同样步骤，否则 _set_status 的 UPDATE 会悄无声息地影响 0 行。

    teardown 时删除 fr_factor_eval_runs + fr_factor_eval_metrics 两张表对应行。
    """
    from backend.storage.mysql_client import mysql_conn

    run_id = uuid.uuid4().hex
    # factor_version / params_hash 此时还没固化，先占位，run_eval 过程中的列不会动它们。
    # 这两列是 API 层的 INSERT 默认行为：先写 0 / 占位 hash，让 run 表有完整的 PRIMARY KEY。
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_factor_eval_runs
                (run_id, factor_id, factor_version, params_hash, params_json,
                 pool_id, freq, start_date, end_date, forward_periods, n_groups,
                 status, progress, created_at)
                VALUES (%s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s)
                """,
                (
                    run_id, "reversal_n", 0, "0" * 40, "{}",
                    0, "1d", "2024-01-10", "2024-01-31", "1", 3,
                    "pending", 0, datetime.now(),
                ),
            )
        c.commit()
    try:
        yield run_id
    finally:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "DELETE FROM fr_factor_eval_metrics WHERE run_id=%s", (run_id,)
                )
                cur.execute(
                    "DELETE FROM fr_factor_eval_runs WHERE run_id=%s", (run_id,)
                )
            c.commit()


@pytest.fixture
def smoke_backtest_run_id():
    """类似 ``smoke_eval_run_id``，但走 fr_backtest_* 三张表 + data/artifacts/<run_id>/。"""
    from backend.config import settings
    from backend.storage.mysql_client import mysql_conn

    run_id = uuid.uuid4().hex
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_backtest_runs
                (run_id, factor_id, factor_version, params_hash, params_json,
                 pool_id, freq, start_date, end_date,
                 status, progress, created_at)
                VALUES (%s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s)
                """,
                (
                    run_id, "reversal_n", 0, "0" * 40, "{}",
                    0, "1d", "2024-01-10", "2024-01-31",
                    "pending", 0, datetime.now(),
                ),
            )
        c.commit()
    try:
        yield run_id
    finally:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "DELETE FROM fr_backtest_artifacts WHERE run_id=%s", (run_id,)
                )
                cur.execute(
                    "DELETE FROM fr_backtest_metrics WHERE run_id=%s", (run_id,)
                )
                cur.execute(
                    "DELETE FROM fr_backtest_runs WHERE run_id=%s", (run_id,)
                )
            c.commit()
        artifact_dir = Path(settings.artifact_dir) / run_id
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)


# ---------------------------- 实际用例 ----------------------------


@pytest.mark.integration
def test_eval_smoke_end_to_end(
    seed_bar_1d, smoke_pool_id, smoke_eval_run_id
):
    """跑完整 ``run_eval``，断言 status=success + 指标写入 + payload 关键字段齐。"""
    import json

    from backend.services.eval_service import run_eval
    from backend.storage.mysql_client import mysql_conn

    body = {
        "factor_id": "reversal_n",
        "pool_id": smoke_pool_id,
        "start_date": "2024-01-10",
        "end_date": "2024-01-31",
        "params": {"window": 3},
        "forward_periods": [1],
        "n_groups": 3,
    }
    # 整个函数不该抛；抛的话说明管道有断。
    run_eval(smoke_eval_run_id, body)

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT status, progress, error_message "
                "FROM fr_factor_eval_runs WHERE run_id=%s",
                (smoke_eval_run_id,),
            )
            run_row = cur.fetchone()
            cur.execute(
                "SELECT ic_mean, turnover_mean, payload_json "
                "FROM fr_factor_eval_metrics WHERE run_id=%s",
                (smoke_eval_run_id,),
            )
            metric_row = cur.fetchone()

    assert run_row is not None, "run row 消失了"
    assert run_row["status"] == "success", (
        f"eval 失败：{run_row['error_message']}"
    )
    assert run_row["progress"] == 100

    assert metric_row is not None, "metrics 未写入"
    # 指标本身数值不断言（样本只有几只股票 × 十几天，IC 是噪声），
    # 只断言字段存在且 payload 结构正确。
    payload = json.loads(metric_row["payload_json"])
    # 这 6 个 key 是评估详情页 / 前端图表强依赖的，任一缺失都是回归。
    for key in ("ic", "rank_ic", "group_returns", "turnover_series",
                "value_hist", "health"):
        assert key in payload, f"payload 缺少 {key!r}"
    # health 结构里 5 项指标齐
    assert payload["health"]["overall"] in {"green", "yellow", "red"}
    assert len(payload["health"]["items"]) == 5


@pytest.mark.integration
def test_backtest_smoke_end_to_end(
    seed_bar_1d, smoke_pool_id, smoke_backtest_run_id
):
    """跑完整 ``run_backtest``，断言 status=success + metrics 写入 + artifacts 齐。"""
    import json

    from backend.services.backtest_service import run_backtest
    from backend.storage.mysql_client import mysql_conn

    body = {
        "factor_id": "reversal_n",
        "pool_id": smoke_pool_id,
        "start_date": "2024-01-10",
        "end_date": "2024-01-31",
        "params": {"window": 3},
        "n_groups": 3,
        "rebalance_period": 1,
        "position": "top",
        "cost_bps": 3.0,
        "init_cash": 1e6,
    }
    run_backtest(smoke_backtest_run_id, body)

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT status, progress, error_message "
                "FROM fr_backtest_runs WHERE run_id=%s",
                (smoke_backtest_run_id,),
            )
            run_row = cur.fetchone()
            cur.execute(
                "SELECT total_return, sharpe_ratio, payload_json "
                "FROM fr_backtest_metrics WHERE run_id=%s",
                (smoke_backtest_run_id,),
            )
            metric_row = cur.fetchone()
            cur.execute(
                "SELECT artifact_type FROM fr_backtest_artifacts "
                "WHERE run_id=%s",
                (smoke_backtest_run_id,),
            )
            artifact_types = {row["artifact_type"] for row in cur.fetchall()}

    assert run_row is not None
    assert run_row["status"] == "success", (
        f"backtest 失败：{run_row['error_message']}"
    )
    assert run_row["progress"] == 100

    assert metric_row is not None, "backtest metrics 未写入"
    # payload_json 存的是 vbt pf.stats() 转 dict 的结果（"Total Return [%]" 等）；
    # 净值曲线走 parquet 不在 json 里。这里只做"非空 dict"层级的 smoke 断言，
    # 具体 key 名会随 vbt 版本漂移，不强断定。
    payload = json.loads(metric_row["payload_json"])
    assert isinstance(payload, dict) and len(payload) > 0, (
        "backtest payload_json 为空或格式异常"
    )

    # 三类 artifact 都写了 parquet，缺一不可（前端下载 / 回放需要）
    assert artifact_types == {"equity", "orders", "trades"}, (
        f"artifact 缺失：{artifact_types}"
    )

    # 对应磁盘 parquet 也应存在
    from backend.config import settings
    run_dir = Path(settings.artifact_dir) / smoke_backtest_run_id
    for name in ("equity.parquet", "orders.parquet", "trades.parquet"):
        assert (run_dir / name).exists(), f"缺 {name}"
