"""FactorRegistry 集成测试：扫描 + MySQL 元数据写入 + 查询。

所有用例带 ``@pytest.mark.integration`` mark，需要本机起 docker-compose-test
的 MySQL，并已 ``run_init`` 建好 ``fr_factor_meta`` 表。

为什么要 autouse 清理 fr_factor_meta：
- ``_persist_meta`` 的逻辑是"已存在且 code_hash 变 → version+1"；
- 上一次跑完的记录里 code_hash 是当时源码的 hash；
- 下一次启动若源码（含行号注释）已变，会被检测为 code 变动，version 递增；
- 断言 version=1 就不稳定。
- 更一般地：factor 元数据表是单例状态，测试间必须隔离。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_factor_meta():
    """每个用例开始前清理本文件关注的 4 个 factor_id。

    仅对本文件的测试生效（避免影响其他集成测试）。
    """
    from backend.storage.mysql_client import mysql_conn

    factor_ids = (
        "reversal_n",
        "momentum_n",
        "realized_vol",
        "turnover_ratio",
    )
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_factor_meta WHERE factor_id IN %s",
                (factor_ids,),
            )
        c.commit()
    yield


def _fresh_registry():
    """构造"看似新的" registry：清空单例内部状态以获得干净起点。

    由于 FactorRegistry 是进程级单例，用例间会共享 _classes / _version。
    这里直接访问内部字段做 reset 是测试友好的妥协——生产代码不会这样做。
    """
    from backend.runtime.factor_registry import FactorRegistry

    reg = FactorRegistry()
    with reg._lock:
        reg._classes.clear()
        reg._code_hash.clear()
        reg._version.clear()
    return reg


@pytest.mark.integration
def test_scan_registers_builtins():
    """扫描后 4 个内置 factor_id 全部被注册。"""
    reg = _fresh_registry()
    updated = reg.scan_and_register("backend.factors")
    # 首次注册，4 个都应在 updated 列表里。
    expected = {"reversal_n", "momentum_n", "realized_vol", "turnover_ratio"}
    assert expected.issubset(set(updated))
    listed_ids = {item["factor_id"] for item in reg.list()}
    assert expected.issubset(listed_ids)


@pytest.mark.integration
def test_get_instance():
    """get(factor_id) 返回可直接调用的 BaseFactor 实例。"""
    reg = _fresh_registry()
    reg.scan_and_register("backend.factors")

    inst = reg.get("reversal_n")
    # required_warmup 按 params 计算：window=20 + 5 = 25。
    assert inst.required_warmup({"window": 20}) == 25
    # 类级属性被正确保留。
    assert inst.factor_id == "reversal_n"
    assert inst.category == "reversal"


@pytest.mark.integration
def test_factor_meta_persisted():
    """扫描后 fr_factor_meta 里能查到 4 条记录，且 code_hash 为 40 位 hex。"""
    from backend.storage.mysql_client import mysql_conn

    reg = _fresh_registry()
    reg.scan_and_register("backend.factors")

    factor_ids = (
        "reversal_n",
        "momentum_n",
        "realized_vol",
        "turnover_ratio",
    )
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT factor_id, code_hash, version, category "
                "FROM fr_factor_meta WHERE factor_id IN %s",
                (factor_ids,),
            )
            rows = cur.fetchall()

    assert len(rows) == 4
    for row in rows:
        # sha1 hex 长度固定 40。
        assert isinstance(row["code_hash"], str)
        assert len(row["code_hash"]) == 40
        # 每一条 code_hash 纯十六进制。
        int(row["code_hash"], 16)
        # 首次注册 version 应为 1。
        assert row["version"] == 1
        assert row["factor_id"] in factor_ids


@pytest.mark.integration
def test_rescan_same_code_is_idempotent():
    """重复扫描同一份代码 version 不递增；updated 列表为空。"""
    reg = _fresh_registry()
    first = reg.scan_and_register("backend.factors")
    assert len(first) >= 4
    second = reg.scan_and_register("backend.factors")
    # 代码未变，不应出现在 updated。
    for fid in ("reversal_n", "momentum_n", "realized_vol", "turnover_ratio"):
        assert fid not in second
        assert reg.current_version(fid) == 1
