"""DataService：因子研究平台对外统一的行情 / 股票池访问入口。

API 定位（严格遵守）：
- ``load_bars(symbols, start, end, freq, adjust, fields)``：按 symbol 维度返回
  ``dict[symbol, DataFrame]``；DataFrame 的 index 为 ``DatetimeIndex``（按日升序），
  列包含 ``fields`` 指定的子集，至少覆盖 ``open/high/low/close/volume/amount_k``。
- ``load_panel(symbols, start, end, freq, field, adjust)``：返回单字段宽表
  DataFrame（``index=date``, ``columns=symbol``）。内部复用 ``load_bars`` 后 pivot。
- ``resolve_pool(pool_id)``：JOIN ``stock_pool_symbol`` + ``stock_symbol``，
  按 ``sort_order`` 返回 symbol 列表。

实现要点 / 约束：
- **频率**：MVP 只支持 ``freq="1d"``；其它频率抛 ``NotImplementedError``，
  让调用方尽早失败（避免静默返回空数据造成上层回测误判）。
- **数据源**：日线从 ClickHouse ``stock_bar_1d`` 表读，**存的是未复权价**；
  若 ``adjust="qfq"``，从 MySQL ``fr_qfq_factor`` 表拿每日因子，逐日乘到 OHLC 上。
- **qfq 对齐**：factor 表按 (symbol_id, trade_date) 主键；``_load_qfq_factors``
  两阶段查询——窗口内因子 + 每个 sid 的左侧 seed 行（``trade_date < start`` 的最近
  一条），组合后用 ``reindex(method="ffill")`` 向前填充，保证查询窗口首日就能
  拿到有效因子。若某 sid 在因子表里**完全没有记录**，退回 factor=1.0 并打 warning。
- **SQL 注入防御**：ClickHouse / MySQL 都用参数化；整型列表通过 tuple/list 传入。
- **返回类型**：不要让调用方再做 ``pd.to_datetime`` 这一步，index 在本层就转好。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

from backend.storage.clickhouse_client import ch_client
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver

logger = logging.getLogger(__name__)

# 日频 K 线默认返回字段集合；amount_k 单位为千元（见 init_clickhouse.sql 注释）。
_DAILY_FIELDS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount_k",
)

# qfq 因子需要作用的价格列（volume/amount_k 不受因子影响）。
_PRICE_COLS: tuple[str, ...] = ("open", "high", "low", "close")


class DataService:
    """因子研究平台对外统一的行情 / 股票池访问入口。"""

    def __init__(self) -> None:
        # SymbolResolver 的 lru_cache 按实例隔离；一个 DataService 只持一个
        # resolver，命中率已足够。如需全局共享缓存，后续可重构 resolver 为模块单例。
        self.resolver = SymbolResolver()

    # ---------------------------- 公共 API ----------------------------

    def load_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        freq: Literal["1d", "1m", "5m", "15m", "30m", "60m"] = "1d",
        adjust: Literal["none", "qfq"] = "qfq",
        fields: tuple[str, ...] = _DAILY_FIELDS,
    ) -> dict[str, pd.DataFrame]:
        """按 symbol 维度返回行情 DataFrame。

        Args:
            symbols: symbol 字符串列表（如 ``["000001.SZ"]``），未知 symbol 自动过滤。
            start / end: 日期闭区间。
            freq: 频率。MVP 仅支持 ``"1d"``。
            adjust: ``"none"`` 返回原价；``"qfq"`` 做前复权。
            fields: 需要保留的字段子集。

        Returns:
            ``{symbol: DataFrame}``。DataFrame index 为 DatetimeIndex（日期升序）。
            无数据时返回空 dict。
        """
        if freq != "1d":
            raise NotImplementedError(
                f"DataService.load_bars 暂不支持 freq={freq!r}，"
                "MVP 仅实现日频；请扩展聚合脚本或分钟线读取逻辑后再开启。"
            )

        sid_map = self.resolver.resolve_many(symbols)
        if not sid_map:
            return {}
        # sid_map.values() 可能含重复（调用方传入大小写 / 空格不同但规范化后同一 symbol，
        # 或本就显式重复）。ClickHouse IN 子句对重复值不敏感，但 MySQL 的 IN
        # 在 pymysql 参数化下会重复占位，下游 factor_map 也只按唯一 sid 组织；
        # 用 sorted(set(...)) 去重并保证稳定顺序，便于测试 / 日志对齐。
        sid_list = sorted(set(sid_map.values()))

        # 从 ClickHouse 拉未复权 OHLCV；FINAL 保证 ReplacingMergeTree 合并后的结果。
        with ch_client() as ch:
            rows = ch.execute(
                """
                SELECT symbol_id, trade_date, open, high, low, close, volume, amount_k
                FROM quant_data.stock_bar_1d FINAL
                WHERE symbol_id IN %(sids)s
                  AND trade_date BETWEEN %(s)s AND %(e)s
                ORDER BY symbol_id, trade_date
                """,
                {"sids": sid_list, "s": start, "e": end},
            )
        if not rows:
            return {}

        df = pd.DataFrame(
            rows,
            columns=[
                "symbol_id",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount_k",
            ],
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        # 价格列强制 float，便于后续与因子做浮点乘法（CH 读出来是 Float32 / Decimal）。
        for col in _PRICE_COLS:
            df[col] = df[col].astype("float64")

        if adjust == "qfq":
            factor_map = self._load_qfq_factors(sid_list, start, end)
            df = self._apply_qfq(df, factor_map)

        # 反向映射 symbol_id -> 原始 symbol 字符串
        inv = {sid: sym for sym, sid in sid_map.items()}
        requested_fields = list(fields)
        out: dict[str, pd.DataFrame] = {}
        for sid, g in df.groupby("symbol_id", sort=False):
            sym = inv.get(int(sid))
            if sym is None:
                continue
            frame = (
                g.drop(columns=["symbol_id"])
                .set_index("trade_date")
                .sort_index()
            )
            frame.index.name = "trade_date"
            # 只保留调用方要求的字段；若 fields 里含未知列，直接抛 KeyError
            # 是有意的（拼写错误应尽早暴露）。
            out[sym] = frame[requested_fields]
        return out

    def load_panel(
        self,
        symbols: list[str],
        start: date,
        end: date,
        freq: str = "1d",
        field: str = "close",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """返回单字段宽表：``index=trade_date``, ``columns=symbol``。

        无数据时返回空 DataFrame（调用方 ``panel.empty`` 判断即可）。
        """
        bars = self.load_bars(
            symbols, start, end, freq=freq, adjust=adjust, fields=(field,)
        )
        if not bars:
            return pd.DataFrame()
        # 用 pd.concat + keys 构造 MultiIndex 再 unstack 效率不如直接拼 Series。
        # 这里直接把每个 symbol 的单字段 Series 拼成宽表。
        panel = pd.concat(
            {sym: frame[field] for sym, frame in bars.items()}, axis=1
        ).sort_index()
        panel.columns.name = None
        return panel

    def save_factor_values(
        self,
        factor_id: str,
        factor_version: int,
        params_hash: str,
        frame: pd.DataFrame,
    ) -> int:
        """把因子宽表写入 ClickHouse ``factor_value_1d``，返回写入行数。

        Args:
            factor_id: 因子主键，对应 ``fr_factor_meta.factor_id``。
            factor_version: 因子版本号（随代码哈希变更递增），和 ``factor_id`` + ``params_hash``
                联合定位一份缓存。
            params_hash: 参数的 40 字符 SHA-1 摘要（``services.params_hash.params_hash``），
                匹配 ClickHouse ``FixedString(40)``。
            frame: 因子宽表；``index=DatetimeIndex``（trade_date），``columns=symbol``（str），
                值为 float 因子值。NaN 行会被丢弃。

        Returns:
            实际写入的长表行数（已剔除 NaN 与无法解析的 symbol 列）。空表 / 全 NaN 会返回 0。

        实现要点：
        - 用 ``resolver.resolve_many`` 批量把 symbol 转 symbol_id，失败列直接跳过
          （而不是写入后数据对不上 ``fr_factor_meta``）。
        - ``version`` 用毫秒时间戳（``time.time() * 1000``）——满足
          ``ReplacingMergeTree(version)`` 的单调要求；单机 ms 级时间戳不会重复。
          同 run 内重复写 save 的一批行，因毫秒仍在递增，版本更高者覆盖旧行，
          结果数据不会翻倍（``SELECT ... FINAL`` 只保留最新版本）。
        - ``clickhouse-driver`` 在 ``use_numpy=True`` 下 INSERT 必须走 ``columnar=True``
          且每列是 ndarray；dtype 与 schema 对齐：``UInt32`` 用 ``np.uint32``，
          ``Date`` 用 ``object`` 承载 ``datetime.date``，``Float64`` 用 ``np.float64``。
        """
        if frame is None or frame.empty:
            return 0

        cols = [str(c) for c in frame.columns]
        if not cols:
            return 0
        sid_map = self.resolver.resolve_many(cols)
        if not sid_map:
            # 没有任何 symbol 能解析，直接跳过——既不报错也不写入，由调用方日志兜底。
            logger.warning(
                "save_factor_values: 所有列 %s 都无法 resolve 为 symbol_id，跳过写入",
                cols,
            )
            return 0

        # 只保留能 resolve 的列，并按列顺序转长格式：每行 (trade_date, symbol, value)。
        keep_cols = [c for c in cols if c in sid_map]
        sub = frame[keep_cols]
        # ``DataFrame.stack`` 默认会丢 NaN，正好与“只写有值的 (sid, date)”语义吻合。
        long = sub.stack().rename("value").reset_index()
        # stack 后两列默认名根据原 index/columns name 来；这里用位置更稳，
        # 兼容 index.name 为 None 或 'trade_date' 等不同来源。
        long.columns = ["trade_date", "symbol", "value"]
        if long.empty:
            return 0

        long["symbol_id"] = long["symbol"].map(sid_map).astype("uint32")
        # index 可能是 Timestamp；ClickHouse Date 列接受 datetime.date。
        long["trade_date"] = pd.to_datetime(long["trade_date"]).dt.date

        n = len(long)
        # 毫秒时间戳作 ReplacingMergeTree 的 version：单机 ms 级单调足够；
        # 若未来出现同进程毫秒级连写同一 (factor_id, version, phash, sid, date)，
        # 新版本号仍 > 旧版本号（batch 全行共用同一版本号时，后写的 batch 覆盖前一个）。
        version = int(time.time() * 1000)

        # clickhouse-driver 在 use_numpy=True + columnar=True 下，
        # **只接受 list-of-ndarray**（按列顺序）；传 dict 会让驱动静默挂起。
        # conftest.seed_bar_1d 已有同款写法，这里对齐。
        columns_np = [
            np.array([factor_id] * n, dtype=object),
            np.array([factor_version] * n, dtype=np.uint32),
            np.array([params_hash] * n, dtype=object),
            long["symbol_id"].to_numpy(dtype=np.uint32),
            np.asarray(long["trade_date"].to_numpy(), dtype=object),
            long["value"].to_numpy(dtype=np.float64),
            np.array([version] * n, dtype=np.uint64),
        ]
        with ch_client() as ch:
            ch.execute(
                "INSERT INTO quant_data.factor_value_1d "
                "(factor_id, factor_version, params_hash, symbol_id, "
                "trade_date, value, version) VALUES",
                columns_np,
                columnar=True,
            )
        return n

    def load_factor_values(
        self,
        factor_id: str,
        factor_version: int,
        params_hash: str,
        symbols: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """从 ``factor_value_1d`` 读取因子宽表；无数据时返回空 DataFrame。

        pivot 为宽表：``index=DatetimeIndex``, ``columns=symbol``（用 resolver
        反查 ``symbol_id``）。
        """
        if not symbols:
            return pd.DataFrame()
        sid_map = self.resolver.resolve_many(symbols)
        if not sid_map:
            return pd.DataFrame()
        sid_list = sorted(set(sid_map.values()))

        with ch_client() as ch:
            rows = ch.execute(
                """
                SELECT symbol_id, trade_date, value
                FROM quant_data.factor_value_1d FINAL
                WHERE factor_id=%(fid)s
                  AND factor_version=%(fv)s
                  AND params_hash=%(ph)s
                  AND symbol_id IN %(sids)s
                  AND trade_date BETWEEN %(s)s AND %(e)s
                """,
                {
                    "fid": factor_id,
                    "fv": int(factor_version),
                    "ph": params_hash,
                    "sids": sid_list,
                    "s": start,
                    "e": end,
                },
            )
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["symbol_id", "trade_date", "value"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 反查 symbol 字符串；保留写入时的原始字符串形态（resolver.resolve_many 的 key 是原 symbol）。
        inv = {sid: sym for sym, sid in sid_map.items()}
        df["symbol"] = df["symbol_id"].map(inv)
        # 极少数 id 不在 inv（symbol 列表传入后被去重）时丢弃；但正常不会发生。
        df = df.dropna(subset=["symbol"])

        panel = df.pivot(index="trade_date", columns="symbol", values="value")
        panel = panel.sort_index()
        panel.columns.name = None
        return panel

    def resolve_pool(self, pool_id: int, as_of: date | None = None) -> list[str]:
        """按 sort_order 升序返回股票池内的 symbol 列表。

        Args:
            pool_id: ``stock_pool.pool_id``。
            as_of: 预留参数，MVP 阶段 ``stock_pool_symbol`` 无时间版本字段，
                任何 ``as_of`` 都返回当前成员；参数保留是为了后续支持动态成分。

        Returns:
            symbol 字符串列表，如 ``["000001.SZ", "600519.SH"]``。
            池不存在或为空时返回空列表。
        """
        # as_of 当前未使用，占位以便未来扩展而不破坏调用方签名。
        _ = as_of
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    SELECT b.symbol
                    FROM stock_pool_symbol s
                    JOIN stock_symbol b ON b.symbol_id = s.symbol_id
                    WHERE s.pool_id = %s
                    ORDER BY s.sort_order, s.symbol_id
                    """,
                    (pool_id,),
                )
                return [row["symbol"] for row in cur.fetchall()]

    # ---------------------------- 内部辅助 ----------------------------

    def _load_qfq_factors(
        self, sid_list: list[int], start: date, end: date
    ) -> dict[int, pd.Series]:
        """批量读取 ``fr_qfq_factor``，返回 ``{symbol_id: Series(index=date, value=factor)}``。

        两阶段查询策略（避免"窗口内无因子就退回 1.0"导致的错误价格）：

        1. **window 查询**：拉 [start, end] 窗口内的所有 (sid, trade_date, factor)。
        2. **seed 查询**：对每个 sid，取 ``trade_date < start`` 中最大的一条
           （即窗口左侧最接近的一次因子落点）作为 seed 行；合并进最终 Series 后，
           上层 ``_apply_qfq`` 的 ``reindex(method="ffill")`` 就能把这条因子传播
           到 ``start`` 那一天及之后的所有行。

        为什么不再用"窗口向左扩展 N 天"的旧做法：
        - 旧做法假设最后一次因子写入距查询窗口不超过 30 天；对活跃股这没问题，
          但对长期停牌 / 因子文件长期未更新的股，会取不到因子，ffill 失败后被
          ``_apply_qfq`` 的 ``fillna(1.0)`` 吞掉，产出**错误价格**。
        - seed 查询按 sid 分组取最大 trade_date，O(sid 数)，代价极低。

        若 seed + window 都为空，说明该 sid 在 ``fr_qfq_factor`` 表里根本没有任何
        记录，上层会 warn 后退到 factor=1.0（原价）。
        """
        if not sid_list:
            return {}

        sid_tuple = tuple(sid_list)

        # 1) 窗口内的因子。pymysql 支持 ``IN %s`` + tuple 参数化，自动展开为
        #    ``IN (a, b, c)``，避免手工拼接 placeholders。
        sql_window = (
            "SELECT symbol_id, trade_date, factor "
            "FROM fr_qfq_factor "
            "WHERE symbol_id IN %s AND trade_date BETWEEN %s AND %s"
        )
        # 2) seed 行：每个 sid 取 trade_date < start 的最大一条。
        #    用 JOIN 子查询而不是窗口函数，兼容低版本 MySQL。
        sql_seed = (
            "SELECT f.symbol_id, f.trade_date, f.factor "
            "FROM fr_qfq_factor f "
            "JOIN ("
            "    SELECT symbol_id, MAX(trade_date) AS md "
            "    FROM fr_qfq_factor "
            "    WHERE symbol_id IN %s AND trade_date < %s "
            "    GROUP BY symbol_id"
            ") m ON f.symbol_id = m.symbol_id AND f.trade_date = m.md"
        )
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(sql_window, (sid_tuple, start, end))
                window_rows = cur.fetchall()
                cur.execute(sql_seed, (sid_tuple, start))
                seed_rows = cur.fetchall()

        # 合并：seed 行按其真实 trade_date 放进去，后面 reindex(ffill) 会自然把它
        # 传播到 start 那一天。window 行放在后面（其实顺序不影响，dict 按日期去重）。
        all_rows = list(seed_rows) + list(window_rows)
        if not all_rows:
            return {}
        # 分组聚合成 Series；同一 (sid, date) 上 seed 与 window 若重复，window 覆盖 seed
        # （因为后 insert），但实际上 seed 的日期 < start < window 最小日期，不会冲突。
        buckets: dict[int, dict[pd.Timestamp, float]] = {}
        for r in all_rows:
            sid = int(r["symbol_id"])
            buckets.setdefault(sid, {})[
                pd.to_datetime(r["trade_date"])
            ] = float(r["factor"])
        return {
            sid: pd.Series(pairs).sort_index()
            for sid, pairs in buckets.items()
        }

    def _apply_qfq(
        self, df: pd.DataFrame, factor_map: dict[int, pd.Series]
    ) -> pd.DataFrame:
        """把每日因子乘到 OHLC 四列上；volume / amount_k 不受影响。

        对齐方式：
        - 因子 Series 的 index 可能和 bar 的 trade_date 不完全重合
          （因子来自 Parquet，bar 来自行情），通过 ``reindex(method="ffill")``
          以 bar 的日期为基准向前填充；
        - ``_load_qfq_factors`` 已经把窗口左侧的 seed 行补进来，所以正常情况下
          ffill 不会留下 NaN；
        - 若某 sid 根本不在 factor_map（seed + window 都为空），或 reindex 后仍
          有 NaN（理论上只可能是 seed 查询也空），说明该 sid 在 fr_qfq_factor
          里**没有任何记录**。此时发 warning 并回退到 factor=1.0，便于开发者
          调查数据缺失；价格本身按未复权原价返回。
        """
        if not factor_map:
            # 因子表完全空时，所有参与计算的 sid 都无因子，逐个 warn 太吵；
            # 这里只用一个聚合 warning 提示上游——调用方应确认是否预期。
            sids = df["symbol_id"].unique().tolist()
            logger.warning(
                "fr_qfq_factor 中 sid %s 均无任何记录，全部回退 factor=1.0", sids
            )
            return df
        df = df.copy()
        for sid in df["symbol_id"].unique():
            sid_int = int(sid)
            series = factor_map.get(sid_int)
            if series is None or series.empty:
                logger.warning(
                    "sid %s 无任何 qfq 因子记录，回退 factor=1.0", sid_int
                )
                continue
            mask = df["symbol_id"] == sid
            idx = df.loc[mask, "trade_date"]
            # reindex 要求目标 index 单调，这里 idx 来自 SQL ORDER BY，已有序。
            factors_series = series.reindex(idx, method="ffill")
            if factors_series.isna().any():
                # 正常情况下 seed 查询已经把左侧锚点补进来，到这里还 NaN 说明
                # seed 和 window 都空——等价于 factor_map.get(sid) 为空。
                # 但我们还是显式处理一次，避免 NaN 污染价格列。
                logger.warning(
                    "sid %s 部分日期无 qfq 因子（ffill 后仍为 NaN），"
                    "这些行回退 factor=1.0",
                    sid_int,
                )
            # 这里显式用 pandas 的 fillna：numpy 的 isnan 对 object dtype 不稳。
            factors = factors_series.fillna(1.0).to_numpy()
            for col in _PRICE_COLS:
                df.loc[mask, col] = df.loc[mask, col].to_numpy() * factors
        return df
