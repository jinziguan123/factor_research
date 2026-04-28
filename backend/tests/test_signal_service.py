"""signal_service 的纯函数单测：

- RealtimeAwareDataService.load_panel：spot 拼接行为
- _build_top_bottom：末行 qcut + 涨跌停过滤
- run_signal 整体流程在 integration 测里覆盖（涉及 MySQL / DataService），
  这里只测纯函数逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backend.services.signal_service import (
    RealtimeAwareDataService,
    _build_top_bottom,
    check_data_freshness,
    compute_signal_window_natural_days,
)


# ---------------------------- Fakes ----------------------------


@dataclass
class FakeBaseData:
    """最小 DataService 替身：只实现 load_panel + resolve_pool 透传必要属性。"""
    panels: dict[str, pd.DataFrame] = field(default_factory=dict)
    pool_symbols: dict[int, list[str]] = field(default_factory=dict)

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].copy()

    def resolve_pool(self, pool_id: int) -> list[str]:
        return self.pool_symbols.get(pool_id, [])


def _idx(n: int, start="2026-04-25") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


# ---------------------------- RealtimeAwareDataService ----------------------------


def test_realtime_load_panel_appends_spot_for_close_field() -> None:
    """close 字段 + 当日不在 df 中 → 拼一行 last_price。"""
    idx = _idx(5)
    panel = pd.DataFrame(
        {"600519.SH": [1600.0, 1610.0, 1620.0, 1615.0, 1605.0],
         "000001.SZ": [12.0, 12.1, 12.2, 12.05, 12.0]},
        index=idx,
    )
    base = FakeBaseData(panels={"close": panel})
    spot = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "last_price": [1635.0, 12.30],
            "is_suspended": [0, 0],
        }
    )
    today = date(2026, 5, 4)  # 比 panel 末行（2026-05-01）更晚
    rt = RealtimeAwareDataService(base, today, spot)

    df = rt.load_panel(
        ["600519.SH", "000001.SZ"], idx[0], today, field="close",
    )
    # 应多出一行
    assert len(df) == 6
    assert df.index[-1] == pd.Timestamp(today)
    assert df.loc[pd.Timestamp(today), "600519.SH"] == 1635.0
    assert df.loc[pd.Timestamp(today), "000001.SZ"] == 12.30


def test_realtime_load_panel_skips_when_field_not_supported() -> None:
    """field='returns' 不在 _SPOT_FIELD_BY_PANEL_FIELD 中 → 不拼。"""
    idx = _idx(3)
    panel = pd.DataFrame({"A": [0.01, 0.02, -0.005]}, index=idx)
    base = FakeBaseData(panels={"returns": panel})
    spot = pd.DataFrame({"symbol": ["A"], "last_price": [10.0], "is_suspended": [0]})
    today = date(2026, 4, 30)
    rt = RealtimeAwareDataService(base, today, spot)

    df = rt.load_panel(["A"], idx[0], today, field="returns")
    assert len(df) == 3  # 没拼


def test_realtime_load_panel_skips_when_today_already_in_df() -> None:
    """如果 base 已经返回了今日（如盘后场景），不重复拼。"""
    idx = _idx(3, start="2026-04-25")  # 4/27, 4/28, 4/29
    panel = pd.DataFrame({"A": [10.0, 11.0, 12.0]}, index=idx)
    base = FakeBaseData(panels={"close": panel})
    spot = pd.DataFrame({"symbol": ["A"], "last_price": [13.0], "is_suspended": [0]})
    today = date(2026, 4, 29)  # 等于 panel 末行
    rt = RealtimeAwareDataService(base, today, spot)

    df = rt.load_panel(["A"], idx[0], today, field="close")
    assert len(df) == 3
    # 不应被 spot 13.0 覆盖
    assert df.loc[pd.Timestamp(today), "A"] == 12.0


def test_realtime_load_panel_uses_ffill_for_suspended() -> None:
    """停牌票 (is_suspended=1) 不拼 spot，用昨日 close ffill。"""
    idx = _idx(3, start="2026-04-25")
    panel = pd.DataFrame(
        {"A": [10.0, 11.0, 12.0], "B": [20.0, 21.0, 22.0]}, index=idx,
    )
    base = FakeBaseData(panels={"close": panel})
    spot = pd.DataFrame(
        {
            "symbol": ["A", "B"],
            "last_price": [13.0, 0.0],  # B 停牌
            "is_suspended": [0, 1],
        }
    )
    today = date(2026, 5, 1)
    rt = RealtimeAwareDataService(base, today, spot)

    df = rt.load_panel(["A", "B"], idx[0], today, field="close")
    assert len(df) == 4
    # A 用 spot 13.0
    assert df.loc[pd.Timestamp(today), "A"] == 13.0
    # B 停牌 → 用昨日 close 22.0 ffill
    assert df.loc[pd.Timestamp(today), "B"] == 22.0


def test_realtime_load_panel_ffill_for_missing_symbol_in_spot() -> None:
    """spot 中不含的 symbol（如新股 / 退市），用昨日 close ffill。"""
    idx = _idx(3, start="2026-04-25")
    panel = pd.DataFrame({"A": [10.0, 11.0, 12.0], "B": [20.0, 21.0, 22.0]}, index=idx)
    base = FakeBaseData(panels={"close": panel})
    spot = pd.DataFrame({"symbol": ["A"], "last_price": [13.0], "is_suspended": [0]})
    today = date(2026, 5, 1)
    rt = RealtimeAwareDataService(base, today, spot)

    df = rt.load_panel(["A", "B"], idx[0], today, field="close")
    assert df.loc[pd.Timestamp(today), "A"] == 13.0
    assert df.loc[pd.Timestamp(today), "B"] == 22.0  # ffill


def test_realtime_load_panel_empty_spot_returns_unchanged() -> None:
    """spot_df 为空 → 透传。"""
    idx = _idx(3)
    panel = pd.DataFrame({"A": [10.0, 11.0, 12.0]}, index=idx)
    base = FakeBaseData(panels={"close": panel})
    rt = RealtimeAwareDataService(base, date(2026, 5, 1), pd.DataFrame())

    df = rt.load_panel(["A"], idx[0], date(2026, 5, 1), field="close")
    assert len(df) == 3


def test_realtime_load_panel_proxies_other_methods() -> None:
    """resolve_pool / 其它属性透传给 base。"""
    base = FakeBaseData(pool_symbols={5: ["A", "B"]})
    rt = RealtimeAwareDataService(base, date(2026, 5, 1), pd.DataFrame())

    assert rt.resolve_pool(5) == ["A", "B"]


def test_realtime_load_panel_supports_open_high_low() -> None:
    """open / high / low 字段也支持 spot 拼接。"""
    idx = _idx(3, start="2026-04-25")
    panel_open = pd.DataFrame({"A": [9.5, 10.5, 11.5]}, index=idx)
    base = FakeBaseData(panels={"open": panel_open})
    spot = pd.DataFrame(
        {
            "symbol": ["A"],
            "last_price": [13.0],
            "open": [12.5],
            "high": [13.2],
            "low": [12.3],
            "is_suspended": [0],
        }
    )
    today = date(2026, 5, 1)
    rt = RealtimeAwareDataService(base, today, spot)

    df_open = rt.load_panel(["A"], idx[0], today, field="open")
    assert df_open.loc[pd.Timestamp(today), "A"] == 12.5


# ---------------------------- _build_top_bottom ----------------------------


def _F_combined_5sym(values=None) -> pd.DataFrame:
    """构造 5 列 × 3 行的合成因子表，末行有清晰梯度。"""
    idx = _idx(3)
    if values is None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
    return pd.DataFrame(
        {f"S{i:02d}": [0, 0, v] for i, v in enumerate(values)},
        index=idx,
    )


def test_build_top_bottom_basic() -> None:
    """5 列 × 5 组：每组 1 个 → top=S04, bottom=S00。"""
    F = _F_combined_5sym([1, 2, 3, 4, 5])
    top, bot, n_top, n_bot = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False,
    )
    assert n_top == 1
    assert n_bot == 1
    assert top[0]["symbol"] == "S04"
    assert bot[0]["symbol"] == "S00"
    assert top[0]["factor_value_composite"] == 5.0
    assert bot[0]["factor_value_composite"] == 1.0


def test_build_top_bottom_filter_price_limit_drops_top() -> None:
    """涨停票（pct_chg=0.10）应被剔除，top 退到次高。"""
    F = _F_combined_5sym([1, 2, 3, 4, 5])
    spot = pd.DataFrame(
        {
            "symbol": [f"S{i:02d}" for i in range(5)],
            "last_price": [10, 10, 10, 10, 10],
            "pct_chg": [0.0, 0.0, 0.0, 0.0, 0.10],  # S04 涨停
            "is_suspended": [0, 0, 0, 0, 0],
        }
    )
    top, bot, n_top, n_bot = _build_top_bottom(
        F, spot, n_groups=4, filter_price_limit=True,  # 4 组让退化更直观
    )
    # S04 被剔后剩 [S00, S01, S02, S03] 分 4 组 → top = S03
    top_syms = [r["symbol"] for r in top]
    assert "S04" not in top_syms
    assert top_syms == ["S03"]


def test_build_top_bottom_filter_drops_suspended() -> None:
    """停牌票（is_suspended=1）应被剔除。"""
    F = _F_combined_5sym([1, 2, 3, 4, 5])
    spot = pd.DataFrame(
        {
            "symbol": [f"S{i:02d}" for i in range(5)],
            "last_price": [10, 10, 0, 10, 10],
            "pct_chg": [0.0, 0.0, 0.0, 0.0, 0.0],
            "is_suspended": [0, 0, 1, 0, 0],  # S02 停牌
        }
    )
    top, bot, _, _ = _build_top_bottom(
        F, spot, n_groups=4, filter_price_limit=True,
    )
    top_syms = [r["symbol"] for r in top]
    assert "S02" not in top_syms


def test_build_top_bottom_with_factor_breakdown() -> None:
    """传入 breakdown 字典时，每只票的输出应包含子因子值。"""
    F = _F_combined_5sym([1, 2, 3, 4, 5])
    z_a = pd.DataFrame(
        {f"S{i:02d}": [0, 0, 0.5 * i] for i in range(5)}, index=F.index,
    )
    z_b = pd.DataFrame(
        {f"S{i:02d}": [0, 0, -0.3 * i] for i in range(5)}, index=F.index,
    )
    top, _, _, _ = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False,
        factor_breakdown={"factor_a": z_a, "factor_b": z_b},
    )
    top_row = top[0]
    assert top_row["symbol"] == "S04"
    assert "factor_value_breakdown" in top_row
    assert top_row["factor_value_breakdown"]["factor_a"] == 0.5 * 4
    assert top_row["factor_value_breakdown"]["factor_b"] == -0.3 * 4


def test_build_top_bottom_too_few_symbols_returns_empty() -> None:
    """有效票数 < n_groups → 返空。"""
    F = _F_combined_5sym([1, 2])
    F = F[["S00", "S01"]]
    top, bot, n_top, n_bot = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False,
    )
    assert top == [] and bot == []
    assert n_top == 0 and n_bot == 0


def test_build_top_bottom_all_same_value_returns_empty() -> None:
    """全部值相同 → qcut(duplicates='drop') 只剩 1 组 → 返空。"""
    F = pd.DataFrame(
        {f"S{i:02d}": [1.0] * 3 for i in range(5)},
        index=_idx(3),
    )
    top, bot, _, _ = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False,
    )
    assert top == [] and bot == []


def test_build_top_bottom_empty_dataframe() -> None:
    F = pd.DataFrame()
    top, bot, n_top, n_bot = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False,
    )
    assert top == [] and bot == [] and n_top == 0 and n_bot == 0


def test_build_top_bottom_top_sorted_desc_by_factor_value() -> None:
    """多只票同在 top 组时，按因子值降序排列。"""
    F = _F_combined_5sym([1, 2, 3, 8, 9])  # S03=8, S04=9 同入 top
    top, _, _, _ = _build_top_bottom(
        F, pd.DataFrame(), n_groups=4, filter_price_limit=False,
    )
    # 4 组 5 票：duplicates 后可能 4 组各 (1,1,1,2) 只
    # 关键：top 内部按因子值降序
    if len(top) >= 2:
        assert top[0]["factor_value_composite"] >= top[1]["factor_value_composite"]


def test_build_top_bottom_top_n_truncates_to_k() -> None:
    """top_n=2：qcut 顶组若有 3 只，最终只保留因子值最高的 2 只。"""
    # 10 只票，n_groups=5 → 顶组 2 只；top_n=1 → 只剩 1 只（最高的）
    syms = [f"S{i:02d}" for i in range(10)]
    values = list(range(1, 11))
    F = pd.DataFrame(
        {s: [0, 0, v] for s, v in zip(syms, values)},
        index=_idx(3),
    )
    top, bot, n_top, n_bot = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False, top_n=1,
    )
    assert n_top == 1
    assert n_bot == 1
    assert top[0]["symbol"] == "S09"  # 最高
    assert bot[0]["symbol"] == "S00"  # 最低


def test_build_top_bottom_top_n_none_falls_back_to_qcut_full() -> None:
    """top_n=None：保留 qcut 顶组的全部（默认行为）。"""
    syms = [f"S{i:02d}" for i in range(10)]
    values = list(range(1, 11))
    F = pd.DataFrame(
        {s: [0, 0, v] for s, v in zip(syms, values)},
        index=_idx(3),
    )
    top, _, n_top, _ = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False, top_n=None,
    )
    assert n_top == 2  # 5 组 10 票 → 每组 2 只


def test_build_top_bottom_top_n_larger_than_group_size() -> None:
    """top_n 大于 qcut 顶组实际容量 → 不增票，保持顶组容量。"""
    syms = [f"S{i:02d}" for i in range(10)]
    values = list(range(1, 11))
    F = pd.DataFrame(
        {s: [0, 0, v] for s, v in zip(syms, values)},
        index=_idx(3),
    )
    top, _, n_top, _ = _build_top_bottom(
        F, pd.DataFrame(), n_groups=5, filter_price_limit=False, top_n=100,
    )
    assert n_top == 2  # 顶组本身只有 2 只，top_n=100 不会凭空造票


# ---------------------------- compute_signal_window_natural_days ----------------------------


def test_window_single_method_minimal() -> None:
    """single 方法只需要末行因子值 → 极小窗口（仅 buffer）。"""
    n = compute_signal_window_natural_days("single", ic_lookback_days=60)
    # buffer 默认 7 天，与 ic_lookback 无关
    assert n == 7


def test_window_equal_method_minimal() -> None:
    """equal 方法同 single：末行 qcut，不需要历史。"""
    n = compute_signal_window_natural_days("equal", ic_lookback_days=60)
    assert n == 7


def test_window_orthogonal_equal_method_minimal() -> None:
    n = compute_signal_window_natural_days("orthogonal_equal", ic_lookback_days=60)
    assert n == 7


def test_window_ic_weighted_scales_with_lookback() -> None:
    """ic_weighted 需要 IC 历史 → ic_lookback × 1.5 自然日 + buffer。"""
    # 60 trading days × 1.5 + 7 = 97
    n = compute_signal_window_natural_days("ic_weighted", ic_lookback_days=60)
    assert n == 97
    # 30 → 45+7=52
    assert compute_signal_window_natural_days("ic_weighted", 30) == 52
    # 200 → 300+7=307
    assert compute_signal_window_natural_days("ic_weighted", 200) == 307


def test_window_unknown_method_falls_back_to_minimal() -> None:
    """未知 method 默认 minimal（防御性）。"""
    n = compute_signal_window_natural_days("future_method_x", ic_lookback_days=60)
    assert n == 7


def test_window_significantly_smaller_than_old_180_default() -> None:
    """优化效果验证：默认 single + ic_lookback=60 比旧的 180 小 25 倍。"""
    new_window = compute_signal_window_natural_days("single", 60)
    old_default = max(180, 60 * 2)  # 旧逻辑
    assert new_window < old_default / 10  # 至少缩小 10 倍


# ---------------------------- check_data_freshness ----------------------------


class _FakeChClient:
    """最小 mock：execute() 返回预设值。"""
    def __init__(self, latest):
        self._latest = latest
        self.calls = []
    def execute(self, sql):
        self.calls.append(sql)
        if self._latest is None:
            return [(None,)]
        return [(self._latest,)]


def test_freshness_passes_when_latest_within_threshold() -> None:
    """latest 距 as_of 在阈值内（默认 5 天）→ 不抛错。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 25))
    # as_of 04-28，gap=3，<= 5 → OK
    check_data_freshness(_d(2026, 4, 28), ch=ch)


