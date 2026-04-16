"""因子引擎的基类定义。

所有用户因子必须继承 ``BaseFactor`` 并实现 ``compute()``。引擎会扫描
``backend/factors/`` 下的所有子类并通过 ``FactorRegistry`` 注册。

设计要点（Design §4.1）：
- **宽表产出**：``compute()`` 必须返回宽表 DataFrame，``index=trade_date``
  （DatetimeIndex），``columns=symbol``（字符串代码），值为因子值（float）。
- **预热期自管理**：``required_warmup(params)`` 告诉调用方需要多少天历史数据用于
  计算；``compute()`` 在 ``load_panel`` 时通常把 ``start_date - warmup_days``
  作为数据起点读多一点，最后再 ``.loc[start_date:]`` 切回。这样外层调度（评估 /
  回测）只需要知道计算窗口 ``[start_date, end_date]`` 而不用操心实现细节。
- **无状态**：因子类实例不要持有状态；每次计算传入 ``FactorContext`` + ``params``，
  便于并行化（ProcessPool）和热加载（换新类直接重算，不受旧状态影响）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pandas as pd

from backend.storage.data_service import DataService


@dataclass
class FactorContext:
    """传给 ``BaseFactor.compute()`` 的上下文。

    Attributes:
        data: ``DataService`` 实例，用于读取 OHLCV / 股票池等。因子实现**不应**
            直接 ``from backend.storage.mysql_client import mysql_conn`` 绕过本层。
        symbols: 要计算的股票列表（已解析为标准代码格式，如 ``'000001.SZ'``）。
        start_date: 计算窗口起点（闭区间）。因子产出的 DataFrame 第一行就应该是这一天。
        end_date: 计算窗口终点（闭区间）。
        warmup_days: 预热期天数，便于调用方复用 ``required_warmup()`` 的结果。
            因子 ``compute()`` 通常会 load_panel 时用 ``start_date - warmup_days``
            （加一点安全 buffer）作为数据起点，最后再切回 ``start_date`` 做输出。
    """

    data: DataService
    symbols: list[str]
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    warmup_days: int


class BaseFactor:
    """因子基类。

    **子类必须填**：
    - ``factor_id``：全局唯一的因子标识，snake_case（如 ``reversal_n``）。
      FactorRegistry 以此为主键持久化到 ``fr_factor_meta``。
    - ``display_name``：中文可读名，给前端展示用。
    - ``category``：分类（``reversal`` / ``momentum`` / ``volatility`` / ``volume`` /
      ``custom``...），通常与所在子目录一致。

    **可选**：
    - ``description``：简介，前端 tooltip。
    - ``params_schema``：参数描述（类型 / 默认值 / 范围），前端据此生成表单。
    - ``default_params``：``params_schema`` 中默认值的快照，便于调用方不填参直接跑。
    - ``supported_freqs``：该因子可用于哪些频率（MVP 只有 ``"1d"``）。

    **必须实现**：
    - ``required_warmup(params) -> int``：根据参数告知需要多少天的预热数据。
    - ``compute(ctx, params) -> pd.DataFrame``：真正的计算逻辑。
    """

    factor_id: ClassVar[str]
    display_name: ClassVar[str]
    category: ClassVar[str]
    description: ClassVar[str] = ""
    params_schema: ClassVar[dict] = {}
    default_params: ClassVar[dict] = {}
    supported_freqs: ClassVar[tuple[str, ...]] = ("1d",)

    def required_warmup(self, params: dict) -> int:
        """给定参数需要多少天预热数据。

        调用方（Task 6 评估 / Task 7 回测）会用这个值决定向 DataService 请求数据时
        把起点前移多少天。返回整数天数（自然日，而非交易日；因子内部按需放大）。
        """
        raise NotImplementedError

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        """计算因子值。

        Returns:
            宽表 DataFrame：``index=DatetimeIndex(trade_date)``,
            ``columns=symbol (str)``，值为 float 因子值。无数据时返回空 DataFrame。
        """
        raise NotImplementedError
