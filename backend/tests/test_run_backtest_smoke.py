"""run_backtest 主体冒烟测试（mock DB / 真跑 VectorBT）。

这是首个不依赖真实数据库、端到端覆盖 run_backtest 写结果路径的测试。此前这条路径
只有 ``@pytest.mark.integration`` 用例（需连库）覆盖，导致 ``NameError: close`` 这类
变量引用 bug 长期未被发现（回测在有数据时必失败）。本测试 monkeypatch 掉 DB /
状态更新 / artifact 目录，注入合成 ``BacktestInputs`` 真跑 vbt，确保
``from_orders → benchmark → metrics → 落盘`` 整条链路无运行期错误。

    uv run pytest backend/tests/test_run_backtest_smoke.py -v
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("vectorbt")

from backend.services import backtest_service as bs  # noqa: E402


def test_run_backtest_smoke_no_db(monkeypatch, tmp_path):
    idx = pd.date_range("2024-01-02", periods=6)
    cols = ["A", "B"]
    close = pd.DataFrame(
        [[10, 20], [11, 19], [12, 21], [11, 22], [13, 20], [12, 23]],
        index=idx, columns=cols, dtype=float,
    )
    exec_price = close.copy()
    # 第 1 个交易日起持有 A（单边多头），覆盖建仓 + 持有路径。
    w_exec = pd.DataFrame(
        [[0.0, 0.0]] + [[1.0, 0.0]] * 5, index=idx, columns=cols,
    )
    size = (w_exec * 1e6 / exec_price).fillna(0.0)
    daily_amount = pd.DataFrame(1e12, index=idx, columns=cols)
    fees = np.zeros((6, 2))
    slip = np.zeros((6, 2))

    inputs = bs.BacktestInputs(
        factor_id="x", factor_version=0, params_hash="0" * 40, params={},
        pool_id=1, symbols=cols, F=close.copy(), close=close, size=size,
        exec_price=exec_price, fees_arr=fees, slip_arr=slip, w_exec=w_exec,
        daily_amount=daily_amount, slippage_bps=5.0, impact_coef=0.1,
        init_cash=1e6, freq="1d", n_bars=6,
    )

    monkeypatch.setattr(bs, "_prepare_backtest_inputs", lambda body: inputs)
    monkeypatch.setattr(bs, "check_abort", lambda *a, **k: None)
    monkeypatch.setattr(bs, "ARTIFACT_DIR", tmp_path)

    # 记录所有状态更新，断言最终是 success 而非 failed（run_backtest 吞异常落 failed）。
    calls: list[dict] = []
    monkeypatch.setattr(bs, "_update_status", lambda run_id, **k: calls.append(k))

    @contextlib.contextmanager
    def fake_conn():
        yield MagicMock()

    monkeypatch.setattr(bs, "mysql_conn", fake_conn)

    bs.run_backtest("smoke_run", {"factor_id": "x", "pool_id": 1})

    fails = [c for c in calls if c.get("status") == "failed"]
    assert not fails, f"run_backtest 内部报错:\n{fails[0].get('error', '')[:800]}"
    assert any(c.get("status") == "success" for c in calls), "未走到 success"
    for name in ("equity.parquet", "orders.parquet", "trades.parquet"):
        assert (tmp_path / "smoke_run" / name).exists(), f"缺 {name}"
