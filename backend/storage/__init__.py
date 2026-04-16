"""数据读取层：负责对接 ClickHouse（行情 / 因子值）与 MySQL（元数据 / 维度表）。

模块划分：
- ``clickhouse_client`` / ``mysql_client``：薄封装，提供上下文管理器接口；
- ``symbol_resolver``：symbol ↔ symbol_id 互转，带进程内 LRU 缓存；
- ``data_service``：对外统一 API（``load_bars`` / ``load_panel`` / ``resolve_pool``）。

上层业务（因子计算、评估引擎、回测引擎、API 层）只应依赖 ``data_service``，
避免把底层 SQL / 列名直接暴露出去，便于后续切换存储或加速实现。
"""