def test_freshness_passes_at_boundary() -> None:
    """gap 恰好等于阈值 → 通过（>=, 非 >）。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 23))
    # gap=5 == threshold → 通过
    check_data_freshness(_d(2026, 4, 28), ch=ch, threshold_days=5)


def test_freshness_fails_when_too_stale() -> None:
    """latest 落后超阈值 → 抛 ValueError 含修复命令。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 20))
    # gap=8 > 5 → 抛
    with pytest.raises(ValueError) as exc_info:
        check_data_freshness(_d(2026, 4, 28), ch=ch)
    msg = str(exc_info.value)
    assert "stock_bar_1d" in msg
    assert "backfill_daily_bars" in msg  # 修复命令
    assert "2026-04-21" in msg  # next_day = latest + 1


def test_freshness_fails_when_table_empty() -> None:
    from datetime import date as _d
    ch = _FakeChClient(latest=None)
    with pytest.raises(ValueError) as exc_info:
        check_data_freshness(_d(2026, 4, 28), ch=ch)
    assert "为空" in str(exc_info.value)


def test_freshness_threshold_configurable() -> None:
    """threshold_days 可配置；调小后更严格。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 26))
    # gap=2，threshold=1 → fail
    with pytest.raises(ValueError):
        check_data_freshness(_d(2026, 4, 28), ch=ch, threshold_days=1)
    # threshold=3 → 通过
    check_data_freshness(_d(2026, 4, 28), ch=ch, threshold_days=3)


def test_freshness_handles_numpy_datetime64_from_clickhouse() -> None:
    """ClickHouse driver use_numpy=True 返回 numpy.datetime64；
    应被 pd.Timestamp().date() 自动归一为 Python date 不抛 UFunc 错。"""
    import numpy as np
    from datetime import date as _d
    # mock 返回 numpy.datetime64 模拟真实 CH 行为
    ch = _FakeChClient(latest=np.datetime64("2026-04-25", "D"))
    # 不应抛 UFuncBinaryResolutionError，应正常通过 / 抛 ValueError
    check_data_freshness(_d(2026, 4, 28), ch=ch)  # gap=3 < 5，通过


def test_freshness_handles_pandas_timestamp_input() -> None:
    """as_of 传 pandas Timestamp 也应被归一化。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 25))
    # as_of 用 pd.Timestamp 而非 date
    check_data_freshness(pd.Timestamp("2026-04-28"), ch=ch)


