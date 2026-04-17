"""API Pydantic schemas 的单测（不触达 DB / network）。"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.api.schemas import (
    CreateCompositionIn,
    CreateCostSensitivityIn,
    CreateEvalIn,
)


def _base_body(**overrides):
    """构造最小合法 CreateEvalIn 入参 dict，调用处用 overrides 覆盖特定字段。"""
    body = {
        "factor_id": "reversal_n",
        "pool_id": 1,
        "start_date": date(2024, 1, 10),
        "end_date": date(2024, 1, 31),
    }
    body.update(overrides)
    return body


def test_create_eval_without_split_date_is_valid():
    """split_date 缺省 → None，和老评估行为一致，不应报错。"""
    m = CreateEvalIn(**_base_body())
    assert m.split_date is None


def test_create_eval_with_valid_split_date():
    """split_date 严格位于 (start, end) 内应通过。"""
    m = CreateEvalIn(**_base_body(split_date=date(2024, 1, 20)))
    assert m.split_date == date(2024, 1, 20)


def test_create_eval_rejects_split_date_equal_to_start():
    """split_date == start_date → 训练段为空，应拒绝。"""
    with pytest.raises(ValidationError):
        CreateEvalIn(**_base_body(split_date=date(2024, 1, 10)))


def test_create_eval_rejects_split_date_equal_to_end():
    """split_date == end_date → 测试段为空，应拒绝。"""
    with pytest.raises(ValidationError):
        CreateEvalIn(**_base_body(split_date=date(2024, 1, 31)))


def test_create_eval_rejects_split_date_before_start():
    """split_date 早于 start_date → 训练段完全为空，应拒绝。"""
    with pytest.raises(ValidationError):
        CreateEvalIn(**_base_body(split_date=date(2024, 1, 1)))


def test_create_eval_rejects_split_date_after_end():
    """split_date 晚于 end_date → 测试段完全为空，应拒绝。"""
    with pytest.raises(ValidationError):
        CreateEvalIn(**_base_body(split_date=date(2024, 2, 15)))


# ---------------------------- CreateCostSensitivityIn ----------------------------


def _cs_base_body(**overrides):
    """构造最小合法 CreateCostSensitivityIn 入参 dict。"""
    body = {
        "factor_id": "reversal_n",
        "pool_id": 1,
        "start_date": date(2024, 1, 10),
        "end_date": date(2024, 1, 31),
        "cost_bps_list": [0.0, 3.0, 10.0],
    }
    body.update(overrides)
    return body


def test_cost_sensitivity_valid_minimum():
    """最小合法入参（3 个点，默认其它字段）应通过。"""
    m = CreateCostSensitivityIn(**_cs_base_body())
    assert m.cost_bps_list == [0.0, 3.0, 10.0]
    assert m.n_groups == 5  # 默认


def test_cost_sensitivity_rejects_single_point():
    """单点列表没意义（应走单次 backtest），min_length=2 拒绝。"""
    with pytest.raises(ValidationError):
        CreateCostSensitivityIn(**_cs_base_body(cost_bps_list=[3.0]))


def test_cost_sensitivity_rejects_empty_list():
    """空列表同样拒绝。"""
    with pytest.raises(ValidationError):
        CreateCostSensitivityIn(**_cs_base_body(cost_bps_list=[]))


def test_cost_sensitivity_rejects_negative():
    """负费率没有物理意义，应拒绝。"""
    with pytest.raises(ValidationError):
        CreateCostSensitivityIn(**_cs_base_body(cost_bps_list=[-1.0, 3.0]))


def test_cost_sensitivity_rejects_unreasonable_large():
    """>200bp 通常是单位搞错（把 0.03 传成 300），拒绝。"""
    with pytest.raises(ValidationError):
        CreateCostSensitivityIn(**_cs_base_body(cost_bps_list=[3.0, 300.0]))


def test_cost_sensitivity_rejects_more_than_20_points():
    """曲线上点超过 20 个已失去分辨力，max_length=20 拒绝。"""
    many = [float(i) for i in range(21)]
    with pytest.raises(ValidationError):
        CreateCostSensitivityIn(**_cs_base_body(cost_bps_list=many))


# ---------------------------- CreateCompositionIn ----------------------------


def _comp_base_body(**overrides):
    """构造最小合法 CreateCompositionIn 入参 dict。"""
    body = {
        "pool_id": 1,
        "start_date": date(2024, 1, 10),
        "end_date": date(2024, 1, 31),
        "factor_items": [
            {"factor_id": "reversal_n", "params": None},
            {"factor_id": "realized_vol", "params": None},
        ],
        "method": "equal",
    }
    body.update(overrides)
    return body


def test_composition_valid_minimum():
    """最小合法入参（2 个因子，equal）应通过，默认字段符合预期。"""
    m = CreateCompositionIn(**_comp_base_body())
    assert m.method == "equal"
    assert m.n_groups == 5
    assert m.ic_weight_period == 1
    assert [it.factor_id for it in m.factor_items] == ["reversal_n", "realized_vol"]


def test_composition_rejects_single_factor():
    """< 2 因子应走 /evals，schema 拒绝。"""
    with pytest.raises(ValidationError):
        CreateCompositionIn(
            **_comp_base_body(
                factor_items=[{"factor_id": "reversal_n", "params": None}]
            )
        )


def test_composition_rejects_more_than_8_factors():
    """> 8 相关矩阵已经看不清，且正交化数值稳定性下降，max_length=8 拒绝。"""
    many = [{"factor_id": f"f{i}", "params": None} for i in range(9)]
    with pytest.raises(ValidationError):
        CreateCompositionIn(**_comp_base_body(factor_items=many))


def test_composition_rejects_duplicate_factor_id():
    """因子 id 重复会让相关矩阵出现伪 1.0 对角附近值，语义混乱，拒绝。"""
    with pytest.raises(ValidationError):
        CreateCompositionIn(
            **_comp_base_body(
                factor_items=[
                    {"factor_id": "reversal_n", "params": None},
                    {"factor_id": "reversal_n", "params": {"window": 5}},
                ]
            )
        )


def test_composition_rejects_invalid_method():
    """method 仅接受 equal/ic_weighted/orthogonal_equal，其它值拒绝。"""
    with pytest.raises(ValidationError):
        CreateCompositionIn(**_comp_base_body(method="pca"))


def test_composition_rejects_start_ge_end():
    """start_date >= end_date 没有样本可用，拒绝。"""
    with pytest.raises(ValidationError):
        CreateCompositionIn(
            **_comp_base_body(
                start_date=date(2024, 1, 31),
                end_date=date(2024, 1, 10),
            )
        )
    with pytest.raises(ValidationError):
        CreateCompositionIn(
            **_comp_base_body(
                start_date=date(2024, 1, 20),
                end_date=date(2024, 1, 20),
            )
        )


def test_composition_ic_weight_period_bounds():
    """ic_weight_period ∈ [1, 20]。"""
    with pytest.raises(ValidationError):
        CreateCompositionIn(**_comp_base_body(ic_weight_period=0))
    with pytest.raises(ValidationError):
        CreateCompositionIn(**_comp_base_body(ic_weight_period=21))
