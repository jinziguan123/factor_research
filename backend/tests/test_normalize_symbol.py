"""normalize_symbol 单测：覆盖各市场代码段，含北交所新代码 920xxx / 930xxx。"""
from __future__ import annotations

import pytest

from backend.adapters.base import normalize_symbol


# ---------------------------- QMT 已是标准格式 ----------------------------


def test_qmt_format_passthrough() -> None:
    assert normalize_symbol("600000.SH") == "600000.SH"
    assert normalize_symbol("000001.SZ") == "000001.SZ"
    assert normalize_symbol("920471.BJ") == "920471.BJ"


def test_qmt_format_lowercase_normalized() -> None:
    assert normalize_symbol("600000.sh") == "600000.SH"
    assert normalize_symbol(" 000001.sz ") == "000001.SZ"


# ---------------------------- baostock 格式 ----------------------------


def test_baostock_format() -> None:
    assert normalize_symbol("sh.600000") == "600000.SH"
    assert normalize_symbol("sz.000001") == "000001.SZ"
    assert normalize_symbol("bj.920000") == "920000.BJ"


# ---------------------------- 裸 6 位代码（按首位推断）----------------------------


def test_bare_code_shanghai_main_board() -> None:
    """6 开头 → 沪市主板。"""
    assert normalize_symbol("600000") == "600000.SH"  # 工行（举例）
    assert normalize_symbol("601318") == "601318.SH"
    assert normalize_symbol("603259") == "603259.SH"
    assert normalize_symbol("688981") == "688981.SH"  # 科创板


def test_bare_code_shanghai_b_share_legacy() -> None:
    """9 开头（已停的 B 股）保留沪市归类（兼容历史）。"""
    assert normalize_symbol("900901") == "900901.SH"


def test_bare_code_shanghai_fund() -> None:
    """5 开头 → 沪市基金 / ETF / LOF。"""
    assert normalize_symbol("510050") == "510050.SH"


def test_bare_code_shenzhen() -> None:
    """0 开头 → 深市主板；3 开头 → 创业板。"""
    assert normalize_symbol("000001") == "000001.SZ"
    assert normalize_symbol("002594") == "002594.SZ"
    assert normalize_symbol("300750") == "300750.SZ"


def test_bare_code_beijing_legacy_4_8() -> None:
    """旧北交所代码段：4 / 8 开头。"""
    assert normalize_symbol("430489") == "430489.BJ"
    assert normalize_symbol("835174") == "835174.BJ"


def test_bare_code_beijing_new_92_93() -> None:
    """**关键修复**：北交所 2023 年起的新代码段 92xxxx / 93xxxx。

    之前 9 开头被错误归类为沪市 .SH，导致 SymbolResolver 在 stock_symbol
    表（这些票存为 920xxx.BJ）中查不到，spot 数据写入丢失。
    """
    assert normalize_symbol("920000") == "920000.BJ"
    assert normalize_symbol("920471") == "920471.BJ"
    assert normalize_symbol("920957") == "920957.BJ"
    assert normalize_symbol("930001") == "930001.BJ"
    assert normalize_symbol("930999") == "930999.BJ"


def test_bare_code_unknown_first_digit_raises() -> None:
    """1 开头（暂无 A 股代码段使用）→ 抛 ValueError 让调用方明确处理。"""
    with pytest.raises(ValueError):
        normalize_symbol("100000")


# ---------------------------- 异常输入 ----------------------------


def test_invalid_format_raises() -> None:
    """非标准格式应抛 ValueError，不静默降级。"""
    with pytest.raises(ValueError):
        normalize_symbol("INVALID")
    with pytest.raises(ValueError):
        normalize_symbol("60000")  # 5 位数字
    with pytest.raises(ValueError):
        normalize_symbol("")
