"""MarketAdapter 抽象 + symbol 规范化。

职责：
- 定义所有"市场相关规则"应该暴露的接口（涨跌停规则、交易日历来源、行业分类源
  等），让核心评估 / 回测引擎对市场无关；
- 提供统一的 symbol 规范化工具：本平台对外**一律使用 QMT 格式**（``600000.SH`` /
  ``000001.SZ``）；各 adapter 内部负责输入 / 输出转换。

MVP 阶段只落地一条 A 股链路（``CnMarketAdapter``），暴露的接口先保持最小：
``normalize_symbol`` + ``from_baostock_symbol`` + ``to_baostock_symbol``。未来接
港股 / 美股时再横向扩展。
"""
from __future__ import annotations

import re
from typing import Literal

Market = Literal["CN", "HK", "US"]

# QMT 标准格式：``6 位数字.SH|SZ|BJ``；大小写敏感，后缀大写。
_QMT_SYMBOL_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")

# Baostock 格式：``sh.600000`` / ``sz.000001`` / ``bj.xxxxxx``，前缀小写。
_BAOSTOCK_SYMBOL_RE = re.compile(r"^(sh|sz|bj)\.(\d{6})$")


def normalize_symbol(raw: str) -> str:
    """把任意来源的 A 股代码规范化到 QMT 格式（``600000.SH``）。

    支持输入：
    - ``600000.SH`` / ``000001.sz``（大小写混写） → ``600000.SH`` / ``000001.SZ``
    - ``sh.600000`` / ``sz.000001``（Baostock）   → ``600000.SH`` / ``000001.SZ``
    - ``600000``（裸代码）                         → 按 6 位数字首位推断市场

    对无法识别的输入抛 ``ValueError``，调用方必须显式处理（例如 Baostock 返回一个
    临时/异常代码时应记 WARN 并跳过，而不是默默写一条错的进 DB）。
    """
    s = raw.strip().upper()

    # QMT 已是标准格式（可能只是大小写问题）
    m = _QMT_SYMBOL_RE.match(s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"

    # Baostock（先 lower 再匹配）
    m = _BAOSTOCK_SYMBOL_RE.match(raw.strip().lower())
    if m:
        prefix, code = m.group(1), m.group(2)
        return f"{code}.{prefix.upper()}"

    # 裸代码：按首位数字推断 A 股市场（沪/深/北）
    if re.fullmatch(r"\d{6}", s):
        code = s
        # 北交所优先判定：旧 4/8 开头 + 新 92/93 段（2023 年北交所新增代码段，
        # 必须先于 9 开头的沪市规则，否则 920xxx / 930xxx 会被误判为沪市）。
        if code[0] in "48" or code[:2] in ("92", "93"):
            return f"{code}.BJ"
        # 沪市：6 开头（A 股主板）/ 5 开头（基金）/ 9 开头（已停的 B 股 900xxx，保留兼容）
        if code[0] in "69" or code.startswith("5"):
            return f"{code}.SH"
        if code[0] in "03":
            return f"{code}.SZ"

    raise ValueError(f"cannot normalize symbol: {raw!r}")


def to_baostock_symbol(symbol: str) -> str:
    """QMT 格式 → Baostock 格式。``600000.SH`` → ``sh.600000``。"""
    m = _QMT_SYMBOL_RE.match(symbol.strip().upper())
    if not m:
        raise ValueError(f"not a QMT symbol: {symbol!r}")
    code, suffix = m.group(1), m.group(2)
    return f"{suffix.lower()}.{code}"


def infer_exchange(symbol: str) -> str:
    """从 QMT 格式的 symbol 提取交易所。``600000.SH`` → ``SH``。"""
    m = _QMT_SYMBOL_RE.match(symbol.strip().upper())
    if not m:
        raise ValueError(f"not a QMT symbol: {symbol!r}")
    return m.group(2)


class MarketAdapter:
    """市场相关规则的抽象接口。

    MVP 阶段仅定义占位，Phase 1 不强制实现所有方法；随着后续接入涨跌停、交易日
    历的扩展，相关子类（``CnMarketAdapter`` / ``HkMarketAdapter`` / ``UsMarketAdapter``）
    会逐步把子方法填满。这里先留接口，让调用方从一开始就按"市场无关"的方式写代码。
    """

    market: Market

    def price_limit_allowed(self, symbol: str) -> bool:
        """该标的当日是否允许涨跌停交易（A 股会返回 False 代表一字板不可成交）。

        Phase 1 占位；后续在 ``CnMarketAdapter`` 里结合 ``fr_daily_basic`` 的涨跌停
        价格实现。
        """
        raise NotImplementedError

    def allow_intraday_turnaround(self) -> bool:
        """是否允许日内回转（T+0）。A 股 False，港股/美股 True。"""
        raise NotImplementedError


class CnMarketAdapter(MarketAdapter):
    """A 股 MarketAdapter。Phase 1 只给出常量，逻辑占位。"""

    market: Market = "CN"

    def allow_intraday_turnaround(self) -> bool:
        return False
