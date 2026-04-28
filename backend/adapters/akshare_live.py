"""akshare 实盘行情适配器：spot 快照 + 1m K 拉取。

设计要点：
- akshare API 调用通过函数参数注入（``spot_fetcher`` / ``bar_fetcher``），
  方便单测用 fake 替换；生产环境不传参时延迟 import akshare。
- 字段映射（中文 → 英文）和 symbol 规范化（裸代码 → QMT 格式）集中在本模块，
  让 DAO 层只接收已规范化的 DataFrame。
- 错误处理：spot 调用是"原子性"的（一次 HTTP 拉全市场），失败抛异常让上层重试；
  1m K batch 调用是"逐票独立"的，失败的 symbol 收集到 errors 列表，不影响其它。

akshare 字段参考：
- ``stock_zh_a_spot``（**新浪源**，本项目 spot 实际走这个）：分页拉取，返回中文字段
  ["代码","名称","最新价","涨跌额","涨跌幅","买入","卖出","昨收","今开","最高","最低",
  "成交量","成交额","时间戳"]，约 5000+ 行。**代码格式是 ``sh600519`` / ``sz000001`` /
  ``bj920000``（带前缀的 6 位）**——本模块预处理时剥前缀再 normalize。
  涨跌幅单位百分数（1.23 = 1.23%），转成小数。
  之前用过 ``stock_zh_a_spot_em``（东财 push2），实测限流敏感（连续探测后被 RST），
  改新浪后稳定性大幅提升；新浪源的不利点是分页拉取 → 总响应 10-30s（东财 1-3s）。
- ``stock_zh_a_hist_min_em``：每次返回单只票当日全量 1m K（已 sorted by 时间）；
  注意接口入参是 6 位裸代码（如 "000001"），不是 QMT 格式。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pandas as pd

from backend.adapters.base import normalize_symbol

log = logging.getLogger(__name__)


# spot_em 中文字段 → 内部英文字段
# 仅列出 signal_service 需要的字段；其它（市盈率 / 市净率等）暂不入库。
_SPOT_REQUIRED_FIELDS_ZH = [
    "代码",
    "最新价",
    "涨跌幅",
    "成交量",
    "成交额",
    "今开",
    "最高",
    "最低",
    "昨收",
]
_SPOT_RENAME = {
    "代码": "_raw_code",
    "最新价": "last_price",
    "涨跌幅": "_pct_chg_pct",  # 百分数，下面转小数
    "成交量": "volume",
    "成交额": "amount",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "昨收": "prev_close",
}

# 1m K 接口字段
_BAR_RENAME = {
    "时间": "trade_time",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}

# 日线接口字段（``stock_zh_a_hist`` period='daily'）
_DAILY_BAR_RENAME = {
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",     # 单位：手（akshare 默认）
    "成交额": "amount",      # 单位：元
}


def fetch_spot_snapshot(
    spot_fetcher: Callable[[], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """拉取全市场 spot 快照并规范化。

    Args:
        spot_fetcher: 可注入的 akshare 函数，签名 ``() -> DataFrame``；
            生产环境留 ``None``，会延迟 import ``akshare.stock_zh_a_spot``
            （**新浪源**，比 ``stock_zh_a_spot_em`` 更抗限流）。
            单测时传一个 fake，避免真实 HTTP / akshare 依赖。

    Returns:
        规范化的 DataFrame，列（英文，已转换单位）：
        ``symbol`` (str, QMT 格式)、``last_price`` / ``open`` / ``high`` / ``low``
        / ``prev_close`` (float, 元)、``pct_chg`` (float, 小数 0.01=1%)、
        ``volume`` (int64, 手)、``amount`` (float, 元)、``is_suspended`` (uint8, 0/1)。

    Raises:
        RuntimeError: akshare 返回空 / 缺字段时抛出（让上层重试）。
    """
    if spot_fetcher is None:
        import akshare as ak  # noqa: PLC0415（延迟 import 避免依赖污染）

        spot_fetcher = ak.stock_zh_a_spot

    df_raw = spot_fetcher()
    if df_raw is None or df_raw.empty:
        raise RuntimeError("akshare spot returned empty DataFrame")

    missing = [c for c in _SPOT_REQUIRED_FIELDS_ZH if c not in df_raw.columns]
    if missing:
        raise RuntimeError(
            f"akshare spot missing fields: {missing} "
            f"(available: {list(df_raw.columns)[:10]}...)"
        )

    df = df_raw[_SPOT_REQUIRED_FIELDS_ZH].rename(columns=_SPOT_RENAME)

    # 数值转换：先转 float，缺失值变 NaN
    num_cols = ["last_price", "open", "high", "low", "prev_close", "_pct_chg_pct", "amount"]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

    # symbol 规范化：兼容两种代码格式：
    # - 新浪：``sh600519`` / ``sz000001`` / ``bj920000``（带前缀的 6 位）
    # - 东财 / 裸码：``600519`` / ``000001``
    # normalize_symbol 自身只接受裸 6 位 + QMT + Baostock；这里把新浪前缀剥掉
    # 后再交给它推断 .SH / .SZ / .BJ 后缀。无法识别的行（指数代码混入等）丢弃。
    def _norm(raw: object) -> str | None:
        s = str(raw).strip().lower()
        if len(s) == 8 and s[:2] in ("sh", "sz", "bj"):
            s = s[2:]
        try:
            return normalize_symbol(s.zfill(6))
        except ValueError:
            return None

    df["symbol"] = df["_raw_code"].apply(_norm)
    df = df[df["symbol"].notna()].copy()

    # 涨跌幅：百分数 → 小数（1.23 → 0.0123）。akshare 偶有 NaN，保持 NaN 不强转 0。
    df["pct_chg"] = df["_pct_chg_pct"] / 100.0

    # 停牌判定：last_price == 0 或 amount == 0 视为停牌。NaN 也视为停牌
    # （上游接口不可达时保守降级）。
    df["is_suspended"] = (
        (df["last_price"].fillna(0) == 0) | (df["amount"].fillna(0) == 0)
    ).astype("uint8")

    df = df.drop(columns=["_raw_code", "_pct_chg_pct"])
    return df[
        [
            "symbol",
            "last_price",
            "open",
            "high",
            "low",
            "prev_close",
            "pct_chg",
            "volume",
            "amount",
            "is_suspended",
        ]
    ].reset_index(drop=True)


def fetch_1m_bars_one(
    symbol: str,
    bar_fetcher: Callable[[str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """单只票当日全量 1m K 线。

    akshare ``stock_zh_a_hist_min_em`` 接受 6 位裸代码，period='1' 取 1 分钟线；
    返回字段中文，本函数转换成英文 + 加 symbol 列。

    Args:
        symbol: QMT 格式（如 ``"600519.SH"``）。
        bar_fetcher: 可注入的获取函数，签名 ``(bare_code: str) -> DataFrame``。
            生产环境留 ``None`` 会用 akshare 默认参数（period='1', adjust=''）。

    Returns:
        DataFrame，列：``symbol`` / ``trade_time`` / ``open`` / ``high`` /
        ``low`` / ``close`` / ``volume`` / ``amount``。
        无数据（停牌 / 接口异常）时返回空 DataFrame。
    """
    bare_code = symbol.split(".")[0]

    if bar_fetcher is None:
        import akshare as ak  # noqa: PLC0415

        # period='1' = 1 分钟；adjust='' = 不复权（落库存原始价）
        bar_fetcher = lambda code: ak.stock_zh_a_hist_min_em(  # noqa: E731
            symbol=code, period="1", adjust=""
        )

    df_raw = bar_fetcher(bare_code)
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    missing = [c for c in _BAR_RENAME if c not in df_raw.columns]
    if missing:
        raise RuntimeError(
            f"akshare hist_min_em missing fields for {symbol}: {missing}"
        )

    df = df_raw[list(_BAR_RENAME.keys())].rename(columns=_BAR_RENAME).copy()
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    for col in ["open", "high", "low", "close", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["symbol"] = symbol

    return df[
        ["symbol", "trade_time", "open", "high", "low", "close", "volume", "amount"]
    ].reset_index(drop=True)


def fetch_1m_bars_batch(
    symbols: list[str],
    max_workers: int = 20,
    bar_fetcher: Callable[[str], pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """并发拉取 N 只票当日 1m K，合并成长表。

    Args:
        symbols: QMT 格式 symbol 列表。
        max_workers: 并发数。akshare 实测 ~20 安全（30+ 偶发触发 IP 频控）。
        bar_fetcher: 可注入获取函数，单测用。

    Returns:
        ``(combined_df, errors)``：
        - ``combined_df``：合并后的长表，所有成功 symbol 的 1m K 拼接；空时返空 DF。
        - ``errors``：``[(symbol, error_message), ...]``——失败 symbol 列表，
          不影响成功 symbol 的数据返回。

    设计取舍：
    - 用 ``ThreadPoolExecutor`` 而非 asyncio：akshare 内部用 requests 同步 HTTP，
      asyncio 没有原生支持；线程池在 GIL 释放点（HTTP 等待）能并行。
    - 失败收集而非抛异常：5000 票里有几只接口异常很正常（新股 / 停牌 / 退市），
      不应影响其它票的数据落库。
    """
    if not symbols:
        return pd.DataFrame(), []

    results: list[pd.DataFrame] = []
    errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_sym = {
            ex.submit(fetch_1m_bars_one, s, bar_fetcher): s for s in symbols
        }
        for fut in as_completed(future_to_sym):
            sym = future_to_sym[fut]
            try:
                df = fut.result()
                if not df.empty:
                    results.append(df)
            except Exception as e:  # noqa: BLE001 - 故意宽 except，单点失败不传播
                errors.append((sym, str(e)))
                log.warning("fetch 1m K failed for %s: %s", sym, e)

    combined = (
        pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    )
    return combined, errors


# ---------------------------- 日线 backfill ----------------------------


def fetch_daily_bars_one(
    symbol: str,
    start_date: str,
    end_date: str,
    daily_fetcher: Callable[[str, str, str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """单只票指定区间的日线（用于补 stock_bar_1d 的缺口）。

    akshare ``stock_zh_a_hist(symbol, period='daily', start_date, end_date, adjust='')``
    接受 6 位裸代码 + ``YYYYMMDD`` 格式日期；返回未复权日线。

    Args:
        symbol: QMT 格式（如 ``"600519.SH"``）。
        start_date / end_date: ``YYYYMMDD`` 字符串（akshare 要求）。
        daily_fetcher: 可注入获取函数，签名 ``(bare_code, start, end) -> DataFrame``。

    Returns:
        DataFrame，列：``symbol`` / ``trade_date`` (date) / ``open`` / ``high`` /
        ``low`` / ``close`` / ``volume`` (手) / ``amount`` (元)。
        无数据（停牌 / 全段休市）时返回空 DF。
    """
    bare_code = symbol.split(".")[0]

    if daily_fetcher is None:
        import akshare as ak  # noqa: PLC0415

        daily_fetcher = lambda c, s, e: ak.stock_zh_a_hist(  # noqa: E731
            symbol=c, period="daily", start_date=s, end_date=e, adjust=""
        )

    df_raw = daily_fetcher(bare_code, start_date, end_date)
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    missing = [c for c in _DAILY_BAR_RENAME if c not in df_raw.columns]
    if missing:
        raise RuntimeError(
            f"akshare stock_zh_a_hist missing fields for {symbol}: {missing}"
        )

    df = df_raw[list(_DAILY_BAR_RENAME.keys())].rename(columns=_DAILY_BAR_RENAME).copy()
    # akshare 返回 "日期" 是 datetime.date 或字符串，统一转 date
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for col in ["open", "high", "low", "close", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["symbol"] = symbol

    return df[
        ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    ].reset_index(drop=True)


def fetch_daily_bars_batch(
    symbols: list[str],
    start_date: str,
    end_date: str,
    max_workers: int = 20,
    daily_fetcher: Callable[[str, str, str], pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """并发拉 N 只票指定区间的日线，合并成长表。

    与 ``fetch_1m_bars_batch`` 同款签名（错误隔离 + 线程池）。
    """
    if not symbols:
        return pd.DataFrame(), []

    results: list[pd.DataFrame] = []
    errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_sym = {
            ex.submit(fetch_daily_bars_one, s, start_date, end_date, daily_fetcher): s
            for s in symbols
        }
        for fut in as_completed(future_to_sym):
            sym = future_to_sym[fut]
            try:
                df = fut.result()
                if not df.empty:
                    results.append(df)
            except Exception as e:  # noqa: BLE001
                errors.append((sym, str(e)))
                log.warning("fetch daily K failed for %s: %s", sym, e)

    combined = (
        pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    )
    return combined, errors
