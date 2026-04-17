"""API Pydantic schemas 的单测（不触达 DB / network）。"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.api.schemas import CreateEvalIn


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