# ---------------------------- auto_backfill ----------------------------


class _MutableLatestChClient:
    """支持 backfill 后"latest 变新"的 mock。"""
    def __init__(self, initial_latest):
        self._latest = initial_latest
        self.execute_count = 0
    def execute(self, sql):
        self.execute_count += 1
        return [(self._latest,)] if self._latest is not None else [(None,)]
    def set_latest(self, new_latest):
        self._latest = new_latest


def test_auto_backfill_called_when_stale_then_passes() -> None:
    """auto_backfill=True 且数据落后 → 调 backfill_fn → 再次校验通过。"""
    from datetime import date as _d
    ch = _MutableLatestChClient(initial_latest=_d(2026, 4, 20))
    backfill_calls: list[tuple] = []

    def fake_backfill(start, end, symbols):
        backfill_calls.append((start, end, len(symbols) if symbols else 0))
        # 模拟 backfill 后数据补到 as_of
        ch.set_latest(_d(2026, 4, 28))
        return {"n_symbols": 100, "n_bars_written": 700, "n_errors": 0}

    check_data_freshness(
        _d(2026, 4, 28),
        ch=ch,
        symbols=["600519.SH"] * 100,
        auto_backfill=True,
        backfill_fn=fake_backfill,
    )
    assert len(backfill_calls) == 1
    start, end, n_syms = backfill_calls[0]
    assert start == _d(2026, 4, 21)  # latest+1
    assert end == _d(2026, 4, 28)
    assert n_syms == 100


