"""特质波动率动量因子，使用EWMA波动率并做截面百分位排名。"""
from __future__ import annotations
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class IdioVolMomentumRank(BaseFactor):
    factor_id = 'idio_vol_reversal_evo3'
    display_name = '特质波动动量(排名)'
    category = 'volatility'
    description = '使用EWMA加权特质波动率，输出截面百分位排名。因子值越大，预期未来收益越高（动量假设）。'
    hypothesis = '高特质波动率代表噪音交易者过度关注，定价偏差在短期持续，产生动量效应，近期高波动股票未来短期收益更高。失效于流动性危机或恐慌性抛售时定价偏差快速修正。'
    default_params = {'halflife': 10}
    params_schema = {'halflife': {'type': 'int', 'default': 10, 'min': 2, 'max': 60, 'desc': 'EWMA半衰期（交易日）'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        halflife = int(params.get('halflife', self.default_params['halflife']))
        return int(halflife * 3 * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        halflife = int(params.get('halflife', self.default_params['halflife']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='close', adjust='qfq')
        if close.empty:
            return pd.DataFrame()
        ret = close.pct_change()
        mkt_ret = ret.mean(axis=1)
        excess_ret = ret.sub(mkt_ret, axis=0)
        idio_vol = excess_ret.ewm(halflife=halflife, min_periods=halflife).std()
        rank_factor = idio_vol.rank(axis=1, pct=True)
        result = rank_factor.loc[ctx.start_date:]
        return result
