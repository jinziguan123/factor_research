"""因子评估服务（EvalService）：端到端跑一次因子评估并把结果写回 MySQL。

流程概览（run_eval）：
1. 进度 5 → "running"：扫描注册因子；
2. 从 ``fr_factor_meta`` 读 latest version（固化到本次 run，避免中途热加载导致错版本）；
3. 解析股票池 + 预热期 → 构造 ``FactorContext`` → ``factor.compute()``；
4. 取 qfq close 宽表 → 构造 1/5/10 日 forward return；
5. 计算 Pearson IC / Rank IC / 五分位分组收益 / 多空收益 / 换手率 / 值直方图；
6. 组织 payload_json + 结构化指标列 → ``REPLACE INTO fr_factor_eval_metrics``；
7. 进度 100 → "success"。任何异常统一走 failed 分支，error_message 留 traceback。

Task 6 不写 save_factor_values（那是 Task 7 的事），只算指标和直方图。
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from datetime import datetime
from typing import Any

import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
from backend.services import metrics
from backend.services.params_hash import params_hash as _hash
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


# ---------------------------- 内部辅助 ----------------------------


def _set_status(
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """统一更新 ``fr_factor_eval_runs`` 的状态字段。

    只更新显式传入的字段，避免把别的字段误写为 NULL。``started`` / ``finished``
    为 True 时分别写入 ``started_at`` / ``finished_at = UTC now``。
    无任何字段更新时直接 return（避免生成空 SET 语句）。
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
        vals.append(datetime.utcnow())
    if finished:
        sets.append("finished_at=%s")
        vals.append(datetime.utcnow())
    if not sets:
        return
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE fr_factor_eval_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


def _nan_to_none(x: Any) -> Any:
    """把 NaN / inf / -inf 转 None，以便 MySQL 存 NULL + ``json.dumps`` 不崩。"""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _series_to_obj(s: pd.Series) -> dict:
    """把 ``pd.Series`` 转为前端友好的 ``{dates, values}`` dict。

    - dates：ISO 日期字符串（YYYY-MM-DD）；
    - values：原样浮点，NaN 转 None，保证 JSON 序列化不抛。
    """
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in s.index],
        "values": [None if pd.isna(x) else float(x) for x in s.values],
    }


def _df_to_obj(df: pd.DataFrame) -> dict:
    """把分组收益等宽表转 ``{dates, g1, g2, ...}`` 结构。

    列名如果是整数（pandas 默认 0..n-1），自动前缀 ``g{i+1}``；
    其它列名原样 str 化。NaN 转 None。
    """
    if df.empty:
        return {"dates": []}
    obj: dict = {"dates": [d.strftime("%Y-%m-%d") for d in df.index]}
    for col in df.columns:
        if isinstance(col, (int,)) or (
            isinstance(col, float) and col.is_integer()
        ):
            key = f"g{int(col)+1}"
        else:
            key = str(col)
        obj[key] = [None if pd.isna(x) else float(x) for x in df[col].values]
    return obj


# ---------------------------- 公共入口 ----------------------------


