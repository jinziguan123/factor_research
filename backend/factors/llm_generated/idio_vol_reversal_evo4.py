"""特质波动率反转因子 V1。

改进自 idio_vol_reversal_evo3 的反馈：
原因子高波动→高收益的动量假设被市场证伪，实际呈现反转。
本因子直接取特质波动率的负值，将信号转化为规范的反转因子，
使 IC 正向化并与多空收益方向一致。
"""
from __future__ import annotations
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class IdioVolReversalV1(BaseFactor):
    factor_id = 'idio_vol_reversal_evo4'
    display_name = '特质波动率反转V1'
    category = 'reversal'
    description = '因子值（负特质波动率）越大，即特质波动率越低，预期未来短期收益越正（反转因子）。'
    hypothesis = '反转假设——高特质波动率反映噪音交易者过度关注，但定价偏差会在短期快速修正，导致高波动股票未来收益差。低特质波动率股票缺少噪音推升，后续相对表现更好。机制是过度关注后的均值回归。在趋势性牛市或恐慌暴跌初期可能失效，此时定价偏差延续而非反转。'
    default_params = {'window': 20, 'skip': 0, 'ema_halflife': 0}
    params_schema = {'window': {'type': 'int', 'default': 20, 'min': 5, 'max': 252, 'desc': '特质波动率计算窗口（交易日）'}, 'skip': {'type': 'int', 'default': 0, 'min': 0, 'max': 60, 'desc': '信号生效前跳过的交易日数'}, 'ema_halflife': {'type': 'float', 'default': 0.0, 'min': 0.0, 'max': 50.0, 'desc': '对因子值做指数平滑的半衰期（交易日），0 表示不平滑'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get('window', self.default_params['window']))
        skip = int(params.get('skip', self.default_params['skip']))
        ema_hl = float(params.get('ema_halflife', self.default_params['ema_halflife']))
        return int((window + skip + ema_hl) * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get('window', self.default_params['window']))
        skip = int(params.get('skip', self.default_params['skip']))
        ema_hl = float(params.get('ema_halflife', self.default_params['ema_halflife']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='close', adjust='qfq')
        if close.empty:
            return pd.DataFrame()
        ret = close.pct_change().fillna(0)
        ret_resid = ret.sub(ret.mean(axis=1), axis=0)
        min_periods = max(5, int(window * 0.5))
        iv = ret_resid.rolling(window=window, min_periods=min_periods).std()
        factor = -iv.shift(skip)
        if ema_hl > 0:
            factor = factor.ewm(halflife=ema_hl, min_periods=1).mean()
        return factor.loc[ctx.start_date:]
