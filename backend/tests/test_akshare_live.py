"""akshare 实盘适配器的纯单测——通过依赖注入的 fake fetcher 跑，
不依赖 akshare 包，不打真实网络。

覆盖：
- 字段中文 → 英文映射
- 涨跌幅百分数 → 小数转换
- symbol 后缀推断（沪/深/北）
- 停牌判定（last_price=0 / amount=0 / NaN）
- 1m K 批量拉取的并发收敛 + 错误隔离
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.adapters.akshare_live import (
    fetch_1m_bars_batch,
    fetch_1m_bars_one,
    fetch_daily_bars_batch,
    fetch_daily_bars_one,
    fetch_spot_snapshot,
)


# ---------------------------- spot_snapshot ----------------------------


def _fake_spot_df() -> pd.DataFrame:
    """构造一份拟真的 spot_em 返回（5 只票，覆盖各种边界）。"""
    return pd.DataFrame(
        {
            "代码": ["600519", "000001", "300750", "688981", "002594"],
            "名称": ["贵州茅台", "平安银行", "宁德时代", "中芯国际", "比亚迪"],
            "最新价": [1620.50, 12.30, 0.0, 89.45, 250.10],  # 第三只 last=0 → 停牌
            "涨跌幅": [1.23, -0.50, 0.0, 2.50, -3.10],  # 百分数
            "涨跌额": [19.7, -0.06, 0.0, 2.18, -8.0],
            "成交量": [123456, 9876543, 0, 234567, 567890],
            "成交额": [2e8, 1.2e8, 0.0, 2.1e7, 1.4e8],
            "今开": [1605.0, 12.45, 0.0, 87.20, 258.0],
            "最高": [1635.0, 12.55, 0.0, 90.10, 261.0],
            "最低": [1602.0, 12.20, 0.0, 86.90, 248.5],
            "昨收": [1601.30, 12.36, 12.00, 87.27, 258.1],
        }
    )


def test_spot_field_mapping_and_unit_conversion() -> None:
    """中文字段映射 + 涨跌幅百分数→小数 + symbol 后缀推断。"""
    df = fetch_spot_snapshot(spot_fetcher=_fake_spot_df)
    # 至少包含 5 只票（停牌的也保留，由 is_suspended 标记）
    assert len(df) == 5
    # symbol 后缀推断：6xxxxx → SH，0xxxxx / 3xxxxx → SZ，68xxxx → SH
    sym_map = dict(zip(df["symbol"], df["last_price"]))
    assert "600519.SH" in sym_map
    assert "000001.SZ" in sym_map
    assert "300750.SZ" in sym_map
    assert "688981.SH" in sym_map  # 科创板 68 开头属沪
    assert "002594.SZ" in sym_map
    # pct_chg 已转小数（1.23% → 0.0123）
    moutai = df[df["symbol"] == "600519.SH"].iloc[0]
    assert abs(moutai["pct_chg"] - 0.0123) < 1e-6
    # 列名英文
    assert {"last_price", "open", "high", "low", "prev_close", "pct_chg",
            "volume", "amount", "is_suspended"}.issubset(set(df.columns))


def test_spot_handles_suspended() -> None:
    """last_price=0 或 amount=0 → is_suspended=1。"""
    df = fetch_spot_snapshot(spot_fetcher=_fake_spot_df)
    # 第三只票（300750）last_price=0 → 应被标停牌
    cyc = df[df["symbol"] == "300750.SZ"].iloc[0]
    assert cyc["is_suspended"] == 1
    # 其它票应非停牌
    moutai = df[df["symbol"] == "600519.SH"].iloc[0]
    assert moutai["is_suspended"] == 0


def test_spot_raises_on_empty_response() -> None:
    """akshare 返回空 DataFrame 时抛 RuntimeError 让上层重试。"""
    with pytest.raises(RuntimeError, match="empty"):
        fetch_spot_snapshot(spot_fetcher=lambda: pd.DataFrame())


def test_spot_raises_on_missing_fields() -> None:
    """缺关键字段时抛错（如 akshare 接口字段名变更）。"""
    bad_df = pd.DataFrame({"代码": ["600519"], "最新价": [1620.5]})  # 大量字段缺失

    with pytest.raises(RuntimeError, match="missing fields"):
        fetch_spot_snapshot(spot_fetcher=lambda: bad_df)


def test_spot_skips_unknown_symbol_format() -> None:
    """异常代码（不能 normalize）应被丢弃，不污染输出。"""
    bad_df = _fake_spot_df()
    # 加一行无法 normalize 的代码
    bad_df = pd.concat(
        [
            bad_df,
            pd.DataFrame(
                {
                    "代码": ["INVALID_X"],
                    "名称": ["脏数据"],
                    "最新价": [1.0],
                    "涨跌幅": [0.0],
                    "涨跌额": [0.0],
                    "成交量": [1],
                    "成交额": [1.0],
                    "今开": [1.0],
                    "最高": [1.0],
                    "最低": [1.0],
                    "昨收": [1.0],
                }
            ),
        ],
        ignore_index=True,
    )
    df = fetch_spot_snapshot(spot_fetcher=lambda: bad_df)
    # 只有 5 只合法票，脏数据被丢
    assert len(df) == 5
    assert "INVALID_X" not in df["symbol"].values


def test_spot_handles_sina_prefixed_codes() -> None:
    """新浪 ``stock_zh_a_spot`` 返回的 ``sh600519`` / ``sz000001`` / ``bj920000``
    带前缀的 8 字符代码——预处理剥前缀后再 normalize。"""
    raw = pd.DataFrame(
        {
            "代码": ["sh600519", "sz000001", "sz300750", "sh688981", "bj920082"],
            "名称": ["贵州茅台", "平安银行", "宁德时代", "中芯国际", "北交所样本"],
            "最新价": [1620.5, 12.3, 32.4, 89.45, 5.5],
            "涨跌幅": [1.23, -0.5, 0.8, 2.5, 0.1],
            "涨跌额": [19.7, -0.06, 0.25, 2.18, 0.005],
            "成交量": [123456, 9876543, 234567, 234567, 12345],
            "成交额": [2e8, 1.2e8, 3.4e7, 2.1e7, 1.2e6],
            "今开": [1605.0, 12.45, 32.0, 87.20, 5.4],
            "最高": [1635.0, 12.55, 33.0, 90.10, 5.6],
            "最低": [1602.0, 12.20, 31.8, 86.90, 5.3],
            "昨收": [1601.30, 12.36, 32.1, 87.27, 5.45],
        }
    )
    df = fetch_spot_snapshot(spot_fetcher=lambda: raw)
    syms = set(df["symbol"])
    assert "600519.SH" in syms
    assert "000001.SZ" in syms
    assert "300750.SZ" in syms
    assert "688981.SH" in syms
    assert "920082.BJ" in syms  # 北交所新代码段（bj 前缀）


def test_spot_normalizes_short_codes() -> None:
    """5 位甚至更短的代码应被 zfill 到 6 位再推断市场。"""
    raw = pd.DataFrame(
        {
            "代码": [600519, 1, 300750],  # int 格式 + 不足 6 位
            "名称": ["a", "b", "c"],
            "最新价": [1.0, 1.0, 1.0],
            "涨跌幅": [0.0, 0.0, 0.0],
            "涨跌额": [0.0, 0.0, 0.0],
            "成交量": [1, 1, 1],
            "成交额": [1.0, 1.0, 1.0],
            "今开": [1.0, 1.0, 1.0],
            "最高": [1.0, 1.0, 1.0],
            "最低": [1.0, 1.0, 1.0],
            "昨收": [1.0, 1.0, 1.0],
        }
    )
    df = fetch_spot_snapshot(spot_fetcher=lambda: raw)
    syms = set(df["symbol"])
    assert "600519.SH" in syms
    assert "000001.SZ" in syms  # 1 → 000001 → 0 开头深市
    assert "300750.SZ" in syms


# ---------------------------- 1m K bars ----------------------------


def _fake_1m_bars(bare_code: str) -> pd.DataFrame:
    """构造拟真的 1m K 返回（5 条 bar）。"""
    base_dt = pd.Timestamp("2026-04-27 09:30:00")
    return pd.DataFrame(
        {
            "时间": [base_dt + pd.Timedelta(minutes=i) for i in range(5)],
            "开盘": [10.0, 10.05, 10.10, 10.15, 10.20],
            "收盘": [10.05, 10.10, 10.15, 10.20, 10.25],
            "最高": [10.06, 10.11, 10.16, 10.21, 10.26],
            "最低": [9.98, 10.04, 10.09, 10.14, 10.19],
            "成交量": [1000, 1500, 800, 2000, 1200],
            "成交额": [10050.0, 15100.0, 8120.0, 20200.0, 12150.0],
        }
    )


def test_fetch_1m_bars_one_field_mapping() -> None:
    """中文字段 → 英文 + 加 symbol 列。"""
    df = fetch_1m_bars_one("600519.SH", bar_fetcher=_fake_1m_bars)
    assert len(df) == 5
    assert list(df.columns) == [
        "symbol", "trade_time", "open", "high", "low", "close",
        "volume", "amount",
    ]
    assert (df["symbol"] == "600519.SH").all()
    # trade_time 应是 datetime
    assert pd.api.types.is_datetime64_any_dtype(df["trade_time"])
    # close 与 open 是 5 条
    assert df["close"].iloc[-1] == 10.25


def test_fetch_1m_bars_one_returns_empty_on_no_data() -> None:
    """接口返回空（停牌票）→ 空 DataFrame，不抛错。"""
    df = fetch_1m_bars_one("600519.SH", bar_fetcher=lambda c: pd.DataFrame())
    assert df.empty


def test_fetch_1m_bars_batch_concurrent_no_data_loss() -> None:
    """50 只票每只 5 条 bar，并发拉取后总数 = 250。"""
    symbols = [f"600{i:03d}.SH" for i in range(50)]
    combined, errors = fetch_1m_bars_batch(
        symbols, max_workers=10, bar_fetcher=_fake_1m_bars,
    )
    assert len(combined) == 50 * 5
    assert errors == []
    # symbol 列覆盖所有输入
    assert set(combined["symbol"]) == set(symbols)


def test_fetch_1m_bars_batch_collects_errors_without_blocking() -> None:
    """部分票抛异常时，其它票仍能成功，错误收集到 errors。"""
    failing_bare = {"600002", "600007"}

    def flaky_fetcher(bare_code: str) -> pd.DataFrame:
        if bare_code in failing_bare:
            raise RuntimeError("simulated network error")
        return _fake_1m_bars(bare_code)

    failing = {f"{c}.SH" for c in failing_bare}

    symbols = [f"600{i:03d}.SH" for i in range(10)]
    combined, errors = fetch_1m_bars_batch(
        symbols, max_workers=5, bar_fetcher=flaky_fetcher,
    )
    # 8 只成功 × 5 bar = 40
    assert len(combined) == 8 * 5
    # 2 个错误
    assert len(errors) == 2
    error_syms = {sym for sym, _ in errors}
    assert error_syms == failing


def test_fetch_1m_bars_batch_empty_input() -> None:
    """空输入应直接返回空，不创建线程池。"""
    combined, errors = fetch_1m_bars_batch([])
    assert combined.empty
    assert errors == []


# ---------------------------- daily K backfill ----------------------------


def _fake_daily_bars(bare_code: str, start: str, end: str) -> pd.DataFrame:
    """构造拟真的日线返回（5 个交易日）。"""
    from datetime import date as _date
    return pd.DataFrame(
        {
            "日期": [_date(2026, 4, 21), _date(2026, 4, 22),
                    _date(2026, 4, 23), _date(2026, 4, 24),
                    _date(2026, 4, 25)],
            "开盘": [10.0, 10.05, 10.10, 10.15, 10.20],
            "收盘": [10.05, 10.10, 10.15, 10.20, 10.25],
            "最高": [10.06, 10.11, 10.16, 10.21, 10.26],
            "最低": [9.98, 10.04, 10.09, 10.14, 10.19],
            "成交量": [10000, 15000, 8000, 20000, 12000],
            "成交额": [1.005e5, 1.51e5, 8.12e4, 2.02e5, 1.215e5],
        }
    )


def test_fetch_daily_bars_one_field_mapping() -> None:
    df = fetch_daily_bars_one(
        "600519.SH", "20260421", "20260425", daily_fetcher=_fake_daily_bars,
    )
    assert len(df) == 5
    assert list(df.columns) == [
        "symbol", "trade_date", "open", "high", "low", "close", "volume", "amount",
    ]
    assert (df["symbol"] == "600519.SH").all()
    # trade_date 是 date 不是 datetime
    from datetime import date as _date
    assert isinstance(df["trade_date"].iloc[0], _date)
    assert df["close"].iloc[-1] == 10.25


def test_fetch_daily_bars_one_empty() -> None:
    df = fetch_daily_bars_one(
        "600519.SH", "20260421", "20260425",
        daily_fetcher=lambda c, s, e: pd.DataFrame(),
    )
    assert df.empty


def test_fetch_daily_bars_batch_concurrent() -> None:
    """20 只票各 5 个交易日 → 总 100 行。"""
    symbols = [f"600{i:03d}.SH" for i in range(20)]
    combined, errors = fetch_daily_bars_batch(
        symbols, "20260421", "20260425",
        max_workers=5, daily_fetcher=_fake_daily_bars,
    )
    assert len(combined) == 20 * 5
    assert errors == []
    assert set(combined["symbol"]) == set(symbols)


def test_fetch_daily_bars_batch_collects_errors() -> None:
    """部分票失败时其它票仍能拿到。"""
    failing_bare = {"600002", "600007"}

    def flaky(bare, s, e):
        if bare in failing_bare:
            raise RuntimeError("akshare 503")
        return _fake_daily_bars(bare, s, e)

    symbols = [f"600{i:03d}.SH" for i in range(10)]
    combined, errors = fetch_daily_bars_batch(
        symbols, "20260421", "20260425", max_workers=5, daily_fetcher=flaky,
    )
    assert len(combined) == 8 * 5
    assert {sym for sym, _ in errors} == {f"{c}.SH" for c in failing_bare}


def test_fetch_daily_bars_batch_empty_input() -> None:
    combined, errors = fetch_daily_bars_batch([], "20260421", "20260425")
    assert combined.empty and errors == []