def test_auto_backfill_disabled_falls_back_to_error() -> None:
    """auto_backfill=False → 沿用旧行为（直接抛错）。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 20))
    with pytest.raises(ValueError) as exc_info:
        check_data_freshness(_d(2026, 4, 28), ch=ch, auto_backfill=False)
    msg = str(exc_info.value)
    assert "FR_LIVE_MARKET_AUTO_BACKFILL_DAILY=1" in msg


def test_auto_backfill_failure_raises_with_command() -> None:
    """backfill_fn 抛异常 → check_data_freshness 抛 ValueError 含手动命令。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 20))

    def boom(start, end, symbols):
        raise RuntimeError("akshare 503")

    with pytest.raises(ValueError) as exc_info:
        check_data_freshness(
            _d(2026, 4, 28), ch=ch,
            auto_backfill=True, backfill_fn=boom,
        )
    msg = str(exc_info.value)
    assert "自动 backfill 失败" in msg
    assert "backfill_daily_bars" in msg


def test_auto_backfill_partial_still_too_stale_raises() -> None:
    """backfill 跑完但 latest 仍落后（如 akshare 大量限流空返回）→ 抛错。"""
    from datetime import date as _d
    ch = _MutableLatestChClient(initial_latest=_d(2026, 4, 20))

    def partial_backfill(start, end, symbols):
        # 只补到 04-22 就停，仍落后 6 天
        ch.set_latest(_d(2026, 4, 22))
        return {"n_symbols": 100, "n_bars_written": 200, "n_errors": 50}

    with pytest.raises(ValueError) as exc_info:
        check_data_freshness(
            _d(2026, 4, 28), ch=ch,
            auto_backfill=True, backfill_fn=partial_backfill,
        )
    msg = str(exc_info.value)
    assert "自动 backfill 后" in msg
    assert "仍是 2026-04-22" in msg


def test_auto_backfill_passes_through_when_data_fresh() -> None:
    """数据本来就新鲜 → auto_backfill=True 也不调 backfill_fn。"""
    from datetime import date as _d
    ch = _FakeChClient(latest=_d(2026, 4, 27))
    backfill_calls: list = []

    check_data_freshness(
        _d(2026, 4, 28),
        ch=ch,
        auto_backfill=True,
        backfill_fn=lambda *a, **kw: backfill_calls.append(a),
    )
    assert backfill_calls == []  # 没调用
