"""因子参数哈希：把 params dict 归一化为 40 位 sha1 hex。

用途：
- ``fr_factor_eval_runs.params_hash`` / ``fr_backtest_runs.params_hash`` 做幂等键；
- ClickHouse ``factor_value_1d`` 按 (factor_id, version, params_hash) 区分不同参数的因子值。

归一化规则：
- ``sort_keys=True``：Python dict 顺序不稳定，排序后保证同一语义 dict 得到同一 hash；
- ``ensure_ascii=False``：中文键 / 值保持原样（避免 \\uXXXX 化影响跨语言一致性，例如
  前端如果后续也做 hash 对比也应遵循同样约定）；
- ``default=str``：遇到 numpy / Decimal / 日期等非原生可序列化对象时退到 str()，
  避免 TypeError；调用方若想要"原生 repr 语义"请自行提前转好（例如 ``float(x)``）。
"""
from __future__ import annotations

import hashlib
import json


def params_hash(params: dict) -> str:
    """把 params dict 归一化为 40 位 sha1 hex。

    key 顺序 / 空白不影响哈希。空 dict 也有固定 hash（``sha1("{}")``），
    调用方不必特殊处理 ``None``——建议先 ``params or {}`` 归一化再传入。
    """
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
