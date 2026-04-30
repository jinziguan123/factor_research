"""评估引擎数学库单测（纯函数，不依赖数据库）。

覆盖点：
- Pearson IC 对完美正 / 负相关的正确性；
- Rank IC 对"所有因子值相同"这类退化输入不崩；
- 分组收益在因子 == 未来收益时的单调性；
- 换手率在排名不变时应为 0；
- IC 汇总统计（mean / std / ir / win_rate）基本边界；
- 值直方图；
- 多空收益汇总；
- eval_service 只做 import smoke（不触达 DB）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import metrics


def _mk_panel(n_dates: int = 60, n_syms: int = 20, seed: int = 0) -> pd.DataFrame:
    """构造一个形如 (n_dates, n_syms) 的宽表随机因子矩阵。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    cols = [f"S{i:02d}" for i in range(n_syms)]
    return pd.DataFrame(
        rng.standard_normal((n_dates, n_syms)), index=idx, columns=cols
    )


def test_ic_perfect_positive_relationship():
    """未来收益 = 因子 * 0.1 + 极小噪声 → IC 应 ≈ 1。"""
    f = _mk_panel()
    rng = np.random.default_rng(42)
    r = f * 0.1 + rng.standard_normal(f.shape) * 1e-8
    ic = metrics.cross_sectional_ic(f, r)
    assert ic.mean() > 0.99


def test_ic_perfect_negative_relationship():
    """未来收益 = -因子 * 0.1 + 极小噪声 → IC 应 ≈ -1。"""
    f = _mk_panel()
    rng = np.random.default_rng(42)
    r = -f * 0.1 + rng.standard_normal(f.shape) * 1e-8
    ic = metrics.cross_sectional_ic(f, r)
    assert ic.mean() < -0.99


def test_rank_ic_handles_ties():
    """所有因子值相同时 rank corr 无法定义，函数应跳过该日而不是抛或返回 inf。"""
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    f = pd.DataFrame(1.0, index=idx, columns=["A", "B", "C", "D"])
    r = _mk_panel(n_dates=5, n_syms=4)
    # columns 要对齐：用相同 symbols
    r.columns = ["A", "B", "C", "D"]
    r.index = idx
    rr = metrics.cross_sectional_rank_ic(f, r)
    # 要么 series 为空，要么所有值非 inf / 非 NaN
    if not rr.empty:
        assert not np.isinf(rr).any()
        assert not rr.isna().any()


def test_group_returns_monotonic_when_factor_predicts_return():
    """因子直接 = 未来收益时，分组后各组均收益应严格单调递增。"""
    f = _mk_panel(n_dates=80, n_syms=50)
    r = f.copy()
    g = metrics.group_returns(f, r, n_groups=5)
    assert not g.empty
    means = g.mean().values
    for i in range(len(means) - 1):
        assert means[i] <= means[i + 1], (
            f"分组收益非单调：means[{i}]={means[i]} > means[{i+1}]={means[i+1]}"
        )


def test_turnover_zero_when_factor_rank_constant():
    """每日因子排名一致 → top 组每日成员相同 → 换手率 = 0。"""
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(10)]
    vals = np.tile(np.arange(10), (10, 1))  # 每行都是 [0,1,...,9]
    f = pd.DataFrame(vals, index=idx, columns=cols)
    to = metrics.turnover_series(f, n_groups=5, which="top")
    assert not to.empty
    assert (to == 0).all()


def test_ic_summary_basic():
    """ic_summary 返回五项指标，ir=mean/std、win_rate 在 [0,1]。"""
    ic = pd.Series([0.1, 0.05, -0.02, 0.08, 0.06])
    s = metrics.ic_summary(ic)
    assert s["ic_mean"] > 0
    assert 0 <= s["ic_win_rate"] <= 1
    # ir 应等于 mean / std(ddof=1)
    assert s["ic_ir"] == pytest.approx(ic.mean() / ic.std(ddof=1))
    # t_stat 应等于 mean / (std/sqrt(n))
    expected_t = ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))
    assert s["ic_t_stat"] == pytest.approx(expected_t)


def test_ic_summary_empty_series():
    """空 IC Series 应返回全零 dict，不抛。"""
    s = metrics.ic_summary(pd.Series([], dtype=float))
    assert s["ic_mean"] == 0
    assert s["ic_std"] == 0
    assert s["ic_ir"] == 0
    assert s["ic_win_rate"] == 0
    assert s["ic_t_stat"] == 0


