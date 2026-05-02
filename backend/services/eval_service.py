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

import numpy as np
import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
from backend.services import metrics
from backend.services.abort_check import AbortedError, check_abort
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
    feedback: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """统一更新 ``fr_factor_eval_runs`` 的状态字段。

    只更新显式传入的字段，避免把别的字段误写为 NULL。``started`` / ``finished``
    为 True 时分别写入 ``started_at`` / ``finished_at``。
    无任何字段更新时直接 return（避免生成空 SET 语句）。

    Args:
        feedback: LLM 友好的"诊断 + 改进建议"文本（写入 ``feedback_text`` 列）。
            与 ``error`` 互补——error 仅 failed 时写 traceback，feedback 在
            success / failed 都可写（例如 success 但 IC 极低需要诊断）。
            借鉴 RD-Agent 反馈三元组的语义槽位。

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
    if feedback is not None:
        sets.append("feedback_text=%s")
        vals.append(feedback)
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


def _build_eval_feedback_rule_based(structured: dict) -> str:
    """根据评估结构化指标拼一段 LLM / 用户友好的"诊断 + 改进建议"文本（规则版）。

    用于 fr_factor_eval_runs.feedback_text，作为 RD-Agent 借鉴的反馈三元组
    的**保底实现**：当 LLM 诊断（``llm_eval_diagnose.diagnose_with_llm``）
    失败 / 超时 / 没配 API key 时，由 ``_build_eval_feedback`` catch 回落到
    本函数。判定阈值是经验值（比 backtest_service._build_health 的红黄绿略宽
    松）：
    - |IC| < 0.02：信号弱
    - 0.02 ≤ |IC| < 0.05：信号一般
    - |IC| ≥ 0.05：信号显著
    - IC_IR < 0.3：方向不稳；≥ 0.5：稳健
    - long_short_sharpe < 0：分组单调性破坏（多空反向）
    - long_short_sharpe ≥ 1.0：多空可用

    Args:
        structured: ``evaluate_factor_panel`` 返回的扁平指标 dict。

    Returns:
        多行诊断文本；若指标全 NaN（极端边角）返回空串，调用方会跳过写库。
    """
    ic_mean = structured.get("ic_mean")
    ic_ir = structured.get("ic_ir")
    rank_ic_mean = structured.get("rank_ic_mean")
    long_short_sharpe = structured.get("long_short_sharpe")
    long_short_annret = structured.get("long_short_annret")
    turnover_mean = structured.get("turnover_mean")

    # 全 NaN：因子在评估窗口内完全没产生有效输出（如 warmup 不足）
    if all(
        v is None or (isinstance(v, float) and not math.isfinite(v))
        for v in (ic_mean, ic_ir, rank_ic_mean, long_short_sharpe)
    ):
        return (
            "评估窗口内未产生有效因子值，所有指标都缺失。"
            "可能原因：required_warmup 不足 / 因子 compute 返回空 / 对齐失败。"
            "建议核对 required_warmup 与窗口长度，或换更长的评估区间。"
        )

    lines: list[str] = []
    # IC 强度
    if ic_mean is not None and isinstance(ic_mean, (int, float)) and math.isfinite(ic_mean):
        abs_ic = abs(ic_mean)
        if abs_ic < 0.02:
            lines.append(
                f"📉 IC 偏弱（mean={ic_mean:.4f}）——预测力不显著；"
                "建议换公式 / 换变量族（量价 / 资金流 / 基本面交叉）。"
            )
        elif abs_ic < 0.05:
            lines.append(
                f"📊 IC 一般（mean={ic_mean:.4f}）——边际可用，可在多因子合成里"
                "搭配其它因子放大；单独使用胜率有限。"
            )
        else:
            lines.append(
                f"✅ IC 显著（mean={ic_mean:.4f}）——预测力较强，建议进入"
                "回测 / 多因子合成阶段。"
            )

    # IC 方向稳定性
    if ic_ir is not None and isinstance(ic_ir, (int, float)) and math.isfinite(ic_ir):
        abs_ir = abs(ic_ir)
        if abs_ir < 0.3:
            lines.append(
                f"⚠️ IC_IR={ic_ir:.3f} 偏低，IC 方向不稳定；样本外可能反转。"
            )
        elif abs_ir >= 0.5:
            lines.append(f"✅ IC_IR={ic_ir:.3f} 稳健。")

    # 多空 Sharpe
    if (
        long_short_sharpe is not None
        and isinstance(long_short_sharpe, (int, float))
        and math.isfinite(long_short_sharpe)
    ):
        if long_short_sharpe < 0:
            lines.append(
                f"❌ 多空 Sharpe={long_short_sharpe:.2f} 为负——分组单调性破坏，"
                "可能 IC 方向假设与实际相反；试将因子取负号。"
            )
        elif long_short_sharpe >= 1.0:
            ann_pct = (
                f"{long_short_annret*100:.1f}%"
                if long_short_annret is not None and math.isfinite(long_short_annret)
                else "NA"
            )
            lines.append(
                f"✅ 多空 Sharpe={long_short_sharpe:.2f}（年化 {ann_pct}），实战可用。"
            )

    # 换手率（信息量）
    if (
        turnover_mean is not None
        and isinstance(turnover_mean, (int, float))
        and math.isfinite(turnover_mean)
    ):
        if turnover_mean > 0.5:
            lines.append(
                f"💸 换手 {turnover_mean:.1%} 偏高，实盘成本会蚕食 alpha；"
                "考虑 EMA 平滑或拉长 forward periods。"
            )

    return "\n".join(lines) if lines else ""


def _build_eval_feedback(
    structured: dict,
    *,
    payload: dict | None = None,
    hypothesis: str = "",
    factor_id: str = "",
) -> str:
    """优先用 LLM 解读完整 payload，失败 catch 回落规则版（L2.C）。

    LLM 比规则版能多看几件事：
    - IC 衰减曲线的形状（规则版只看均值）
    - 分组单调与 hypothesis 方向是否符合
    - 健康度 / Alphalens 增强等结构化指标的综合解读

    LLM 失败信号 → 回落 ``_build_eval_feedback_rule_based``。这两条路径的
    输出都直接落 ``fr_factor_eval_runs.feedback_text``；前端无差别展示。
    """
    if payload is not None and (hypothesis or factor_id):
        try:
            from backend.services.llm_eval_diagnose import diagnose_with_llm

            return diagnose_with_llm(
                structured=structured,
                payload=payload,
                hypothesis=hypothesis,
                factor_id=factor_id,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("LLM 诊断失败，回落规则版：%s", e)
            log.debug("LLM 诊断失败详情", exc_info=True)
    return _build_eval_feedback_rule_based(structured)


def _nan_to_none(x: Any) -> Any:
    """把 NaN / inf / -inf 转 None，以便 MySQL 存 NULL + ``json.dumps`` 不崩。"""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _nan_dict(d: dict | None) -> dict:
    """把 ``ic_summary`` 这类 ``{key: float}`` 字典里的 NaN / inf 全部替换成 None。

    payload json.dumps 开了 ``allow_nan=False``，这里提前清洗一次是"早失败优于晚失败"：
    一旦出现非有限浮点（比如 train/test 段样本极少导致 std=0 / t_stat=inf），
    在写入前就转成 None，MySQL 存 NULL，前端读到显示 "-"。
    """
    if not d:
        return {}
    return {k: _nan_to_none(v) for k, v in d.items()}


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

    要求 index 为 DatetimeIndex（调用方保证）。
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


def _df_to_rows(df: pd.DataFrame) -> dict:
    """把任意 index 的 DataFrame 转成 ``{columns, data: [{col: val}]}``。

    适用于非日期 index 的 DataFrame（如个股时序评估的 per_symbol_summary 结果）。
    """
    if df.empty:
        return {"columns": [], "data": []}
    rows: list[dict] = []
    for idx_val in df.index:
        row: dict = {"_index": str(idx_val)}
        for col in df.columns:
            v = df.at[idx_val, col]
            if isinstance(v, float) and not np.isfinite(v):
                row[str(col)] = None
            elif isinstance(v, (np.integer,)):
                row[str(col)] = int(v)
            elif isinstance(v, (np.floating,)):
                row[str(col)] = float(v)
            elif pd.isna(v):
                row[str(col)] = None
            else:
                row[str(col)] = v
        rows.append(row)
    return {"columns": [str(c) for c in df.columns], "data": rows}


def _build_alphalens_extras(
    F: pd.DataFrame,
    close: pd.DataFrame,
    *,
    fwd_periods: list[int],
    n_groups: int,
) -> dict:
    """调用 Alphalens 计算增量指标，返回 dict 供 payload["alphalens"] ��用。

    三项增量：rank_autocorrelation / group_cumulative_returns / alpha_beta。
    任何一项失败只跳过该项；整体失败返回空 dict。前端按 key 存在与否决定渲染。
    """
    try:
        import alphalens
    except ImportError:
        log.debug("alphalens 未安装，跳过增强指标")
        return {}

    if F.empty or close.empty:
        return {}

    import warnings

    try:
        factor_long = F.stack()
        factor_long.index.names = ["date", "asset"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            factor_data = alphalens.utils.get_clean_factor_and_forward_returns(
                factor_long, close,
                periods=tuple(fwd_periods),
                quantiles=n_groups,
                max_loss=1.0,
            )
    except Exception:
        log.warning("alphalens get_clean_factor 失败，跳过增强指标", exc_info=True)
        return {}

    base_period_col = factor_data.columns[0]
    extras: dict = {}

    # A. 因子排名自相关（自行实现，规避 Alphalens 的 asfreq(None) 兼容问题）
    try:
        ranks_wide = (
            factor_data.groupby(level="date")["factor"]
            .rank()
            .reset_index()
            .pivot(index="date", columns="asset", values="factor")
        )
        autocorr = ranks_wide.corrwith(ranks_wide.shift(1), axis=1).dropna()
        extras["rank_autocorrelation"] = _series_to_obj(autocorr)
    except Exception:
        log.warning("alphalens rank_autocorrelation 失败", exc_info=True)

    # B. 分组累积净值（去均值口径，和现有原始口径互补）
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mr, _ = alphalens.performance.mean_return_by_quantile(
                factor_data, by_date=True, demeaned=True,
            )
        daily = mr[base_period_col].unstack(level="date").T.sort_index()
        daily.columns = range(len(daily.columns))
        cum = (1 + daily).cumprod()
        extras["group_cumulative_returns"] = _df_to_obj(cum)
    except Exception:
        log.warning("alphalens group_cumulative_returns 失败", exc_info=True)

    # C. Factor Alpha/Beta（基准 = 截面等权均值收益）
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ab = alphalens.performance.factor_alpha_beta(factor_data)
        ann_alpha = float(ab.loc["Ann. alpha", base_period_col])
        beta = float(ab.loc["beta", base_period_col])
        daily_alpha = (1 + ann_alpha) ** (1 / 252) - 1
        extras["alpha_beta"] = {
            "alpha": _nan_to_none(daily_alpha),
            "beta": _nan_to_none(beta),
            "annualized_alpha": _nan_to_none(ann_alpha),
        }
    except Exception:
        log.warning("alphalens alpha_beta 失败", exc_info=True)

    return extras


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

    # 防御式净化：所有数值字段都必须 JSON 可序列化（allow_nan=False）。
    # 即便某个指标函数未来 return 了 NaN / inf，也不应让整个 payload 写入失败。
    for it in items:
        if not isinstance(it["value"], (int, float)) or not math.isfinite(it["value"]):
            it["value"] = 0.0

    levels = {it["level"] for it in items}
    if "red" in levels:
        overall = "red"
    elif "yellow" in levels:
        overall = "yellow"
    else:
        overall = "green"
    return {"overall": overall, "items": items}


# ---------------------------- 公共评估内核（供 composition_service 复用）----------------------------


def evaluate_factor_panel(
    F: pd.DataFrame,
    close: pd.DataFrame,
    *,
    forward_periods: list[int],
    n_groups: int,
    split_date: Any | None = None,
) -> tuple[dict, dict]:
    """对一张"因子宽表 + close 宽表"计算全套评估指标。

    从 run_eval 抽出来独立一函数，目的是让 composition_service 能把"按方法合成出
    来的因子 wide table"送进同一套评估管线（IC / 分组 / 多空 / 换手 / 体检 / 可选
    train-test split），避免到处复制粘贴 metrics 调用。

    Args:
        F: 因子宽表，index=trade_date、columns=symbol_code。NaN 表示缺失。
        close: qfq close 宽表，index / columns 应与 F 同构（上游会做 inner-align）。
        forward_periods: 前瞻期数组，例如 [1,5,10]。
        n_groups: qcut 分组数。
        split_date: 可选 ISO 字符串/pd.Timestamp。非 None 时追加 train/test IC 汇总。

    Returns:
        (payload, structured)：
        - payload：嵌套 dict，直接可 ``json.dumps`` 落 payload_json。
        - structured：扁平 dict，含 ic_mean/std/ir/win_rate/t_stat 等结构化列。
    """
    fwd_periods = [int(x) for x in forward_periods]
    # 未来 k 日收益。close 已在上游 align 过，这里只负责 shift。
    fwd_rets = {k: close.shift(-k) / close - 1 for k in fwd_periods}

    ic = {k: metrics.cross_sectional_ic(F, fwd_rets[k]) for k in fwd_periods}
    rank_ic = {
        k: metrics.cross_sectional_rank_ic(F, fwd_rets[k]) for k in fwd_periods
    }

    base_period = fwd_periods[0] if fwd_periods else 1
    g_rets = metrics.group_returns(F, fwd_rets[base_period], n_groups=n_groups)
    ls = metrics.long_short_series(g_rets)
    to = metrics.turnover_series(F, n_groups=n_groups, which="top")
    hist = metrics.value_histogram(F)

    ic_sum = metrics.ic_summary(ic[base_period])
    rank_ic_sum = metrics.ic_summary(rank_ic[base_period])
    ls_stats = metrics.long_short_metrics(ls)

    # Train / Test split：只切 base_period 的 IC / Rank IC，避免 payload 膨胀。
    ic_sum_train = ic_sum_test = None
    rank_ic_sum_train = rank_ic_sum_test = None
    if split_date:
        split_ts = pd.to_datetime(split_date)
        ic_train = ic[base_period].loc[: split_ts - pd.Timedelta(days=1)]
        ic_test = ic[base_period].loc[split_ts:]
        rank_ic_train = rank_ic[base_period].loc[
            : split_ts - pd.Timedelta(days=1)
        ]
        rank_ic_test = rank_ic[base_period].loc[split_ts:]
        ic_sum_train = metrics.ic_summary(ic_train)
        ic_sum_test = metrics.ic_summary(ic_test)
        rank_ic_sum_train = metrics.ic_summary(rank_ic_train)
        rank_ic_sum_test = metrics.ic_summary(rank_ic_test)

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
        "long_short_equity": _series_to_obj(
            (1 + ls).cumprod() if not ls.empty else ls
        ),
        "turnover_series": _series_to_obj(to),
        "value_hist": hist,
        "long_short_n_effective": ls_stats["long_short_n_effective"],
        "health": health,
    }
    if split_date:
        payload["split_date"] = str(split_date)
        payload["ic_summary_train"] = _nan_dict(ic_sum_train)
        payload["ic_summary_test"] = _nan_dict(ic_sum_test)
        payload["rank_ic_summary_train"] = _nan_dict(rank_ic_sum_train)
        payload["rank_ic_summary_test"] = _nan_dict(rank_ic_sum_test)

    al_extras = _build_alphalens_extras(
        F, close, fwd_periods=fwd_periods, n_groups=n_groups,
    )
    if al_extras:
        payload["alphalens"] = al_extras

    structured = {
        "ic_mean": _nan_to_none(ic_sum["ic_mean"]),
        "ic_std": _nan_to_none(ic_sum["ic_std"]),
        "ic_ir": _nan_to_none(ic_sum["ic_ir"]),
        "ic_win_rate": _nan_to_none(ic_sum["ic_win_rate"]),
        "ic_t_stat": _nan_to_none(ic_sum["ic_t_stat"]),
        "rank_ic_mean": _nan_to_none(rank_ic_sum["ic_mean"]),
        "rank_ic_std": _nan_to_none(rank_ic_sum["ic_std"]),
        "rank_ic_ir": _nan_to_none(rank_ic_sum["ic_ir"]),
        "turnover_mean": _nan_to_none(
            float(to.mean()) if not to.empty else 0.0
        ),
        "long_short_sharpe": _nan_to_none(ls_stats["long_short_sharpe"]),
        "long_short_annret": _nan_to_none(ls_stats["long_short_annret"]),
    }
    return payload, structured


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
        # 协作式中断：每个阶段边界查一次 status，用户点"中断"后这里就会抛
        # AbortedError，由下方专属 except 分支落 aborted 终态。
        check_abort("eval", run_id)

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
        check_abort("eval", run_id)  # 下面 factor.compute 可能跑几十秒，先查一次
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
        check_abort("eval", run_id)  # close 加载 + 指标计算前最后一次机会

        close = data.load_panel(
            symbols, start.date(), end.date(), field="close", adjust="qfq"
        )
        fwd_periods = [int(x) for x in body.get("forward_periods", [1, 5, 10])]

        _set_status(run_id, progress=70)
        # 所有横截面/分组/多空/换手/体检 逻辑都在 evaluate_factor_panel 内完成，
        # 保证 run_eval 与 composition_service 走同一套评估管线（单一真相源）。
        payload, structured = evaluate_factor_panel(
            F,
            close,
            forward_periods=fwd_periods,
            n_groups=n_groups_req,
            split_date=body.get("split_date"),
        )

        # 个股时序评估：对每只股票独立计算 IC / Hit Rate / 自相关
        _set_status(run_id, progress=85)
        try:
            from backend.services.metrics import (
                per_symbol_summary,
                ts_summary_stats,
            )

            fwd_ret_1d = close.shift(-1) / close - 1
            ts_per_symbol = per_symbol_summary(F, fwd_ret_1d)
            ts_stats = ts_summary_stats(ts_per_symbol)
            payload["time_series"] = {
                "summary": ts_stats,
                "per_symbol": _df_to_rows(ts_per_symbol),
                "top_n": _df_to_rows(ts_per_symbol.head(30)),
                "bottom_n": _df_to_rows(
                    ts_per_symbol.dropna(subset=["ts_ic"]).tail(30)
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception("个股时序评估失败 run_id=%s（不影响主流程）", run_id)
            payload["time_series"] = None

        _set_status(run_id, progress=90)
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
                        structured["ic_mean"],
                        structured["ic_std"],
                        structured["ic_ir"],
                        structured["ic_win_rate"],
                        structured["ic_t_stat"],
                        structured["rank_ic_mean"],
                        structured["rank_ic_std"],
                        structured["rank_ic_ir"],
                        structured["turnover_mean"],
                        structured["long_short_sharpe"],
                        structured["long_short_annret"],
                        # ensure_ascii=False 保留中文；payload 内已经把 NaN 全部
                        # 转成 None（_series_to_obj / _df_to_obj / value_hist 处理），
                        # 但为了 belt-and-suspenders 也用 allow_nan=False 让异常尽早暴露。
                        json.dumps(payload, ensure_ascii=False, allow_nan=False),
                    ),
                )
            c.commit()

        # 写 LLM / 用户友好的诊断 feedback：优先 LLM 解读完整 payload，失败
        # 回落规则版。与 success 终态分开调用——即便诊断逻辑出 bug，也不影响
        # run 落 success 终态。
        try:
            feedback_text = _build_eval_feedback(
                structured,
                payload=payload,
                hypothesis=getattr(factor, "hypothesis", "") or "",
                factor_id=body["factor_id"],
            )
            if feedback_text:
                _set_status(run_id, feedback=feedback_text)
        except Exception:  # noqa: BLE001
            log.exception("生成 feedback 失败 run_id=%s（不影响主流程）", run_id)

        _set_status(run_id, status="success", progress=100, finished=True)
    except AbortedError as exc:
        # 用户主动中断：落 "aborted" 终态，不写 error_message（没有 traceback 可言）。
        # 单独一条日志行便于运维区分被动失败和主动终止。
        log.info("eval aborted: run_id=%s reason=%s", run_id, exc)
        try:
            _set_status(run_id, status="aborted", finished=True)
        except Exception:
            log.exception("_set_status 落 aborted 失败: run_id=%s", run_id)
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
