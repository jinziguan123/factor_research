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
    # dropna：某年的 IC 若全是 NaN（例如样本太稀），groupby.mean() 会返回 NaN；
    # 这种年份不该参与稳定性判断，否则 cv 会被 NaN 污染、allow_nan=False 的
    # payload 序列化会抛。
    by_year = ic_series.groupby(ic_series.index.year).mean().dropna()
    if by_year.empty:
        return {
            "years": [],
            "ic_mean_by_year": [],
            "sign_consistent": True,
            "cv": 0.0,
        }
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
    # 必须过滤非有限值（NaN / ±inf）而不只是 NaN：np.histogram 的 range auto-detect
    # 会对 ±inf 边界直接抛 ValueError("range ... is not finite")，把一次原本可恢复的
    # 画图调用变成整条 run 崩溃。源头常见是 pct_change 在 close=0 上得到 inf，
    # 因子缓存里混入异常值时以前这里会炸，现在统一在直方图边界上挡住。
    arr = arr.astype(float, copy=False)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"bins": [], "counts": []}
    counts, edges = np.histogram(arr, bins=bins)
    return {"bins": edges.tolist(), "counts": counts.tolist()}


# ------------------- 高级评估指标 -------------------


def sector_neutral_ic(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    sector: pd.DataFrame,
) -> pd.Series:
    """行业中性化后的截面 Pearson IC。

    对每个交易日，将因子值对行业哑变量做 OLS 回归，取残差作为行业中性的因子值，
    再计算残差与 forward_ret 的 Pearson 相关系数。

    这排除了"因子靠行业暴露赚 beta"的可能，衡量的是纯选股 alpha。

    Args:
        factor: 因子宽表 ``index=date, columns=symbol``。
        forward_ret: 同结构的前向收益。
        sector: 行业分类宽表 ``index=date, columns=symbol``，值为行业代码（str/int）。
            行业信息缺失的 symbol 对应的值为 NaN/None，该行会被跳过。

    Returns:
        ``pd.Series(index=date, value=corr)``——行业中性化的每日 IC。
    """
    aligned_f, aligned_r = factor.align(forward_ret, join="inner")
    _, aligned_s = aligned_f.align(sector, join="inner")
    out: dict = {}
    for dt, f_row in aligned_f.iterrows():
        r_row = aligned_r.loc[dt]
        s_row = aligned_s.loc[dt]
        mask = f_row.notna() & r_row.notna() & s_row.notna()
        if mask.sum() < 10:
            # 行业中性化需要至少 2 个行业 × 若干股票，样本太少回归不稳定
            continue
        # 行业哑变量（drop_first 避免共线性）
        try:
            dummies = pd.get_dummies(s_row[mask], drop_first=True, dtype=float)
        except Exception:
            continue
        if dummies.shape[1] == 0 or dummies.shape[0] <= dummies.shape[1] + 1:
            continue
        # OLS: factor ~ sectors → 残差 = 行业中性的 alpha
        from numpy.linalg import lstsq

        X = np.column_stack([np.ones(dummies.shape[0]), dummies.values])
        y = f_row[mask].values.astype(float)
        try:
            coef, _, _, _ = lstsq(X, y)
        except np.linalg.LinAlgError:
            continue
        residual = y - X @ coef
        if len(residual) < 3:
            continue
        r_vals = r_row[mask].values.astype(float)
        valid = np.isfinite(residual) & np.isfinite(r_vals)
        if valid.sum() < 3:
            continue
        corr = np.corrcoef(residual[valid], r_vals[valid])[0, 1]
        if np.isfinite(corr):
            out[dt] = float(corr)
    return pd.Series(out).sort_index()


def ic_decay(
    factor: pd.DataFrame,
    forward_rets: dict[int, pd.DataFrame],
) -> dict[int, float]:
    """计算不同持有期的 IC 均值（IC 衰减结构）。

    Args:
        factor: 因子宽表。
        forward_rets: ``{hold_days: forward_ret_panel}``，如
            ``{1: ret_1d, 5: ret_5d, 10: ret_10d, 21: ret_21d}``。

    Returns:
        ``{hold_days: ic_mean}``，每个持有期的截面 Rank IC 均值。
        某个持有期 IC 序列为空则该 key 值为 0.0。
    """
    result: dict[int, float] = {}
    for hold_days, ret_panel in sorted(forward_rets.items()):
        ic = cross_sectional_rank_ic(factor, ret_panel)
        result[hold_days] = float(ic.mean()) if not ic.empty else 0.0
    return result


def conditional_ic(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    condition: pd.Series,
) -> dict[str, pd.Series]:
    """按市场状态拆分的条件 IC。

    将交易日按 condition 的中位数分为"高"和"低"两组（如市场波动率高低），
    分别计算每组的截面 Rank IC，用于识别因子在不同市场环境中的有效性差异。

    Args:
        factor: 因子宽表。
        forward_ret: 前向收益宽表。
        condition: ``pd.Series(index=date)``，市场状态变量（如市场日收益、VIX 等）。

    Returns:
        ``{"high": ic_series_high, "low": ic_series_low}``。
        空 condition 返回两个空 Series。
    """
    if condition.empty:
        return {"high": pd.Series(dtype=float), "low": pd.Series(dtype=float)}
    median = condition.median()
    high_dates = condition.index[condition > median]
    low_dates = condition.index[condition <= median]
    ic_full = cross_sectional_rank_ic(factor, forward_ret)
    return {
        "high": ic_full.reindex(ic_full.index.intersection(high_dates)).dropna(),
        "low": ic_full.reindex(ic_full.index.intersection(low_dates)).dropna(),
    }


