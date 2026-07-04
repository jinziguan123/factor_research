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
                         max_adds_per_symbol=2, min_hold_days=3, max_hold_days=20,
                         atr_stop_multiplier=2.5, atr_window=10,
                         trailing_stop=True, pyramid_min_profit_pct=0.03,
                         signal_mode="cross_quantile", signal_quantile=0.95,
                         signal_top_n=20, signal_zscore_window=40)
    assert m.mode == "signal"
    assert m.stop_mode == "avg_cost"
    assert m.signal_mode == "cross_quantile"
    assert m.signal_quantile == 0.95
    assert m.signal_top_n == 20
    assert m.max_adds_per_symbol == 2
    assert m.atr_stop_multiplier == 2.5
    assert m.trailing_stop is True
    assert m.pyramid_min_profit_pct == 0.03


def test_signal_config_from_body_maps_enhancements():
    """body → SignalConfig 映射覆盖增强字段（ATR/跟踪/条件加仓）。"""
    from backend.services.signal_backtest import _signal_config_from_body, SignalConfig
    from types import SimpleNamespace

    bundle = SimpleNamespace(commission_bps=2.5, transfer_fee_bps=0.1, stamp_tax_bps=5.0)
    cfg = _signal_config_from_body(
        {"atr_stop_multiplier": 2.5, "atr_window": 10, "trailing_stop": True,
         "pyramid_min_profit_pct": 0.03, "stop_mode": "avg_cost"},
        bundle,
    )
    assert isinstance(cfg, SignalConfig)
    assert cfg.atr_stop_multiplier == 2.5
    assert cfg.atr_window == 10
    assert cfg.trailing_stop is True
    assert cfg.pyramid_min_profit_pct == 0.03


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
