"""实盘行情 DAO：spot 快照 + 1m K 的批量写入与查询。

设计要点：
- 接收适配器层规范化后的 DataFrame（含 ``symbol`` 字符串列），
  在 DAO 内部用 ``SymbolResolver`` 转换为 ``symbol_id`` 后写入 ClickHouse。
- 写入采用 ``columnar=True`` 列式批量插入，与 ``data_service.save_factor_values``
  保持同款，避免 dict 写入挂起的坑。
- 查询函数（``latest_spot_snapshot``）面向"取最新一条"场景，用 ``argMax`` 聚合
  避免触发 ReplacingMergeTree 的 ``FINAL``（FINAL 强制 merge，性能差）。
- ``ch`` client 通过参数注入支持单测；生产留 ``None`` 走 ``ch_client()`` 默认。
- **表不存在友好降级**：``latest_spot_age_sec`` / ``latest_spot_snapshot`` 捕获
  ClickHouse Code=60（Unknown table）→ 返 None / empty，让上层 service 自然
  降级到"昨日 close"模式。这是 migration 008 没跑时的友好保护，避免日志被
  完整堆栈刷爆。写入函数不捕获该错误（写不进去应该 fail-fast）。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import numpy as np
import pandas as pd
from clickhouse_driver.errors import ServerException

from backend.storage.clickhouse_client import ch_client
from backend.storage.symbol_resolver import SymbolResolver

log = logging.getLogger(__name__)

# ClickHouse 错误码：60 = Unknown table（migration 008 未跑时触发）
_CH_UNKNOWN_TABLE = 60
_MIGRATION_008_HINT = (
    "请先跑 migration 008 创建 stock_spot_realtime + stock_bar_1m 表："
    "clickhouse-client --query=\"$(cat backend/scripts/migrations/008_realtime_market_tables.sql)\""
)

_SPOT_TABLE = "quant_data.stock_spot_realtime"
_BAR_1M_TABLE = "quant_data.stock_bar_1m"

# spot 表的字段顺序（必须与 INSERT 语句一致；columnar 写入靠位置匹配）
_SPOT_COLUMNS = [
    "symbol_id",
    "snapshot_at",
    "trade_date",
    "last_price",
    "open",
    "high",
    "low",
    "prev_close",
    "pct_chg",
    "volume",
    "amount",
    "bid1",
    "ask1",
    "is_suspended",
    "version",
]

# 1m K 表的字段顺序
_BAR_1M_COLUMNS = [
    "symbol_id",
    "trade_time",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "version",
]


# ---------------------------- 写入 ----------------------------


def write_spot_snapshot(
    df: pd.DataFrame,
    snapshot_at: datetime,
    *,
    resolver: SymbolResolver | None = None,
    ch=None,
) -> int:
    """把规范化后的 spot DataFrame 批量写入 ``stock_spot_realtime``。

    Args:
        df: 来自 ``akshare_live.fetch_spot_snapshot`` 的输出，需包含字段：
            ``symbol`` / ``last_price`` / ``open`` / ``high`` / ``low`` /
            ``prev_close`` / ``pct_chg`` / ``volume`` / ``amount`` / ``is_suspended``。
            **不要求** 包含 ``bid1`` / ``ask1``（spot_em 接口不直接给，
            缺失时落 0.0 占位，下游遇 0 视为未提供）。
        snapshot_at: 拉取时刻（秒精度），全行共用一个值（同一 batch 同时刻）。
        resolver: SymbolResolver 实例，用于 symbol → symbol_id；None 时新建一个。
        ch: clickhouse client（依赖注入，单测用）；None 时新建。

    Returns:
        实际写入的行数（已剔除无法 resolve 的 symbol）。
    """
    if df is None or df.empty:
        return 0

    if resolver is None:
        resolver = SymbolResolver()

    # symbol → symbol_id；未知 symbol 丢弃（不写脏数据）
    sym_map = resolver.resolve_many(df["symbol"].tolist())
    df = df[df["symbol"].isin(sym_map)].copy()
    if df.empty:
        log.warning("write_spot_snapshot: 所有 symbol 都无法 resolve，跳过写入")
        return 0

    n = len(df)
    trade_date = snapshot_at.date()
    version = time.time_ns()  # batch 内全行同一版本号；后写 batch 覆盖前一个

    # 缺失字段补默认值（spot_em 不提供 bid1/ask1）
    bid1 = df.get("bid1", pd.Series(0.0, index=df.index)).fillna(0.0)
    ask1 = df.get("ask1", pd.Series(0.0, index=df.index)).fillna(0.0)

    # 列式 numpy 数组（顺序必须与 _SPOT_COLUMNS / INSERT 字段顺序一致）
    columns_np = [
        df["symbol"].map(sym_map).to_numpy(dtype=np.uint32),  # symbol_id
        np.array([snapshot_at] * n, dtype=object),            # snapshot_at
        np.array([trade_date] * n, dtype=object),             # trade_date
        df["last_price"].fillna(0.0).to_numpy(dtype=np.float32),
        df["open"].fillna(0.0).to_numpy(dtype=np.float32),
        df["high"].fillna(0.0).to_numpy(dtype=np.float32),
        df["low"].fillna(0.0).to_numpy(dtype=np.float32),
        df["prev_close"].fillna(0.0).to_numpy(dtype=np.float32),
        df["pct_chg"].fillna(0.0).to_numpy(dtype=np.float32),
        df["volume"].fillna(0).to_numpy(dtype=np.uint64),
        df["amount"].fillna(0.0).to_numpy(dtype=np.float64),
        bid1.to_numpy(dtype=np.float32),
        ask1.to_numpy(dtype=np.float32),
        df["is_suspended"].fillna(0).to_numpy(dtype=np.uint8),
        np.array([version] * n, dtype=np.uint64),
    ]

    sql = (
        f"INSERT INTO {_SPOT_TABLE} ({', '.join(_SPOT_COLUMNS)}) VALUES"
    )
    if ch is None:
        with ch_client() as c:
            c.execute(sql, columns_np, columnar=True)
    else:
        ch.execute(sql, columns_np, columnar=True)
    return n


def write_1m_bars(
    df: pd.DataFrame,
    *,
    resolver: SymbolResolver | None = None,
    ch=None,
) -> int:
    """把 1m K 长表写入 ``stock_bar_1m``。

    Args:
        df: 长表，必需字段：``symbol`` / ``trade_time`` / ``open`` / ``high`` /
            ``low`` / ``close`` / ``volume`` / ``amount``。
        resolver: SymbolResolver；None 新建。
        ch: ClickHouse client，None 新建。

    Returns:
        写入行数。
    """
    if df is None or df.empty:
        return 0

    if resolver is None:
        resolver = SymbolResolver()

    sym_map = resolver.resolve_many(df["symbol"].unique().tolist())
    df = df[df["symbol"].isin(sym_map)].copy()
    if df.empty:
        log.warning("write_1m_bars: 所有 symbol 都无法 resolve，跳过写入")
        return 0

    n = len(df)
    version = time.time_ns()

    df["symbol_id"] = df["symbol"].map(sym_map).astype("uint32")
    df["trade_date"] = pd.to_datetime(df["trade_time"]).dt.date

    columns_np = [
        df["symbol_id"].to_numpy(dtype=np.uint32),
        np.asarray(df["trade_time"].to_numpy(), dtype=object),
        np.asarray(df["trade_date"].to_numpy(), dtype=object),
        df["open"].fillna(0.0).to_numpy(dtype=np.float32),
        df["high"].fillna(0.0).to_numpy(dtype=np.float32),
        df["low"].fillna(0.0).to_numpy(dtype=np.float32),
        df["close"].fillna(0.0).to_numpy(dtype=np.float32),
        df["volume"].fillna(0).to_numpy(dtype=np.uint64),
        df["amount"].fillna(0.0).to_numpy(dtype=np.float64),
        np.array([version] * n, dtype=np.uint64),
    ]

    sql = (
        f"INSERT INTO {_BAR_1M_TABLE} ({', '.join(_BAR_1M_COLUMNS)}) VALUES"
    )
    if ch is None:
        with ch_client() as c:
            c.execute(sql, columns_np, columnar=True)
    else:
        ch.execute(sql, columns_np, columnar=True)
    return n


# ---------------------------- 查询 ----------------------------


def latest_spot_snapshot(
    symbols: list[str],
    *,
    trade_date: date | None = None,  # 保留参数兼容旧调用，但内部不再使用
    resolver: SymbolResolver | None = None,
    ch=None,
) -> pd.DataFrame:
    """查每只票"最新一条" spot 记录（按 snapshot_at 取最大）。

    用 ``argMax(field, snapshot_at)`` 聚合而非 ``FINAL``——后者强制 merge 全分区，
    在盘中频繁查询场景下很慢；前者在 5000 行 / 1 个分区内快得多。

    **不再按 trade_date 过滤**：实测客户端 / 服务端时区不一致时，
    ``WHERE trade_date = %(td)s`` 会出现"写入了但查不到"的诡异现象。
    改为只按 snapshot_at 取最新——上层用 ``latest_spot_age_sec`` + stale 阈值
    判断"是否够新鲜"，跨日的旧 spot 自然会被视为陈旧不被使用。
    ``trade_date`` 字段保留作为分区键不变。

    Args:
        symbols: QMT 格式 symbol 列表。
        trade_date: 已废弃（保留参数避免调用方修改），内部不使用。
        resolver: SymbolResolver。
        ch: client。

    Returns:
        DataFrame，列：``symbol`` / ``snapshot_at`` / ``last_price`` / ...
        若 symbols 全无效或表无数据，返空 DF。
    """
    if not symbols:
        return pd.DataFrame()

    if resolver is None:
        resolver = SymbolResolver()

    sym_map = resolver.resolve_many(symbols)
    if not sym_map:
        return pd.DataFrame()
    sids = list(sym_map.values())

    # 注意：``max(snapshot_at) AS snapshot_at`` 在 ClickHouse 22+ 的新 query
    # analyzer 下会被解析成"聚合函数的输出又被 argMax 当参数"——报 Code 184
    # "Aggregate function found inside another aggregate function"。
    # 用不同别名 ``snapshot_at_max`` 避开，Python 端再 rename 回 ``snapshot_at``。
    sql = f"""
        SELECT
            symbol_id,
            max(snapshot_at) AS snapshot_at_max,
            argMax(last_price, snapshot_at) AS last_price,
            argMax(open, snapshot_at) AS open,
            argMax(high, snapshot_at) AS high,
            argMax(low, snapshot_at) AS low,
            argMax(prev_close, snapshot_at) AS prev_close,
            argMax(pct_chg, snapshot_at) AS pct_chg,
            argMax(volume, snapshot_at) AS volume,
            argMax(amount, snapshot_at) AS amount,
            argMax(is_suspended, snapshot_at) AS is_suspended
        FROM {_SPOT_TABLE}
        WHERE symbol_id IN %(sids)s
        GROUP BY symbol_id
    """
    params = {"sids": tuple(sids)}

    try:
        if ch is None:
            with ch_client() as c:
                rows = c.execute(sql, params, with_column_types=True)
        else:
            rows = ch.execute(sql, params, with_column_types=True)
    except ServerException as e:
        if getattr(e, "code", None) == _CH_UNKNOWN_TABLE:
            log.warning(
                "stock_spot_realtime 表不存在；返空 DF 让上层降级到昨日 close 模式。%s",
                _MIGRATION_008_HINT,
            )
            return pd.DataFrame()
        raise

    data, col_types = rows
    if not data:
        return pd.DataFrame()
    cols = [name for name, _ in col_types]
    df = pd.DataFrame(data, columns=cols)
    # 把 SQL 端为避语法冲突起的别名复原，让上层（signal_service / 前端）
    # 看到的字段名仍是 snapshot_at。
    if "snapshot_at_max" in df.columns:
        df = df.rename(columns={"snapshot_at_max": "snapshot_at"})

    # symbol_id → symbol 反查
    inv_map = {sid: sym for sym, sid in sym_map.items()}
    df["symbol"] = df["symbol_id"].map(inv_map)
    df = df.drop(columns=["symbol_id"])
    return df.reset_index(drop=True)


def latest_spot_age_sec(
    *,
    trade_date: date | None = None,  # 已废弃，保留参数避免调用方改动
    ch=None,
) -> float | None:
    """库里最新一条 spot 距当前 NOW() 的秒数。

    用途：signal_service 启动时判断"实时数据是否新鲜"——若 > 600（10min），
    自动降级到 use_realtime=false 模式（用昨日 close 替代）。

    **不再按 trade_date 过滤**（同 ``latest_spot_snapshot`` 的修改）：
    时区错位会让"写入了但查不到"。改为全表 max(snapshot_at)，跨日数据
    会被 stale 阈值自然识别为陈旧。

    Returns:
        秒数（float）；表为空 / 不存在时返 None。
    """
    sql = f"SELECT max(snapshot_at) FROM {_SPOT_TABLE}"
    try:
        if ch is None:
            with ch_client() as c:
                rows = c.execute(sql)
        else:
            rows = ch.execute(sql)
    except ServerException as e:
        if getattr(e, "code", None) == _CH_UNKNOWN_TABLE:
            log.warning(
                "stock_spot_realtime 表不存在；返 None 让上层降级。%s",
                _MIGRATION_008_HINT,
            )
            return None
        raise
    if not rows or rows[0][0] is None:
        return None
    last_ts = rows[0][0]
    # ClickHouse driver 在 use_numpy=True 模式下把 DateTime 列返回成
    # numpy.datetime64，与 Python datetime 直接相减抛 ufunc 错；统一转 Timestamp
    # 后用 .to_pydatetime() 拿 Python datetime。
    try:
        last_ts = pd.Timestamp(last_ts).to_pydatetime()
    except Exception:  # noqa: BLE001 - 任何异常都视为"读不出"，让上层降级
        return None
    return (datetime.now() - last_ts).total_seconds()
