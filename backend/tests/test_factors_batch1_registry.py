"""批次 1 因子的注册集成验证。

验证：
1. 8 个新因子文件能被 import 不抛错（FactorRegistry.scan_and_register 内部 import 各
   factor 模块，import 失败会让整个 registry 启动失败）。
2. 8 个 factor_id 都在 registry 里（保证 UI 下拉能看到）。
3. 每个因子的 BaseFactor 必填 ClassVar（factor_id / display_name / category）非空。
"""
from __future__ import annotations
import pytest
from backend.engine.base_factor import BaseFactor


_BATCH1_FACTOR_IDS = {
    "alpha101_6", "alpha101_12", "alpha101_101",
    "earnings_yield", "roe_yoy", "gp_margin_stability",
    "idio_vol_reversal", "max_anomaly",
}


def test_all_batch1_factor_modules_import_cleanly():
    """8 个因子模块全部成功 import（不抛异常）。"""
    from backend.factors.alpha101 import alpha101_6, alpha101_12, alpha101_101
    from backend.factors.fundamental import earnings_yield, roe_yoy, gp_margin_stability
    from backend.factors.volatility import idio_vol_reversal, max_anomaly


def test_factor_registry_finds_all_batch1_factors():
    """FactorRegistry 扫到全部 8 个新 factor_id。"""
    from backend.runtime.factor_registry import FactorRegistry
    reg = FactorRegistry()
    # FactorRegistry 是进程级单例；清掉内存缓存以保证从干净状态扫描。
    with reg._lock:
        reg._classes.clear()
        reg._code_hash.clear()
        reg._version.clear()
    reg.scan_and_register("backend.factors")
    ids = {item["factor_id"] for item in reg.list()}
    missing = _BATCH1_FACTOR_IDS - ids
    assert not missing, f"批次 1 因子未注册：{missing}"


def test_each_batch1_factor_has_required_classvars():
    """每个新因子的 factor_id / display_name / category 都非空。"""
    from backend.factors.alpha101.alpha101_6 import Alpha101_6
    from backend.factors.alpha101.alpha101_12 import Alpha101_12
    from backend.factors.alpha101.alpha101_101 import Alpha101_101
    from backend.factors.fundamental.earnings_yield import EarningsYield
    from backend.factors.fundamental.roe_yoy import RoeYoy
    from backend.factors.fundamental.gp_margin_stability import GpMarginStability
    from backend.factors.volatility.idio_vol_reversal import IdioVolReversal
    from backend.factors.volatility.max_anomaly import MaxAnomaly

    cls_list = [Alpha101_6, Alpha101_12, Alpha101_101,
                EarningsYield, RoeYoy, GpMarginStability,
                IdioVolReversal, MaxAnomaly]
    for cls in cls_list:
        assert isinstance(cls.factor_id, str) and cls.factor_id
        assert isinstance(cls.display_name, str) and cls.display_name
        assert isinstance(cls.category, str) and cls.category
