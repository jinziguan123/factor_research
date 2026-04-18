"""数据导入子包。

三条链路：

- ``stock_1m`` —— QMT ``.DAT`` → ClickHouse ``stock_bar_1m``
- ``qfq``      —— parquet 因子宽表 → MySQL ``fr_qfq_factor``
- ``aggregate`` —— 分钟线 → 日线（位于上层 ``backend.scripts.aggregate_bar_1d``）

共用内部模块用单下划线前缀（``_qmt_mmap`` / ``_bar_rows`` / ``_state``），
对外只暴露 ``stock_1m.run_import`` / ``qfq.run_import`` 两个入口函数，供
``backend.api.routers.admin`` 的 BackgroundTasks 直接调用。

历史入口 ``backend.scripts.import_qfq`` 仍然保留，作为 ``qfq`` 的 shim。
"""
