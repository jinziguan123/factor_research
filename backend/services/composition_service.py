"""多因子合成与因子相关性分析服务（CompositionService）。

一次 ``run_composition`` 对一组因子（``factor_items``）在同一个池 / 窗口下：
1. 逐个加载（或缓存命中）因子宽表，统一 align 到同一张 (date × symbol) 面板；
2. 计算两两横截面 Pearson 相关矩阵（逐日算 → 时间平均），写入 ``corr_matrix_json``；
3. 按 ``method`` 合成出一张新因子宽表：
   - ``equal``：对每个因子按日 z-score，再等权算术平均；
   - ``ic_weighted``：用 rolling（全窗口）IC 均值作权重，保留方向 sign，然后 Σ|w|=1 归一化；
   - ``orthogonal_equal``：按给定顺序做横截面 Gram-Schmidt 残差正交化（每日逐列
     对已正交的前几列做最小二乘残差），再 z-score + 等权；
4. 把合成因子喂进 ``eval_service.evaluate_factor_panel`` 得到 payload + structured；
5. 同时为每个原始因子算一次 IC 汇总放入 ``per_factor_ic_json``，方便前端对比
   "合成 vs. 单因子"。

设计取舍：
- **合成层次**：在因子值（z-score 后）层面线性组合，不是在分组 / 仓位层面组合。
  前者通用性高（不依赖 qcut 假设），后者语义混乱（Σ top 组合 ≠ Σ z-score 后的 top）。
- **权重保留方向**：``ic_weighted`` 下负 IC 因子不丢弃、保留 sign（等价于取反后正加权），
  避免用户误把反向有效因子当"无用"排除；但若某因子 IC ~ 0，权重也会 ~ 0。
- **不写缓存**：合成因子没有"全局一致"的 params_hash 语义（因为涉及因子集合 + 方法
  + 权重策略），缓存会频繁失效。需要复用请跑新 composition。
- **相关性矩阵**：Pearson 而非 Spearman —— 用户一般关心"数值相关"而非"秩相关"，
  且 Pearson 计算更便宜；未来需要 rank 类因子的真实共线性可另加端点。
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
from backend.services.eval_service import evaluate_factor_panel
from backend.services.params_hash import params_hash as _hash
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


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
    """只更新显式传入的字段。与 eval_service._set_status 同构。"""
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
                f"UPDATE fr_composition_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


def _nan_to_none(x: Any) -> Any:
    """NaN / inf → None（JSON / MySQL 友好）。"""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


# ---------------------------- 合成核心逻辑 ----------------------------


def _zscore_per_day(F: pd.DataFrame) -> pd.DataFrame:
    """每日横截面 z-score。

    同日内对 F.loc[date, :] 做 (x - mean) / std。std=0 用 1e-12 兜底避免除零
    （等价于"该日所有股票因子值完全相同"，合成后贡献 0）。

    保留 NaN（某日某股票原始缺失）原样不填，下游等权/加权相加时另行 NaN 安全。
    """
    mu = F.mean(axis=1)
    sigma = F.std(axis=1, ddof=1).replace(0, np.nan)
    # broadcast：index 是日期，values 是标量；减法会按 index 对齐。
    z = F.sub(mu, axis=0).div(sigma.fillna(1e-12), axis=0)
    return z


def _align_frames(frames: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """把 N 张因子宽表 inner-join 到同一个 (date, symbol) 面板。

    避免"A 因子覆盖 2020~2023、B 因子只覆盖 2022"时，合成因子被 2020~2021 的
    单因子数据污染。inner 后两边可能都显著变小，这是用户需要感知的信号
    （service 入口会做"合成因子至少覆盖 10 天"的保守校验；不足就 raise）。
    """
    if not frames:
        return []
    # 所有 index 取交集
    common_idx = frames[0].index
    common_cols = frames[0].columns
    for f in frames[1:]:
        common_idx = common_idx.intersection(f.index)
        common_cols = common_cols.intersection(f.columns)
    return [f.reindex(index=common_idx, columns=common_cols) for f in frames]


def _pairwise_corr_matrix(
    frames: list[pd.DataFrame], factor_ids: list[str]
) -> dict:
    """计算因子两两横截面 Pearson 相关（逐日算 → 时间均值）。

    为什么是"逐日 Pearson 均值"而不是"把 (date, symbol) 拉平后整体 Pearson"？
    - 后者等价于把 panel stack 成一维向量再求相关，会被日间方差压迫：同方向但不同
      日量级的因子会呈现虚高相关。
    - 前者（Fama-MacBeth 风格）每日 de-meaned，只度量"当日横截面排序的一致性"，
      跨日再平均，语义与多因子研究中的"相关性"一致。

    Returns:
        {"factor_ids": ["reversal_n", ...], "values": [[1.0, 0.23, ...], ...]}
        对角线永远是 1.0；下三角与上三角镜像。NaN 转 None（JSON 友好）。
    """
    n = len(frames)
    if n == 0:
        return {"factor_ids": [], "values": []}

    # 把每张宽表先 z-score 到同一量级，避免某些因子"原始尺度差 10^6"污染 corr 输入。
    # 注意：Pearson 本身对线性缩放不敏感（分子分母同步缩放），这里 z-score 主要是
    # 避免单股票极端值对 corrcoef 计算的数值稳定性冲击。
    zs = [_zscore_per_day(f) for f in frames]

    # index / columns 上游已 align；逐日计算。
    dates = zs[0].index
    mats: list[np.ndarray] = []
    for dt in dates:
        cols = [z.loc[dt] for z in zs]
        # stack 成 (n_factors, n_symbols) 矩阵；mask 掉任何因子有 NaN 的列。
        arr = np.vstack([c.values.astype(float) for c in cols])
        mask = ~np.isnan(arr).any(axis=0)
        if mask.sum() < 3:
            # 当日有效样本 < 3 无法求相关，跳过。
            continue
        a = arr[:, mask]
        # np.corrcoef 对常数行返回 NaN（分母 std=0）；我们接受并在均值时 skip。
        with np.errstate(all="ignore"):
            c = np.corrcoef(a)
        mats.append(c)

    if not mats:
        # 极端情况（窗口内从没凑齐 3 个共同样本）：返回单位矩阵以保前端不崩。
        values = np.eye(n).tolist()
    else:
        stacked = np.stack(mats, axis=0)
        mean = np.nanmean(stacked, axis=0)
        # 对角线强制 1（数值误差可能导致 0.9999…）。
        np.fill_diagonal(mean, 1.0)
        values = [[_nan_to_none(float(x)) for x in row] for row in mean]

    return {"factor_ids": factor_ids, "values": values}


def _compute_ic_weights(
    z_frames: list[pd.DataFrame],
    close: pd.DataFrame,
    factor_ids: list[str],
    period: int = 1,
) -> dict[str, float]:
    """按每个因子的全窗口 IC 均值算权重。

    设计：保留 IC 方向（sign），而不是简单取 |IC|。这样若某因子全窗口 IC 为 -0.02，
    权重是 -0.02（合成时相当于取反），不至于把"反向有效"的因子直接丢掉。

    归一化：按 Σ|w_i| = 1。这一步是惯例，保证合成因子数值尺度与单因子 z-score 同级，
    下游的 qcut / 换手等指标语义不会因为加权变味。

    退化情况：所有因子 IC 都 ~0（Σ|w|=0）→ 退化成等权，并把 warning 让给上层路由记录。
    """
    fwd_ret = close.shift(-period) / close - 1
    weights: dict[str, float] = {}
    for fid, z in zip(factor_ids, z_frames):
        ic_series = metrics.cross_sectional_ic(z, fwd_ret)
        # ic_series 为空（窗口内全部日都算不了 IC）时均值会返回 NaN，转 0。
        m = float(ic_series.mean()) if not ic_series.empty else 0.0
        if not math.isfinite(m):
            m = 0.0
        weights[fid] = m
    total = sum(abs(v) for v in weights.values())
    if total < 1e-12:
        # 全因子都失效：退化为等权，避免返回全 0 的权重产生不可用的合成因子。
        log.warning(
            "ic_weighted: 所有因子 IC 均 ~0，退化为等权。factor_ids=%s", factor_ids
        )
        return {fid: 1.0 / len(factor_ids) for fid in factor_ids}
    return {fid: v / total for fid, v in weights.items()}


def _combine_equal(z_frames: list[pd.DataFrame]) -> pd.DataFrame:
    """等权合成：对 N 张已 z-score 的因子宽表逐元素均值（跨因子 axis）。

    用 pandas.concat + groupby 比 numpy stack 简洁，且保持 DataFrame 结构
    （index / columns 自动对齐）。NaN 用 nanmean 语义：只要有一个因子在 (d, s)
    有值就取有值的均值；若所有因子该处都 NaN，结果仍 NaN（下游 IC / qcut 会 skip）。
    """
    # concat 沿新的第 0 轴堆叠；keys 只是为了防止 index 冲突，后面 mean 会压掉。
    stacked = pd.concat(z_frames, keys=range(len(z_frames)))
    return stacked.groupby(level=1).mean()


def _combine_weighted(
    z_frames: list[pd.DataFrame], weights: dict[str, float], factor_ids: list[str]
) -> pd.DataFrame:
    """按因子权重线性组合（允许负权）。

    公式：F_combined[d, s] = Σ_i w_i * z_i[d, s]。NaN 处理：若某 i 在 (d, s) 为 NaN，
    该项跳过（视为权重 0），防止一个因子缺值就让合成结果也 NaN——这在窗口
    重叠度偏低时很常见。
    """
    result: pd.DataFrame | None = None
    for fid, z in zip(factor_ids, z_frames):
        w = weights.get(fid, 0.0)
        contrib = z.mul(w)
        if result is None:
            # 用第一个因子的框架建 result；其它因子通过 add(fill_value=0) 合入。
            result = contrib.copy()
        else:
            result = result.add(contrib, fill_value=0.0)
    # 如果所有因子在某 (d, s) 都 NaN，fill_value=0 的加法会错误地给出 0；
    # 用"所有项都 NaN"的掩码回填 NaN，保证下游横截面 IC 的 mask 语义正确。
    all_nan_mask = pd.concat(z_frames, keys=range(len(z_frames))).isna().groupby(level=1).all()
    result = result.where(~all_nan_mask)
    return result


def _combine_orthogonal_equal(z_frames: list[pd.DataFrame]) -> pd.DataFrame:
    """按给定顺序做横截面 Gram-Schmidt 残差正交化，再 z-score 等权。

    逐日处理：第一因子保留；第二因子对第一做横截面最小二乘残差；第三因子对前两列
    做残差；…… 最后把残差列再 z-score，等权平均。

    用意：``ic_weighted`` 解决"哪个因子更有用"但无法解决"两个因子强相关的冗余"；
    正交化把后续因子相对前者的独立贡献单独抽出来，等权后不会偏向相关度高的那一组。

    Args:
        z_frames: 已 z-score 的因子宽表列表（align 过）。

    Returns:
        合成因子宽表，index / columns 与入参一致。
    """
    if not z_frames:
        return pd.DataFrame()
    if len(z_frames) == 1:
        return z_frames[0]

    # 沿新轴堆为 (n_factors, n_dates, n_symbols)。
    arr = np.stack([f.values.astype(float) for f in z_frames], axis=0)
    n_f, n_d, n_s = arr.shape

    # 输出：逐日正交化后的 (n_f, n_s) 矩阵，再跨因子做均值。
    out = np.full((n_d, n_s), np.nan)
    for di in range(n_d):
        cur = arr[:, di, :]  # (n_f, n_s)
        # 某日所有因子都有值的 symbol 才参与正交化；其它位置保持 NaN。
        mask_col = ~np.isnan(cur).any(axis=0)
        if mask_col.sum() < 2:
            continue
        valid = cur[:, mask_col]  # (n_f, n_valid)
        # 对 valid 做按行的 Gram-Schmidt：保留第 0 行不变；
        # 第 k 行对前 0..k-1 行做 least squares 残差。
        basis: list[np.ndarray] = [valid[0]]
        for k in range(1, n_f):
            v = valid[k].copy()
            # 依次减去对已正交化 basis 的投影。
            for b in basis:
                denom = float(np.dot(b, b))
                if denom < 1e-12:
                    continue
                proj = float(np.dot(v, b) / denom)
                v = v - proj * b
            basis.append(v)
        orth = np.vstack(basis)  # (n_f, n_valid)
        # 每行 z-score（残差量级可能差几个数量级）。
        m = orth.mean(axis=1, keepdims=True)
        s = orth.std(axis=1, ddof=1, keepdims=True)
        s = np.where(s < 1e-12, 1e-12, s)
        z = (orth - m) / s
        out[di, mask_col] = z.mean(axis=0)

    return pd.DataFrame(out, index=z_frames[0].index, columns=z_frames[0].columns)


def _load_or_compute_factor(
    data: DataService,
    reg: FactorRegistry,
    factor_id: str,
    params: dict | None,
    symbols: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, int, str, dict]:
    """加载或计算一个因子；返回 (宽表 F, version, params_hash, params)。

    复用 run_eval 里的缓存协议（factor_value_1d）：(factor_id, version, params_hash)
    如果窗口覆盖完整就用缓存，否则 compute 并回写。
    """
    factor = reg.get(factor_id)
    version = reg.latest_version_from_db(factor_id)
    effective_params = params or factor.default_params
    phash = _hash(effective_params)

    warmup = factor.required_warmup(effective_params)
    ctx = FactorContext(
        data=data,
        symbols=symbols,
        start_date=start,
        end_date=end,
        warmup_days=warmup,
    )
    cached = data.load_factor_values(
        factor_id, version, phash, symbols, start.date(), end.date()
    )
    if (
        not cached.empty
        and cached.index.min() <= start
        and cached.index.max() >= end
    ):
        F = cached
    else:
        F = factor.compute(ctx, effective_params)
        if not F.empty:
            data.save_factor_values(factor_id, version, phash, F)
    return F, version, phash, effective_params


# ---------------------------- 公共入口 ----------------------------


def run_composition(run_id: str, body: dict) -> None:
    """执行一次多因子合成。

    Args:
        run_id: ``fr_composition_runs.run_id``，API 层 INSERT 时生成并传入。
        body: 请求体 dict，字段：
            - ``pool_id``（int）
            - ``start_date`` / ``end_date``（ISO 字符串或日期对象）
            - ``factor_items``：list[{"factor_id": str, "params": dict | None}]
            - ``method``：``equal`` / ``ic_weighted`` / ``orthogonal_equal``
            - ``n_groups``（int，默认 5）
            - ``forward_periods``（list[int]，默认 [1,5,10]）
            - ``ic_weight_period``（int，默认 1；仅 ic_weighted 用）
            - ``freq``（默认 "1d"）

    副作用：
        - 更新 ``fr_composition_runs.status / progress / started_at / finished_at``；
        - 成功时 UPDATE 本行：payload_json + corr_matrix_json + per_factor_ic_json
          + weights_json + 结构化指标列；
        - 失败时 ``status='failed'``，``error_message`` 留 traceback（截 4000 字符）。
    """
    try:
        _update_status(run_id, status="running", started=True, progress=5)

        factor_items: list[dict] = list(body.get("factor_items") or [])
        if len(factor_items) < 2:
            # 单因子合成等同于评估，应走 /evals。
            raise ValueError("factor_items 至少需要 2 个因子")
        method = str(body.get("method") or "equal")
        if method not in ("equal", "ic_weighted", "orthogonal_equal"):
            raise ValueError(f"method={method!r} 不支持，仅接受 equal/ic_weighted/orthogonal_equal")

        reg = FactorRegistry()
        reg.scan_and_register()
        data = DataService()
        symbols = data.resolve_pool(int(body["pool_id"]))
        n_groups_req = int(body.get("n_groups", 5))
        if len(symbols) < n_groups_req:
            raise ValueError(
                f"股票池 pool_id={body['pool_id']} 仅含 {len(symbols)} 只股票，"
                f"小于 n_groups={n_groups_req}，无法计算横截面 IC / 分组。"
            )

        start = pd.to_datetime(body["start_date"])
        end = pd.to_datetime(body["end_date"])
        fwd_periods = [int(x) for x in body.get("forward_periods", [1, 5, 10])]
        ic_weight_period = int(body.get("ic_weight_period", 1))

        _update_status(run_id, progress=15)

        # 1. 逐个因子加载（或 compute + 缓存回写）。
        frames: list[pd.DataFrame] = []
        factor_ids: list[str] = []
        resolved_items: list[dict] = []
        for idx, it in enumerate(factor_items):
            fid = it["factor_id"]
            fparams = it.get("params")
            F, version, phash, effective_params = _load_or_compute_factor(
                data, reg, fid, fparams, symbols, start, end
            )
            if F.empty:
                raise ValueError(
                    f"因子 {fid}（params={effective_params}）在指定窗口内无数据，"
                    f"无法参与合成"
                )
            frames.append(F)
            factor_ids.append(fid)
            resolved_items.append(
                {
                    "factor_id": fid,
                    "factor_version": int(version),
                    "params_hash": phash,
                    "params": effective_params,
                }
            )
            # 加载阶段 15..35
            _update_status(run_id, progress=15 + int(20 * (idx + 1) / len(factor_items)))

        # 2. 对齐：inner-join index / columns，保证后续合成 / 相关性同 panel。
        aligned = _align_frames(frames)
        if aligned[0].empty or len(aligned[0].index) < 10:
            raise ValueError(
                "对齐后共同窗口不足 10 天，合成无统计意义。"
                "请检查各因子的数据覆盖区间或缩小 start/end。"
            )

        _update_status(run_id, progress=40)

        # 3. 相关性矩阵（基于 z-score 后的因子，避免量级差异污染）。
        corr = _pairwise_corr_matrix(aligned, factor_ids)

        _update_status(run_id, progress=55)

        # 4. 准备 close 宽表（IC 加权需要，评估也需要）。
        common_syms = list(aligned[0].columns)
        close = data.load_panel(
            common_syms, start.date(), end.date(), field="close", adjust="qfq"
        )

        # 5. 按 method 合成。
        z_frames = [_zscore_per_day(f) for f in aligned]
        weights: dict[str, float] | None = None
        if method == "equal":
            F_combined = _combine_equal(z_frames)
        elif method == "ic_weighted":
            weights = _compute_ic_weights(
                z_frames, close, factor_ids, period=ic_weight_period
            )
            F_combined = _combine_weighted(z_frames, weights, factor_ids)
        else:  # orthogonal_equal
            F_combined = _combine_orthogonal_equal(z_frames)

        if F_combined.empty:
            raise ValueError("合成后的因子宽表为空，无法进行评估")

        _update_status(run_id, progress=75)

        # 6. 评估合成因子（完全复用 evaluate_factor_panel）。
        payload, structured = evaluate_factor_panel(
            F_combined,
            close,
            forward_periods=fwd_periods,
            n_groups=n_groups_req,
        )

        # 7. 每个原始因子的 IC 汇总（base_period 上），方便前端对比"合成 vs. 单因子"。
        base_period = fwd_periods[0] if fwd_periods else 1
        fwd_ret_base = close.shift(-base_period) / close - 1
        per_factor_ic: dict[str, dict] = {}
        for fid, z in zip(factor_ids, z_frames):
            ic_s = metrics.cross_sectional_ic(z, fwd_ret_base)
            ic_sum = metrics.ic_summary(ic_s)
            per_factor_ic[fid] = {
                "ic_mean": _nan_to_none(ic_sum["ic_mean"]),
                "ic_ir": _nan_to_none(ic_sum["ic_ir"]),
                "ic_win_rate": _nan_to_none(ic_sum["ic_win_rate"]),
            }

        _update_status(run_id, progress=90)

        # 8. 入库：把四份 JSON + 结构化列一次 UPDATE 写入（INSERT 已在 router 做）。
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fr_composition_runs
                    SET ic_mean=%s, ic_std=%s, ic_ir=%s, ic_win_rate=%s, ic_t_stat=%s,
                        rank_ic_mean=%s, rank_ic_std=%s, rank_ic_ir=%s,
                        turnover_mean=%s, long_short_sharpe=%s, long_short_annret=%s,
                        corr_matrix_json=%s, per_factor_ic_json=%s,
                        weights_json=%s, payload_json=%s
                    WHERE run_id=%s
                    """,
                    (
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
                        json.dumps(corr, ensure_ascii=False, allow_nan=False),
                        json.dumps(per_factor_ic, ensure_ascii=False, allow_nan=False),
                        json.dumps(weights, ensure_ascii=False, allow_nan=False)
                        if weights is not None
                        else None,
                        json.dumps(payload, ensure_ascii=False, allow_nan=False),
                        run_id,
                    ),
                )
            c.commit()

        # resolved_items（含 version / params_hash）也回写一次，让后续审计能还原"哪份缓存参与了合成"。
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE fr_composition_runs SET factor_items_json=%s WHERE run_id=%s",
                    (json.dumps(resolved_items, ensure_ascii=False), run_id),
                )
            c.commit()

        _update_status(run_id, status="success", progress=100, finished=True)
    except Exception:
        log.exception("composition failed: run_id=%s", run_id)
        try:
            _update_status(
                run_id,
                status="failed",
                error=traceback.format_exc()[:4000],
                finished=True,
            )
        except Exception:
            log.exception(
                "_update_status 记录失败时自身也抛异常: run_id=%s", run_id
            )
