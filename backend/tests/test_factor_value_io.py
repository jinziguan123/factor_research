"""factor_value_1d 读写集成测试。

覆盖：
1. save → load 往返：写入宽表后能按 symbol 列还原；
2. 空查询：不存在的 (factor_id, factor_version, params_hash) 返回空 DataFrame；
3. version 隔离：不同 factor_version 不会相互串扰。

测试使用固定的 ``factor_id='__test_fv__'`` 作为命名空间，前后 fixture 负责清理，
不触碰生产因子数据；``params_hash`` 采用 40 位固定字符串，满足 ``FixedString(40)``。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest


@pytest.fixture
def clean_factor_value_1d():
    """清理 ``factor_value_1d`` 中 ``factor_id='__test_fv__'`` 的数据（前后各一次）。

    ClickHouse ``ALTER TABLE ... DELETE`` 是异步 mutation，但本地单机 + 低并发
    测试库下执行完即可见；测试不依赖跨 fixture 的顺序，所以不需要 OPTIMIZE FINAL。
    """
    from backend.storage.clickhouse_client import ch_client

    with ch_client() as ch:
        # mutations_sync=2 让 DELETE 同步等 mutation 完成，避免"删除还没生效就 SELECT FINAL"
        # 导致测试之间残留脏数据（本地测试库并发低，等一下没有性能压力）。
        ch.execute(
            "ALTER TABLE quant_data.factor_value_1d DELETE WHERE factor_id=%(fid)s "
            "SETTINGS mutations_sync=2",
            {"fid": "__test_fv__"},
        )
    yield
    with ch_client() as ch:
        # mutations_sync=2 让 DELETE 同步等 mutation 完成，避免"删除还没生效就 SELECT FINAL"
        # 导致测试之间残留脏数据（本地测试库并发低，等一下没有性能压力）。
        ch.execute(
            "ALTER TABLE quant_data.factor_value_1d DELETE WHERE factor_id=%(fid)s "
            "SETTINGS mutations_sync=2",
            {"fid": "__test_fv__"},
        )


@pytest.mark.integration
def test_factor_value_roundtrip(clean_factor_value_1d):
    """写入宽表 5 天 × 2 symbol，load 出来行/列/数值必须对得上。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    idx = pd.date_range("2024-04-01", periods=5, freq="B")
    df = pd.DataFrame(
        {
            "000001.SZ": [1.0, 2.0, 3.0, 4.0, 5.0],
            "000002.SZ": [5.0, 4.0, 3.0, 2.0, 1.0],
        },
        index=idx,
    )
    n = svc.save_factor_values("__test_fv__", 1, "a" * 40, df)
    assert n == 10

    got = svc.load_factor_values(
        "__test_fv__",
        1,
        "a" * 40,
        ["000001.SZ", "000002.SZ"],
        date(2024, 3, 31),
        date(2024, 4, 30),
    )
    assert got.shape == (5, 2)
    # 数值严格按宽表对齐（index 精确匹配 idx；列名是原始 symbol）
    assert (got.loc[idx, "000001.SZ"].to_numpy() == [1, 2, 3, 4, 5]).all()
    assert (got.loc[idx, "000002.SZ"].to_numpy() == [5, 4, 3, 2, 1]).all()


@pytest.mark.integration
def test_factor_value_load_empty(clean_factor_value_1d):
    """不存在的 factor_id 查询应返回 ``DataFrame.empty==True``，不能抛。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    got = svc.load_factor_values(
        "__nonexistent__",
        1,
        "a" * 40,
        ["000001.SZ"],
        date(2024, 1, 1),
        date(2024, 1, 31),
    )
    assert got.empty


@pytest.mark.integration
def test_factor_value_version_isolation(clean_factor_value_1d):
    """不同 factor_version 互不串扰：同 symbol 同日期也能区分。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    idx = pd.date_range("2024-04-01", periods=3, freq="B")
    df1 = pd.DataFrame({"000001.SZ": [1.0, 2.0, 3.0]}, index=idx)
    df2 = pd.DataFrame({"000001.SZ": [10.0, 20.0, 30.0]}, index=idx)
    svc.save_factor_values("__test_fv__", 1, "a" * 40, df1)
    svc.save_factor_values("__test_fv__", 2, "a" * 40, df2)

    got1 = svc.load_factor_values(
        "__test_fv__", 1, "a" * 40, ["000001.SZ"], date(2024, 3, 1), date(2024, 5, 1)
    )
    got2 = svc.load_factor_values(
        "__test_fv__", 2, "a" * 40, ["000001.SZ"], date(2024, 3, 1), date(2024, 5, 1)
    )
    assert (got1["000001.SZ"].to_numpy() == [1, 2, 3]).all()
    assert (got2["000001.SZ"].to_numpy() == [10, 20, 30]).all()
