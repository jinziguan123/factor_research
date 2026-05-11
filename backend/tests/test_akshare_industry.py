"""申万行业适配器单测——通过 mock 避免真实网络调用。"""
from __future__ import annotations

import types
from unittest.mock import patch

import pandas as pd
import pytest


# --------------- fake data ---------------

def _fake_l1_info():
    return pd.DataFrame({
        "行业代码": ["801010.SI", "801080.SI"],
        "行业名称": ["农林牧渔", "电子"],
        "成份个数": [50, 200],
        "静态市盈率": [30.0, 45.0],
        "TTM(滚动)市盈率": [28.0, 42.0],
        "市净率": [3.0, 5.0],
        "静态股息率": [1.5, 0.8],
    })


def _fake_l2_info():
    return pd.DataFrame({
        "行业代码": ["801011.SI", "801081.SI"],
        "行业名称": ["种植业", "半导体"],
        "上级行业": ["农林牧渔", "电子"],
        "成份个数": [20, 80],
        "静态市盈率": [25.0, 50.0],
        "TTM(滚动)市盈率": [23.0, 48.0],
        "市净率": [2.5, 6.0],
        "静态股息率": [2.0, 0.5],
    })


def _fake_l3_info():
    return pd.DataFrame({
        "行业代码": ["850111.SI", "850811.SI"],
        "行业名称": ["粮食种植", "集成电路设计"],
        "上级行业": ["种植业", "半导体"],
        "成份个数": [10, 40],
        "静态市盈率": [20.0, 55.0],
        "TTM(滚动)市盈率": [18.0, 52.0],
        "市净率": [2.0, 7.0],
        "静态股息率": [2.5, 0.3],
    })


def _fake_component(symbol: str):
    data = {
        "801010": pd.DataFrame({
            "证券代码": ["600598", "002714"],
            "证券名称": ["A", "B"],
            "最新权重": [1.0, 1.0],
            "计入日期": ["2024-01-01", "2024-01-01"],
        }),
        "801080": pd.DataFrame({
            "证券代码": ["002049", "603501"],
            "证券名称": ["C", "D"],
            "最新权重": [1.0, 1.0],
            "计入日期": ["2024-01-01", "2024-01-01"],
        }),
        "850111": pd.DataFrame({
            "证券代码": ["600598"],
            "证券名称": ["A"],
            "最新权重": [1.0],
            "计入日期": ["2024-01-01"],
        }),
        "850811": pd.DataFrame({
            "证券代码": ["002049", "603501"],
            "证券名称": ["C", "D"],
            "最新权重": [1.0, 1.0],
            "计入日期": ["2024-01-01", "2024-01-01"],
        }),
    }
    return data.get(symbol, pd.DataFrame())


def _make_fake_ak():
    """创建一个 fake akshare 模块对象。"""
    ak = types.ModuleType("akshare")
    ak.sw_index_first_info = _fake_l1_info
    ak.sw_index_second_info = _fake_l2_info
    ak.sw_index_third_info = _fake_l3_info
    ak.index_component_sw = _fake_component
    return ak


# --------------- tests ---------------

@patch.dict("sys.modules", {"akshare": _make_fake_ak()})
@patch("backend.adapters.akshare_industry.CALL_INTERVAL", 0)
def test_fetch_sw_hierarchy():
    """验证层级映射正确构建。"""
    from backend.adapters.akshare_industry import fetch_sw_hierarchy

    l3_code_to_name, l3_name_to_l2, l2_name_to_l1 = fetch_sw_hierarchy()

    # L3 code → name
    assert l3_code_to_name["850111"] == "粮食种植"
    assert l3_code_to_name["850811"] == "集成电路设计"

    # L3 name → L2 name
    assert l3_name_to_l2["粮食种植"] == "种植业"
    assert l3_name_to_l2["集成电路设计"] == "半导体"

    # L2 name → L1 name
    assert l2_name_to_l1["种植业"] == "农林牧渔"
    assert l2_name_to_l1["半导体"] == "电子"


@patch.dict("sys.modules", {"akshare": _make_fake_ak()})
@patch("backend.adapters.akshare_industry.CALL_INTERVAL", 0)
def test_fetch_sw_industry_all():
    """验证完整流程：L1 兜底 + L3 覆盖。"""
    from backend.adapters.akshare_industry import fetch_sw_industry_all

    result = fetch_sw_industry_all()

    # 002049.SZ 在 L1=电子（兜底）后被 L3=集成电路设计 覆盖
    assert "002049.SZ" in result
    assert result["002049.SZ"]["sw_l3"] == "集成电路设计"
    assert result["002049.SZ"]["sw_l2"] == "半导体"
    assert result["002049.SZ"]["sw_l1"] == "电子"

    # 600598.SH 在 L1=农林牧渔（兜底）后被 L3=粮食种植 覆盖
    assert "600598.SH" in result
    assert result["600598.SH"]["sw_l3"] == "粮食种植"
    assert result["600598.SH"]["sw_l2"] == "种植业"
    assert result["600598.SH"]["sw_l1"] == "农林牧渔"

    # 002714.SZ 只出现在 L1 中（不在任何 L3 中），应只有 sw_l1
    assert "002714.SZ" in result
    assert result["002714.SZ"]["sw_l1"] == "农林牧渔"
    assert result["002714.SZ"]["sw_l2"] == ""
    assert result["002714.SZ"]["sw_l3"] == ""

    # 603501.SH 同样被 L3 覆盖
    assert "603501.SH" in result
    assert result["603501.SH"]["sw_l3"] == "集成电路设计"
    assert result["603501.SH"]["sw_l2"] == "半导体"
    assert result["603501.SH"]["sw_l1"] == "电子"