def test_value_histogram_basic():
    """bins=5 应得到 5 个 count，counts 加总 = 有效值数。"""
    f = pd.DataFrame(
        {"A": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
        index=pd.date_range("2024-01-01", periods=10, freq="B"),
    )
    hist = metrics.value_histogram(f, bins=5)
    assert len(hist["counts"]) == 5
    # numpy 约定 bins 是边界，len(edges) = n+1
    assert len(hist["bins"]) == 6
    assert sum(hist["counts"]) == 10


def test_value_histogram_all_nan():
    """全 NaN 不应抛，返回空 dict。"""
    f = pd.DataFrame(
        {"A": [np.nan] * 5},
        index=pd.date_range("2024-01-01", periods=5, freq="B"),
    )
    hist = metrics.value_histogram(f, bins=5)
    assert hist["bins"] == []
    assert hist["counts"] == []


def test_value_histogram_ignores_inf():
    """混入 ±inf 不应让 np.histogram 的 range 推断崩溃；应只计有限值。

    历史事故：composition_service 走到 evaluate_factor_panel → value_histogram
    时，若因子表里有 inf（pct_change(close=0) 是常见来源），会抛
    ``ValueError: autodetected range is not finite``。修复后过滤 np.isfinite。
    """
    f = pd.DataFrame(
        {"A": [1.0, 2.0, 3.0, 4.0, 5.0, np.inf, -np.inf, np.nan]},
        index=pd.date_range("2024-01-01", periods=8, freq="B"),
    )
    hist = metrics.value_histogram(f, bins=5)
    # 只剩 5 个有限值。
    assert sum(hist["counts"]) == 5
    assert all(np.isfinite(e) for e in hist["bins"])


def test_long_short_metrics_basic():
    """常数 5bp 日收益 → 年化 = 0.0005 * 252。"""
    idx = pd.date_range("2024-01-01", periods=252, freq="B")
    ls = pd.Series([0.0005] * len(idx), index=idx)
    m = metrics.long_short_metrics(ls)
    assert m["long_short_annret"] == pytest.approx(0.0005 * 252)
    # std=0 被兜底为 1e-12，sharpe 会非常大但是有限
    assert np.isfinite(m["long_short_sharpe"])


def test_long_short_metrics_empty():
    """空 Series 不崩，返回零 dict。"""
    m = metrics.long_short_metrics(pd.Series([], dtype=float))
    assert m["long_short_annret"] == 0
    assert m["long_short_sharpe"] == 0


def test_long_short_series_basic():
    """顶组 - 底组应得到 (top - bot)。"""
    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    g = pd.DataFrame(
        {0: [0.01, 0.02, 0.03], 1: [0.02, 0.03, 0.04], 2: [0.03, 0.05, 0.07]},
        index=idx,
    )
    ls = metrics.long_short_series(g)
    expected = pd.Series([0.02, 0.03, 0.04], index=idx)
    pd.testing.assert_series_equal(
        ls.rename(None), expected.rename(None), check_names=False
    )


def test_long_short_series_drops_nan_rows():
    """top 或 bot 是 NaN 的日期应被过滤。

    现实触发场景：rank 类因子值只有少量 bucket，qcut(duplicates='drop') 合并
    bin 后 group_returns.reindex(n_groups) 填 NaN，top/bot 其中一组不存在时
    top - bot = NaN。若不过滤：
    - 下游 (1+ls).cumprod() 从首个 NaN 起整条净值变 NaN；
    - long_short_metrics 的 .mean()/.std() 静默跳 NaN，掩盖有效样本数不足。
    所以 long_short_series 必须内部 dropna。
    """
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    # 第 1、3 天 top 或 bot 缺失 → NaN 应被过滤
    g = pd.DataFrame(
        {
            0: [0.01, 0.02, np.nan, 0.04, 0.05],
            1: [0.02, 0.03, 0.04, 0.05, 0.06],
            2: [np.nan, 0.04, 0.05, 0.06, 0.07],
        },
        index=idx,
    )
    ls = metrics.long_short_series(g)
    # 仅第 2/4/5 天有效（idx[1], idx[3], idx[4]）
    assert len(ls) == 3
    assert idx[0] not in ls.index  # top NaN
    assert idx[2] not in ls.index  # bot NaN
    # 其余 top-bot 正确
    assert ls.loc[idx[1]] == pytest.approx(0.04 - 0.02)
    assert ls.loc[idx[3]] == pytest.approx(0.06 - 0.04)


def test_long_short_metrics_returns_n_effective():
    """long_short_metrics 应额外返回 long_short_n_effective（= 输入长度）。

    前端据此展示"样本不足"告警，避免用户被极端日主导的 Sharpe 误导。
    """
    ls = pd.Series(
        [0.01, -0.02, 0.03], index=pd.date_range("2024-01-01", periods=3, freq="B")
    )
    m = metrics.long_short_metrics(ls)
    assert m["long_short_n_effective"] == 3
    # 空序列：n_effective = 0
    m_empty = metrics.long_short_metrics(pd.Series([], dtype=float))
    assert m_empty["long_short_n_effective"] == 0


def test_turnover_single_sided_upper_bound():
    """单边换手率 ∈ [0, 1]，组完全换仓时 = 1（不是旧双边公式的 2）。

    回归测试：历史上用对称差/组大小（双边），完全换仓时会显示 200%，
    UI 上被误读成"每天反手"。改成"新进占比"后上限是 100%。
    """
    # 10 只股票，前 5 天因子 A→J 升序、后 5 天 J→A 降序。
    # n_groups=2 时 top 组：前 5 天 = {F..J}，后 5 天 = {A..E} → 完全不重合。
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    cols = list("ABCDEFGHIJ")
    first_half = np.tile(np.arange(10), (5, 1))
    second_half = np.tile(np.arange(10)[::-1], (5, 1))
    f = pd.DataFrame(np.vstack([first_half, second_half]), index=idx, columns=cols)

    to = metrics.turnover_series(f, n_groups=2, which="top")
    # 所有值应在 [0, 1]
    assert (to >= 0).all() and (to <= 1).all(), f"turnover 越界：{to.tolist()}"
    # 切换日（第 6 天）应是 1.0（完全换仓）
    assert to.loc[idx[5]] == pytest.approx(1.0)


def test_params_hash_deterministic():
    """同一 dict 两次调用得相同 hash，key 顺序不影响。"""
    from backend.services.params_hash import params_hash

    h1 = params_hash({"a": 1, "b": 2})
    h2 = params_hash({"b": 2, "a": 1})
    assert h1 == h2
    assert len(h1) == 40
    assert all(c in "0123456789abcdef" for c in h1)


def test_params_hash_differs_on_different_params():
    """不同 params 应得到不同 hash。"""
    from backend.services.params_hash import params_hash

    assert params_hash({"a": 1}) != params_hash({"a": 2})


def test_cross_section_uniqueness_continuous_factor():
    """连续因子每行都近乎全不同 → uniqueness ≈ 1。"""
    f = _mk_panel(n_dates=20, n_syms=30, seed=1)
    u = metrics.cross_section_uniqueness(f)
    # 随机正态 30 只股票，实际值都不同 → 每日都是 30/30 = 1.0。
    assert u == pytest.approx(1.0)


def test_cross_section_uniqueness_rank_style_factor():
    """rank/argmax 类因子：每天横截面只有少量离散值 → uniqueness 远小于 1。"""
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(20)]
    # 模拟 argmax 型：每只股票取 0~4 的整数 position
    rng = np.random.default_rng(0)
    vals = rng.integers(0, 5, size=(10, 20)).astype(float)
    f = pd.DataFrame(vals, index=idx, columns=cols)
    u = metrics.cross_section_uniqueness(f)
    # 20 只股票只能落在 5 个 bucket 里 → nunique/n ≤ 5/20 = 0.25。
    assert u <= 0.25


