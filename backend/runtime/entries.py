"""Worker 进程调用入口。

ProcessPoolExecutor 的 ``submit(fn, ...)`` 只能接受模块顶层可 pickle 函数。
把 ``run_eval`` / ``run_backtest`` 包一层 thin wrapper 放在这里，好处：

1. 避免 ``api/routers`` 直接 import services，降低启动时的 import 图；
2. 这里的 wrapper 可以统一做 logging 初始化、signal 处理等跨任务通用事宜；
3. 两个 entry 都把 services 的 import 放在函数体内（lazy import），
   主进程启动 / 路由层 import 本模块时不会预加载沉重的 services 依赖
   （pandas / numpy 等在 worker 进程首次执行时才付出启动成本）。

注意：
- 函数参数只能是 primitive（str + dict），与 ``run_eval`` / ``run_backtest``
  的既有契约一致，保证跨进程 pickle 安全；
- 本模块自身不应 import services，避免 pickle submit(fn) 时顺带序列化 services 的 import 副作用。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def eval_entry(run_id: str, body: dict) -> None:
    """评估任务入口：在 worker 进程内调用 ``run_eval``。"""
    # Lazy import：只有 worker 实际执行时才拉起 eval_service 及其依赖。
    from backend.services.eval_service import run_eval

    run_eval(run_id, body)


def backtest_entry(run_id: str, body: dict) -> None:
    """回测任务入口：在 worker 进程内调用 ``run_backtest``。"""
    # Lazy import：同上，降低父进程和同伴路由模块的冷启动开销。
    from backend.services.backtest_service import run_backtest

    run_backtest(run_id, body)
