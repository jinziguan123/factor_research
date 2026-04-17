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

MVP 注记：``run_eval`` 调用 ``_set_status`` 7 次 + ``REPLACE INTO fr_factor_eval_metrics`` 1 次，
每次都独立开/关 MySQL 连接。pymysql 无内置连接池，单机串行场景无压力。
若 Task 8 ProcessPool 并发 worker 数 >4 引起连接数紧张，可考虑把 conn 传入 _set_status 复用。
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
    为 True 时分别写入 ``started_at`` / ``finished_at``。
    无任何字段更新时直接 return（避免生成空 SET 语句）。

    注意：started_at / finished_at 用本地时间（``datetime.now()``），不带时区。
    本项目单机部署 + 单时区（Asia/Shanghai），避免 UTC 引入前端 -8h 展示偏差，
    同时与其他业务表（例如 timing_driven 的 backtest_runs）语义一致。
    如未来多时区部署，应改为 TIMESTAMP 列或统一 UTC + 前端 +8h 展示。
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


def _build_health(
    *,
    factor_panel: pd.DataFrame,
    ic_series: pd.Series,
    turnover_series: pd.Series,
    long_short_n_effective: int,
    n_groups: int,
) -> dict:
    """把 5 个因子健康指标打包成 ``{items: [...], overall: green|yellow|red}``。

    阈值经验来自 A 股日频横截面因子的常见分布，偏保守：
    - 横截面独特值率：≥50% 连续型 / 10~50% 半离散 / <10% 离散（rank/argmax 风险区）；
    - qcut 满组率：≥90% 正常 / 50~90% 偶有退化 / <50% 严重退化；
    - 多空有效样本比：≥80% 正常 / 30~80% 勉强可看 / <30% 基本无信号；
    - IC 按年稳定性：年度 IC 符号一致且 CV≤1.5 绿；符号一致但 CV>1.5 黄；符号翻转红；
    - 换手率水平：5%~30% 合理 / 30%~60% 偏高 / <5% 近乎不动 或 >60% 过度交易。

    ``overall`` 取 items 里最严重那档（red > yellow > green）。任一项 red → overall red。
    """
    items: list[dict] = []

    # 1. 横截面独特值率
    uniq = metrics.cross_section_uniqueness(factor_panel)
    if uniq >= 0.5:
        level, msg = "green", "横截面取值充分分散，适合分位分组。"
    elif uniq >= 0.1:
        level, msg = "yellow", "横截面独特值偏少，分组可能退化成几档。"
    else:
        level, msg = "red", "横截面几乎只有离散几档取值（如 rank/argmax），qcut 分组会严重退化。"
    items.append(
        {
            "key": "cross_section_uniqueness",
            "label": "横截面独特值率",
            "value": float(uniq),
            "display": f"{uniq * 100:.1f}%",
            "level": level,
            "message": msg,
        }
    )

    # 2. qcut 满组率
    full = metrics.qcut_full_rate(factor_panel, n_groups)
    if full >= 0.9:
        level, msg = "green", f"绝大多数日期都能切出 {n_groups} 组。"
    elif full >= 0.5:
        level, msg = "yellow", "部分日期 qcut 并组，分组曲线有轻度失真。"
    else:
        level, msg = "red", f"qcut 严重退化（平均只切出 ~{full * n_groups:.1f} 组），建议减小 n_groups 或换因子。"
    items.append(
        {
            "key": "qcut_full_rate",
            "label": "qcut 满组率",
            "value": float(full),
            "display": f"{full * 100:.1f}%",
            "level": level,
            "message": msg,
        }
    )

    # 3. 多空有效样本比
    total_days = int(len(factor_panel.index))
    ls_ratio = (long_short_n_effective / total_days) if total_days > 0 else 0.0
    if ls_ratio >= 0.8:
        level, msg = "green", "多空组合样本充分。"
    elif ls_ratio >= 0.3:
        level, msg = "yellow", "多空有效样本偏少，净值曲线参考价值有限。"
    else:
        level, msg = "red", "多空有效样本极少，多半是因子横截面过度离散导致分组失败。"
    items.append(
        {
            "key": "long_short_effective_ratio",
            "label": "多空有效样本比",
            "value": float(ls_ratio),
            "display": f"{long_short_n_effective}/{total_days}",
            "level": level,
            "message": msg,
        }
    )

    # 4. IC 按年稳定性
    ann = metrics.ic_annual_stability(ic_series)
    cv = float(ann.get("cv", 0.0))
    if not ann["years"]:
        level, msg = "yellow", "IC 样本年份不足，无法评估跨年稳定性。"
    elif not ann["sign_consistent"]:
        level, msg = "red", f"IC 年度均值出现符号翻转：{ann['ic_mean_by_year']}。因子方向不稳定。"
    elif cv <= 1.5:
        level, msg = "green", "IC 方向一致且量级稳定。"
    else:
        level, msg = "yellow", f"IC 方向一致但年度波动较大（CV={cv:.2f}）。"
    items.append(
        {
            "key": "ic_annual_stability",
            "label": "IC 按年稳定性",
            "value": cv,
            "display": ", ".join(
                f"{y}:{v:+.3f}"
                for y, v in zip(ann["years"], ann["ic_mean_by_year"])
            ),
            "level": level,
            "message": msg,
        }
    )

    # 5. 换手率水平（top 组，单边）
    if turnover_series.empty:
        to_mean = 0.0
    else:
        to_mean = float(turnover_series.mean())
    if 0.05 <= to_mean <= 0.3:
        level, msg = "green", "换手率处于合理区间。"
    elif (0.3 < to_mean <= 0.6) or (0.02 <= to_mean < 0.05):
        level, msg = "yellow", (
            "换手率偏高，交易成本会明显侵蚀收益。"
            if to_mean > 0.3
            else "换手率偏低，因子更新很慢（可能是短期无变化或长窗口因子）。"
        )
    else:
        level, msg = "red", (
            "换手率过高，几乎每天换仓，实盘成本不可忽视。"
            if to_mean > 0.6
            else "换手率接近 0，因子在区间内几乎不变，没有可交易信号。"
        )
    items.append(
        {
            "key": "turnover_level",
            "label": "换手率水平",
            "value": to_mean,
            "display": f"{to_mean * 100:.1f}%",
            "level": level,
            "message": msg,
        }
    )

    levels = {it["level"] for it in items}
    if "red" in levels:
        overall = "red"
    elif "yellow" in levels:
        overall = "yellow"
    else:
        overall = "green"
    return {"overall": overall, "items": items}


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

        params = body.get("params") or factor.default_params
        phash = _hash(params)

        data = DataService()
        symbols = data.resolve_pool(int(body["pool_id"]))
        n_groups_req = int(body.get("n_groups", 5))
        # 横截面指标至少要求池内股票数 ≥ n_groups：
        # - IC / Rank IC 每天需要 ≥3 个样本才能算相关系数；
        # - group_returns / turnover 需要 ≥n_groups 才能做 qcut 分组。
        # 否则所有指标只会返回空 Series，任务表面 success 但指标全 0，严重误导用户。
        if len(symbols) < n_groups_req:
            raise ValueError(
                f"股票池 pool_id={body['pool_id']} 仅含 {len(symbols)} 只股票，"
                f"小于 n_groups={n_groups_req}，无法计算横截面 IC / 分组 / 换手等指标。"
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

        _set_status(run_id, progress=15)
        # Task 7 接入 factor_value_1d 缓存：相同 (factor_id, version, params_hash)
        # 下，若缓存窗口覆盖 [start, end] 且非空，直接复用；否则全量重算并回写。
        # 部分覆盖不做"增量补算"——保守策略，防止与上游 qfq 因子回填、数据回灌导致
        # 缓存状态不一致；一旦窗口变大或起点更早，就整段重算。
        cached = data.load_factor_values(
            body["factor_id"],
            version,
            phash,
            symbols,
            start.date(),
            end.date(),
        )
        if (
            not cached.empty
            and cached.index.min() <= start
            and cached.index.max() >= end
        ):
            F = cached
        else:
            F = factor.compute(ctx, params)
            # 空结果不写缓存（避免把"计算失败或池全部过滤"的空宽表固化进来，下次还会被
            # 第二个条件 cached.empty 拒掉，逻辑上无害；但写入 0 行也没意义）。
            if not F.empty:
                data.save_factor_values(body["factor_id"], version, phash, F)
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
        n_groups = n_groups_req
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

        health = _build_health(
            factor_panel=F,
            ic_series=ic[base_period],
            turnover_series=to,
            long_short_n_effective=int(ls_stats["long_short_n_effective"]),
            n_groups=n_groups,
        )

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
            # 多空有效样本数：ls 已在 long_short_series 里 dropna，长度即有效天数。
            # 前端据此展示"样本不足"告警（rank 类因子 + qcut 退化时常见）。
            "long_short_n_effective": ls_stats["long_short_n_effective"],
            "health": health,
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
        # _set_status 自己也可能抛异常（例如 DB 挂了），再用 try/except 包一层，
        # 避免 exception-in-exception 把 run 永远卡在 running 状态。
        try:
            _set_status(
                run_id,
                status="failed",
                error=traceback.format_exc()[:4000],
                finished=True,
            )
        except Exception:
            log.exception(
                "_set_status 记录失败时自身也抛异常: run_id=%s", run_id
            )