def test_cross_section_uniqueness_empty():
    """空表 → 0.0，不抛。"""
    assert metrics.cross_section_uniqueness(pd.DataFrame()) == 0.0


def test_qcut_full_rate_high_for_continuous_factor():
    """连续因子 + n_groups=5：每日都能切满 5 组 → full_rate = 1.0。"""
    f = _mk_panel(n_dates=20, n_syms=50, seed=2)
    r = metrics.qcut_full_rate(f, n_groups=5)
    assert r == pytest.approx(1.0)


def test_qcut_full_rate_low_for_heavily_tied_factor():
    """argmax 类值域很小 + 高度 tied → qcut 大量合并边界，full_rate 远 < 1。

    这是用户之前踩的坑的直接量化指标：如果这个值很低，说明分组 / 多空
    必然有大量日期只能出 <N 组。
    """
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(100)]
    rng = np.random.default_rng(3)
    # 100 只股票只分布在 0~2 三个值上
    vals = rng.integers(0, 3, size=(10, 100)).astype(float)
    f = pd.DataFrame(vals, index=idx, columns=cols)
    r = metrics.qcut_full_rate(f, n_groups=5)
    # 请求 5 组，实际最多只能切出 3 组 → ≤ 0.6
    assert r <= 0.6


def test_qcut_full_rate_zero_for_all_equal_rows():
    """所有行全是同一个值 → qcut 退化，full_rate = 0。"""
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    cols = [f"S{i}" for i in range(10)]
    f = pd.DataFrame(0.5, index=idx, columns=cols)
    r = metrics.qcut_full_rate(f, n_groups=5)
    assert r == 0.0


