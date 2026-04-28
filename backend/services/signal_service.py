"""实盘信号服务（SignalService）：盘中 / 用户触发的因子选股。

流程（``run_signal``）：
1. 状态 → ``running``、started_at = now；解析 body 参数；
2. 拉取最近一次 spot 快照（``realtime_dao.latest_spot_snapshot``）；
3. 检查 spot 新鲜度（``latest_spot_age_sec``）；> 600s 自动降级 use_realtime=False；
4. 构造 ``RealtimeAwareDataService`` 包装层：让因子的 ``load_panel`` 调用在
   close / open / high / low / volume / amount 字段上自动拼接"今日 spot 一行"；
5. 历史窗口取 ``[as_of_date - max(180, ic_lookback_days * 2), as_of_date]``，
   逐个因子用 ``composition_service._load_or_compute_factor`` 加载（含缓存）；
6. ``_zscore_per_day`` + ``_combine_{equal,weighted,orthogonal_equal}``（复用）；
7. 对合成因子的**最后一行**做 qcut，取 top / bottom 组，剔除涨跌停 / 停牌票；
8. 写 ``fr_signal_runs.payload_json``，更新 status='success'。

关键设计取舍：
- **复用 composition_service**：合成 / IC 加权 / IC 贡献 全部 import，避免双份维护；
- **RealtimeAwareDataService 装饰**：让现有因子代码完全不改；
- **只取末行而非全 W 矩阵**：信号场景不需要回测期内的历史持仓，只要"今日的"，
  避免无谓计算成本（_build_weights 内部还会再 qcut 历史每天，浪费）；
- **复权一致性近似**：spot last_price 是不复权价，与历史 qfq close 不在同一基准上，
  但 A 股交易日内复权因子不变，今日没有除权除息时拼接合理；除权日（rare）可能
  带来一行异常因子值，由 dropna / qcut.duplicates='drop' 自然吸收。
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from backend.runtime.factor_registry import FactorRegistry
from backend.services.composition_service import (
    _align_frames,
    _combine_equal,
    _combine_orthogonal_equal,
    _combine_weighted,
    _compute_ic_contributions,
    _compute_ic_weights,
    _load_or_compute_factor,
    _nan_to_none,
    _zscore_per_day,
)
from backend.services import metrics
from backend.storage import realtime_dao
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

# 当 spot 数据距 NOW > 此秒数 → 自动降级 use_realtime=False
_SPOT_STALE_THRESHOLD_SEC = 600
# 涨跌停阈值（与 backtest_service._compute_price_limit_mask 一致）
_PRICE_LIMIT_THRESHOLD = 0.097
# 历史窗口最小 buffer（自然日）：兜节假日，让因子至少有几天有效输出
_MIN_NATURAL_DAYS_BUFFER = 7
# IC 加权回看窗口的自然日折算系数：trading days × 1.5 ≈ natural days
_IC_LOOKBACK_NATURAL_FACTOR = 1.5


def compute_signal_window_natural_days(
    method: str, ic_lookback_days: int,
) -> int:
    """signal 场景下，service 层应往前拉多少自然日的"因子值输出窗口"。

    - 单因子 / equal / orthogonal_equal：只需要 ``as_of_date`` 这天的横截面就能
      取末行 qcut，理论上 0 天就够；给 7 天 buffer 兜节假日。
    - ic_weighted：需要历史 IC 序列做加权，至少要 ic_lookback_days 个交易日
      的因子值输出 → 折成 ~1.5× 自然日 + buffer。

    注意：因子 ``compute`` 内部会**额外**往前推 ``required_warmup`` 去加载
    底层 K 线，所以最终拉的 K 线窗口 = ``natural_days + max(factor_warmup)``。
    本函数只决定 service 层的"输出窗口"。
    """
    if method == "ic_weighted":
        return int(ic_lookback_days * _IC_LOOKBACK_NATURAL_FACTOR) + _MIN_NATURAL_DAYS_BUFFER
    return _MIN_NATURAL_DAYS_BUFFER

# spot DataFrame 字段 → factor.load_panel 的 field 参数 映射
_SPOT_FIELD_BY_PANEL_FIELD = {
    "close": "last_price",       # 当下成交价 = 今日 close 估计
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
    "amount": "amount",
    "amount_k": "amount",        # 历史日 K 用 amount_k（千元），spot 用 amount（元）；这里近似
}


# ---------------------------- 内部辅助 ----------------------------


def _update_status(
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """更新 fr_signal_runs 状态字段（与 composition_service._update_status 同构）。"""
    sets: list[str] = []
    vals: list[Any] = []
    if status is not None:
        sets.append("status=%s")
        vals.append(status)
    if progress is not None:
        sets.append("progress=%s")
        vals.append(progress)
    if error is not None:
        sets.append("error_message=%s")
        vals.append(error)
    if started:
        sets.append("started_at=%s")
        vals.append(datetime.now())
    if finished:
        sets.append("finished_at=%s")
        vals.append(datetime.now())
    if not sets:
        return
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE fr_signal_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


# ---------------------------- 实时数据装饰层 ----------------------------


class RealtimeAwareDataService:
    """包装 ``DataService``，在 ``load_panel`` 时把今日 spot 拼到末尾。

    覆写 ``load_panel`` 一个方法；其它属性 / 方法（resolve_pool /
    load_factor_values / save_factor_values 等）通过 ``__getattr__`` 透传给 base，
    无需为每个因子加过载。

    spot_df 期望字段：``symbol`` / ``last_price`` / ``open`` / ``high`` / ``low`` /
    ``volume`` / ``amount`` / ``is_suspended``（来自 ``latest_spot_snapshot``）。
    """

    def __init__(
        self,
        base: DataService,
        as_of_date: date,
        spot_df: pd.DataFrame | None,
    ) -> None:
        self._base = base
        self._as_of_date = pd.Timestamp(as_of_date)
        self._spot_df = spot_df if spot_df is not None else pd.DataFrame()

    def __getattr__(self, name: str) -> Any:
        """除 load_panel 外的方法 / 属性透传给 base。"""
        return getattr(self._base, name)

    def load_panel(
        self,
        symbols: list[str],
        start,
        end,
        freq: str = "1d",
        field: str = "close",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """加载历史日 K 后，按需把"今日 spot 一行"拼到末尾。

        触发条件（全部满足才拼）：
        - 请求窗口包含 as_of_date（end >= as_of_date）；
        - field 在 _SPOT_FIELD_BY_PANEL_FIELD 中（close/open/high/low/volume/amount[_k]）；
        - df 中尚未包含 as_of_date 这一行（避免重复拼）；
        - spot_df 非空。

        缺失票（spot 中没有 / 停牌）按"昨日值 ffill"——保持因子计算的连续性，
        不要硬塞 NaN 到末行触发整列 NaN 传播。
        """
        df = self._base.load_panel(
            symbols, start, end, freq=freq, field=field, adjust=adjust
        )
        if self._spot_df.empty:
            return df
        if field not in _SPOT_FIELD_BY_PANEL_FIELD:
            return df
        end_ts = pd.Timestamp(end)
        if self._as_of_date > end_ts:
            return df
        if self._as_of_date in df.index:
            return df

        spot_field = _SPOT_FIELD_BY_PANEL_FIELD[field]
        if spot_field not in self._spot_df.columns:
            return df

        # 用 spot 数据构造今日一行；缺失票从 df 末行 ffill。
        today_values = pd.Series(np.nan, index=df.columns, dtype="float64")
        spot_lookup = self._spot_df.set_index("symbol")
        for sym in df.columns:
            if sym in spot_lookup.index:
                row = spot_lookup.loc[sym]
                # 停牌票不拼 spot 数据，让 ffill 取昨日
                if row.get("is_suspended", 0) == 0:
                    today_values[sym] = float(row[spot_field])

        # 缺失位 ffill：用 df 当前最后一行兜底（如果有）
        if not df.empty:
            today_values = today_values.fillna(df.iloc[-1])

        today_df = pd.DataFrame(
            [today_values.values], index=[self._as_of_date], columns=df.columns,
        )
        return pd.concat([df, today_df])


# ---------------------------- 选股逻辑 ----------------------------


def _build_top_bottom(
    F_combined: pd.DataFrame,
    spot_df: pd.DataFrame,
    n_groups: int,
    filter_price_limit: bool,
    factor_breakdown: dict[str, pd.DataFrame] | None = None,
    top_n: int | None = None,
) -> tuple[list[dict], list[dict], int, int]:
    """对合成因子最后一行做 qcut，取 top / bottom 组。

    Args:
        F_combined: 合成因子宽表（以 z-score 后的口径）。
        spot_df: 当日 spot DataFrame，用于查 last_price / pct_chg / is_suspended
            和涨跌停判断；可空（use_realtime=False 时）。
        n_groups: 分组数。
        filter_price_limit: 是否剔除涨停 / 跌停 / 停牌票。
        factor_breakdown: 可选，每个原始因子的 z-score 宽表，用于 payload 中
            展示"该票在每个子因子的得分"——单因子时传 None。

    Returns:
        (top, bottom, n_top, n_bot)：
        - top / bottom：list of dict，每只票的 {symbol, factor_value_composite,
          factor_value_breakdown, last_price, pct_chg}。
        - n_top / n_bot：剔除涨跌停后实际入选数。
    """
    if F_combined.empty:
        return [], [], 0, 0

    last_date = F_combined.index[-1]
    last_row = F_combined.loc[last_date].dropna()

    # 涨跌停 / 停牌过滤
    if filter_price_limit and not spot_df.empty:
        ban: set[str] = set()
        for _, r in spot_df.iterrows():
            sym = r["symbol"]
            if int(r.get("is_suspended", 0)):
                ban.add(sym)
            elif abs(float(r.get("pct_chg", 0))) >= _PRICE_LIMIT_THRESHOLD:
                ban.add(sym)
        last_row = last_row[~last_row.index.isin(ban)]

    if len(last_row) < n_groups:
        return [], [], 0, 0

    # qcut 分组：duplicates='drop' 容忍因子值并列
    try:
        labels = pd.qcut(
            last_row, q=n_groups, labels=False, duplicates="drop"
        ).dropna()
    except ValueError:
        return [], [], 0, 0
    if labels.empty:
        return [], [], 0, 0

    top_label = int(labels.max())
    bot_label = int(labels.min())
    if top_label == bot_label:
        # 全部值相同 → 无法分组
        return [], [], 0, 0

    top_syms = labels[labels == top_label].index.tolist()
    bot_syms = labels[labels == bot_label].index.tolist()

    spot_lookup: dict[str, dict] = (
        spot_df.set_index("symbol").to_dict("index") if not spot_df.empty else {}
    )

    def _row(sym: str) -> dict:
        sd = spot_lookup.get(sym, {})
        breakdown = {}
        if factor_breakdown:
            for fid, z in factor_breakdown.items():
                if last_date in z.index and sym in z.columns:
                    val = z.loc[last_date, sym]
                    breakdown[fid] = _nan_to_none(float(val)) if pd.notna(val) else None
        return {
            "symbol": sym,
            "factor_value_composite": _nan_to_none(float(last_row[sym])),
            "factor_value_breakdown": breakdown,
            "last_price": _nan_to_none(float(sd.get("last_price", 0)) or None),
            "pct_chg": _nan_to_none(float(sd.get("pct_chg", 0)) or None),
        }

    top = [_row(s) for s in top_syms]
    bot = [_row(s) for s in bot_syms]
    # 按因子值排序：top 降序、bottom 升序
    top.sort(
        key=lambda x: x["factor_value_composite"]
        if x["factor_value_composite"] is not None else -math.inf,
        reverse=True,
    )
    bot.sort(
        key=lambda x: x["factor_value_composite"]
        if x["factor_value_composite"] is not None else math.inf,
    )
    # top_n 限制：在 qcut 顶组内取因子值最高的 K 只（bottom 同理取最低 K 只）。
    # None / <= 0 → 不限制（回退到 qcut 顶组全部）。
    if top_n is not None and top_n > 0:
        top = top[:top_n]
        bot = bot[:top_n]
    return top, bot, len(top), len(bot)


# ---------------------------- 公共入口 ----------------------------


def run_signal(run_id: str, body: dict) -> None:
    """执行一次实盘信号计算。

    Args:
        run_id: ``fr_signal_runs.run_id``，由 router INSERT 时生成并传入。
        body: 请求体 dict，字段：
            - ``factor_items``：list[{"factor_id": str, "params": dict | None}]
            - ``method``: ``equal`` / ``ic_weighted`` / ``orthogonal_equal`` / ``single``
            - ``pool_id``（int）
            - ``n_groups``（int，默认 5）
            - ``ic_lookback_days``（int，默认 60；仅 ic_weighted 用）
            - ``as_of_time``（ISO 字符串；默认 NOW()）
            - ``use_realtime``（bool，默认 True）
            - ``filter_price_limit``（bool，默认 True）

    副作用：
        - 更新 ``fr_signal_runs.status / progress / started_at / finished_at``；
        - 成功时 UPDATE ``payload_json + n_holdings_top / n_holdings_bot``；
        - 失败时 ``status='failed'``，``error_message`` 留 traceback。
    """
    try:
        _update_status(run_id, status="running", started=True, progress=5)

        factor_items: list[dict] = list(body.get("factor_items") or [])
        if not factor_items:
            raise ValueError("factor_items 不能为空")
        method = str(body.get("method") or "equal")
        if method not in ("equal", "ic_weighted", "orthogonal_equal", "single"):
            raise ValueError(
                f"method={method!r} 不支持，仅接受 equal/ic_weighted/"
                f"orthogonal_equal/single"
            )
        if method == "single" and len(factor_items) != 1:
            raise ValueError("method='single' 时 factor_items 必须正好 1 个")

        as_of_time_raw = body.get("as_of_time")
        as_of_time = (
            pd.to_datetime(as_of_time_raw) if as_of_time_raw else pd.Timestamp.now()
        )
        as_of_date = as_of_time.date()
        n_groups = int(body.get("n_groups", 5))
        ic_lookback_days = int(body.get("ic_lookback_days", 60))
        use_realtime = bool(body.get("use_realtime", True))
        filter_price_limit = bool(body.get("filter_price_limit", True))
        top_n_raw = body.get("top_n")
        top_n: int | None = int(top_n_raw) if top_n_raw is not None else None

        reg = FactorRegistry()
        reg.scan_and_register()
        base_data = DataService()
        symbols = base_data.resolve_pool(int(body["pool_id"]))
        if len(symbols) < n_groups:
            raise ValueError(
                f"股票池 pool_id={body['pool_id']} 仅含 {len(symbols)} 只，"
                f"小于 n_groups={n_groups}"
            )

        _update_status(run_id, progress=15)

        # 加载 spot + 检查新鲜度
        spot_df = pd.DataFrame()
        if use_realtime:
            try:
                age = realtime_dao.latest_spot_age_sec(trade_date=as_of_date)
                if age is None or age > _SPOT_STALE_THRESHOLD_SEC:
                    log.warning(
                        "spot 数据陈旧（age=%s），降级 use_realtime=False", age,
                    )
                    use_realtime = False
                else:
                    spot_df = realtime_dao.latest_spot_snapshot(
                        symbols, trade_date=as_of_date
                    )
            except Exception:
                log.exception("加载 spot 失败，降级 use_realtime=False")
                use_realtime = False

        # 历史窗口：按 method + ic_lookback_days 算最小"输出窗口"自然日；
        # 因子 compute 内部还会再减 required_warmup 去加载底层 K 线，
        # 所以这里 service 层不必额外预留 warmup（避免双重 warmup 拉到几年前数据）。
        natural_days = compute_signal_window_natural_days(method, ic_lookback_days)
        start_date = as_of_date - timedelta(days=natural_days)
        log.info(
            "signal window: as_of=%s, method=%s, ic_lookback=%dd → "
            "start_date=%s (natural_days=%d)",
            as_of_date, method, ic_lookback_days, start_date, natural_days,
        )

        # 构造装饰层 DataService
        data = (
            RealtimeAwareDataService(base_data, as_of_date, spot_df)
            if use_realtime
            else base_data
        )

        _update_status(run_id, progress=30)

        # 加载因子（复用 composition 的缓存协议）
        frames: list[pd.DataFrame] = []
        factor_ids: list[str] = []
        resolved_items: list[dict] = []
        for idx, it in enumerate(factor_items):
            fid = it["factor_id"]
            F, version, phash, params = _load_or_compute_factor(
                data,
                reg,
                fid,
                it.get("params"),
                symbols,
                pd.Timestamp(start_date),
                pd.Timestamp(as_of_date),
            )
            if F.empty:
                raise ValueError(f"因子 {fid} 在窗口内无数据")
            frames.append(F)
            factor_ids.append(fid)
            resolved_items.append(
                {
                    "factor_id": fid,
                    "factor_version": int(version),
                    "params_hash": phash,
                    "params": params,
                }
            )
            _update_status(run_id, progress=30 + int(30 * (idx + 1) / len(factor_items)))

        # 对齐到共同 (date, symbol) 面板
        aligned = _align_frames(frames)
        if aligned[0].empty:
            raise ValueError("因子对齐后窗口为空，无法生成信号")

        _update_status(run_id, progress=70)

        # 历史 close 用于 IC 加权 + 子因子 IC（用 base 非 realtime，避免 spot 噪声）
        common_syms = list(aligned[0].columns)
        close_for_ic = (
            base_data.load_panel(
                common_syms, start_date, as_of_date, field="close", adjust="qfq",
            )
            if len(factor_ids) > 1
            else pd.DataFrame()  # 单因子不需要算 IC 加权 / 贡献
        )

        # z-score 后合成
        z_frames = [_zscore_per_day(f) for f in aligned]
        weights: dict[str, float] | None = None
        if method in ("equal", "single"):
            F_combined = (
                _combine_equal(z_frames) if len(z_frames) > 1 else z_frames[0]
            )
        elif method == "ic_weighted":
            weights = _compute_ic_weights(
                z_frames, close_for_ic, factor_ids, period=1,
            )
            F_combined = _combine_weighted(z_frames, weights, factor_ids)
        else:  # orthogonal_equal
            F_combined = _combine_orthogonal_equal(z_frames)

        _update_status(run_id, progress=85)

        # 取末行 top / bottom
        breakdown = (
            {fid: z for fid, z in zip(factor_ids, z_frames)}
            if len(factor_ids) > 1
            else None
        )
        top, bot, n_top, n_bot = _build_top_bottom(
            F_combined,
            spot_df,
            n_groups=n_groups,
            filter_price_limit=filter_price_limit,
            factor_breakdown=breakdown,
            top_n=top_n,
        )

        # 子因子 IC + 贡献度（仅多因子）
        per_factor_ic: dict[str, dict] = {}
        if len(factor_ids) > 1 and not close_for_ic.empty:
            fwd_ret = close_for_ic.shift(-1) / close_for_ic - 1
            for fid, z in zip(factor_ids, z_frames):
                ic_s = metrics.cross_sectional_ic(z, fwd_ret)
                ic_sum = metrics.ic_summary(ic_s)
                per_factor_ic[fid] = {
                    "ic_mean": _nan_to_none(ic_sum["ic_mean"]),
                    "ic_ir": _nan_to_none(ic_sum["ic_ir"]),
                    "ic_win_rate": _nan_to_none(ic_sum["ic_win_rate"]),
                }
            ic_contribs = _compute_ic_contributions(
                per_factor_ic,
                weights if method == "ic_weighted" else None,
                factor_ids,
            )
            for fid in factor_ids:
                per_factor_ic[fid]["ic_contribution"] = ic_contribs[fid]

        # 拼 payload
        spot_meta = {
            "snapshot_at": (
                str(spot_df["snapshot_at"].iloc[0])
                if not spot_df.empty and "snapshot_at" in spot_df.columns
                else None
            ),
            "n_symbols_total": len(symbols),
            "n_spot_rows": int(len(spot_df)),
            "use_realtime": bool(use_realtime),
        }
        payload = {
            "top": top,
            "bottom": bot,
            "weights": weights,
            "per_factor_ic": per_factor_ic if per_factor_ic else None,
            "factor_items": resolved_items,
            "spot_meta": spot_meta,
        }

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fr_signal_runs
                    SET n_holdings_top=%s, n_holdings_bot=%s, payload_json=%s
                    WHERE run_id=%s
                    """,
                    (
                        n_top,
                        n_bot,
                        json.dumps(payload, ensure_ascii=False, allow_nan=False),
                        run_id,
                    ),
                )
            c.commit()

        _update_status(run_id, status="success", progress=100, finished=True)
    except Exception:
        log.exception("signal failed: run_id=%s", run_id)
        try:
            _update_status(
                run_id,
                status="failed",
                error=traceback.format_exc()[:4000],
                finished=True,
            )
        except Exception:
            log.exception("_update_status 落 failed 失败: run_id=%s", run_id)
