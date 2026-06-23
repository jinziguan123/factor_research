"""execution.py 纯函数单测：成交价 / 不对称费用 / 滑点冲击 / 容量裁剪。

无 DB 依赖，可独立运行：
    uv run pytest backend/tests/test_execution.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import execution as ex


def _df(data):
    return pd.DataFrame(data)


# ---------------------------- shift_for_t1 ----------------------------


def test_shift_for_t1_moves_one_row():
    w = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    out = ex.shift_for_t1(w)
    assert out["a"].tolist() == [0.0, 1.0, 2.0]


def test_shift_for_t1_empty():
    assert ex.shift_for_t1(pd.DataFrame()).empty


# ---------------------------- build_exec_price ----------------------------


def test_build_exec_price_open():
    o = _df([[1.0, 2.0]])
    z = _df([[9.0, 9.0]])
    out = ex.build_exec_price(o, z, z, z, "open")
    assert out.iloc[0, 0] == 1.0 and out.iloc[0, 1] == 2.0


def test_build_exec_price_vwap():
    o = _df([[1.0]])
    h = _df([[6.0]])
    low = _df([[3.0]])
    c = _df([[3.0]])
    out = ex.build_exec_price(o, h, low, c, "vwap")
    assert out.iloc[0, 0] == pytest.approx((6.0 + 3.0 + 3.0) / 3.0)


def test_build_exec_price_bad_mode():
    z = _df([[1.0]])
    with pytest.raises(ValueError):
        ex.build_exec_price(z, z, z, z, "foo")


# ---------------------------- build_fee_array ----------------------------


def test_build_fee_array_buy_vs_sell():
    # w_exec：第0行0(空仓起步)，第1行建仓买入0.5，第2行减仓到0.2(卖出)
    w = pd.DataFrame({"a": [0.0, 0.5, 0.2]})
    arr = ex.build_fee_array(
        w, commission_bps=2.5, stamp_tax_bps=5.0, transfer_fee_bps=0.1
    )
    buy = (2.5 + 0.1) / 1e4
    sell = (2.5 + 5.0 + 0.1) / 1e4
    assert arr[1, 0] == pytest.approx(buy)   # dw=+0.5 买入
    assert arr[2, 0] == pytest.approx(sell)  # dw=-0.3 卖出


def test_build_fee_array_shape():
    w = pd.DataFrame({"a": [0.0, 1.0], "b": [0.0, 0.5]})
    arr = ex.build_fee_array(w, 2.5, 5.0, 0.1)
    assert arr.shape == (2, 2)


# ---------------------------- build_slippage_array ----------------------------


def test_build_slippage_impact_sqrt():
    w = pd.DataFrame({"a": [0.0, 1.0]})  # 第1行成交额 = 1 * cash
    amount = pd.DataFrame({"a": [1e9, 1e9]})
    px = pd.DataFrame({"a": [10.0, 10.0]})
    slip = ex.build_slippage_array(
        w, init_cash=1e8, daily_amount=amount, exec_price=px,
        slippage_bps=5.0, impact_coef=0.1,
    )
    base = 5.0 / 1e4
    # order=1e8, amount=1e9, ratio=0.1, impact=0.1*sqrt(0.1)
    assert slip[1, 0] == pytest.approx(base + 0.1 * np.sqrt(0.1))
    assert slip[0, 0] == pytest.approx(base)  # 首行无成交，仅固定滑点


def test_build_slippage_zero_amount_no_impact():
    w = pd.DataFrame({"a": [0.0, 1.0]})
    amount = pd.DataFrame({"a": [0.0, 0.0]})
    px = pd.DataFrame({"a": [10.0, 10.0]})
    slip = ex.build_slippage_array(w, 1e8, amount, px, 5.0, 0.1)
    assert slip[1, 0] == pytest.approx(5.0 / 1e4)  # amount=0 → 冲击=0


def test_build_slippage_impact_off():
    w = pd.DataFrame({"a": [0.0, 1.0]})
    amount = pd.DataFrame({"a": [1e9, 1e9]})
    px = pd.DataFrame({"a": [10.0, 10.0]})
    slip = ex.build_slippage_array(w, 1e8, amount, px, 5.0, impact_coef=0.0)
    assert np.allclose(slip, 5.0 / 1e4)  # 关闭冲击只剩固定滑点


# ---------------------------- apply_volume_cap ----------------------------


def test_apply_volume_cap_clips_oversized():
    target = pd.DataFrame({"a": [1000.0]})
    amount = pd.DataFrame({"a": [1000.0]})  # 元
    px = pd.DataFrame({"a": [1.0]})
    out = ex.apply_volume_cap(target, amount, px, max_volume_pct=0.1)
    # cap=0.1*1000=100元; order=1000>100; scale=0.1; delta=100股
    assert out.iloc[0, 0] == pytest.approx(100.0)


def test_apply_volume_cap_no_clip_within():
    target = pd.DataFrame({"a": [50.0]})
    amount = pd.DataFrame({"a": [1000.0]})
    px = pd.DataFrame({"a": [1.0]})
    out = ex.apply_volume_cap(target, amount, px, max_volume_pct=0.1)
    assert out.iloc[0, 0] == pytest.approx(50.0)  # order=50<=100 不裁剪


def test_apply_volume_cap_disabled():
    target = pd.DataFrame({"a": [1000.0]})
    amount = pd.DataFrame({"a": [1.0]})
    px = pd.DataFrame({"a": [1.0]})
    out = ex.apply_volume_cap(target, amount, px, max_volume_pct=0.0)
    assert out.iloc[0, 0] == pytest.approx(1000.0)  # 关闭不裁剪


def test_apply_volume_cap_path_dependent():
    # 目标两日都持仓 100 股，每日限额 5%
    target = pd.DataFrame({"a": [100.0, 100.0]})
    amount = pd.DataFrame({"a": [1000.0, 1000.0]})
    px = pd.DataFrame({"a": [1.0, 1.0]})
    out = ex.apply_volume_cap(target, amount, px, max_volume_pct=0.05)
    # day0: cap=50, delta=100→裁到50, 持仓=50
    # day1: delta=100-50=50, order=50<=50, 持仓=100
    assert out.iloc[0, 0] == pytest.approx(50.0)
    assert out.iloc[1, 0] == pytest.approx(100.0)


def test_apply_volume_cap_halt_zero_amount():
    # 停牌日 amount=0 → cap=0 → 持仓不变
    target = pd.DataFrame({"a": [100.0, 100.0]})
    amount = pd.DataFrame({"a": [0.0, 1e9]})
    px = pd.DataFrame({"a": [1.0, 1.0]})
    out = ex.apply_volume_cap(target, amount, px, max_volume_pct=0.1)
    assert out.iloc[0, 0] == pytest.approx(0.0)    # 停牌买不进
    assert out.iloc[1, 0] == pytest.approx(100.0)  # 复牌补到目标