def test_ic_annual_stability_sign_consistent():
    """所有年份 IC 同号 → sign_consistent=True，cv 有限。"""
    # 2023~2025 三年，每年 IC mean 分别为 +0.03 / +0.02 / +0.04（都为正）
    dates = (
        list(pd.date_range("2023-01-02", periods=10, freq="B"))
        + list(pd.date_range("2024-01-02", periods=10, freq="B"))
        + list(pd.date_range("2025-01-02", periods=10, freq="B"))
    )
    vals = [0.03] * 10 + [0.02] * 10 + [0.04] * 10
    ic = pd.Series(vals, index=pd.DatetimeIndex(dates))
    out = metrics.ic_annual_stability(ic)
    assert out["years"] == [2023, 2024, 2025]
    assert out["sign_consistent"] is True
    assert out["ic_mean_by_year"] == [0.03, 0.02, 0.04]
    assert out["cv"] > 0  # 三年不完全相同，cv 应 > 0


def test_ic_annual_stability_sign_flipped():
    """IC 在不同年份反号（因子失效）→ sign_consistent=False。"""
    dates = (
        list(pd.date_range("2023-01-02", periods=10, freq="B"))
        + list(pd.date_range("2024-01-02", periods=10, freq="B"))
    )
    vals = [0.05] * 10 + [-0.04] * 10  # 2023 正、2024 负
    ic = pd.Series(vals, index=pd.DatetimeIndex(dates))
    out = metrics.ic_annual_stability(ic)
    assert out["sign_consistent"] is False


def test_ic_annual_stability_empty():
    """空输入不抛、返回合法 dict。"""
    out = metrics.ic_annual_stability(pd.Series([], dtype=float))
    assert out["years"] == []
    assert out["ic_mean_by_year"] == []
    assert out["sign_consistent"] is True
    assert out["cv"] == 0.0


def test_eval_service_imports():
    """eval_service 模块可 import，run_eval 可调用签名存在。"""
    from backend.services.eval_service import run_eval

    # run_eval 是函数，不 crash 即可
    assert callable(run_eval)


def test_set_status_writes_feedback_text(monkeypatch):
    """_set_status feedback 参数应该拼到 SQL 的 feedback_text=%s。"""
    from unittest.mock import MagicMock

    from backend.services import eval_service

    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda s, *a: None
    executed: list[tuple] = []

    def _exec(sql, vals=()):
        executed.append((sql, vals))

    cursor.execute.side_effect = _exec
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    cm = MagicMock()
    cm.__enter__ = lambda s: conn
    cm.__exit__ = lambda s, *a: None
    monkeypatch.setattr(eval_service, "mysql_conn", MagicMock(return_value=cm))

    eval_service._set_status(
        "RUN1",
        status="success",
        feedback="IC=0.001 偏弱；与因子 X 相关 0.99 重复，建议改写或合并",
    )

    assert len(executed) == 1
    sql, vals = executed[0]
    assert "feedback_text=%s" in sql
    # vals 顺序：status / feedback / run_id
    assert vals[1].startswith("IC=0.001")
    assert vals[-1] == "RUN1"


def test_set_status_omits_feedback_when_not_passed(monkeypatch):
    """不传 feedback → SQL 里不应包含 feedback_text 子句（避免无意义写空）。"""
    from unittest.mock import MagicMock

    from backend.services import eval_service

    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda s, *a: None
    executed: list[tuple] = []
    cursor.execute.side_effect = lambda sql, vals=(): executed.append((sql, vals))
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    cm = MagicMock()
    cm.__enter__ = lambda s: conn
    cm.__exit__ = lambda s, *a: None
    monkeypatch.setattr(eval_service, "mysql_conn", MagicMock(return_value=cm))

    eval_service._set_status("RUN2", progress=50)

    sql, _ = executed[0]
    assert "feedback_text" not in sql


def test_build_health_green_for_healthy_factor():
    """连续高斯因子 + 稳定正向 IC + 合理换手 → overall green。"""
    from backend.services.eval_service import _build_health

    rng = np.random.default_rng(0)
    idx = pd.date_range("2022-01-03", periods=500, freq="B")
    cols = [f"S{i:02d}" for i in range(30)]
    factor = pd.DataFrame(
        rng.standard_normal((len(idx), len(cols))), index=idx, columns=cols
    )
    # IC 序列：多年均为正且量级稳定
    ic_series = pd.Series(
        rng.normal(0.05, 0.02, size=len(idx)), index=idx
    )
    to_series = pd.Series(0.15, index=idx)  # 15% 单边换手，落在绿区
    out = _build_health(
        factor_panel=factor,
        ic_series=ic_series,
        turnover_series=to_series,
        long_short_n_effective=len(idx),
        n_groups=5,
    )
    assert out["overall"] == "green"
    assert len(out["items"]) == 5
    keys = {it["key"] for it in out["items"]}
    assert keys == {
        "cross_section_uniqueness",
        "qcut_full_rate",
        "long_short_effective_ratio",
        "ic_annual_stability",
        "turnover_level",
    }


