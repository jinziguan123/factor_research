"""纯函数单元测试：``backend.scripts.importers`` 子包下的解析 / 归一化辅助函数。

不触库、不落磁盘（除 tmp_path 内的合成 .DAT）；跑得快，可作为回归网。

覆盖：
- ``_bar_rows.compress_amount_to_k``
- ``_bar_rows.VALID_MINUTE_SLOTS``（检查 9:30 排除 + 12:00 午休排除等关键点）
- ``_bar_rows.normalize_symbol_bar_frame``（字段校验、槽位过滤、去重、amount 压缩）
- ``_qmt_mmap._period_dir`` / ``get_dat_file_path`` / ``read_iquant_mmap``
  (用 ``IQUANT_DTYPE`` 构造一个 2 条记录的 tiny .DAT)
- ``_state._decode_market``
- ``stock_1m._incremental_start_ts``
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest


# ------------------------- _bar_rows -------------------------


def test_compress_amount_to_k_basic():
    from backend.scripts.importers._bar_rows import compress_amount_to_k

    assert compress_amount_to_k(0) == 0
    assert compress_amount_to_k(999) == 1  # 四舍五入
    assert compress_amount_to_k(1500) == 2  # 1.5 → 2
    assert compress_amount_to_k(12_345) == 12
    # 负数防御：max(0, ...) 兜底
    assert compress_amount_to_k(-500) == 0


def test_valid_minute_slots_boundaries():
    from backend.scripts.importers._bar_rows import VALID_MINUTE_SLOTS

    # 9:30 (570) 必须被排除 —— iQuant 第一根 K 线是 9:31
    assert 570 not in VALID_MINUTE_SLOTS
    assert 571 in VALID_MINUTE_SLOTS  # 9:31
    assert 690 in VALID_MINUTE_SLOTS  # 11:30
    assert 691 not in VALID_MINUTE_SLOTS  # 11:31 午休开始
    assert 780 not in VALID_MINUTE_SLOTS  # 13:00
    assert 781 in VALID_MINUTE_SLOTS  # 13:01
    assert 900 in VALID_MINUTE_SLOTS  # 15:00
    assert 901 not in VALID_MINUTE_SLOTS  # 15:01 收盘后
    # 总共 240 根：上午 120 + 下午 120
    assert len(VALID_MINUTE_SLOTS) == 240


def test_normalize_symbol_bar_frame_happy_path():
    from backend.scripts.importers._bar_rows import normalize_symbol_bar_frame

    idx = pd.to_datetime(
        [
            "2024-03-01 09:31:00",  # slot 571 ✓
            "2024-03-01 09:30:00",  # slot 570 ✗（会被过滤）
            "2024-03-01 12:00:00",  # slot 720 ✗（午休）
            "2024-03-01 13:01:00",  # slot 781 ✓
        ]
    )
    frame = pd.DataFrame(
        {
            "open": [10.0, 9.9, 10.1, 10.2],
            "high": [10.5, 10.0, 10.2, 10.4],
            "low": [9.8, 9.8, 10.0, 10.1],
            "close": [10.2, 9.95, 10.15, 10.3],
            "volume": [100.0, 50.0, 0.0, 200.0],
            "amount": [1_000_000.0, 500_000.0, 0.0, 2_500_000.0],
        },
        index=idx,
    )

    rows = normalize_symbol_bar_frame(symbol_id=7, frame=frame)
    # 两条合法槽位被保留
    assert len(rows) == 2

    # 第 1 条
    d1, slot1, sid1, o, h, l, c, v, ak = rows[0]
    assert sid1 == 7
    assert d1 == date(2024, 3, 1)
    assert slot1 == 571
    assert o == pytest.approx(10.0)
    assert v == 100
    assert ak == 1_000  # 1_000_000 元 → 1_000 千元

    # 第 2 条：13:01
    assert rows[1][1] == 781
    assert rows[1][8] == 2_500  # amount_k


def test_normalize_symbol_bar_frame_empty_and_missing_columns():
    from backend.scripts.importers._bar_rows import normalize_symbol_bar_frame

    assert normalize_symbol_bar_frame(1, pd.DataFrame()) == []

    bad = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
        index=pd.to_datetime(["2024-03-01 10:00"]),
    )
    with pytest.raises(ValueError, match="missing"):
        normalize_symbol_bar_frame(1, bad)


def test_normalize_symbol_bar_frame_dedup_last_wins():
    """同一分钟索引重复时，保留最后一条，与 mmap reader 去重口径一致。"""
    from backend.scripts.importers._bar_rows import normalize_symbol_bar_frame

    idx = pd.to_datetime(["2024-03-01 09:31:00", "2024-03-01 09:31:00"])
    frame = pd.DataFrame(
        {
            "open": [10.0, 11.0],
            "high": [10.5, 11.5],
            "low": [9.8, 10.8],
            "close": [10.2, 11.2],
            "volume": [100.0, 200.0],
            "amount": [1_000.0, 2_000.0],
        },
        index=idx,
    )
    rows = normalize_symbol_bar_frame(symbol_id=3, frame=frame)
    assert len(rows) == 1
    # last wins：open 应该是 11.0
    assert rows[0][3] == pytest.approx(11.0)


# ------------------------- _qmt_mmap -------------------------


def test_period_dir_values():
    from backend.scripts.importers._qmt_mmap import _period_dir

    assert _period_dir("1m") == "60"
    assert _period_dir("1d") == "86400"
    with pytest.raises(ValueError):
        _period_dir("5m")


def test_get_dat_file_path_layout(tmp_path):
    from backend.scripts.importers._qmt_mmap import get_dat_file_path

    p = get_dat_file_path("000001.SZ", period="1m", base_dir=tmp_path)
    # 平台无关比较：用 Path 归一化
    from pathlib import Path

    assert Path(p) == tmp_path / "SZ" / "60" / "000001.DAT"

    with pytest.raises(ValueError, match="symbol must look like"):
        get_dat_file_path("000001", base_dir=tmp_path)


def _write_tiny_iquant_dat(path, records: list[tuple[int, int, int, int, int, int, int]]):
    """构造一个合法的 iQuant .DAT 测试文件。

    ``records`` 每项：``(time_ts, open_i, high_i, low_i, close_i, volume, amount)``，
    其中价格是 int（= 真价×1000），time_ts 是 UTC 秒。

    文件结构：8 字节 header + N × 56 字节记录。
    """
    from backend.scripts.importers._qmt_mmap import IQUANT_DTYPE

    arr = np.zeros(len(records), dtype=IQUANT_DTYPE)
    for i, (t, o, h, l, c, v, a) in enumerate(records):
        arr[i]["time"] = t
        arr[i]["open"] = o
        arr[i]["high"] = h
        arr[i]["low"] = l
        arr[i]["close"] = c
        arr[i]["volume"] = v
        arr[i]["amount"] = a
    with open(path, "wb") as f:
        f.write(b"\x00" * 8)  # header 占位
        f.write(arr.tobytes())


def test_read_iquant_mmap_roundtrip(tmp_path):
    from backend.scripts.importers._qmt_mmap import read_iquant_mmap

    # 构造两条：2024-03-01 09:31 和 09:32（北京时间）
    # 北京 09:31 → UTC 01:31 → epoch = 2024-03-01T01:31:00Z
    import calendar

    t1 = calendar.timegm((2024, 3, 1, 1, 31, 0, 0, 0, 0))
    t2 = t1 + 60

    path = tmp_path / "000001.DAT"
    _write_tiny_iquant_dat(
        path,
        [
            (t1, 10_000, 10_500, 9_800, 10_200, 100, 1_020_000),
            (t2, 10_200, 10_600, 10_100, 10_400, 120, 1_250_000),
        ],
    )

    df = read_iquant_mmap(path)
    assert len(df) == 2
    # 价格被 /1000 还原
    assert df["open"].iloc[0] == pytest.approx(10.0)
    assert df["close"].iloc[1] == pytest.approx(10.4)
    # 时间转成北京时间（tz-naive），09:31 和 09:32
    assert df.index[0] == pd.Timestamp("2024-03-01 09:31:00")
    assert df.index[1] == pd.Timestamp("2024-03-01 09:32:00")


def test_read_iquant_mmap_start_ts_filter(tmp_path):
    from backend.scripts.importers._qmt_mmap import read_iquant_mmap
    import calendar

    t1 = calendar.timegm((2024, 3, 1, 1, 31, 0, 0, 0, 0))
    t2 = t1 + 60
    t3 = t2 + 60

    path = tmp_path / "000001.DAT"
    _write_tiny_iquant_dat(
        path,
        [
            (t1, 10_000, 10_500, 9_800, 10_200, 100, 1_020_000),
            (t2, 10_200, 10_600, 10_100, 10_400, 120, 1_250_000),
            (t3, 10_400, 10_700, 10_300, 10_500, 80, 800_000),
        ],
    )

    # 只保留 t2 及之后
    df = read_iquant_mmap(path, start_ts=t2)
    assert len(df) == 2
    assert df.index[0] == pd.Timestamp("2024-03-01 09:32:00")


def test_read_iquant_mmap_missing_file_returns_empty(tmp_path):
    from backend.scripts.importers._qmt_mmap import read_iquant_mmap

    df = read_iquant_mmap(tmp_path / "does_not_exist.DAT")
    assert df.empty


def test_read_iquant_mmap_header_only_returns_empty(tmp_path):
    from backend.scripts.importers._qmt_mmap import read_iquant_mmap

    path = tmp_path / "empty.DAT"
    path.write_bytes(b"\x00" * 4)  # 不足 8 字节 header
    assert read_iquant_mmap(path).empty


# ------------------------- _state -------------------------


def test_decode_market_valid_and_invalid():
    from backend.scripts.importers._state import _decode_market

    code, mid, norm = _decode_market("000001.SZ")
    assert (code, mid, norm) == ("000001", 2, "000001.SZ")

    code, mid, norm = _decode_market("600000.sh")  # 小写大写均可
    assert (code, mid, norm) == ("600000", 1, "600000.SH")

    code, mid, norm = _decode_market("830000.BJ")
    assert (code, mid, norm) == ("830000", 3, "830000.BJ")

    with pytest.raises(ValueError):
        _decode_market("000001")  # 缺 market 后缀
    with pytest.raises(ValueError):
        _decode_market("00001.SZ")  # code 不是 6 位
    with pytest.raises(ValueError):
        _decode_market("000001.US")  # 未支持市场


# ------------------------- stock_1m 纯函数 -------------------------


def test_incremental_start_ts_none_input_returns_none():
    from backend.scripts.importers.stock_1m import _incremental_start_ts

    assert _incremental_start_ts(None, rewind_days=3) is None


def test_incremental_start_ts_rewinds_and_is_tz_stable():
    """(D - rewind) 的北京零点对应 UTC 秒 = pd.Timestamp(naive).timestamp() - 28800。"""
    from backend.scripts.importers.stock_1m import _incremental_start_ts
    import calendar

    last = date(2024, 3, 10)
    ts = _incremental_start_ts(last, rewind_days=3)
    # 北京 2024-03-07 00:00 = UTC 2024-03-06 16:00
    expected = calendar.timegm((2024, 3, 6, 16, 0, 0, 0, 0, 0))
    assert ts == expected


def test_incremental_start_ts_rewind_lower_bound():
    """rewind_days<=0 会被 max(1, ...) 兜成 1，避免 start_ts == last 时边界漏数据。"""
    from backend.scripts.importers.stock_1m import _incremental_start_ts

    ts0 = _incremental_start_ts(date(2024, 3, 10), rewind_days=0)
    ts1 = _incremental_start_ts(date(2024, 3, 10), rewind_days=1)
    assert ts0 == ts1
