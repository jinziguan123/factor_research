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


@pytest.mark.integration
def test_reload_module_picks_up_source_change():
    """修改因子源文件后 ``reload_module`` 应真正 importlib.reload，version +1。

    通过写入一个临时因子文件到 ``backend/factors/custom/_hot_reload_probe.py``
    验证端到端：首次 scan → 注册 version=1 → 改写源码 → reload_module →
    version=2。测试结束清理文件 + sys.modules + MySQL 行。

    为什么不用 tmp_path：``FactorRegistry.scan_and_register`` 走的是
    ``pkgutil.walk_packages(backend.factors.__path__)``，只能扫到真实包目录
    下的文件；放到 tmp_path 里 registry 根本看不见。
    """
    import importlib
    import sys
    from pathlib import Path

    from backend.runtime.factor_registry import FactorRegistry
    from backend.storage.mysql_client import mysql_conn

    factors_root = Path(
        importlib.import_module("backend.factors").__path__[0]
    )
    probe_path = factors_root / "custom" / "_hot_reload_probe.py"
    mod_name = "backend.factors.custom._hot_reload_probe"
    factor_id = "_hot_reload_probe"

    # 源码 v1：description 里带 "v1" 标识以保证 hash 不同于 v2。
    src_v1 = (
        '"""Hot reload probe factor v1."""\n'
        "from __future__ import annotations\n\n"
        "import pandas as pd\n\n"
        "from backend.factors.base import BaseFactor, FactorContext\n\n\n"
        "class HotReloadProbe(BaseFactor):\n"
        f'    factor_id = "{factor_id}"\n'
        '    display_name = "Hot Reload Probe"\n'
        '    category = "custom"\n'
        '    description = "v1"\n'
        "    params_schema = {}\n"
        "    default_params = {}\n"
        '    supported_freqs = ("1d",)\n\n'
        "    def required_warmup(self, params: dict) -> int:\n"
        "        return 1\n\n"
        "    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:\n"
        "        return pd.DataFrame()\n"
    )
    src_v2 = src_v1.replace('description = "v1"', 'description = "v2"')

    try:
        probe_path.write_text(src_v1, encoding="utf-8")
        reg = _fresh_registry()
        updated_first = reg.scan_and_register("backend.factors")
        assert factor_id in updated_first
        assert reg.current_version(factor_id) == 1

        # 改写源码：必须真的写到文件系统，inspect.getsource 才能读到新内容。
        probe_path.write_text(src_v2, encoding="utf-8")

        updated_second = reg.reload_module(mod_name)
        # reload_module 返回 list 类型是弱保证，真正关键：
        # - reload 真的发生 → inspect.getsource 看到 v2 → code_hash 变 → version+1
        assert isinstance(updated_second, list)
        assert factor_id in updated_second
        assert reg.current_version(factor_id) == 2
    finally:
        # 清理磁盘文件 + sys.modules 缓存 + MySQL 行，避免污染后续测试。
        if probe_path.exists():
            probe_path.unlink()
        sys.modules.pop(mod_name, None)
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "DELETE FROM fr_factor_meta WHERE factor_id = %s",
                    (factor_id,),
                )
            c.commit()
