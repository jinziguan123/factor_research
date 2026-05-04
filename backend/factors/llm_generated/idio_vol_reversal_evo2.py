"""EWMA特质波动率反转因子——改进自idio_vol_reversal。"""
from __future__ import annotations
import numpy as np
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class IdioVolEwmaReversal(BaseFactor):
    factor_id = 'idio_vol_reversal_evo2'
    display_name = 'EWMA特质波动率反转'
    category = 'volatility'
    description = '计算个股特质波动率（EWMA加权），取负。因子值越大，预期未来收益越正。'
    hypothesis = '延续IVOL异象——高特质波动率代表定价过高或套利限制，未来收益更低。采用EWMA赋予近期波动更高权重，提升信号时效性和IC稳定性，期望改善IC_IR。机制：噪音交易者过度关注高波动股票，定价偏差在短期持续。在流动性危机、恐慌性抛售时失效。'
    default_params = {'window': 42, 'halflife': 10}
    params_schema = {'window': {'type': 'int', 'default': 42, 'min': 5, 'max': 252, 'desc': '波动率计算回看窗口（交易日）'}, 'halflife': {'type': 'int', 'default': 10, 'min': 1, 'max': 63, 'desc': 'EWMA半衰期（交易日）'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get('window', self.default_params['window']))
        halflife = int(params.get('halflife', self.default_params['halflife']))
        return int((window + halflife * 3) * 1.5) + 10

    @staticmethod
    def _ewm_std_last(x: np.ndarray, halflife: int) -> float:
        """计算窗口内 EWMA 标准差并返回最后一个值。"""
        s = pd.Series(x).ewm(halflife=halflife, min_periods=0).std()
        return s.iloc[-1] if not s.empty else np.nan

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get('window', self.default_params['window']))
        halflife = int(params.get('halflife', self.default_params['halflife']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='close', adjust='qfq')
        if close.empty:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        mkt_ret = ret.mean(axis=1)
        resid = ret.sub(mkt_ret, axis=0)
        ivol = resid.rolling(window, min_periods=int(window * 0.8)).apply(lambda x: self._ewm_std_last(x.values, halflife), raw=False)
        factor = -ivol
        return factor.loc[ctx.start_date:]
