"""常驻 worker 进程（与 FastAPI 主进程隔离）。

包含的 worker：
- live_market：盘中拉 spot 快照 + 盘后归档 1m K，由 launchd / supervisord 守护。

所有 worker 都是独立 ``python -m`` 入口；不依赖 FastAPI 启动事件，避免 API 重启
影响数据采集。
"""