def test_build_health_red_for_rank_argmax_style():
    """值域只有 {0,1} 的离散因子 → 唯一值率 / qcut 满组率 / 多空样本比全红。"""
    from backend.services.eval_service import _build_health

    idx = pd.date_range("2024-01-02", periods=30, freq="B")
    cols = [f"S{i:02d}" for i in range(50)]
    # 每行只有 1 列为 1，其余为 0：典型 argmax 因子
    # 50 只股票里每天只有 2 个独特值 → ratio=2/50=0.04 < 0.1（红区）
    factor = pd.DataFrame(0.0, index=idx, columns=cols)
    for i, date in enumerate(idx):
        factor.iloc[i, i % len(cols)] = 1.0

    ic_series = pd.Series(0.01, index=idx)
    to_series = pd.Series(0.0, index=idx)  # 换手为 0 也异常
    out = _build_health(
        factor_panel=factor,
        ic_series=ic_series,
        turnover_series=to_series,
        long_short_n_effective=0,
        n_groups=5,
    )
    assert out["overall"] == "red"
    by_key = {it["key"]: it for it in out["items"]}
    assert by_key["cross_section_uniqueness"]["level"] == "red"
    assert by_key["qcut_full_rate"]["level"] == "red"
    assert by_key["long_short_effective_ratio"]["level"] == "red"
    assert by_key["turnover_level"]["level"] == "red"


def test_build_health_yellow_when_ic_cv_large():
    """IC 年度方向一致但量级波动大 → 该项 yellow，其它项若绿则 overall yellow。"""
    from backend.services.eval_service import _build_health

    rng = np.random.default_rng(1)
    idx = pd.date_range("2020-01-02", periods=1000, freq="B")
    cols = [f"S{i:02d}" for i in range(30)]
    factor = pd.DataFrame(
        rng.standard_normal((len(idx), len(cols))), index=idx, columns=cols
    )
    # 人工做成年度 IC 方向一致但量级差异巨大的序列：
    # 3 年 ~0.01 的弱 IC + 1 年 ~0.5 的强 IC → CV ≈ 1.8，方向都是正 → yellow
    ic_values = []
    for d in idx:
        base = 0.5 if d.year == 2023 else 0.01
        ic_values.append(base + rng.normal(0, 0.002))
    ic_series = pd.Series(ic_values, index=idx)
    to_series = pd.Series(0.2, index=idx)
    out = _build_health(
        factor_panel=factor,
        ic_series=ic_series,
        turnover_series=to_series,
        long_short_n_effective=len(idx),
        n_groups=5,
    )
    by_key = {it["key"]: it for it in out["items"]}
    assert by_key["ic_annual_stability"]["level"] == "yellow"
    assert out["overall"] in {"yellow", "red"}


def test_build_health_red_when_ic_sign_flips():
    """IC 年度均值出现符号翻转 → IC 项 red，overall red。"""
    from backend.services.eval_service import _build_health

    rng = np.random.default_rng(2)
    idx = pd.date_range("2020-01-02", periods=1000, freq="B")
    cols = [f"S{i:02d}" for i in range(30)]
    factor = pd.DataFrame(
        rng.standard_normal((len(idx), len(cols))), index=idx, columns=cols
    )
    ic_values = []
    for d in idx:
        base = 0.05 if d.year < 2022 else -0.05
        ic_values.append(base + rng.normal(0, 0.005))
    ic_series = pd.Series(ic_values, index=idx)
    to_series = pd.Series(0.2, index=idx)
    out = _build_health(
        factor_panel=factor,
        ic_series=ic_series,
        turnover_series=to_series,
        long_short_n_effective=len(idx),
        n_groups=5,
    )
    by_key = {it["key"]: it for it in out["items"]}
    assert by_key["ic_annual_stability"]["level"] == "red"
    assert out["overall"] == "red"


# --------------- evaluate_factor_panel 端到端 sanity check ---------------


