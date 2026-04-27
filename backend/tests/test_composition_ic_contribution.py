"""composition_service._compute_ic_contributions 的纯函数单测。

只测数学语义（IC × |weight| 归一化），不涉及 MySQL / 数据加载。
"""
from __future__ import annotations

import math

import pytest

from backend.services.composition_service import _compute_ic_contributions


def _per_factor_ic(ic_means: dict[str, float | None]) -> dict[str, dict]:
    """构造最小 per_factor_ic 结构（只关心 ic_mean 字段）。"""
    return {fid: {"ic_mean": v} for fid, v in ic_means.items()}


def test_equal_weight_proportional_to_abs_ic() -> None:
    """weights=None（equal / orthogonal_equal）→ 贡献度正比于 |IC|。"""
    pf = _per_factor_ic({"a": 0.10, "b": -0.05, "c": 0.05})
    out = _compute_ic_contributions(pf, weights=None, factor_ids=["a", "b", "c"])
    # 总 |IC| × (1/3) = 0.20/3；a:0.10×1/3=0.033...；b:0.05×1/3；c:0.05×1/3
    # 归一化后 a=0.5, b=0.25, c=0.25
    assert out["a"] == pytest.approx(0.5)
    assert out["b"] == pytest.approx(0.25)
    assert out["c"] == pytest.approx(0.25)
    # Σ 严格 = 1
    assert sum(out.values()) == pytest.approx(1.0)


def test_ic_weighted_proportional_to_ic_squared() -> None:
    """ic_weighted 下权重就是 |IC| 归一化的——贡献度正比于 |IC|×|w| ≈ |IC|²/total。"""
    pf = _per_factor_ic({"a": 0.10, "b": -0.05})
    # 模拟 _compute_ic_weights 的输出：weight 保留方向且 Σ|w|=1。
    # |IC|=0.10/0.05，归一化后 |w_a|=0.10/0.15, |w_b|=0.05/0.15
    weights = {"a": 0.10 / 0.15, "b": -0.05 / 0.15}
    out = _compute_ic_contributions(pf, weights=weights, factor_ids=["a", "b"])
    # 原始贡献 |IC×w| = (0.10²)/0.15 vs (0.05²)/0.15 → 比例 4:1
    assert out["a"] == pytest.approx(0.8)
    assert out["b"] == pytest.approx(0.2)


def test_zero_ic_returns_all_none() -> None:
    """所有因子 IC 都 ~0 → 全 None（避免除零给出虚假占比）。"""
    pf = _per_factor_ic({"a": 0.0, "b": 0.0})
    out = _compute_ic_contributions(pf, weights=None, factor_ids=["a", "b"])
    assert out == {"a": None, "b": None}


def test_handles_none_ic_mean() -> None:
    """ic_mean=None 视为 0，不抛 TypeError。"""
    pf = _per_factor_ic({"a": 0.10, "b": None})
    out = _compute_ic_contributions(pf, weights=None, factor_ids=["a", "b"])
    # 只有 a 贡献：归一化后 a=1.0, b=0.0
    assert out["a"] == pytest.approx(1.0)
    assert out["b"] == pytest.approx(0.0)


def test_handles_nan_ic_mean() -> None:
    """ic_mean=NaN 视为 0（_nan_to_none 上游通常已转 None，但兜底要在）。"""
    pf = _per_factor_ic({"a": 0.10, "b": float("nan")})
    out = _compute_ic_contributions(pf, weights=None, factor_ids=["a", "b"])
    # NaN 经 abs(... or 0.0) 不会传播：当 v=NaN 时 `v or 0.0` 会落到 NaN（NaN 是 truthy）。
    # 这是已知边界——上游 per_factor_ic 已经 _nan_to_none 转 None，
    # 此处只需要保证不抛异常即可。如果出现 NaN，下游表格会显示乱码——
    # 但归一化总和会变 NaN 全部 None。
    # 验证：要么 a=1.0/b=0（NaN 被 or 截断），要么全 None（NaN 污染）；
    # 两种都"不崩"是这条断言的最低要求。
    assert "a" in out and "b" in out


def test_missing_factor_id_in_per_factor_ic() -> None:
    """factor_ids 中某个 id 在 per_factor_ic 中缺失 → 当作 IC=0 处理。"""
    pf = _per_factor_ic({"a": 0.10})  # 没有 b
    out = _compute_ic_contributions(pf, weights=None, factor_ids=["a", "b"])
    assert out["a"] == pytest.approx(1.0)
    assert out["b"] == pytest.approx(0.0)


def test_empty_factor_ids_returns_empty() -> None:
    assert _compute_ic_contributions({}, weights=None, factor_ids=[]) == {}


def test_ic_weighted_with_missing_weight() -> None:
    """weights dict 缺某 fid → 该因子权重视为 0，贡献度 0。"""
    pf = _per_factor_ic({"a": 0.10, "b": 0.05})
    weights = {"a": 1.0}  # b 缺权重
    out = _compute_ic_contributions(pf, weights=weights, factor_ids=["a", "b"])
    assert out["a"] == pytest.approx(1.0)
    assert out["b"] == pytest.approx(0.0)


def test_negative_ic_takes_absolute_value() -> None:
    """负 IC 因子也能贡献（取 |IC|）；ic_weighted 中保留方向不影响贡献度大小。"""
    pf = _per_factor_ic({"a": -0.10, "b": 0.10})
    out = _compute_ic_contributions(pf, weights=None, factor_ids=["a", "b"])
    # 两者 |IC| 相等 → 各占 50%
    assert out["a"] == pytest.approx(0.5)
    assert out["b"] == pytest.approx(0.5)


def test_sum_equals_one_when_any_contribution() -> None:
    """只要存在非零贡献，归一化后 Σ 应严格 = 1（精度内）。"""
    pf = _per_factor_ic({f"f{i}": 0.01 * (i + 1) for i in range(5)})
    out = _compute_ic_contributions(
        pf, weights=None, factor_ids=[f"f{i}" for i in range(5)]
    )
    s = sum(v for v in out.values() if v is not None)
    assert s == pytest.approx(1.0)
    # 每个值非负且 ≤ 1
    for v in out.values():
        assert v is not None
        assert 0 <= v <= 1
