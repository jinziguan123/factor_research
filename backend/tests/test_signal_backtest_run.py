"""run_signal_backtest 编排层测试（mock DB / 真跑信号引擎）。

覆盖两条：
1) ``_build_signal_metrics_payload`` 纯函数——payload 结构 + json 序列化安全
   （关键：profit_factor 可能是 inf，必须在落库前兜底成 None，否则
   ``json.dumps(..., allow_nan=False)`` 抛 ValueError）。
2) ``run_signal_backtest`` 端到端 smoke——monkeypatch 掉 _prepare_price_cost /
   DB / 状态更新 / artifact 目录，注入合成 PriceCostBundle 真跑引擎，确保
   ``prepare → 引擎 → summarize → 组装 metrics → 落盘 → 写库`` 整条链路无
   运行期错误，且写出 equity/orders/trades 三个 parquet、走到 success。

    backend/.venv/bin/pytest backend/tests/test_signal_backtest_run.py -v
"""
from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from backend.services import backtest_service as bs
from backend.services import signal_backtest as sbt


def _make_bundle(monkeypatch=None):
    """构造一段能触发至少一笔信号交易的合成 PriceCostBundle。

    A 在第 0 日出信号（factor > 阈值），第 1 日开盘=10 建仓，之后涨到 12，
    数据末尾 end_of_data 强平 → 至少一笔盈利交易。
    """
    dates = pd.date_range("2026-01-05", periods=5, freq="B")
    cols = ["A", "B"]
    close = pd.DataFrame(
        [[10, 20], [10, 20], [11, 20], [12, 20], [12, 20]],
        index=dates, columns=cols, dtype=float,
    )
    open_ = close.copy()
    high = close.copy()
    low = close.copy()
    exec_price = close.copy()
    # A 第 0 日出信号（1.0 > 0），B 从不出信号。
    factor = pd.DataFrame(
        [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        index=dates, columns=cols,
    )
    daily_amount = pd.DataFrame(1e12, index=dates, columns=cols)
    mask_false = pd.DataFrame(False, index=dates, columns=cols)

    return bs.PriceCostBundle(
        factor_id="x", factor_version=0, params_hash="0" * 40, params={},
        pool_id=1, symbols=cols, factor=factor, close=close, open_=open_,
        high=high, low=low, exec_price=exec_price, daily_amount=daily_amount,
        limit_up_mask=mask_false, limit_down_mask=mask_false,
        n_groups=5, exec_mode="open", commission_bps=2.5, stamp_tax_bps=5.0,
        transfer_fee_bps=0.1, slippage_bps=5.0, impact_coef=0.1,
        max_volume_pct=0.10, init_cash=5000.0,
    )


def _signal_body():
    return {
        "factor_id": "x", "pool_id": 1,
        "start_date": "2026-01-05", "end_date": "2026-01-09",
        "mode": "signal",
        "signal_threshold": 0.0,
        "cash_per_lot": 2000.0,
        "stop_loss_pct": 0.0,
        "take_profit_pct": 0.0,
        "stop_mode": "per_lot",
        "min_hold_days": 0,
        "max_hold_days": 0,
        "allow_pyramiding": False,
        "max_adds_per_symbol": 0,
        "max_concurrent_lots": 10,
        "init_cash": 5000.0,
    }


def test_build_payload_json_safe_with_inf_profit_factor():
    """profit_factor=inf（无亏损、有盈利）时 payload 仍能 allow_nan=False 序列化。"""
    dates = pd.date_range("2026-01-05", periods=3, freq="B")
    # 单笔盈利、无亏损 → summarize 的 profit_factor = inf
    trades = pd.DataFrame([{
        "symbol": "A", "entry_date": dates[0], "entry_price": 10.0,
        "exit_date": dates[2], "exit_price": 12.0, "qty": 100.0,
        "pnl": 200.0, "return_pct": 0.2, "hold_days": 2,
        "exit_reason": "end_of_data", "add_seq": 0, "lot_id": 1,
    }])
    equity = pd.Series([1000.0, 1050.0, 1200.0], index=dates)
    res = sbt.SignalResult(
        equity=equity, trades=trades,
        orders=pd.DataFrame(columns=["date", "symbol", "side", "price", "qty"]),
        skipped=pd.DataFrame(columns=["date", "symbol", "reason"]),
    )
    summary = sbt.summarize(res)
    assert summary["profit_factor"] == float("inf")  # 前提成立

    payload = sbt._build_signal_metrics_payload(
        res, summary, init_cash=1000.0, close=None,
    )
    # 关键：inf 已被兜底，可 allow_nan=False 序列化，不抛。
    s = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert s
    # profit_factor 落库前必须是 None（inf 兜底），不能是 inf。
    assert payload["profit_factor"] is None
    # 收益类字段存在
    for k in ("total_return", "annual_return", "sharpe", "max_drawdown",
              "win_rate", "trade_count"):
        assert k in payload


def test_run_signal_backtest_smoke_no_db(monkeypatch, tmp_path):
    bundle = _make_bundle()
    monkeypatch.setattr(bs, "_prepare_price_cost", lambda body: bundle)
    monkeypatch.setattr(bs, "check_abort", lambda *a, **k: None)
    monkeypatch.setattr(bs, "ARTIFACT_DIR", tmp_path)

    calls: list[dict] = []
    monkeypatch.setattr(
        bs, "_update_status", lambda run_id, **k: calls.append(k)
    )

    @contextlib.contextmanager
    def fake_conn():
        yield MagicMock()

    monkeypatch.setattr(bs, "mysql_conn", fake_conn)

    sbt.run_signal_backtest("sig_smoke", _signal_body())

    fails = [c for c in calls if c.get("status") == "failed"]
    assert not fails, f"run_signal_backtest 内部报错:\n{fails[0].get('error', '')[:1200]}"
    assert any(c.get("status") == "success" for c in calls), "未走到 success"
    for name in ("equity.parquet", "orders.parquet", "trades.parquet"):
        assert (tmp_path / "sig_smoke" / name).exists(), f"缺 {name}"

    # 产物内容自检：至少一笔交易、equity 末值为浮点。
    trades = pd.read_parquet(tmp_path / "sig_smoke" / "trades.parquet")
    assert len(trades) >= 1
    equity = pd.read_parquet(tmp_path / "sig_smoke" / "equity.parquet")
    assert "equity" in equity.columns
    assert equity.index.name == "trade_date"
