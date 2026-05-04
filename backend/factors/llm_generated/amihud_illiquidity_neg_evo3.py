"""增强版 Amihud 非流动性因子：综合 ILLIQ、波动率、换手率倒数的截面组合。"""
from __future__ import annotations
import numpy as np
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class EnhancedAmihudIlliq(BaseFactor):
    factor_id = 'amihud_illiquidity_neg_evo3'
    display_name = '增强 Amihud 非流动性'
    category = 'volume'
    description = '结合 Amihud ILLIQ、波动率与换手率倒数，截面标准化后合成。因子值越大，预期未来收益越正（非流动性溢价增强版）。'
    hypothesis = '非流动性溢价假设——Amihud (2002) 指出流动性差的股票需更高预期收益补偿，Liu-Stambaugh-Yuan (2019) 强调非流动性因子在 A 股有效。增强版纳入波动率（不确定性增大补偿）和换手率倒数（流动性差的代理），三者正向叠加提升截面预测力。失效于流动性危机（如2015年股灾）或极端的市场风格切换期。'
    default_params = {'window': 20}
    params_schema = {'window': {'type': 'int', 'default': 20, 'min': 5, 'max': 120, 'desc': '计算平均的窗口（交易日）'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get('window', self.default_params['window']))
        return int(window * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get('window', self.default_params['window']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        end_date = ctx.end_date.date()
        close = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='close', adjust='qfq')
        amount_k = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='amount_k', adjust='none')
        volume = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='volume', adjust='none')
        if close.empty:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        amount = amount_k * 1000.0
        with np.errstate(divide='ignore', invalid='ignore'):
            illiq_daily = np.abs(ret) / amount
        illiq_daily = illiq_daily.replace([np.inf, -np.inf], np.nan)
        illiq_avg = illiq_daily.rolling(window, min_periods=max(5, window // 2)).mean()
        volatility = ret.rolling(window, min_periods=max(5, window // 2)).std()
        volume_avg = volume.rolling(window, min_periods=max(5, window // 2)).mean()
        turnover_inv = 1.0 / volume_avg.replace(0, np.nan)

        def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
            mu = df.mean(axis=1, skipna=True)
            sigma = df.std(axis=1, skipna=True)
            sigma = sigma.where(sigma > 0, np.nan)
            return df.sub(mu, axis=0).div(sigma, axis=0)
        z1 = _cs_zscore(illiq_avg)
        z2 = _cs_zscore(volatility)
        z3 = _cs_zscore(turnover_inv)
        factor = (z1 + z2 + z3) / 3.0
        return factor.loc[ctx.start_date:]
