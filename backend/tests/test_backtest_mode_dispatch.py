"""Task 16/17：回测 mode 分发 + CreateBacktestIn 信号字段。"""
from __future__ import annotations

from backend.api.schemas import CreateBacktestIn


def test_schema_signal_fields_defaults():
    m = CreateBacktestIn(factor_id="f", pool_id=1,
                         start_date="2026-01-01", end_date="2026-06-30")
    # 向后兼容：不传 mode 默认 quantile，信号字段有合理默认
    assert m.mode == "quantile"
    assert m.stop_mode == "per_lot"
    assert m.cash_per_lot == 1e6
    assert m.allow_pyramiding is False


def test_schema_signal_fields_roundtrip():
    m = CreateBacktestIn(factor_id="f", pool_id=1,
                         start_date="2026-01-01", end_date="2026-06-30",
                         mode="signal", stop_loss_pct=0.05, take_profit_pct=0.15,
                         stop_mode="avg_cost", allow_pyramiding=True,
                         max_adds_per_symbol=2, min_hold_days=3, max_hold_days=20)
    assert m.mode == "signal"
    assert m.stop_mode == "avg_cost"
    assert m.max_adds_per_symbol == 2


def test_run_backtest_dispatches_signal(monkeypatch):
    import backend.services.backtest_service as bs

    called = {}

    def fake_run_signal(run_id, body):
        called["run_id"] = run_id
        called["mode"] = body.get("mode")

    monkeypatch.setattr(
        "backend.services.signal_backtest.run_signal_backtest", fake_run_signal
    )
    bs.run_backtest("rid-1", {"mode": "signal", "factor_id": "f"})
    assert called == {"run_id": "rid-1", "mode": "signal"}
