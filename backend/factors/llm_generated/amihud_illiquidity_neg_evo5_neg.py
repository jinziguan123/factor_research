"""改进型 Amihud 非流动性因子：日内振幅/成交额，短期窗口，方向反转，市场状态滤波。"""
from __future__ import annotations
import numpy as np
import pandas as pd
from backend.factors.base import BaseFactor, FactorContext

class AmihudIlliquidityEvo3_20dFilteredNeg(BaseFactor):
    factor_id = 'amihud_illiquidity_neg_evo5_neg'
    display_name = 'Amihud非流动性改进3号（短期反转，滤波）（取负）'
    category = 'custom'
    description = '用日内振幅/成交额20日均值衡量价格冲击，取相反数使高流动性股票得分高，因子值越大预期未来5日收益越正；叠加市场状态滤波减少危机期信号。'
    hypothesis = '【已取负，方向反转】流动性溢价假设——低流动性股票需更高预期收益补偿，但高频振幅更能捕捉订单流价格冲击。较短窗口（20日）提升换手，取相反数后高流动性得分高正向预测收益。机制：流动性提供者库存风险溢价+日内波动反映信息不对称。市场急跌时流动性枯竭，因子失效，通过市场平均收益滤波抑制信号，规避危机期暴跌风险。在单边牛市或流动性泛滥阶段因子可能弱化。'
    default_params = {'illiquidity_window': 20, 'damp_window': 20, 'damp_scale': 15.0, 'damp_threshold': -0.005}
    params_schema = {'illiquidity_window': {'type': 'int', 'default': 20, 'min': 5, 'max': 60, 'desc': '非流动性均值窗口（交易日）'}, 'damp_window': {'type': 'int', 'default': 20, 'min': 5, 'max': 60, 'desc': '市场状态滤波回顾窗口（交易日）'}, 'damp_scale': {'type': 'float', 'default': 15.0, 'min': 1.0, 'max': 50.0, 'desc': '状态滤波Sigmoid陡度'}, 'damp_threshold': {'type': 'float', 'default': -0.005, 'min': -0.05, 'max': 0.01, 'desc': '市场日均收益阈值，低于此值滤波抑制因子'}}
    supported_freqs = ('1d',)

    def required_warmup(self, params: dict) -> int:
        illiq_win = int(params.get('illiquidity_window', self.default_params['illiquidity_window']))
        damp_win = int(params.get('damp_window', self.default_params['damp_window']))
        max_win = max(illiq_win, damp_win)
        return int(max_win * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        illiq_win = int(params.get('illiquidity_window', self.default_params['illiquidity_window']))
        damp_win = int(params.get('damp_window', self.default_params['damp_window']))
        damp_scale = float(params.get('damp_scale', self.default_params['damp_scale']))
        damp_threshold = float(params.get('damp_threshold', self.default_params['damp_threshold']))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        high = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='high', adjust='qfq')
        low = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='low', adjust='qfq')
        amount = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='amount_k', adjust='none')
        close = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq='1d', field='close', adjust='qfq')
        if any((df.empty for df in [high, low, amount, close])):
            return -pd.DataFrame()
        amplitude_ratio = (high - low) / close.replace(0, np.nan)
        raw_illiquidity = np.log(amplitude_ratio / (amount / 10000).replace(0, np.nan).clip(1e-06))
        illiquidity = raw_illiquidity.rolling(window=illiq_win, min_periods=max(5, illiq_win // 2)).mean()
        factor_raw = -illiquidity
        market_ret = close.pct_change().mean(axis=1)
        market_ret_mean = market_ret.rolling(window=damp_win, min_periods=max(5, damp_win // 2)).mean()
        damp = 1.0 / (1.0 + np.exp(-damp_scale * (market_ret_mean - damp_threshold)))
        factor = factor_raw.multiply(damp, axis=0)
        return -factor.loc[ctx.start_date:]
