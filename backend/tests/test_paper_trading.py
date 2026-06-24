"""模拟盘调仓引擎 + 编排测试。

plan_rebalance 是纯函数(调仓算法心脏)，直接测；rebalance 编排 mock 掉
signal/DB，验证串联无 bug(防 run_backtest 式盲区)。

    uv run pytest backend/tests/test_paper_trading.py -v
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.schemas import CreatePaperAccountIn
from backend.execution_layer import OrderSide, SimulatedBroker
from backend.services import paper_trading_service as pt


# ---------------------------- SimulatedBroker 持仓恢复 ----------------------------


def test_broker_restores_initial_positions():
    b = SimulatedBroker(init_cash=1000.0, init_positions={"x": (100.0, 5.0)})
    pos = b.get_positions()
    assert pos["x"].qty == 100.0 and pos["x"].avg_price == 5.0
    # 恢复的持仓可被卖出
    o = b.submit_order("x", OrderSide.SELL, 100, 6.0)
    assert o.filled_qty == 100
    assert "x" not in b.get_positions()


# ---------------------------- plan_rebalance(纯函数)----------------------------


def test_plan_rebalance_builds_equal_weight_from_empty():
    plan = pt.plan_rebalance(
        cash=1_000_000.0, positions={},
        prices={"A": 10.0, "B": 20.0}, target_symbols=["A", "B"],
        commission_bps=0, stamp_tax_bps=0, transfer_fee_bps=0,
    )
    pos = plan["new_positions"]
    # 等权：每只 ~50 万；A 价 10 → ~50000 股(整百)，B 价 20 → ~25000 股
    assert pos["A"][0] == pytest.approx(50000, abs=100)
    assert pos["B"][0] == pytest.approx(25000, abs=100)
    assert plan["new_cash"] >= 0


def test_plan_rebalance_sells_out_of_target():
    # 持有 C(不在目标)，目标 [A,B] → C 应被清仓
    plan = pt.plan_rebalance(
        cash=100_000.0, positions={"C": (1000.0, 8.0)},
        prices={"A": 10.0, "B": 20.0, "C": 9.0}, target_symbols=["A", "B"],
        commission_bps=0, stamp_tax_bps=0, transfer_fee_bps=0,
    )
    assert "C" not in plan["new_positions"]      # 清仓
    assert "A" in plan["new_positions"]
    assert "B" in plan["new_positions"]


def test_plan_rebalance_nav_conserved_minus_fees():
    # 无费时净值守恒(撮合不创造/毁灭价值，只有整手取整的零头留现金)
    plan = pt.plan_rebalance(
        cash=1_000_000.0, positions={},
        prices={"A": 10.0, "B": 20.0}, target_symbols=["A", "B"],
        commission_bps=0, stamp_tax_bps=0, transfer_fee_bps=0,
    )
    assert plan["nav"] == pytest.approx(1_000_000.0, rel=1e-6)


def test_plan_rebalance_skips_missing_price():
    # 目标里某票没有快照价 → 跳过(不买)，不报错
    plan = pt.plan_rebalance(
        cash=100_000.0, positions={},
        prices={"A": 10.0}, target_symbols=["A", "B_no_price"],
        commission_bps=0, stamp_tax_bps=0, transfer_fee_bps=0,
    )
    assert "A" in plan["new_positions"]
    assert "B_no_price" not in plan["new_positions"]


def test_plan_rebalance_sell_fee_reduces_cash():
    # 卖出含印花税，回笼现金应低于"裸市值"
    plan = pt.plan_rebalance(
        cash=0.0, positions={"C": (1000.0, 8.0)},
        prices={"C": 10.0}, target_symbols=[],  # 全部清仓
        commission_bps=2.5, stamp_tax_bps=5.0, transfer_fee_bps=0.1,
    )
    # 裸市值 10000；扣费(2.5+5+0.1)bp ≈ 7.6 元
    assert plan["new_cash"] < 10_000.0
    assert plan["new_cash"] == pytest.approx(10_000.0 - 7.6, abs=0.1)


# ---------------------------- rebalance 编排(mock signal/DB)----------------------------


def test_rebalance_orchestration(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(pt, "_load_account", lambda aid: {
        "account_id": aid, "cash": 1_000_000.0, "method": "equal",
        "pool_id": 1, "n_groups": 5, "top_n": 2,
        "factor_items_json": "[]",
    })
    monkeypatch.setattr(pt, "_load_positions", lambda aid: {"C": (1000.0, 8.0)})
    # signal 给目标 [A,B] + 价
    monkeypatch.setattr(pt, "_run_signal_top", lambda acct: (["A", "B"], {"A": 10.0, "B": 20.0}))
    # 卖出侧 C 的快照价
    monkeypatch.setattr(pt, "_spot_prices", lambda syms: {"C": 9.0})
    monkeypatch.setattr(pt, "_save_account_state", lambda aid, cash, pos: calls.update(saved=(cash, pos)))
    monkeypatch.setattr(pt, "_record_trades", lambda aid, fills: calls.update(trades=len(fills)))
    monkeypatch.setattr(pt, "_record_nav", lambda aid, nav, cash: calls.update(nav=nav))

    out = pt.rebalance("acc1")

    assert out["n_targets"] == 2
    assert "saved" in calls          # 状态落库被调用
    assert calls["trades"] >= 1      # 至少卖 C + 买 A/B
    assert out["nav"] > 0
    # C 被清仓，A/B 进目标
    _, new_pos = calls["saved"]
    assert "C" not in new_pos
    assert "A" in new_pos and "B" in new_pos


# ---------------------------- CreatePaperAccountIn schema 校验 ----------------------------


def test_paper_account_schema_valid_defaults():
    m = CreatePaperAccountIn(name="t", factor_items=[{"factor_id": "x"}], pool_id=1)
    assert m.init_cash == 1e6 and m.method == "equal" and m.n_groups == 5


def test_paper_account_schema_rejects_empty_factor_items():
    with pytest.raises(ValidationError):
        CreatePaperAccountIn(name="t", factor_items=[], pool_id=1)


def test_paper_account_schema_rejects_nonpositive_cash():
    with pytest.raises(ValidationError):
        CreatePaperAccountIn(
            name="t", factor_items=[{"factor_id": "x"}], pool_id=1, init_cash=0,
        )
