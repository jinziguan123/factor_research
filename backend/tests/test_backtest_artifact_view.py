"""单测：backtest_artifact_view 的降采样 + parquet 读取。

只测纯行为：降采样首尾保留、分页越界、列兼容；parquet 本身走 tmp_path。
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest


# ---------------------------- downsample_step ----------------------------


def test_downsample_step_no_sample_when_under_limit():
    from backend.services.backtest_artifact_view import downsample_step

    assert downsample_step(100, 2000) == 1
    assert downsample_step(2000, 2000) == 1
    assert downsample_step(0, 2000) == 1


def test_downsample_step_basic():
    from backend.services.backtest_artifact_view import downsample_step

    # 10000 点 → 2000 上限：step=5
    assert downsample_step(10_000, 2000) == 5
    # 2001 → 2000: step=2（宁可少一倍也不要超标）
    assert downsample_step(2001, 2000) == 2


def test_downsample_step_defensive_zero_limit():
    """max_points=0 时不该崩，退化为不抽。"""
    from backend.services.backtest_artifact_view import downsample_step

    assert downsample_step(1000, 0) == 1


# ---------------------------- load_equity_series ----------------------------


def _write_equity_parquet(path, dates, values) -> None:
    df = pd.DataFrame({"equity": values}, index=pd.to_datetime(dates))
    df.index.name = "trade_date"
    df.to_parquet(path)


def test_load_equity_series_small(tmp_path):
    from backend.services.backtest_artifact_view import load_equity_series

    p = tmp_path / "equity.parquet"
    _write_equity_parquet(
        p,
        ["2024-01-02", "2024-01-03", "2024-01-04"],
        [1.0, 1.05, 0.98],
    )
    out = load_equity_series(p)
    assert out["total"] == 3
    assert out["sampled"] is False
    assert out["dates"] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert out["values"] == [1.0, 1.05, 0.98]


def test_load_equity_series_empty(tmp_path):
    """空 parquet：正常返回空列表，不抛。"""
    from backend.services.backtest_artifact_view import load_equity_series

    p = tmp_path / "equity.parquet"
    pd.DataFrame({"equity": []}, index=pd.to_datetime([])).to_parquet(p)
    out = load_equity_series(p)
    assert out == {"dates": [], "values": [], "total": 0, "sampled": False}


def test_load_equity_series_downsamples_and_keeps_ends(tmp_path):
    """10000 点 + max_points=100：结果点数 <= 101，且必须包含首尾。"""
    from backend.services.backtest_artifact_view import load_equity_series

    p = tmp_path / "equity.parquet"
    dates = pd.date_range("2000-01-01", periods=10_000, freq="D")
    values = [float(i) for i in range(10_000)]
    pd.DataFrame({"equity": values}, index=dates).to_parquet(p)

    out = load_equity_series(p, max_points=100)
    assert out["total"] == 10_000
    assert out["sampled"] is True
    # step=100，10000/100=100 整除 → 首段循环到 9900；需手工补尾 9999 → 101 个点
    assert len(out["dates"]) <= 101
    assert out["dates"][0] == "2000-01-01"
    assert out["values"][0] == 0.0
    # 尾点必须是最后一个 —— 100 - 1 = 9999 天后
    expected_last_date = (
        pd.Timestamp("2000-01-01") + pd.Timedelta(days=9999)
    ).strftime("%Y-%m-%d")
    assert out["dates"][-1] == expected_last_date
    assert out["values"][-1] == 9999.0


def test_load_equity_series_handles_nan(tmp_path):
    """NaN 值必须 → None，否则 JSON 序列化会出 NaN（非法 JSON）。"""
    from backend.services.backtest_artifact_view import load_equity_series

    p = tmp_path / "equity.parquet"
    _write_equity_parquet(
        p,
        ["2024-01-02", "2024-01-03"],
        [1.0, float("nan")],
    )
    out = load_equity_series(p)
    assert out["values"] == [1.0, None]


def test_load_equity_series_falls_back_to_first_column(tmp_path):
    """列名不叫 equity 时退化取第 0 列——兼容 vectorbt 版本差异。"""
    from backend.services.backtest_artifact_view import load_equity_series

    p = tmp_path / "equity.parquet"
    df = pd.DataFrame(
        {"value": [1.0, 1.1]}, index=pd.to_datetime(["2024-01-02", "2024-01-03"])
    )
    df.to_parquet(p)
    out = load_equity_series(p)
    assert out["values"] == [1.0, 1.1]


# ---------------------------- load_trades_page ----------------------------


def _write_trades_parquet(path, n: int = 10) -> None:
    df = pd.DataFrame(
        {
            "Size": [100.0 + i for i in range(n)],
            "Entry Timestamp": pd.to_datetime(
                [f"2024-01-{(i % 28) + 1:02d}" for i in range(n)]
            ),
            "PnL": [i * 1.5 for i in range(n)],
            "Status": ["Closed"] * n,
        }
    )
    df.to_parquet(path)


def test_load_trades_page_basic(tmp_path):
    from backend.services.backtest_artifact_view import load_trades_page

    p = tmp_path / "trades.parquet"
    _write_trades_parquet(p, n=10)
    out = load_trades_page(p, page=1, size=5)
    assert out["total"] == 10
    assert out["page"] == 1
    assert out["size"] == 5
    assert out["columns"] == ["Size", "Entry Timestamp", "PnL", "Status"]
    assert len(out["rows"]) == 5
    # Timestamp 必须是字符串（JSON 可序列化）
    assert isinstance(out["rows"][0]["Entry Timestamp"], str)
    assert out["rows"][0]["Size"] == 100.0


def _write_signal_trades_parquet(path, n: int = 10) -> None:
    """信号回测的按笔 trades：列名是 entry_date / symbol（非 vectorbt 口径）。"""
    df = pd.DataFrame(
        {
            "symbol": [f"00053{i % 3}.SZ" for i in range(n)],
            "entry_date": pd.to_datetime(
                [f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]
            ),
            "exit_date": pd.to_datetime(
                [f"2026-06-{(i % 28) + 1:02d}" for i in range(n)]
            ),
            "pnl": [i * 1.5 for i in range(n)],
            "exit_reason": ["stop_loss"] * n,
        }
    )
    df.to_parquet(path)


def test_load_trades_page_exit_reason_filter_and_sort(tmp_path):
    """出场原因精确筛选 + 按列升降序（信号回测产物）。"""
    from backend.services.backtest_artifact_view import load_trades_page

    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    df = pd.DataFrame({
        "symbol": [f"00000{i}.SZ" for i in range(6)],
        "entry_date": dates,
        "pnl": [3.0, -1.0, 5.0, -2.0, 1.0, -4.0],
        "exit_reason": ["stop_loss", "take_profit", "stop_loss",
                        "max_hold", "take_profit", "stop_loss"],
    })
    p = tmp_path / "trades.parquet"
    df.to_parquet(p)

    # 出场原因筛选：只留 stop_loss（3 条）
    out = load_trades_page(p, page=1, size=20, exit_reason="stop_loss")
    assert out["total"] == 3
    assert all(r["exit_reason"] == "stop_loss" for r in out["rows"])

    # 按 pnl 升序：首行是最小的 -4.0
    asc = load_trades_page(p, page=1, size=20, sort_by="pnl", sort_order="asc")
    assert asc["rows"][0]["pnl"] == -4.0
    # 降序：首行最大 5.0
    desc = load_trades_page(p, page=1, size=20, sort_by="pnl", sort_order="desc")
    assert desc["rows"][0]["pnl"] == 5.0

    # 筛选 + 排序叠加：stop_loss 里按 pnl 降序，首行 5.0
    both = load_trades_page(p, page=1, size=20, exit_reason="stop_loss",
                            sort_by="pnl", sort_order="desc")
    assert both["total"] == 3 and both["rows"][0]["pnl"] == 5.0

    # 非法排序列 / 无 exit_reason 列的产物：忽略、不报错
    ignore = load_trades_page(p, page=1, size=20, sort_by="不存在的列")
    assert ignore["total"] == 6


def test_load_trades_page_signal_entry_date_filter(tmp_path):
    """回归：信号回测产物用 entry_date 列，按开仓日筛选不应报 400。"""
    from backend.services.backtest_artifact_view import load_trades_page

    p = tmp_path / "trades.parquet"
    _write_signal_trades_parquet(p, n=10)
    # 全区间：10 笔（entry_date 都在 2026-01）
    out = load_trades_page(p, page=1, size=20,
                           start_date="2026-01-01", end_date="2026-08-01")
    assert out["total"] == 10
    # 收窄到只含前 5 天开仓：entry_date 为 01-01..01-05
    out2 = load_trades_page(p, page=1, size=20,
                            start_date="2026-01-01", end_date="2026-01-05")
    assert out2["total"] == 5
    # symbol 子串 + 日期同时用，不报错
    out3 = load_trades_page(p, page=1, size=20, symbol="00053",
                            start_date="2026-01-01", end_date="2026-08-01")
    assert out3["total"] == 10


def test_load_trades_page_last_page(tmp_path):
    from backend.services.backtest_artifact_view import load_trades_page

    p = tmp_path / "trades.parquet"
    _write_trades_parquet(p, n=12)
    out = load_trades_page(p, page=3, size=5)
    assert out["total"] == 12
    assert len(out["rows"]) == 2  # 12 - 5*2 = 2


def test_load_trades_page_out_of_range(tmp_path):
    """越界页：rows 空但 total 必须真实。"""
    from backend.services.backtest_artifact_view import load_trades_page

    p = tmp_path / "trades.parquet"
    _write_trades_parquet(p, n=5)
    out = load_trades_page(p, page=10, size=5)
    assert out["total"] == 5
    assert out["rows"] == []


def test_load_trades_page_empty(tmp_path):
    from backend.services.backtest_artifact_view import load_trades_page

    p = tmp_path / "trades.parquet"
    pd.DataFrame({"Size": [], "PnL": []}).to_parquet(p)
    out = load_trades_page(p, page=1, size=50)
    assert out["total"] == 0
    assert out["rows"] == []
    # 空表仍需要返回 columns，方便前端提前渲染空表头
    assert out["columns"] == ["Size", "PnL"]


def test_load_trades_page_clamps_size(tmp_path):
    """size 超过 500 被截；<=0 提升到 1。"""
    from backend.services.backtest_artifact_view import load_trades_page

    p = tmp_path / "trades.parquet"
    _write_trades_parquet(p, n=5)
    out_big = load_trades_page(p, page=1, size=10_000)
    assert out_big["size"] == 500
    out_zero = load_trades_page(p, page=1, size=0)
    assert out_zero["size"] == 1
