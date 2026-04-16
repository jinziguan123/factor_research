"""``backend.scripts.aggregate_bar_1d`` 的集成测试。

用例构造 5 根分钟 K 线（10:51..10:55，对应 minute_slot 571..575），期望聚合结果：

- ``open``  = 10.0（``argMin(open, minute_slot)``，取最小 slot 的 open）
- ``high``  = 10.6（所有 high 的 max）
- ``low``   = 9.9（所有 low 的 min）
- ``close`` = 10.5（``argMax(close, minute_slot)``，取最大 slot 的 close）
- ``volume`` = 1000+1200+1100+1300+1400 = 6000
- ``amount_k`` = 100+120+110+130+140 = 600

外加幂等性：重复跑同一窗口，stock_bar_1d FINAL 仍然只有 1 行。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest


@pytest.fixture
def clean_bar_1m_and_1d():
    """清空测试窗口内的 1m / 1d 数据（进入 + 退出）。

    注意：ALTER TABLE ... DELETE 在 ClickHouse 中是异步的，但在后续 seed 前足够稳定；
    SELECT 读时仍必须带 FINAL 才能看到删除的效果。
    """
    from backend.storage.clickhouse_client import ch_client

    with ch_client() as ch:
        ch.execute(
            "ALTER TABLE quant_data.stock_bar_1m DELETE "
            "WHERE symbol_id=1 AND trade_date BETWEEN '2024-03-01' AND '2024-03-05'"
        )
        ch.execute(
            "ALTER TABLE quant_data.stock_bar_1d DELETE "
            "WHERE symbol_id=1 AND trade_date BETWEEN '2024-03-01' AND '2024-03-05'"
        )
    yield
    with ch_client() as ch:
        ch.execute(
            "ALTER TABLE quant_data.stock_bar_1m DELETE "
            "WHERE symbol_id=1 AND trade_date BETWEEN '2024-03-01' AND '2024-03-05'"
        )
        ch.execute(
            "ALTER TABLE quant_data.stock_bar_1d DELETE "
            "WHERE symbol_id=1 AND trade_date BETWEEN '2024-03-01' AND '2024-03-05'"
        )


@pytest.fixture
def seed_bar_1m(clean_bar_1m_and_1d):
    """往 stock_bar_1m 插 5 根分钟 K 线（symbol_id=1, 2024-03-04）。"""
    from backend.storage.clickhouse_client import ch_client

    d = date(2024, 3, 4)
    rows = [
        (1, d, 571, 10.0, 10.2, 9.9, 10.1, 1000, 100, 1_700_000_000),
        (1, d, 572, 10.1, 10.3, 10.0, 10.2, 1200, 120, 1_700_000_001),
        (1, d, 573, 10.2, 10.4, 10.1, 10.3, 1100, 110, 1_700_000_002),
        (1, d, 574, 10.3, 10.5, 10.2, 10.4, 1300, 130, 1_700_000_003),
        (1, d, 575, 10.4, 10.6, 10.3, 10.5, 1400, 140, 1_700_000_004),
    ]
    # ch_client() 开启了 use_numpy=True，此时 INSERT 只能走 columnar 格式；
    # 把行式 rows 转成按列的 ndarray 列表，dtype 需匹配 stock_bar_1m 的物理类型。
    cols = list(zip(*rows))
    columns_np = [
        np.asarray(cols[0], dtype=np.uint32),  # symbol_id
        np.asarray(cols[1], dtype=object),     # trade_date (datetime.date)
        np.asarray(cols[2], dtype=np.uint16),  # minute_slot
        np.asarray(cols[3], dtype=np.float32), # open
        np.asarray(cols[4], dtype=np.float32), # high
        np.asarray(cols[5], dtype=np.float32), # low
        np.asarray(cols[6], dtype=np.float32), # close
        np.asarray(cols[7], dtype=np.uint32),  # volume
        np.asarray(cols[8], dtype=np.uint32),  # amount_k
        np.asarray(cols[9], dtype=np.uint64),  # version
    ]
    with ch_client() as ch:
        ch.execute(
            "INSERT INTO quant_data.stock_bar_1m "
            "(symbol_id, trade_date, minute_slot, open, high, low, close, "
            "volume, amount_k, version) VALUES",
            columns_np,
            columnar=True,
        )
        # 立刻合并，保证后续 SELECT FINAL 稳定读到这批数据。
        ch.execute("OPTIMIZE TABLE quant_data.stock_bar_1m FINAL")


@pytest.mark.integration
def test_aggregate_bar_1d_basic(seed_bar_1m):
    from backend.scripts.aggregate_bar_1d import aggregate

    aggregate(date(2024, 3, 1), date(2024, 3, 5))

    from backend.storage.clickhouse_client import ch_client

    with ch_client() as ch:
        rows = ch.execute(
            "SELECT open, high, low, close, volume, amount_k "
            "FROM quant_data.stock_bar_1d FINAL "
            "WHERE symbol_id=1 AND trade_date='2024-03-04'"
        )
    assert len(rows) == 1
    o, h, l, c, v, a = rows[0]
    assert o == pytest.approx(10.0, abs=1e-3)
    assert h == pytest.approx(10.6, abs=1e-3)
    assert l == pytest.approx(9.9, abs=1e-3)
    assert c == pytest.approx(10.5, abs=1e-3)
    assert v == 1000 + 1200 + 1100 + 1300 + 1400
    assert a == 100 + 120 + 110 + 130 + 140


@pytest.mark.integration
def test_aggregate_bar_1d_idempotent(seed_bar_1m):
    from backend.scripts.aggregate_bar_1d import aggregate

    aggregate(date(2024, 3, 1), date(2024, 3, 5))
    aggregate(date(2024, 3, 1), date(2024, 3, 5))

    from backend.storage.clickhouse_client import ch_client

    with ch_client() as ch:
        rows = ch.execute(
            "SELECT count() FROM quant_data.stock_bar_1d FINAL "
            "WHERE symbol_id=1 AND trade_date='2024-03-04'"
        )
    assert rows[0][0] == 1
