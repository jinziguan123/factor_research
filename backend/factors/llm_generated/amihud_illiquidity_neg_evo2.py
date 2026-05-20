"""改进的Amihud非流动性因子——使用日内振幅(HL)替代日间收益。"""
from __future__ import annotations
import numpy as np
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class ImprovedAmihudHL(BaseFactor):
    factor_id = 'amihud_illiquidity_neg_evo2'
    display_name = '改进Amihud非流动性(HL)'
    category = 'custom'
    description = '使用日内振幅与成交额计算非流动性，值越大流动性越差，预期未来1日收益越正（非流动性溢价）。'
    hypothesis = '非流动性溢价假设——流动性差的股票需补偿。Amihud(2002)用日间收益，改进采用日内振幅更能反映订单流价格冲击；机制是流动性提供者要求库存风险溢价。在流动性危机或大盘急跌时可能失效，流动性差的股跌幅更深。'
    default_params = {'window': 21, 'ema_span': 10}
    params_schema = {'window': {'type': 'int', 'default': 21, 'min': 5, 'max': 120, 'desc': '非流动性滚动窗口（交易日）'}, 'ema_span': {'type': 'int', 'default': 10, 'min': 2, 'max': 60, 'desc': '时序EMA平滑周期'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get('window', self.default_params['window']))
        ema_span = int(params.get('ema_span', self.default_params['ema_span']))
        trading_days = window + 3 * ema_span
        return int(trading_days * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get('window', self.default_params['window']))
        ema_span = int(params.get('ema_span', self.default_params['ema_span']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        end_date = ctx.end_date.date()
        high = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='high', adjust='qfq')
        low = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='low', adjust='qfq')
        close = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='close', adjust='qfq')
        amount = ctx.data.load_panel(ctx.symbols, data_start, end_date, freq='1d', field='amount_k', adjust='none')
        if high.empty or low.empty or close.empty or amount.empty:
            return pd.DataFrame()
        amplitude = (high - low) / close.replace(0, np.nan)
        illiquidity_day = amplitude / amount.replace(0, np.nan)
        illiquidity_roll = illiquidity_day.rolling(window=window).mean()
        illiquidity_smooth = illiquidity_roll.ewm(span=ema_span).mean()
        factor_raw = -illiquidity_smooth

        def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
            mu = df.mean(axis=1)
            sigma = df.std(axis=1)
            return df.sub(mu, axis=0).div(sigma.where(sigma > 0), axis=0)
        factor = _cs_zscore(factor_raw)
        return factor.loc[ctx.start_date:]
