"""回测服务（BacktestService）：接一次回测请求到 MySQL + parquet 产物。

流程（``run_backtest``）：
1. 状态 → ``running``、``started_at=now``；
2. 注册表拉因子 + 固化 DB 最新 version；参数 hash；
3. ``_load_or_compute_factor``：先查 ``factor_value_1d``，窗口覆盖就复用，否则重算 + 写回；
4. 取 qfq close 宽表，和因子宽表按 date × symbol 内交集对齐；
5. ``_build_weights``：按 rebalance_period 取调仓日，qcut 分组，top-only / long_short 两种模式，
   非调仓日 ffill 上期权重；
6. ``size = W * init_cash / close``；``vbt.Portfolio.from_orders`` 带
   ``size_type='targetamount'`` + ``cash_sharing`` + ``group_by``；
7. 导出 ``pf.value()`` / ``pf.orders.records_readable`` / ``pf.trades.records_readable``
   为 parquet 放到 ``data/artifacts/<run_id>/``；``pf.stats()`` 序列化进 ``payload_json``；
8. ``REPLACE INTO fr_backtest_metrics``、3 条 ``fr_backtest_artifacts``；
9. ``status='success'``、``finished_at=now``；任何异常统一进 failed 分支，
   ``_update_status`` 自身也可能抛，外层嵌套 try 兜底。

设计取舍：
- 模块顶层函数 + primitive 参数（``run_id: str``、``body: dict``），为 Task 8 ProcessPool
  铺路：无闭包、无类实例依赖、pickle 友好。
- ``_build_weights`` 做成纯函数，独立可单测，不依赖 DB。
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.config import settings
from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
from backend.services.abort_check import AbortedError, check_abort
from backend.services.params_hash import params_hash as _hash
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

# parquet 产物根目录：由 settings.artifact_dir 驱动，运行时按 <run_id> 建子目录；
# 生产 Docker 化时通过 FR_ARTIFACT_DIR 环境变量指向挂载卷即可，无需改代码。
ARTIFACT_DIR = Path(settings.artifact_dir)


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
    """更新 ``fr_backtest_runs`` 的状态字段（与 eval_service._set_status 同构）。

    同样用本地 ``datetime.now()``（非 UTC），以便与 eval 记录语义一致。
    """
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
                f"UPDATE fr_backtest_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


def _nan_to_none(x: Any) -> Any:
    """把 NaN / inf / -inf 转 None（与 eval_service 同款）。"""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _benchmark_metrics(
    equity: pd.Series, close: pd.DataFrame
) -> dict:
    """计算相对基准（等权市场组合）的超额指标。

    Args:
        equity: 策略净值 Series（index=date, value=equity）。
        close: qfq close 宽表（index=date, columns=symbol）。

    Returns:
        dict with ``excess_return``, ``information_ratio``, ``tracking_error``,
        ``benchmark_annret``。
    """
    if equity.empty or close.empty:
        return {
            "excess_return": 0.0, "information_ratio": 0.0,
            "tracking_error": 0.0, "benchmark_annret": 0.0,
        }
    bmk_ret = close.pct_change(fill_method=None).mean(axis=1).dropna()
    common_dates = equity.index.intersection(bmk_ret.index)
    if len(common_dates) < 5:
        return {
            "excess_return": 0.0, "information_ratio": 0.0,
            "tracking_error": 0.0, "benchmark_annret": 0.0,
        }
    bmk_aligned = bmk_ret.loc[common_dates]
    strategy_ret = equity.loc[common_dates].pct_change().dropna()
    common2 = strategy_ret.index.intersection(bmk_aligned.index)
    strategy_ret = strategy_ret.loc[common2]
    bmk_ret_final = bmk_aligned.loc[common2]
    excess = strategy_ret - bmk_ret_final
    n = len(excess)
    if n < 5:
        return {
            "excess_return": 0.0, "information_ratio": 0.0,
            "tracking_error": 0.0, "benchmark_annret": 0.0,
        }
    ann_excess = float(excess.mean() * 252)
    te = float(excess.std(ddof=1)) if n > 1 else 0.0
    ir = ann_excess / te if te > 1e-12 else 0.0
    bmk_ann = float(bmk_ret_final.mean() * 252)
    return {
        "excess_return": ann_excess,
        "information_ratio": ir,
        "tracking_error": float(te * np.sqrt(252)),
        "benchmark_annret": bmk_ann,
    }


def _stats_to_payload(stats: pd.Series) -> dict:
    """把 ``pf.stats()`` 的异构 Series 转成 JSON 可序列化的 dict。

    VectorBT ``stats`` 包含 float / int / str / ``pd.Timedelta`` / ``pd.Timestamp`` 等
    混合类型。``json.dumps`` 默认不认识后两者，需要先降级：
    - 浮点 NaN/Inf → None；
    - 其它非 real 标量直接 ``str(v)`` 兜底（Timedelta / Timestamp 的 ``str`` 已含单位或 ISO）。
    """
    out: dict[str, Any] = {}
    for k, v in stats.items():
        key = str(k)
        if v is None:
            out[key] = None
            continue
        # numpy / python 的整型 & 浮点都走 isreal 判定；object dtype 会返回 False。
        try:
            is_real = np.isreal(v) and not isinstance(v, (pd.Timedelta, pd.Timestamp))
        except TypeError:
            is_real = False
        if is_real:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                out[key] = str(v)
                continue
            out[key] = None if not math.isfinite(fv) else fv
        else:
            out[key] = str(v)
    return out


def _get_price_limit_threshold(symbol: str) -> float:
    """根据股票代码返回对应板块的涨跌停幅度阈值。"""
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith("8") or code.startswith("4"):
        return 0.297  # 北交所 30%
    if code.startswith("688"):
        return 0.197  # 科创板 20%
    if code.startswith("300") or code.startswith("301"):
        return 0.197  # 创业板 20%
    if "ST" in symbol.upper():
        return 0.048  # ST/*ST 5%
    return 0.097  # 主板 10%


def _compute_price_limit_mask(
    close: pd.DataFrame,
) -> pd.DataFrame:
    """按板块精确计算"当日是否触及涨跌停板"的 bool 宽表。

    科创板 / 创业板用 20%，北交所 30%，ST 用 5%，主板用 10%。
    True = 触板，应剔除。

    Args:
        close: qfq close 宽表，index=trade_date，columns=symbol。

    Returns:
        bool 宽表，shape 同 close。每列使用其对��板块的阈值独立判断。
    """
    pct = close.pct_change(fill_method=None)
    mask = pd.DataFrame(False, index=pct.index, columns=pct.columns)
    for col in pct.columns:
        thr = _get_price_limit_threshold(str(col))
        mask[col] = pct[col].abs().ge(thr).fillna(False)
    return mask


def _build_weights(
    F: pd.DataFrame,
    n_groups: int,
    rebalance: int,
    position: str,
    excluded_mask: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """由因子宽表构造权重宽表（与 F 同 shape）。

    语义：
    - 按 ``rebalance`` 间隔选调仓日（F.index 的子集，索引 0, r, 2r, ...）；
    - 调仓日按 F 的每行 ``qcut`` 分组：``label=0..n_groups-1``，高 label = 因子值大；
    - ``position="top"``：label == n_groups-1 组等权正权重；
    - ``position="long_short"``：label == n_groups-1 正权等权，label == 0 负权等权，
      组间等权且多空各占 0.5 / -0.5 总敞口；
    - 非调仓日 ``ffill``，保持上一期权重不变。

    Args:
        F: 因子宽表，index=trade_date，columns=symbol。
        n_groups: 分组数（建议 5 / 10），必须 ≥ 2。
        rebalance: 调仓周期（单位 = 行数 = 交易日数），≥ 1。
        position: "top" 或 "long_short"，其它值抛 ValueError 提前暴露拼写错误。
        excluded_mask: 可选 bool 宽表，True 位置视为该日该 symbol 不可入选
            （典型用途：涨跌停过滤）。逻辑上等价于把对应位置的因子值置 NaN。
            若 ``None`` 则不过滤；``reindex`` 兜底，缺失对齐位 = False。

    Returns:
        权重宽表，与 F 同 index 同 columns；行和按 position 模式：top → 1，
        long_short → 约 0。无调仓日时返回全 0。
    """
    if F.empty:
        return F.copy()
    if n_groups < 2:
        raise ValueError(f"n_groups 必须 ≥ 2，收到 {n_groups}")
    if rebalance < 1:
        raise ValueError(f"rebalance 必须 ≥ 1，收到 {rebalance}")
    if position not in ("top", "long_short"):
        raise ValueError(
            f"position 必须是 'top' 或 'long_short'，收到 {position!r}"
        )

    # 权重宽表和 F 同 shape；先全 0，再按调仓日填写 / 非调仓日 ffill。
    W = pd.DataFrame(0.0, index=F.index, columns=F.columns)
    rebalance_dates = F.index[::rebalance]
    if len(rebalance_dates) == 0:
        return W

    for dt in rebalance_dates:
        row = F.loc[dt]
        # 涨跌停过滤：mask=True 的位置当作 NaN，不参与本期 qcut。
        if excluded_mask is not None and dt in excluded_mask.index:
            # 用 reindex 的 fill_value 一步给缺失列补 False，避开 pandas 2.x
            # 对 object dtype fillna 触发的 FutureWarning。
            ban = excluded_mask.loc[dt].reindex(row.index, fill_value=False).astype(bool)
            row = row.where(~ban)
        valid = row.dropna()
        # qcut 至少需要 n_groups 个不同值；样本过少时本期退回空仓。
        if len(valid) < n_groups:
            continue
        try:
            labels = pd.qcut(
                valid, q=n_groups, labels=False, duplicates="drop"
            )
        except ValueError:
            # 所有值相同等极端情况 → 直接空仓本期。
            continue
        # pandas 1.x 在输入全部相同时，qcut(duplicates='drop', labels=False)
        # 并不抛 ValueError，而是静默返回全 NaN 的 labels。仅靠 except 漏网，
        # 后面 int(labels.max()) 会在 NaN 上炸。dropna 同时兜住"全相同 → 全 NaN"
        # 与"部分 NaN"两种情况。
        labels = labels.dropna()
        if labels.empty:
            continue
        # duplicates="drop" 时实际组数可能 < n_groups，取 labels.max() 作 top label。
        top_label = int(labels.max())
        bottom_label = int(labels.min())
        top_syms = labels[labels == top_label].index
        if len(top_syms) == 0:
            continue
        if position == "top":
            # 等权正权重，和 = 1。
            W.loc[dt, top_syms] = 1.0 / len(top_syms)
        else:  # long_short
            bottom_syms = labels[labels == bottom_label].index
            # top / bottom 可能拿到同样 label（n_groups=1 等极端），前面已挡掉；
            # 这里若 bottom 空仍回退 top-only（避免单边无腿就强行 0 权重）。
            if len(bottom_syms) == 0 or top_label == bottom_label:
                # duplicates="drop" 吃掉重复分位后，实际组数可能 < n_groups，
                # 此时 long_short 无法成对，静默退化会误导用户，这里显式告警。
                n_unique = int(labels.nunique())
                log.warning(
                    "因子值在 %s 上唯一分位只有 %d 组，long_short 退回 top-only",
                    dt,
                    n_unique,
                )
                W.loc[dt, top_syms] = 1.0 / len(top_syms)
            else:
                W.loc[dt, top_syms] = 0.5 / len(top_syms)
                W.loc[dt, bottom_syms] = -0.5 / len(bottom_syms)

    # 非调仓日沿用上一期权重：把非调仓日整行置 NaN 后 ffill，等价"持仓不变"；
    # 最后把首段（首次调仓日之前）未填充的 NaN 补 0，表示空仓起步。
    #
    # W 是用 F.columns 固定预分配的，任何列在所有日都有值（0 / NaN 不会错位），
    # ffill 不会把别人的权重拷到该列上，安全。
    is_rebal = np.isin(W.index.values, rebalance_dates.values)
    mask_2d = np.broadcast_to(is_rebal[:, None], W.shape)
    W = W.where(mask_2d, np.nan).ffill().fillna(0.0)
    return W


def _load_or_compute_factor(
    data: DataService,
    factor,
    ctx: FactorContext,
    params: dict,
    factor_id: str,
    factor_version: int,
    params_hash: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """从 ``factor_value_1d`` 命中缓存就用，否则 compute + 写回。

    与 eval_service 的缓存逻辑同构——覆盖整窗口才算命中，部分覆盖不增量补算。
    """
    cached = data.load_factor_values(
        factor_id,
        factor_version,
        params_hash,
        ctx.symbols,
        start.date(),
        end.date(),
    )
    if (
        not cached.empty
        and cached.index.min() <= start
        and cached.index.max() >= end
    ):
        return cached
    F = factor.compute(ctx, params)
    if not F.empty:
        data.save_factor_values(factor_id, factor_version, params_hash, F)
    return F


# ---------------------------- 可复用输入准备（供 cost_sensitivity 等复用）----------------------------


@dataclass
class BacktestInputs:
    """从 ``body`` 准备好的一次回测所需全部输入，pickle 友好、无 DB 句柄。

    把"因子值 → 权重 → size/close 对齐"这段独立抽出，好处：
    - ``run_cost_sensitivity`` 可以算一次 inputs 后循环跑 N 个 ``cost_bps``，
      不必每次重读 ClickHouse / 重算因子；
    - ``run_backtest`` 本身也更薄，后续接 purged CV / 多因子合成等扩展时
      少一处 copy-paste。
    """

    factor_id: str
    factor_version: int
    params_hash: str
    params: dict
    pool_id: int
    symbols: list[str]
    F: pd.DataFrame  # 因子宽表，已与 close 按 (date × symbol) 内连接对齐
    close: pd.DataFrame  # qfq close 宽表，已 ffill + 非正数占位 1.0
    size: pd.DataFrame  # target amount 宽表
    init_cash: float
    freq: str
    n_bars: int


def _prepare_backtest_inputs(body: dict) -> BacktestInputs:
    """把 ``body`` 翻译成一组可直接投喂 ``vbt.Portfolio.from_orders`` 的输入。

    纯函数（无 status 更新），异常直接抛，由调用方决定落到哪张表的 ``error_message``。
    与 ``run_backtest`` 内嵌版本行为完全等价，逐步骤注释详见原实现。
    """
    # 1) 参数解析 + 因子实例 + 版本 / hash 固化
    reg = FactorRegistry()
    reg.scan_and_register()
    factor = reg.get(body["factor_id"])
    version = reg.latest_version_from_db(body["factor_id"])

    params = body.get("params") or factor.default_params
    phash = _hash(params)

    data = DataService()
    pool_id = int(body["pool_id"])
    symbols = data.resolve_pool(pool_id)
    n_groups_req = int(body.get("n_groups", 5))
    if len(symbols) < n_groups_req:
        raise ValueError(
            f"股票池 pool_id={pool_id} 仅含 {len(symbols)} 只股票，"
            f"小于 n_groups={n_groups_req}，无法进行分组回测。"
            f"请换一个至少包含 {n_groups_req} 只股票的股票池，或减小 n_groups。"
        )

    start = pd.to_datetime(body["start_date"])
    end = pd.to_datetime(body["end_date"])
    warmup = factor.required_warmup(params)
    ctx = FactorContext(
        data=data,
        symbols=symbols,
        start_date=start,
        end_date=end,
        warmup_days=warmup,
    )

    # 2) 因子值：缓存或算
    F = _load_or_compute_factor(
        data, factor, ctx, params,
        body["factor_id"], version, phash, start, end,
    )
    if F.empty:
        raise RuntimeError(
            f"因子 {body['factor_id']!r} 在 [{start.date()}, {end.date()}] 产出空表，"
            "无法执行回测；检查股票池 / 窗口 / 因子实现"
        )

    # 3) qfq close 宽表 + 对齐
    close = data.load_panel(
        symbols, start.date(), end.date(), field="close", adjust="qfq"
    )
    if close.empty:
        raise RuntimeError(
            f"股票池 pool_id={pool_id} 在窗口内无 close 行情，"
            "请先完成 aggregate_bar_1d 聚合"
        )
    common_index = close.index.intersection(F.index)
    common_cols = close.columns.intersection(F.columns)
    if len(common_index) == 0 or len(common_cols) == 0:
        raise RuntimeError("因子表与 close 行情没有共同 (date × symbol) 交集，无法回测")
    close = close.loc[common_index, common_cols].astype("float64")
    F = F.loc[common_index, common_cols].astype("float64")

    # 4) 权重 → size
    n_groups = n_groups_req
    rebalance = int(body.get("rebalance_period", 1))
    position = str(body.get("position", "top"))
    init_cash = float(body.get("init_cash", 1e7))

    # 涨跌停过滤：默认关闭以保留与历史回测的可对比性；用户主动 opt-in 时按
    # _compute_price_limit_mask 的近似口径剔除当日触板票。
    filter_pl = bool(body.get("filter_price_limit", False))
    excluded_mask = _compute_price_limit_mask(close) if filter_pl else None

    W = _build_weights(
        F, n_groups=n_groups, rebalance=rebalance, position=position,
        excluded_mask=excluded_mask,
    )
    size = W * init_cash / close
    size = size.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    # close 零 / NaN 占位：详见 run_backtest 原注释
    close = close.where(close > 0).ffill().fillna(1.0)

    return BacktestInputs(
        factor_id=body["factor_id"],
        factor_version=version,
        params_hash=phash,
        params=params,
        pool_id=pool_id,
        symbols=list(symbols),
        F=F,
        close=close,
        size=size,
        init_cash=init_cash,
        freq=str(body.get("freq", "1d")),
        n_bars=len(close),
    )


# ---------------------------- 公共入口 ----------------------------


def run_backtest(run_id: str, body: dict) -> None:
    """执行一次回测。

    Args:
        run_id: ``fr_backtest_runs.run_id``，由 API 层 INSERT run 记录时生成并传入。
        body: 请求体 dict，字段：
            - ``factor_id``（str）；
            - ``pool_id``（int | str）；
            - ``start_date`` / ``end_date``；
            - ``params``（dict，可选，缺省用因子 default_params）；
            - ``n_groups``（int，默认 5）；
            - ``rebalance_period``（int，默认 1）；
            - ``position``（"top" | "long_short"，默认 "top"）；
            - ``cost_bps``（float，默认 3，基点；单边费率 = cost_bps / 10000）；
            - ``init_cash``（float，默认 1e7）；
            - ``freq``（默认 "1d"）。

    副作用：
        - 更新 ``fr_backtest_runs.status / progress / started_at / finished_at / error_message``；
        - 成功写 ``fr_backtest_metrics`` 一条；
        - 成功写 ``fr_backtest_artifacts`` 三条（equity / orders / trades）；
        - 成功写 ``data/artifacts/<run_id>/{equity,orders,trades}.parquet``。
    """
    try:
        _update_status(run_id, status="running", started=True, progress=5)
        # 协作式中断：阶段边界轮询 status='aborting'，命中就抛 AbortedError
        # 交给下面专属 except 分支落 aborted。
        check_abort("backtest", run_id)

        # 1~4) 复用公共 prepare：因子 / close / size 一条龙
        inputs = _prepare_backtest_inputs(body)
        init_cash = inputs.init_cash
        cost_bps = float(body.get("cost_bps", 3))

        _update_status(run_id, progress=70)
        check_abort("backtest", run_id)  # VectorBT 跑起来就停不下，先查一次

        # 5) VectorBT 组合回测
        # 延迟 import：VectorBT 冷启动 ~1s（numba JIT），放函数内避免污染进程启动。
        import vectorbt as vbt

        pf = vbt.Portfolio.from_orders(
            close=inputs.close,
            size=inputs.size,
            size_type="targetamount",
            fees=cost_bps / 1e4,
            freq="1D",
            init_cash=init_cash,
            cash_sharing=True,
            group_by=True,
        )

        _update_status(run_id, progress=85)
        check_abort("backtest", run_id)  # parquet 落盘前最后一次检查

        # 6) 导出产物
        run_dir = ARTIFACT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        equity = pf.value()
        # equity 是 Series（group_by=True），转 DataFrame 方便 parquet 存；索引名留给 downstream。
        equity_df = equity.to_frame(name="equity")
        equity_df.index.name = "trade_date"
        equity_path = run_dir / "equity.parquet"
        equity_df.to_parquet(equity_path)

        orders_df = pf.orders.records_readable
        orders_path = run_dir / "orders.parquet"
        orders_df.to_parquet(orders_path)

        trades_df = pf.trades.records_readable
        trades_path = run_dir / "trades.parquet"
        trades_df.to_parquet(trades_path)

        # 7) 指标 + JSON payload
        stats = pf.stats()
        stats_payload = _stats_to_payload(stats)
        # 基准对比：等权市场组合
        stats_payload["benchmark"] = _benchmark_metrics(equity, close)

        # 核心结构化指标：尽量从 stats payload 里取，字段名按 VectorBT 约定；
        # stats 里 "Total Return [%]" 是百分数，换成小数更通用。
        total_return = float(stats.get("Total Return [%]", 0.0) or 0.0) / 100.0
        max_drawdown = float(stats.get("Max Drawdown [%]", 0.0) or 0.0) / 100.0
        sharpe_ratio = float(stats.get("Sharpe Ratio", 0.0) or 0.0)
        win_rate = float(stats.get("Win Rate [%]", 0.0) or 0.0) / 100.0
        trade_count = int(stats.get("Total Trades", 0) or 0)
        # 年化（粗估）：日频 ~252 个交易日；用净值序列长度归一化避免短窗口虚高。
        n_bars = len(equity_df)
        years = max(n_bars / 252.0, 1e-9)
        # total_return ≤ -100% 时 (1+tr) ≤ 0，幂运算对非整指数会抛 ValueError 或得复数；
        # 策略亏光本金的极端情形语义上年化就是 -100%，直接兜底 -1.0 避免数值异常。
        base = 1.0 + total_return
        if n_bars == 0:
            annual_return = 0.0
        elif base <= 0:
            annual_return = -1.0
        else:
            annual_return = base ** (1.0 / years) - 1.0

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    REPLACE INTO fr_backtest_metrics
                    (run_id, total_return, annual_return, sharpe_ratio,
                     max_drawdown, win_rate, trade_count, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        # 注意：不能用 `_nan_to_none(x) or 0.0`，0.0 是 falsy 会把合法 0
                        # 也替成 0.0（结果碰巧一样但语义错），海象写法只在 None 时兜底。
                        x if (x := _nan_to_none(total_return)) is not None else 0.0,
                        x if (x := _nan_to_none(annual_return)) is not None else 0.0,
                        x if (x := _nan_to_none(sharpe_ratio)) is not None else 0.0,
                        x if (x := _nan_to_none(max_drawdown)) is not None else 0.0,
                        x if (x := _nan_to_none(win_rate)) is not None else 0.0,
                        trade_count,
                        json.dumps(stats_payload, ensure_ascii=False, allow_nan=False),
                    ),
                )
                # 3 条产物登记，类型字段命名与 timing_driven backtest_runs 语义保持一致。
                for artifact_type, path in (
                    ("equity", equity_path),
                    ("orders", orders_path),
                    ("trades", trades_path),
                ):
                    cur.execute(
                        """
                        REPLACE INTO fr_backtest_artifacts
                        (run_id, artifact_type, artifact_path)
                        VALUES (%s, %s, %s)
                        """,
                        (run_id, artifact_type, str(path)),
                    )
            c.commit()

        _update_status(run_id, status="success", progress=100, finished=True)
    except AbortedError as exc:
        # 主动中断：落 aborted，不写 error_message；与 failed 区分语义，前端按不同 badge 展示。
        log.info("backtest aborted: run_id=%s reason=%s", run_id, exc)
        try:
            _update_status(run_id, status="aborted", finished=True)
        except Exception:
            log.exception("_update_status 落 aborted 失败: run_id=%s", run_id)
    except Exception:
        log.exception("backtest failed: run_id=%s", run_id)
        # 嵌套 try：_update_status 自己也可能抛（例如 DB 挂了），避免把 run 永远卡在 running。
        try:
            _update_status(
                run_id,
                status="failed",
                error=traceback.format_exc()[:4000],
                finished=True,
            )
        except Exception:
            log.exception("_update_status 记录失败时自身也抛异常: run_id=%s", run_id)


# ---------------------------- Walk-Forward 回测 ----------------------------


def run_walk_forward(
    run_id: str,
    body: dict,
) -> None:
    """Walk-forward 滚动窗口回测：把全时段切成训练+测试窗口滑窗评估。

    与单窗口回测的关键区别：每个测试窗口只使用该窗口之前的数据计算因子值，
    彻底消除前视偏差。拼接所有测试窗口的净值形成一条连续的 OOS 权益曲线。

    配置项（body 内）：
    - ``train_days``：训练窗口大小（交易日），默认 252（≈1 年）
    - ``test_days``：测试窗口大小（交易日），默认 63（≈1 季度）
    - ``step_days``：窗口滑动步长（交易日），默认 63；等于 test_days 时不重叠

    每个子窗口运行简化回测（top-only, 不生成 artifacts），最终聚合 OOS 收益。
    """
    from backend.runtime.task_pool import submit  # noqa: F811

    try:
        _update_status(run_id, status="running", started=True)
        reg = FactorRegistry()
        reg.scan_and_register()
        try:
            factor = reg.get(body["factor_id"])
        except KeyError:
            raise ValueError(f"factor_id={body['factor_id']!r} 未注册")
        version = reg.latest_version_from_db(body["factor_id"])
        params = body.get("params") or factor.default_params
        phash = _hash(params)

        data = DataService()
        pool_id = body["pool_id"]
        symbols = data.resolve_pool(pool_id)

        full_start = pd.to_datetime(body["start_date"])
        full_end = pd.to_datetime(body["end_date"])
        n_groups = int(body.get("n_groups", 5))
        train_days = int(body.get("train_days", 252))
        test_days = int(body.get("test_days", 63))
        step_days = int(body.get("step_days", test_days))

        warmup = factor.required_warmup(params)
        data_start = (full_start - pd.Timedelta(days=warmup + train_days)).date()

        # 预加载完整区段的 close + 因子值（只算一次，各窗口切片）
        close = data.load_panel(
            symbols, data_start, full_end.date(), field="close", adjust="qfq"
        )
        if close.empty:
            raise ValueError("close 数据为空")

        ctx = FactorContext(
            data=data, symbols=symbols,
            start_date=pd.Timestamp(data_start),
            end_date=full_end, warmup_days=warmup,
        )
        F_raw = factor.compute(ctx, params)
        F, close_aligned = F_raw.align(close, join="inner")

        # 逐窗口
        windows: list[dict] = []
        oos_equity_parts: list[pd.Series] = []
        cursor = full_start
        win_idx = 0
        while cursor + pd.Timedelta(days=train_days + test_days) <= full_end:
            win_idx += 1
            train_end = cursor + pd.Timedelta(days=train_days)
            test_end = train_end + pd.Timedelta(days=test_days)
            if test_end > full_end:
                test_end = full_end
            check_abort("wf_backtest", run_id)

            # 测试窗口：factor 与 close 都取 [train_end, test_end]
            F_test = F.loc[train_end:test_end]
            C_test = close_aligned.loc[train_end:test_end]
            if F_test.empty or C_test.empty:
                cursor += pd.Timedelta(days=step_days)
                continue

            # 权重：仅 top 组
            from backend.services.backtest_service import _build_weights

            W = _build_weights(F_test, n_groups=n_groups, rebalance=1, position="top")
            W_aligned, C_aligned = W.align(C_test, join="inner")
            if W_aligned.empty:
                cursor += pd.Timedelta(days=step_days)
                continue

            # 简单日收益：权重复制前日，日收益 = W * ret
            ret_1d = C_aligned.pct_change(fill_method=None).shift(-1)
            daily_ret = (W_aligned.shift(1) * ret_1d).sum(axis=1).dropna()
            cum = (1 + daily_ret).cumprod()
            oos_equity_parts.append(cum)

            windows.append({
                "window": win_idx,
                "train_start": str(cursor.date()),
                "train_end": str(train_end.date()),
                "test_start": str(train_end.date()),
                "test_end": str(test_end.date()),
                "n_stocks": len(C_test.columns),
                "total_return": float(cum.iloc[-1] - 1) if len(cum) > 0 else 0.0,
            })
            cursor += pd.Timedelta(days=step_days)

        if not oos_equity_parts:
            raise ValueError("walk-forward 未能生成任何有效窗口（窗口过大或数据不足）")

        # 拼接 OOS 净值
        oos_equity = pd.concat(oos_equity_parts).sort_index()
        oos_equity = oos_equity[~oos_equity.index.duplicated(keep="first")]
        oos_ret = oos_equity.pct_change().dropna()

        # 汇总指标
        n_days = len(oos_ret)
        yrs = max(n_days / 252, 1e-9)
        total_ret = float(oos_equity.iloc[-1] - 1)
        ann_ret = (1 + total_ret) ** (1 / yrs) - 1 if total_ret > -1 else -1.0
        vol = float(oos_ret.std(ddof=1)) if n_days > 1 else 0.0
        sharpe = float(oos_ret.mean() / vol * np.sqrt(252)) if vol > 1e-12 else 0.0
        max_dd = float((oos_equity / oos_equity.cummax() - 1).min())

        payload = {
            "method": "walk_forward",
            "params": {
                "train_days": train_days,
                "test_days": test_days,
                "step_days": step_days,
                "n_windows": len(windows),
            },
            "summary": {
                "total_return": total_ret,
                "annual_return": ann_ret,
                "sharpe_ratio": sharpe,
                "max_drawdown": max_dd,
                "n_days": n_days,
            },
            "windows": windows,
            "oos_equity": {
                "dates": [str(d.date()) for d in oos_equity.index],
                "values": [float(v) for v in oos_equity.values],
            },
        }

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    REPLACE INTO fr_backtest_metrics
                    (run_id, total_return, annual_return, sharpe_ratio,
                     max_drawdown, win_rate, trade_count, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (run_id, total_ret, ann_ret, sharpe, max_dd, 0.0, 0,
                     json.dumps(payload, ensure_ascii=False, allow_nan=False)),
                )
            c.commit()
        _update_status(run_id, status="success", progress=100, finished=True)
    except AbortedError:
        log.info("wf_backtest aborted: run_id=%s", run_id)
        try:
            _update_status(run_id, status="aborted", finished=True)
        except Exception:
            log.exception("wf _update_status aborted 失败: run_id=%s", run_id)
    except Exception:
        log.exception("wf_backtest failed: run_id=%s", run_id)
        try:
            _update_status(run_id, status="failed",
                           error=traceback.format_exc()[:4000], finished=True)
        except Exception:
            log.exception("wf _update_status failed: run_id=%s", run_id)