def newey_west_se(series: pd.Series, max_lags: int | None = None) -> float:
    """Newey-West 自相关稳健标准误。

    对时间序列（如每日 IC）计算异方差和自相关一致（HAC）的标准误。
    用于检验 IC 是否在统计上显著偏离 0。

    Args:
        series: 时间序列。
        max_lags: 最大滞后期数。None 时用 ``int(4 * (n/100)^(2/9))``（Newey-West 1994 推荐）。

    Returns:
        HAC 标准误；序列长度 < 2 返回 0.0。
    """
    n = len(series)
    if n < 2:
        return 0.0
    if max_lags is None:
        max_lags = int(4 * (n / 100.0) ** (2 / 9))
    max_lags = min(max_lags, n - 1)
    x = series.values - series.mean()
    # 方差部分
    S0 = np.sum(x ** 2) / n
    # 自协方差加权部分
    w = 1.0 - np.arange(1, max_lags + 1) / (max_lags + 1.0)  # Bartlett kernel
    for lag in range(1, max_lags + 1):
        S0 += 2.0 * w[lag - 1] * np.sum(x[lag:] * x[:-lag]) / n
    S0 = max(S0, 0.0)
    return float(np.sqrt(S0 / n))


def ic_summary_robust(ic_series: pd.Series) -> dict:
    """带 Newey-West 稳健标准误的 IC 汇总统计。

    Returns:
        dict 在 ``ic_summary`` 基础上增加：
        - ``ic_t_nw``：Newey-West 稳健 t 统计量
        - ``nw_se``：Newey-West 标准误
    """
    base = ic_summary(ic_series)
    if ic_series.empty:
        base["ic_t_nw"] = 0.0
        base["nw_se"] = 0.0
        return base
    nw_se = newey_west_se(ic_series)
    base["nw_se"] = nw_se
    base["ic_t_nw"] = float(base["ic_mean"] / nw_se) if nw_se > 1e-12 else 0.0
    return base


def fama_macbeth(
    factor_panels: dict[str, pd.DataFrame],
    forward_ret: pd.DataFrame,
) -> dict:
    """Fama-MacBeth 两阶段回归。

    Stage 1（横截面）：对每个交易日 t，回归：
        forward_ret_{i,t} = alpha_t + sum(beta_{k,t} * factor_{k,i,t}) + e_{i,t}
    Stage 2（时间序列）：对每个因子 k，beta_k = mean(beta_{k,t})，用 Newey-West 算 t 值。

    Args:
        factor_panels: ``{factor_name: panel}``，每个 panel 是 index=date, columns=symbol。
        forward_ret: 前向收益宽表。

    Returns:
        ``{"alpha": float, "factors": {name: {"coef": float, "t_stat": float, "se": float}}}``。
        空输入或对齐后无有效日期返回 alpha=0 + 各 factor coef=0。
    """
    if not factor_panels:
        return {"alpha": 0.0, "factors": {}}
    # 对齐所有面板
    names = list(factor_panels.keys())
    panels = list(factor_panels.values())
    aligned = [forward_ret] + panels
    common_cols = aligned[0].columns
    common_idx = aligned[0].index
    for p in aligned[1:]:
        common_cols = common_cols.intersection(p.columns)
        common_idx = common_idx.intersection(p.index)
    if len(common_cols) < 5 or len(common_idx) < 10:
        return {"alpha": 0.0, "factors": {n: {"coef": 0.0, "t_stat": 0.0, "se": 0.0} for n in names}}
    # Stage 1：每日横截面回归
    from numpy.linalg import lstsq

    alpha_ts: list[float] = []
    beta_ts: dict[str, list[float]] = {n: [] for n in names}
    for dt in common_idx:
        y = forward_ret.loc[dt, common_cols].values.astype(float)
        X_cols = []
        for p in panels:
            X_cols.append(p.loc[dt, common_cols].values.astype(float))
        X = np.column_stack(X_cols)
        mask = np.isfinite(y)
        for j in range(X.shape[1]):
            mask &= np.isfinite(X[:, j])
        if mask.sum() < len(names) + 2:
            continue
        Xm = np.column_stack([np.ones(mask.sum()), X[mask]])
        ym = y[mask]
        try:
            coef, _, _, _ = lstsq(Xm, ym)
        except np.linalg.LinAlgError:
            continue
        alpha_ts.append(float(coef[0]))
        for j, name in enumerate(names):
            beta_ts[name].append(float(coef[j + 1]))
    if not alpha_ts:
        return {"alpha": 0.0, "factors": {n: {"coef": 0.0, "t_stat": 0.0, "se": 0.0} for n in names}}
    # Stage 2：时间序列平均 + Newey-West
    alpha_series = pd.Series(alpha_ts)
    result = {
        "alpha": float(alpha_series.mean()),
        "alpha_t": float(alpha_series.mean() / newey_west_se(alpha_series)) if newey_west_se(alpha_series) > 1e-12 else 0.0,
        "n_dates": len(alpha_ts),
        "factors": {},
    }
    for name in names:
        if not beta_ts[name]:
            result["factors"][name] = {"coef": 0.0, "t_stat": 0.0, "se": 0.0}
            continue
        beta_s = pd.Series(beta_ts[name])
        se = newey_west_se(beta_s)
        result["factors"][name] = {
            "coef": float(beta_s.mean()),
            "t_stat": float(beta_s.mean() / se) if se > 1e-12 else 0.0,
            "se": float(se),
        }
    return result
