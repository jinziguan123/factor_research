"""超跌急杀·底部金叉反转选股信号（用户自研策略移植）。

原脚本是一个**单票择时扫描器**：逐只股票找"深度超跌后于周线多头趋势中出现
底部金叉"的买点。本因子忠实移植其选股逻辑，五重条件**同时满足**当日取 1，
否则 0——是一个**稀疏的事件型 AND 门选股信号**，而非连续截面打分。

五重条件（信号日 t 全部成立才置 1）：

1. **超卖底部金叉**：v1 = 34 日随机指标 ``(close-LLV)/(HHV-LLV)*100``；
   经三重 ``SMA(_,3,1)`` 平滑（通达信 SMA(s,n,m) 的递推 ``r=(m·s+(n-m)·r₋₁)/n``
   在 m=1,n=3 时 ≡ ``ewm(alpha=1/3, adjust=False)``，与本仓库 KDJ 口径一致）得
   v2/v3/v4；要求 v3 上穿 v4（金叉）且 v3<20（发生在超卖区）。
2. **前期急杀低点邻近**：信号前 30 日窗口内的最低价必须出现在近 15 日内、且该低点
   自身经历过一次"20 日跌幅 > 20%"的急杀。刻画"刚砸出一个恐慌坑"。
3. **年振幅约束**：近一年 ``(HHV-LLV)/LLV*100 < 120%``——剔除已被大幅炒高的标的。
4. **周线多头**：周线（W-FRI 重采样收盘）``EMA21 > EMA55``，确认大级别趋势向上。
5. **信号去重**：距上一次触发至少 10 个交易日，避免同一波行情重复计数。

方向：反转（超跌恐慌错杀 + 大趋势多头 → 预期未来数日反弹）。因子值越大越看多。

—— 评估口径提示 ——
这是稀疏二值信号：单日横截面上绝大多数股票为 0、少数为 1，``qcut`` 分位/多空会
高度并列退化。它更适合当作**多头选股信号**评估（命中率 hit_rate / 事件后收益），
而非分位多空。若需要连续截面打分版本，可把"硬门"改成各分量截面 rank 之积
（参考 ``uptrend_sideways_flash_drop``），语义会从"精确择时"变成"软打分"。

—— 无未来函数 ——
周线多头用 ``reindex(daily, method='ffill')`` 对齐：周中各日取上一个**已完成**周的
EMA 判定（比原脚本"含当周未完成 bar"最多保守 1 周），不引入前视。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import load_hlc


class OversoldCrashBottomReversal(BaseFactor):
    factor_id = "oversold_crash_bottom_reversal"
    display_name = "超跌急杀底部金叉反转"
    category = "custom"
    description = (
        "34 日随机指标三重 SMA 平滑后在超卖区(<20)金叉，且信号前 15 日内有过 "
        "20 日跌幅>20% 的急杀低点、近一年振幅<120%、周线 EMA21>EMA55 多头——"
        "五重条件同时满足当日取 1 否则 0 的稀疏事件型选股信号，值越大越看多。"
    )
    hypothesis = (
        "深度超跌急杀后在周线多头趋势中出现底部金叉，是恐慌错杀后的反转买点；"
        "年振幅与周线趋势过滤剔除长期弱势与已炒高标的以提高胜率。"
    )
    params_schema = {
        "osc_window": {"type": "int", "default": 34, "min": 10, "max": 120, "desc": "随机指标高低区间窗口（交易日）"},
        "oversold_th": {"type": "float", "default": 20.0, "min": 5.0, "max": 50.0, "desc": "超卖阈值（v3 低于该值才算超卖金叉）"},
        "crash_drop_pct": {"type": "float", "default": -20.0, "min": -50.0, "max": -5.0, "desc": "急杀低点的 20 日跌幅阈值(%)，需更负"},
        "crash_max_gap": {"type": "int", "default": 15, "min": 1, "max": 40, "desc": "信号距急杀低点的最大间隔（交易日）"},
        "year_amp_max": {"type": "float", "default": 120.0, "min": 50.0, "max": 300.0, "desc": "近一年最大振幅上限(%)，超过视为已炒高剔除"},
    }
    default_params = {
        "osc_window": 34,
        "oversold_th": 20.0,
        "crash_drop_pct": -20.0,
        "crash_max_gap": 15,
        "year_amp_max": 120.0,
    }
    supported_freqs = ("1d",)

    # —— 策略"签名"常数：源自原脚本，固定不参数化（类比 tiandi 的 5/3/3 平滑常数）——
    _SMOOTH_ALPHA = 1.0 / 3.0    # 三重 SMA(_,3,1) → ewm(alpha=1/3, adjust=False)
    _CRASH_BACK = 30             # 找急杀低点的回看窗口（交易日）
    _CRASH_DROP_WIN = 20         # 急杀跌幅的度量窗口（交易日）
    _YEAR_WINDOW = 252           # 年振幅窗口（交易日）
    _WEEKLY_FAST = 21            # 周线快线 EMA
    _WEEKLY_SLOW = 55            # 周线慢线 EMA
    _MIN_SIGNAL_GAP = 10         # 相邻信号最小间隔（交易日）

    def required_warmup(self, params: dict) -> int:
        osc_window = int(params.get("osc_window", self.default_params["osc_window"]))
        # 绑定约束是周线 E55（约 55 周）与年振幅窗口；再加急杀回看+跌幅窗口的余量。
        lookback_td = (
            max(self._YEAR_WINDOW, self._WEEKLY_SLOW * 5, osc_window)
            + self._CRASH_BACK
            + self._CRASH_DROP_WIN
        )
        return self._calc_warmup(lookback_td)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        osc_window = int(params.get("osc_window", self.default_params["osc_window"]))
        oversold_th = float(params.get("oversold_th", self.default_params["oversold_th"]))
        crash_drop_pct = float(params.get("crash_drop_pct", self.default_params["crash_drop_pct"]))
        crash_max_gap = int(params.get("crash_max_gap", self.default_params["crash_max_gap"]))
        year_amp_max = float(params.get("year_amp_max", self.default_params["year_amp_max"]))

        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = (p.astype(float) for p in panels)

        # —— 条件 1：超卖底部金叉（跨列向量化）——
        llv = low.rolling(osc_window, min_periods=osc_window).min()
        hhv = high.rolling(osc_window, min_periods=osc_window).max()
        rng = (hhv - llv).where(lambda x: x > 0)      # 区间=0 → NaN，避免 inf
        v1 = (close - llv) / rng * 100
        v2 = v1.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        v3 = v2.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        v4 = v3.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        cross_up = (v3.shift(1) <= v4.shift(1)) & (v3 > v4)
        q = (cross_up & (v3 < oversold_th)).fillna(False)

        # —— 条件 3：年振幅约束（跨列向量化）——
        y_high = high.rolling(self._YEAR_WINDOW, min_periods=100).max()
        y_low = low.rolling(self._YEAR_WINDOW, min_periods=100).min()
        y_amp = (y_high - y_low) / y_low.where(lambda x: x > 0) * 100
        year_ok = (y_amp < year_amp_max).fillna(False)

        # —— 条件 4：周线 EMA21>EMA55 多头（重采样→日频 ffill，无前视）——
        wk_close = close.resample("W-FRI").last()
        e_fast = wk_close.ewm(span=self._WEEKLY_FAST, adjust=False).mean()
        e_slow = wk_close.ewm(span=self._WEEKLY_SLOW, adjust=False).mean()
        weekly_up = (e_fast > e_slow).reindex(close.index, method="ffill")
        # reindex 在最早几周引入真 NaN 使布尔帧变 object；用 where 填 False 再定型，
        # 避开 fillna 的 object→bool 隐式降型 FutureWarning。
        weekly_up = weekly_up.where(weekly_up.notna(), False).astype(bool)

        # 先把三个可向量化的条件 AND 起来，得到候选日（稀疏），再对每列做急杀低点判定。
        cand = (q & year_ok & weekly_up).to_numpy(dtype=bool)
        low_v = low.to_numpy(dtype=float)
        close_v = close.to_numpy(dtype=float)
        n_rows, n_cols = close_v.shape
        signal = np.zeros((n_rows, n_cols), dtype=bool)

        back = self._CRASH_BACK
        drop_win = self._CRASH_DROP_WIN
        gap = self._MIN_SIGNAL_GAP

        for c in range(n_cols):
            last_fire = -(10 ** 9)
            for t in np.nonzero(cand[:, c])[0]:
                if t < back:                       # 历史不足以取回看窗口
                    continue
                if t - last_fire < gap:            # 条件 5：信号去重
                    continue
                window = low_v[t - back:t + 1, c]  # 近 back+1 日最低价
                if np.all(np.isnan(window)):
                    continue
                m = (t - back) + int(np.nanargmin(window))   # 急杀低点的全局位置
                if m < drop_win or m >= t:         # 需 m-drop_win 有效且低点在信号之前
                    continue
                if t - m > crash_max_gap:          # 条件 2a：低点须在近 crash_max_gap 日内
                    continue
                c_m, c_m0 = close_v[m, c], close_v[m - drop_win, c]
                if not (np.isfinite(c_m) and np.isfinite(c_m0) and c_m0 != 0):
                    continue
                drp = (c_m / c_m0 - 1.0) * 100.0
                if drp > crash_drop_pct:           # 条件 2b：低点前 20 日跌幅须更深
                    continue
                signal[t, c] = True
                last_fire = t

        # 有价格数据处 0/1，停牌/未上市（close 为 NaN）处 NaN，避免被当作"未选中的 0"参与排名。
        factor = pd.DataFrame(
            np.where(np.isnan(close_v), np.nan, signal.astype(float)),
            index=close.index,
            columns=close.columns,
        )
        return factor.loc[ctx.start_date:]