def run_eval(run_id: str, body: dict) -> None:
    """执行一次因子评估。

    Args:
        run_id: 评估任务主键（``fr_factor_eval_runs.run_id``），调用方（API 层）
            在 INSERT run 记录时生成并传入。
        body: 评估参数 dict，至少包含：
            - ``factor_id``（str）
            - ``pool_id``（int | str）
            - ``start_date``（ISO 日期字符串或 pd 可解析对象）
            - ``end_date``（同上）
            可选：
            - ``params``（dict，因子参数；缺省用 factor.default_params）
            - ``forward_periods``（list[int]，默认 [1,5,10]）
            - ``n_groups``（int，默认 5）

    副作用：
        - 更新 ``fr_factor_eval_runs.status / progress / started_at / finished_at``；
        - 成功时 ``REPLACE INTO fr_factor_eval_metrics`` 写一条评估结果；
        - 失败时 ``status='failed'``，``error_message`` 为 traceback（截 4000 字符）。
    """
    try:
        _set_status(run_id, status="running", started=True, progress=5)

        reg = FactorRegistry()
        reg.scan_and_register()
        factor = reg.get(body["factor_id"])
        # 固化到 DB 最新版本（而非 current_version 的进程快照），避免评估过程中
        # 热加载更新 version 导致任务记录与实际执行不一致。
        version = reg.latest_version_from_db(body["factor_id"])
        _ = version  # 当前 Task 6 不写 factor_value_1d，仅保留固化逻辑备用

        params = body.get("params") or factor.default_params
        phash = _hash(params)
        _ = phash  # 当前 Task 6 不写 factor_value_1d，保留 hash 备日志 / Task 7 使用

        data = DataService()
        symbols = data.resolve_pool(int(body["pool_id"]))
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

        _set_status(run_id, progress=15)
        F = factor.compute(ctx, params)
        # Task 6 不做 save_factor_values —— 那是 Task 7 的事。
        _set_status(run_id, progress=40)

        close = data.load_panel(
            symbols, start.date(), end.date(), field="close", adjust="qfq"
        )
        fwd_periods = [int(x) for x in body.get("forward_periods", [1, 5, 10])]
        # 未来 k 日收益（简单收益）：T+k 收盘 / T 收盘 - 1。
        # close.shift(-k) 把 T+k 的值挪回 T 行，末尾 k 行自然是 NaN（后面 IC 会 mask 掉）。
        fwd_rets = {k: close.shift(-k) / close - 1 for k in fwd_periods}

        _set_status(run_id, progress=55)
        ic = {k: metrics.cross_sectional_ic(F, fwd_rets[k]) for k in fwd_periods}
        rank_ic = {
            k: metrics.cross_sectional_rank_ic(F, fwd_rets[k])
            for k in fwd_periods
        }

        _set_status(run_id, progress=75)
        n_groups = int(body.get("n_groups", 5))
        # 分组 / 换手 / 多空只用 1 日前瞻，避免"窗口重叠但是要每日调仓"的语义歧义。
        base_period = fwd_periods[0] if fwd_periods else 1
        g_rets = metrics.group_returns(
            F, fwd_rets[base_period], n_groups=n_groups
        )
        ls = metrics.long_short_series(g_rets)

        _set_status(run_id, progress=85)
        to = metrics.turnover_series(F, n_groups=n_groups, which="top")
        hist = metrics.value_histogram(F)

        # 结构化指标只取 base_period（通常 1 日）的 IC 汇总；多 period IC 曲线仍在 payload。
        ic_sum = metrics.ic_summary(ic[base_period])
        rank_ic_sum = metrics.ic_summary(rank_ic[base_period])
        ls_stats = metrics.long_short_metrics(ls)

        payload = {
            "ic": {str(k): _series_to_obj(ic[k]) for k in fwd_periods},
            "rank_ic": {str(k): _series_to_obj(rank_ic[k]) for k in fwd_periods},
            "group_returns": _df_to_obj(g_rets),
            # 累计净值 = (1+日收益).cumprod()，常数序列/空序列均可算。
            "long_short_equity": _series_to_obj(
                (1 + ls).cumprod() if not ls.empty else ls
            ),
            "turnover_series": _series_to_obj(to),
            "value_hist": hist,
        }

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    REPLACE INTO fr_factor_eval_metrics
                    (run_id, ic_mean, ic_std, ic_ir, ic_win_rate, ic_t_stat,
                     rank_ic_mean, rank_ic_std, rank_ic_ir,
                     turnover_mean, long_short_sharpe, long_short_annret, payload_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        _nan_to_none(ic_sum["ic_mean"]),
                        _nan_to_none(ic_sum["ic_std"]),
                        _nan_to_none(ic_sum["ic_ir"]),
                        _nan_to_none(ic_sum["ic_win_rate"]),
                        _nan_to_none(ic_sum["ic_t_stat"]),
                        _nan_to_none(rank_ic_sum["ic_mean"]),
                        _nan_to_none(rank_ic_sum["ic_std"]),
                        _nan_to_none(rank_ic_sum["ic_ir"]),
                        _nan_to_none(float(to.mean()) if not to.empty else 0.0),
                        _nan_to_none(ls_stats["long_short_sharpe"]),
                        _nan_to_none(ls_stats["long_short_annret"]),
                        # ensure_ascii=False 保留中文；payload 内已经把 NaN 全部
                        # 转成 None（_series_to_obj / _df_to_obj / value_hist 处理），
                        # 但为了 belt-and-suspenders 也用 allow_nan=False 让异常尽早暴露。
                        json.dumps(payload, ensure_ascii=False, allow_nan=False),
                    ),
                )
            c.commit()

        _set_status(run_id, status="success", progress=100, finished=True)
    except Exception:
        # 任何异常（KeyError / DB 连不上 / 因子 compute 崩 / JSON 序列化失败）统一收口。
        log.exception("eval failed: run_id=%s", run_id)
        _set_status(
            run_id,
            status="failed",
            error=traceback.format_exc()[:4000],
            finished=True,
        )
