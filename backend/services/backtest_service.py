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
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.config import settings
from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
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


def _build_weights(
    F: pd.DataFrame,
    n_groups: int,
    rebalance: int,
    position: str,
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

        # 1) 参数解析 + 因子实例 + 版本 / hash 固化
        reg = FactorRegistry()
        reg.scan_and_register()
        factor = reg.get(body["factor_id"])
        version = reg.latest_version_from_db(body["factor_id"])

        params = body.get("params") or factor.default_params
        phash = _hash(params)

        data = DataService()
        symbols = data.resolve_pool(int(body["pool_id"]))
        n_groups_req = int(body.get("n_groups", 5))
        # qcut 分组回测至少需要 n_groups 只股票：_build_weights 对 <n_groups 的日期
        # 整天退回空仓，结果就是净值永远 =1、指标全 0、看起来"成功"实则无意义。
        # 前置校验直接 failed，信息比空结果有价值得多。
        if len(symbols) < n_groups_req:
            raise ValueError(
                f"股票池 pool_id={body['pool_id']} 仅含 {len(symbols)} 只股票，"
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

        _update_status(run_id, progress=15)

        # 2) 因子值：先查缓存，未命中就算 + 写回
        F = _load_or_compute_factor(
            data, factor, ctx, params,
            body["factor_id"], version, phash, start, end,
        )
        if F.empty:
            raise RuntimeError(
                f"因子 {body['factor_id']!r} 在 [{start.date()}, {end.date()}] 产出空表，"
                "无法执行回测；检查股票池 / 窗口 / 因子实现"
            )

        _update_status(run_id, progress=35)

        # 3) qfq close 宽表 + 对齐
        close = data.load_panel(
            symbols, start.date(), end.date(), field="close", adjust="qfq"
        )
        if close.empty:
            raise RuntimeError(
                f"股票池 pool_id={body['pool_id']} 在窗口内无 close 行情，"
                "请先完成 aggregate_bar_1d 聚合"
            )

        # 日期 + 列双重内连接：factor / 行情任何一边缺失都不能参与订单。
        # close.align(F, join="inner") 只能处理 index；这里手工求两表交集，避免
        # VectorBT 收到列顺序错位。
        common_index = close.index.intersection(F.index)
        common_cols = close.columns.intersection(F.columns)
        if len(common_index) == 0 or len(common_cols) == 0:
            raise RuntimeError(
                "因子表与 close 行情没有共同 (date × symbol) 交集，无法回测"
            )
        close = close.loc[common_index, common_cols].astype("float64")
        F = F.loc[common_index, common_cols].astype("float64")

        _update_status(run_id, progress=55)

        # 4) 权重 → size
        n_groups = n_groups_req
        rebalance = int(body.get("rebalance_period", 1))
        position = str(body.get("position", "top"))
        init_cash = float(body.get("init_cash", 1e7))
        cost_bps = float(body.get("cost_bps", 3))

        W = _build_weights(F, n_groups=n_groups, rebalance=rebalance, position=position)
        # target amount（以标的数量表达的目标仓位）= 目标市值 / 现价。
        # 0 / inf / NaN 全部 → 0，避免 from_orders 拿到脏数据。
        size = W * init_cash / close
        size = size.replace([np.inf, -np.inf], 0.0).fillna(0.0)

        _update_status(run_id, progress=70)

        # 5) VectorBT 组合回测
        # 延迟 import：VectorBT 冷启动 ~1s（numba JIT），放函数内避免污染进程启动。
        import vectorbt as vbt

        pf = vbt.Portfolio.from_orders(
            close=close,
            size=size,
            size_type="targetamount",
            fees=cost_bps / 1e4,
            freq="1D",
            init_cash=init_cash,
            cash_sharing=True,
            group_by=True,
        )

        _update_status(run_id, progress=85)

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
