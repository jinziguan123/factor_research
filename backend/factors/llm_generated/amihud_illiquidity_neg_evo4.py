"""改进版Amihud非流动性因子，使用EMA平滑。"""
from __future__ import annotations
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class AmihudIlliqEma(BaseFactor):
    factor_id = 'amihud_illiquidity_neg_evo4'
    display_name = '非流动性EMA平滑'
    category = 'volume'
    description = '日收益绝对值/成交金额的指数加权移动平均，因子值越大（流动性越差）预期未来收益越正。'
    hypothesis = '流动性溢价假设——流动性差的股票交易成本高，需要更高收益补偿。基于Amihud(2002)非流动性，通过EMA平滑减少噪声，提升IC稳定性。在流动性急剧萎缩的恐慌市可能失效。'
    default_params = {'span': 20}
    params_schema = {'span': {'type': 'int', 'default': 20, 'min': 5, 'max': 120, 'desc': 'EMA平滑窗口（交易日）'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        span = int(params.get('span', self.default_params['span']))
        return int(span * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        span = int(params.get('span', self.default_params['span']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='close', adjust='qfq')
        amount = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='amount_k', adjust='none')
        if close.empty or amount.empty:
            return pd.DataFrame()
        (close, amount) = close.align(amount, join='inner')
        ret = close.pct_change(fill_method=None)
        amount_safe = amount.where(amount > 0)
        daily_illiq = ret.abs() / amount_safe
        illiq_ema = daily_illiq.ewm(span=span, min_periods=span).mean()
        factor = illiq_ema
        return factor.loc[ctx.start_date:]
