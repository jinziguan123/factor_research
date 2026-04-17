"""评估引擎的核心数学库：**纯函数，无 DB 依赖**，便于单测。

所有 API 都接受 pandas 宽表（``index=trade_date``, ``columns=symbol``），
返回 pandas Series / DataFrame / dict。NaN / 极端值的处理：
- 空输入一律返回"空但结构合法"的对象（空 Series / 空 dict 中的 0）；
- 仅当有效样本不足（<阈值）时对该日期 skip，而非整段抛错；
- std=0 的比率类指标用 1e-12 兜底，防止 ZeroDivision / inf。

调用方（``eval_service.run_eval``）拿到这些对象后会再做 ``_nan_to_none``
兜底才写入 MySQL / JSON payload，避免 ``json.dumps`` 因 NaN / inf 崩溃。
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy.stats import ConstantInputWarning, spearmanr


def cross_sectional_ic(
    factor: pd.DataFrame, forward_ret: pd.DataFrame
) -> pd.Series:
    """每日横截面 Pearson IC。

    Args:
        factor: 宽表，``index=date, columns=symbol``，值为因子值。
        forward_ret: 同上结构，值为未来 N 日收益。

    Returns:
        ``pd.Series``（``index=date, value=corr``，按日期升序）。
        对每个日期：只保留两边都非 NaN 的 symbol；有效样本 <3 跳过。
    """
    # align(join="inner") 保证两张表共享的 index / columns 才参与计算；
    # 外部传入的 forward_ret 可能比 factor 短（比如末尾 shift(-k) 后尾部全 NaN）。
    aligned_f, aligned_r = factor.align(forward_ret, join="inner")
    out: dict = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < 3:
            # <3 个点算相关系数无意义（甚至 2 点一定是 ±1），直接跳过。
            continue
        out[dt] = float(np.corrcoef(f_row[mask], r_row[mask])[0, 1])
    return pd.Series(out).sort_index()


def cross_sectional_rank_ic(
    factor: pd.DataFrame, forward_ret: pd.DataFrame
) -> pd.Series:
    """每日横截面 Spearman Rank IC。

    与 ``cross_sectional_ic`` 同构，只是用 ``scipy.stats.spearmanr``。
    当因子在当日所有 symbol 都相等（rank 全相同）时 spearmanr 返回 NaN，
    这里会把该日期跳过，保证返回 Series 不含 NaN / inf。
    """
    aligned_f, aligned_r = factor.align(forward_ret, join="inner")
    out: dict = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < 3:
            continue
        # scipy 对常数输入会发 ConstantInputWarning 并返回 NaN；
        # 我们下方已经检测 NaN 跳过，warning 本身对研究无额外价值，局部静默。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConstantInputWarning)
            rho, _ = spearmanr(f_row[mask], r_row[mask])
        # spearmanr 在退化输入上会返回 nan（如两列完全相同）。
        if rho is None or (isinstance(rho, float) and not np.isfinite(rho)):
            continue
        out[dt] = float(rho)
    return pd.Series(out).sort_index()


def ic_summary(ic_series: pd.Series) -> dict:
    """对 IC 序列做汇总统计。

    Returns:
        dict 包含：
        - ``ic_mean``：均值；
        - ``ic_std``：样本标准差（ddof=1）；
        - ``ic_ir``：Information Ratio = mean / std，std=0 用 1e-12 兜底；
        - ``ic_win_rate``：IC>0 的占比；
        - ``ic_t_stat``：t 统计量 = mean / (std / sqrt(n))。

    空 Series 返回全零 dict。
    """
    if ic_series.empty:
        return {
            "ic_mean": 0,
            "ic_std": 0,
            "ic_ir": 0,
            "ic_win_rate": 0,
            "ic_t_stat": 0,
        }
    n = len(ic_series)
    mean = float(ic_series.mean())
    # std(ddof=1) 对 n=1 返回 NaN；再 ``or 1e-12`` 兜底（NaN 在布尔上下文为 False）。
    std_raw = float(ic_series.std(ddof=1)) if n > 1 else 0.0
    std = std_raw if (std_raw and np.isfinite(std_raw)) else 1e-12
    return {
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": mean / std,
        "ic_win_rate": float((ic_series > 0).mean()),
        "ic_t_stat": mean / (std / np.sqrt(n)),
    }


def group_returns(
    factor: pd.DataFrame,
    forward_ret_1d: pd.DataFrame,
    n_groups: int = 5,
) -> pd.DataFrame:
    """每日按因子 qcut 分 n_groups 组，返回各组日均收益宽表。

    Args:
        factor: 因子宽表。
        forward_ret_1d: 通常是 T+1 收益（= close.shift(-1)/close - 1）。
        n_groups: 分组数（5 组即五分位）。

    Returns:
        ``pd.DataFrame(index=date, columns=0..n_groups-1)``：
        columns 是分组 label（0=最低分位，n_groups-1=最高分位），
        值为该日该组的**算术平均**收益。
        若某日所有因子值相同（qcut 抛 ValueError）或有效样本 <n_groups，
        该日期整行被跳过（不出现在结果 index 里）。
        全部日期都不可用时返回空 DataFrame。
    """
    aligned_f, aligned_r = factor.align(forward_ret_1d, join="inner")
    rows: dict = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        mask = f_row.notna() & r_row.notna()
        if mask.sum() < n_groups:
            continue
        try:
            # duplicates="drop" 允许因子值重复时合并边界，避免 ValueError；
            # 但如果值极度集中（所有点都相同）仍会抛 ValueError，走 except 跳过。
            q = pd.qcut(f_row[mask], n_groups, labels=False, duplicates="drop")
        except ValueError:
            continue
        df = pd.DataFrame({"q": q.values, "r": r_row[mask].values})
        # groupby("q").mean() 返回 <=n_groups 行；reindex 后保证列宽恒定，
        # 某组缺失时用 NaN 填，下游 long_short_series 会用 iloc[-1/-0] 读首尾。
        rows[dt] = df.groupby("q")["r"].mean().reindex(range(n_groups))
    if not rows:
        return pd.DataFrame()
    # 字典 → DataFrame 后 index 是 label，转置回 date 行；sort_index 保证时间递增。
    return pd.DataFrame(rows).T.sort_index()


def turnover_series(
    factor: pd.DataFrame, n_groups: int = 5, which: str = "top"
) -> pd.Series:
    """每日目标组（top / bottom）相对前一期的**单边换手率**。

    公式：``|current \\ prev| / |current|`` —— 今日组内"新进"股票占比。
    值域 [0, 1]：0 = 组成员完全不变，1 = 组成员全换。

    历史注：旧版本用 ``|对称差| / |current|``（双边，最大 2.0），
    与行业惯例不一致且在 UI 上被显示成 ">100%" 误导用户，已改为单边。
    若组大小恒定，单边 = 对称差 / (2 × 组大小)；组大小不一致时用"新进占比"
    更稳，且语义直观（"今天有多少只是昨天没有的"）。

    Args:
        factor: 因子宽表。
        n_groups: 分组数。
        which: ``"top"`` 取最高分位组（label=n_groups-1），``"bottom"`` 取 0 组。

    Returns:
        ``pd.Series(index=date, value=turnover)``。第一期无前期对比被跳过；
        若某天有效 symbol <n_groups 或 qcut 失败，也跳过但不清空 prev。
    """
    prev: set | None = None
    out: dict = {}
    for dt, f_row in factor.iterrows():
        valid = f_row.dropna()
        if len(valid) < n_groups:
            continue
        try:
            q = pd.qcut(valid, n_groups, labels=False, duplicates="drop")
        except ValueError:
            # 当日所有值相同，无法分组：跳过但保留 prev，
            # 下一次可对比的日期仍以最近一次成功分组为基准。
            continue
        target_label = n_groups - 1 if which == "top" else 0
        current = set(valid.index[q == target_label])
        if prev is not None and current and prev:
            # 单边换手：新进股票占当前组的比例，∈ [0, 1]。
            out[dt] = len(current - prev) / max(len(current), 1)
        prev = current
    return pd.Series(out).sort_index()


def long_short_series(group_rets: pd.DataFrame) -> pd.Series:
    """多空组合日收益：顶组（最后列）- 底组（第一列）。

    过滤 top 或 bot 为 NaN 的日期——这种情况通常是因子值大量 tied 导致
    ``group_returns`` 里 ``qcut(duplicates='drop')`` 合并了 bin，首尾分位其中
    一组根本不存在。保留 NaN 会让下游的 cumprod 整条净值从此变 NaN，也会在
    统计时被 ``.mean()`` 静默跳过，掩盖"有效样本数其实很少"的事实。

    返回的 Series 长度即可视作"多空可用交易日数"，调用方据此判断
    Sharpe / 年化是否可信（样本 <30 天基本都是噪声主导）。
    """
    if group_rets.empty:
        return pd.Series(dtype=float)
    top = group_rets.iloc[:, -1]
    bot = group_rets.iloc[:, 0]
    return (top - bot).dropna().rename("long_short")


def long_short_metrics(
    ls: pd.Series, trading_days: int = 252
) -> dict:
    """多空组合的年化收益 / 夏普比率 / 有效样本数。

    Args:
        ls: 多空日收益 Series（应为 ``long_short_series`` 的输出，已 dropna）。
        trading_days: 年化天数（A 股约 252）。

    Returns:
        ``{"long_short_annret": 年化简单收益,
           "long_short_sharpe": 年化夏普,
           "long_short_n_effective": 可用交易日数}``

        空输入返回 ``{0, 0, 0}``。std=0 用 1e-12 兜底避免 inf。

        ``n_effective`` 用于提醒调用方：当 rank 类因子值大量 tied 时，
        qcut 分组退化会让 top 或 bot 组频繁缺失，ls 有效样本可能只有几十天，
        此时 Sharpe / 年化收益被少数极端日主导，统计意义有限（<30 天基本不可用）。
    """
    if ls.empty:
        return {
            "long_short_annret": 0,
            "long_short_sharpe": 0,
            "long_short_n_effective": 0,
        }
    ann = float(ls.mean() * trading_days)
    std_raw = float(ls.std(ddof=1)) if len(ls) > 1 else 0.0
    std = std_raw if (std_raw and np.isfinite(std_raw)) else 1e-12
    sharpe = float(ls.mean() / std * np.sqrt(trading_days))
    return {
        "long_short_annret": ann,
        "long_short_sharpe": sharpe,
        "long_short_n_effective": int(len(ls)),
    }


def cross_section_uniqueness(factor: pd.DataFrame) -> float:
    """每日横截面独特值率的均值：`nunique() / n_valid` 的时间均值。

    直接反映"这个因子在单日是不是高度离散 tied"——rank / argmax / 分档类因子
    值域有限，横截面内大量股票值完全相同，uniqueness 会显著低于 1。连续因子
    （动量、反转、波动率）这个比例通常非常接近 1。

    Returns:
        float ∈ [0, 1]；空表或每行都只有 <1 个非 NaN 值 → 返回 0.0（无法定义）。
    """
    if factor.empty:
        return 0.0
    ratios: list[float] = []
    for _, row in factor.iterrows():
        valid = row.dropna()
        if len(valid) < 1:
            continue
        ratios.append(valid.nunique() / len(valid))
    if not ratios:
        return 0.0
    return float(np.mean(ratios))


def qcut_full_rate(factor: pd.DataFrame, n_groups: int) -> float:
    """每日 `pd.qcut(..., duplicates='drop')` 实际出组数 / 请求组数 的时间均值。

    直接预测"分组 / 多空能不能做出来"——rank 类因子大量 tied 导致 qcut 边界
    合并，实际组数 < n_groups 时，最顶 / 最底分位会经常为空，`long_short_series`
    dropna 后有效样本急剧萎缩（用户曾踩坑的根源）。

    Args:
        factor: 因子宽表。
        n_groups: 请求的分组数（与评估 / 回测里一致即可）。

    Returns:
        float ∈ [0, 1]；每个有效日贡献 `实际组数 / n_groups`，再跨日取均值。
        空表 / n_groups <= 0 / 所有日都 qcut 失败 → 0.0。
    """
    if factor.empty or n_groups <= 0:
        return 0.0
    ratios: list[float] = []
    for _, row in factor.iterrows():
        valid = row.dropna()
        if len(valid) < n_groups:
            continue
        try:
            labels = pd.qcut(valid, q=n_groups, labels=False, duplicates="drop")
        except ValueError:
            # 所有值完全相同等退化 case：记为 0 组（而非跳过），否则会人为高估满组率。
            ratios.append(0.0)
            continue
        # pandas 1.x 对全相同输入可能静默返回全 NaN，dropna 统一处理。
        labels = labels.dropna()
        if labels.empty:
            ratios.append(0.0)
            continue
        actual_groups = int(labels.nunique())
        ratios.append(min(actual_groups / n_groups, 1.0))
    if not ratios:
        return 0.0
    return float(np.mean(ratios))


def ic_annual_stability(ic_series: pd.Series) -> dict:
    """把 IC 序列按年分段，报告年度 IC 均值与跨年稳定性。

    研究里经常一个因子在样本内（比如 2019-2021）IC 很好，样本外（2022+）反号
    或无效。整段平均 IC 会掩盖这种失效。按年拆开一眼就能看出"稳定 / 单年显著
    / 后期塌陷"。

    Args:
        ic_series: 每日 IC（``cross_sectional_ic`` 的输出）；index 必须是
            可访问 ``.year`` 的 DatetimeIndex。

    Returns:
        ``{"years": [2020, 2021, ...],
           "ic_mean_by_year": [0.012, -0.004, ...],
           "sign_consistent": True/False,       # 所有非零年份同号
           "cv": 0.87}``                        # std / |mean| 跨年 IC 均值的变异系数

        空输入返回 ``{"years": [], "ic_mean_by_year": [], "sign_consistent": True, "cv": 0.0}``。
    """
    if ic_series.empty:
        return {
            "years": [],
            "ic_mean_by_year": [],
            "sign_consistent": True,
            "cv": 0.0,
        }
    # groupby 年份；index 已是 DatetimeIndex（cross_sectional_ic 保证）。
    by_year = ic_series.groupby(ic_series.index.year).mean()
    years = [int(y) for y in by_year.index]
    means = [float(v) for v in by_year.values]
    # 一致性：所有|v|>1e-10 的年份符号相同（完全 0 的年份不参与判定）。
    signs = [1 if v > 1e-10 else -1 if v < -1e-10 else 0 for v in means]
    nonzero_signs = [s for s in signs if s != 0]
    sign_consistent = len(set(nonzero_signs)) <= 1
    # 变异系数：跨年 IC mean 的 std / |mean|。1e-12 兜底避免除零。
    arr = np.array(means, dtype=float)
    mean_of_means = float(arr.mean()) if arr.size else 0.0
    std_of_means = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    cv = std_of_means / (abs(mean_of_means) + 1e-12)
    return {
        "years": years,
        "ic_mean_by_year": means,
        "sign_consistent": sign_consistent,
        "cv": float(cv),
    }


def value_histogram(values: pd.DataFrame, bins: int = 50) -> dict:
    """把因子值拉平后算直方图。

    Args:
        values: 因子宽表；会调用 ``.values.ravel()`` 展开成 1-D 数组。
        bins: numpy histogram 的分箱数。

    Returns:
        ``{"bins": [边界列表，长度 bins+1], "counts": [各箱频次，长度 bins]}``。
        全 NaN 返回 ``{"bins": [], "counts": []}``。
    """
    arr = values.values.ravel() if isinstance(values, pd.DataFrame) else np.asarray(values).ravel()
    arr = arr[~np.isnan(arr.astype(float, copy=False))]
    if arr.size == 0:
        return {"bins": [], "counts": []}
    counts, edges = np.histogram(arr, bins=bins)
    return {"bins": edges.tolist(), "counts": counts.tolist()}
