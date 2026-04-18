"""LLM 生成的因子专用目录。

与手写因子（``momentum/`` / ``volatility/`` / 等）刻意分开放：
- 便于批量清理不满意的 LLM 输出——直接 ``rm *.py`` 不碰手写资产；
- FactorRegistry 扫描时天然当作 ``category='custom'`` 的同级目录处理，不需要特殊逻辑；
- 目录级隔离也让 Git diff 一眼能看出"哪些因子是 AI 产物"。

该目录下每个 ``<factor_id>.py`` 由 ``backend.services.factor_assistant`` 落盘生成；
**不要**把手写因子放进来。
"""