def _mk_close(n_dates: int = 252, n_syms: int = 30, seed: int = 42) -> pd.DataFrame:
    """构造仿真 close 宽表：GBM 随机游走，跳过周末以模拟交易日。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n_dates, freq="B")
    cols = [f"S{i:02d}" for i in range(n_syms)]
    log_ret = rng.normal(0.0003, 0.02, (n_dates, n_syms))
    prices = 10.0 * np.exp(np.cumsum(log_ret, axis=0))
    return pd.DataFrame(prices, index=idx, columns=cols)


class TestEvaluateFactorPanelSanity:
    """用"完美因子"和"纯噪音因子"做端到端 sanity check。

    完美因子 = 未来 1 日收益本身，预期 IC ≈ 1、分组收益严格单调。
    纯噪音因子 = 与收益独立的随机数，预期 IC ≈ 0、多空 Sharpe ≈ 0。
    任何指标大幅偏离上述预期 → 数据对齐 / shift 方向 / 复权 / 分组逻辑可能有 bug。
    """

    @pytest.fixture()
    def close(self) -> pd.DataFrame:
        return _mk_close()

    @pytest.fixture()
    def perfect_factor(self, close: pd.DataFrame) -> pd.DataFrame:
        return close.shift(-1) / close - 1

    @pytest.fixture()
    def noise_factor(self, close: pd.DataFrame) -> pd.DataFrame:
        rng = np.random.default_rng(99)
        return pd.DataFrame(
            rng.standard_normal(close.shape), index=close.index, columns=close.columns,
        )

    def test_perfect_factor_ic_near_one(self, perfect_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        _, structured = evaluate_factor_panel(
            perfect_factor, close, forward_periods=[1], n_groups=5,
        )
        assert structured["ic_mean"] > 0.99, f"完美因子 IC 应 ≈1，实际 {structured['ic_mean']}"
        assert structured["rank_ic_mean"] > 0.99
        assert structured["ic_win_rate"] > 0.99

    def test_perfect_factor_group_returns_monotonic(self, perfect_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        payload, _ = evaluate_factor_panel(
            perfect_factor, close, forward_periods=[1], n_groups=5,
        )
        g = payload["group_returns"]
        group_keys = [k for k in sorted(g.keys()) if k.startswith("g")]
        for i in range(len(group_keys) - 1):
            lo = np.nanmean(g[group_keys[i]])
            hi = np.nanmean(g[group_keys[i + 1]])
            assert lo < hi, f"{group_keys[i]} 均值 {lo} 应 < {group_keys[i+1]} 均值 {hi}"

    def test_perfect_factor_long_short_sharpe_positive(self, perfect_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        _, structured = evaluate_factor_panel(
            perfect_factor, close, forward_periods=[1], n_groups=5,
        )
        assert structured["long_short_sharpe"] > 3.0, (
            f"完美因子多空 Sharpe 应极高，实际 {structured['long_short_sharpe']}"
        )

    def test_payload_contains_alphalens_extras(self, noise_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        payload, _ = evaluate_factor_panel(
            noise_factor, close, forward_periods=[1], n_groups=5,
        )
        assert "alphalens" in payload
        al = payload["alphalens"]
        assert "rank_autocorrelation" in al
        assert "group_cumulative_returns" in al
        assert "alpha_beta" in al

    def test_noise_factor_ic_near_zero(self, noise_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        _, structured = evaluate_factor_panel(
            noise_factor, close, forward_periods=[1], n_groups=5,
        )
        assert abs(structured["ic_mean"]) < 0.1, (
            f"噪音因子 IC 应 ≈0，实际 {structured['ic_mean']}"
        )
        assert abs(structured["rank_ic_mean"]) < 0.1

    def test_noise_factor_long_short_sharpe_near_zero(self, noise_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        _, structured = evaluate_factor_panel(
            noise_factor, close, forward_periods=[1], n_groups=5,
        )
        assert abs(structured["long_short_sharpe"]) < 1.5, (
            f"噪音因子多空 Sharpe 应 ≈0，实际 {structured['long_short_sharpe']}"
        )


class TestSingleDayCrossSectionManualVerify:
    """挑一天手算 IC / Rank IC / 分组均值，和 metrics.py 逐项对比。

    独立于 metrics.py 的实现——用 numpy/scipy 直接算，验证 metrics 函数
    没有对齐错位、NaN 过滤遗漏或公式错误。
    """

    @pytest.fixture()
    def data(self):
        close = _mk_close(n_dates=60, n_syms=20, seed=7)
        rng = np.random.default_rng(8)
        factor = pd.DataFrame(
            rng.standard_normal(close.shape), index=close.index, columns=close.columns,
        )
        fwd_ret = close.shift(-1) / close - 1
        return factor, fwd_ret, close

    def test_pearson_ic_matches_manual(self, data):
        factor, fwd_ret, _ = data
        ic_series = metrics.cross_sectional_ic(factor, fwd_ret)

        target_date = ic_series.index[10]
        f_row = factor.loc[target_date].values
        r_row = fwd_ret.loc[target_date].values
        mask = np.isfinite(f_row) & np.isfinite(r_row)
        manual_ic = float(np.corrcoef(f_row[mask], r_row[mask])[0, 1])

        assert ic_series.loc[target_date] == pytest.approx(manual_ic, abs=1e-10)

    def test_rank_ic_matches_manual(self, data):
        from scipy.stats import spearmanr

        factor, fwd_ret, _ = data
        rank_ic_series = metrics.cross_sectional_rank_ic(factor, fwd_ret)

        target_date = rank_ic_series.index[10]
        f_row = factor.loc[target_date].values
        r_row = fwd_ret.loc[target_date].values
        mask = np.isfinite(f_row) & np.isfinite(r_row)
        manual_rho, _ = spearmanr(f_row[mask], r_row[mask])

        assert rank_ic_series.loc[target_date] == pytest.approx(float(manual_rho), abs=1e-10)

    def test_group_returns_matches_manual(self, data):
        factor, fwd_ret, _ = data
        n_groups = 5
        g_rets = metrics.group_returns(factor, fwd_ret, n_groups=n_groups)

        target_date = g_rets.index[10]
        f_row = factor.loc[target_date]
        r_row = fwd_ret.loc[target_date]
        mask = f_row.notna() & r_row.notna()
        f_valid, r_valid = f_row[mask], r_row[mask]
        labels = pd.qcut(f_valid, n_groups, labels=False, duplicates="drop")
        manual_means = pd.DataFrame({"q": labels.values, "r": r_valid.values}).groupby("q")["r"].mean()

        for grp in manual_means.index:
            assert g_rets.loc[target_date, grp] == pytest.approx(manual_means[grp], abs=1e-12)

    def test_ic_summary_matches_manual(self, data):
        factor, fwd_ret, _ = data
        ic_series = metrics.cross_sectional_ic(factor, fwd_ret)
        summary = metrics.ic_summary(ic_series)

        n = len(ic_series)
        manual_mean = float(ic_series.mean())
        manual_std = float(ic_series.std(ddof=1))
        manual_ir = manual_mean / manual_std
        manual_wr = float((ic_series > 0).mean())
        manual_t = manual_mean / (manual_std / np.sqrt(n))

        assert summary["ic_mean"] == pytest.approx(manual_mean, abs=1e-12)
        assert summary["ic_std"] == pytest.approx(manual_std, abs=1e-12)
        assert summary["ic_ir"] == pytest.approx(manual_ir, abs=1e-12)
        assert summary["ic_win_rate"] == pytest.approx(manual_wr, abs=1e-12)
        assert summary["ic_t_stat"] == pytest.approx(manual_t, abs=1e-10)


# --------------- 方案 3：Alphalens 交叉验证 ---------------

alphalens = pytest.importorskip("alphalens")


class TestAlphalensCrossValidation:
    """用 Alphalens 作为独立参考实现，交叉验证 metrics.py 的 Rank IC 和分组收益。

    差异来源（不影响结论）：
    - Alphalens 默认 IC 是 Spearman → 对应我们的 cross_sectional_rank_ic
    - Alphalens 分组编号 1-based → 我们 0-based
    - Alphalens 的数据清洗会丢弃尾部 1 天（无前瞻收益），两侧取交集对比
    """

    @pytest.fixture()
    def shared_data(self):
        import warnings

        rng = np.random.default_rng(42)
        idx = pd.date_range("2023-01-02", periods=120, freq="B")
        cols = [f"S{i:02d}" for i in range(20)]
        log_ret = rng.normal(0.0003, 0.02, (120, 20))
        close = pd.DataFrame(
            10.0 * np.exp(np.cumsum(log_ret, axis=0)), index=idx, columns=cols,
        )
        factor_wide = pd.DataFrame(
            rng.standard_normal(close.shape), index=idx, columns=cols,
        )

        # --- Alphalens 侧 ---
        factor_long = factor_wide.stack()
        factor_long.index.names = ["date", "asset"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            al_data = alphalens.utils.get_clean_factor_and_forward_returns(
                factor_long, close, periods=(1,), quantiles=5, max_loss=1.0,
            )
        al_ic = alphalens.performance.factor_information_coefficient(al_data)
        al_mean_ret, _ = alphalens.performance.mean_return_by_quantile(al_data)

        # --- 我们的 metrics 侧 ---
        fwd_ret_1d = close.shift(-1) / close - 1
        our_rank_ic = metrics.cross_sectional_rank_ic(factor_wide, fwd_ret_1d)
        our_g_rets = metrics.group_returns(factor_wide, fwd_ret_1d, n_groups=5)

        return {
            "al_ic": al_ic, "al_mean_ret": al_mean_ret,
            "our_rank_ic": our_rank_ic, "our_g_rets": our_g_rets,
        }

    def test_rank_ic_series_close_to_alphalens(self, shared_data):
        al_ic = shared_data["al_ic"]["1D"]
        our_ic = shared_data["our_rank_ic"]

        common = al_ic.index.intersection(our_ic.index)
        assert len(common) > 100, f"公共日期过少: {len(common)}"

        diff = (al_ic.loc[common] - our_ic.loc[common]).abs()
        assert diff.max() < 0.05, f"单日最大差异 {diff.max():.4f} 超过容差 0.05"
        assert diff.mean() < 0.01, f"日均差异 {diff.mean():.6f} 超过容差 0.01"

    def test_rank_ic_mean_close_to_alphalens(self, shared_data):
        al_mean = shared_data["al_ic"]["1D"].mean()
        our_mean = shared_data["our_rank_ic"].mean()
        assert abs(al_mean - our_mean) < 0.02, (
            f"IC 均值差异过大: alphalens={al_mean:.4f} vs ours={our_mean:.4f}"
        )

    def test_group_return_rank_order_matches_alphalens(self, shared_data):
        al_ret = shared_data["al_mean_ret"]["1D"]
        our_ret = shared_data["our_g_rets"].mean()

        # Alphalens 1-based (1..5) → 我们 0-based (0..4)，按 rank 比较
        al_order = al_ret.sort_values().index.tolist()
        our_order = our_ret.sort_values().index.tolist()
        # 排名前后位置最多偏移 1 位（qcut 边界差异）
        mismatches = sum(1 for a, o in zip(al_order, our_order) if abs(a - 1 - o) > 1)
        assert mismatches <= 1, (
            f"分组收益排名差异过大: alphalens={al_order} vs ours={our_order}"
        )

    def test_long_short_spread_same_sign(self, shared_data):
        al_ret = shared_data["al_mean_ret"]["1D"]
        al_spread = al_ret.iloc[-1] - al_ret.iloc[0]

        our_ret = shared_data["our_g_rets"].mean()
        our_spread = our_ret.iloc[-1] - our_ret.iloc[0]

        # 多空方向（正/负/零）应一致
        assert np.sign(al_spread) == np.sign(our_spread) or abs(al_spread) < 1e-4, (
            f"多空方向不一致: alphalens={al_spread:.6f} vs ours={our_spread:.6f}"
        )


# --------------- Alphalens 增强视角 ---------------


class TestAlphalensExtras:
    """_build_alphalens_extras 端到端测试。"""

    @pytest.fixture()
    def close(self):
        return _mk_close(n_dates=120, n_syms=20, seed=42)

    @pytest.fixture()
    def factor(self, close):
        rng = np.random.default_rng(99)
        return pd.DataFrame(
            rng.standard_normal(close.shape), index=close.index, columns=close.columns,
        )

    def test_returns_dict_with_three_keys(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras

        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        assert "rank_autocorrelation" in result
        assert "group_cumulative_returns" in result
        assert "alpha_beta" in result

    def test_rank_autocorrelation_format(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras

        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        ra = result["rank_autocorrelation"]
        assert "dates" in ra and "values" in ra
        assert len(ra["dates"]) == len(ra["values"])
        assert len(ra["dates"]) > 50

    def test_group_cumulative_returns_format(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras

        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        gcr = result["group_cumulative_returns"]
        assert "dates" in gcr
        group_keys = [k for k in gcr if k.startswith("g")]
        assert len(group_keys) == 5
        assert all(len(gcr[k]) == len(gcr["dates"]) for k in group_keys)

    def test_group_cumulative_returns_is_demeaned(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras

        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        gcr = result["group_cumulative_returns"]
        group_keys = sorted(k for k in gcr if k.startswith("g"))
        final_sum = sum(gcr[k][-1] for k in group_keys if gcr[k][-1] is not None)
        assert abs(final_sum - 5.0) < 1.0, f"去均值后各组终值之和应 ≈5，实际 {final_sum}"

    def test_alpha_beta_format(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras

        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        ab = result["alpha_beta"]
        assert "alpha" in ab and "beta" in ab and "annualized_alpha" in ab
        assert isinstance(ab["alpha"], float)
        assert isinstance(ab["beta"], float)

    def test_perfect_factor_has_positive_alpha(self, close):
        from backend.services.eval_service import _build_alphalens_extras

        perfect = close.shift(-1) / close - 1
        result = _build_alphalens_extras(perfect, close, fwd_periods=[1], n_groups=5)
        assert result["alpha_beta"]["annualized_alpha"] > 0.5

    def test_graceful_on_empty_factor(self):
        from backend.services.eval_service import _build_alphalens_extras

        result = _build_alphalens_extras(
            pd.DataFrame(), pd.DataFrame(), fwd_periods=[1], n_groups=5,
        )
        assert result == {}
