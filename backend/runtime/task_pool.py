"""任务进程池封装。

职责：
- 维护一个**模块级单例** ``ProcessPoolExecutor``；
- ``max_workers`` 由 ``settings.task_workers`` 控制（默认 2）；
- ``submit(fn, *args, **kw)`` 把任务派发到子进程，返回 ``Future``；
- ``reset_pool()`` 允许运行时重建（例如 ``POST /api/factors/reload`` 之后
  希望让新派发的任务拿到最新代码）。

关于 "worker recycle"（``max_tasks_per_child``）：
    ``concurrent.futures.ProcessPoolExecutor`` 的 ``max_tasks_per_child``
    参数 **Python 3.11 才加入**；本项目当前的 runtime 是 CPython 3.10，
    构造器会直接抛 ``TypeError``，因此此处不传该参数。后果：
        - 子进程会长期驻留，不会在执行 N 个任务后自动回收；
        - 因此**热加载 / 内存回收** 的能力依赖显式 ``reset_pool()``（例如
          因子热加载回调里调用一次），或升级到 Python 3.11+ 后补回该参数。
    该决策与 Task 5 的热加载能力互补：热加载只刷新主进程注册表，
    而真正在 worker 里执行的任务拿到的仍是子进程**首次 import 的因子版本**；
    当发生因子代码变更需要立即生效时，应配合 ``reset_pool()``。
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor
from typing import Callable

from backend.config import settings

log = logging.getLogger(__name__)

# 模块级单例：懒初始化。None 表示"尚未创建 / 已被 reset 掉"。
_pool: ProcessPoolExecutor | None = None


def get_pool() -> ProcessPoolExecutor:
    """获取/懒初始化全局 ``ProcessPoolExecutor``。

    在 ``reset_pool`` 把 ``_pool`` 置 None 之后再次调用，会重建一个新池。
    """
    global _pool
    if _pool is None:
        # NOTE: Python 3.10 不支持 max_tasks_per_child，这里刻意不传；详见模块 docstring。
        _pool = ProcessPoolExecutor(max_workers=settings.task_workers)
        log.info(
            "initialized ProcessPoolExecutor (workers=%d)",
            settings.task_workers,
        )
    return _pool


def submit(fn: Callable, *args, **kw):
    """把任务提交到池，返回 ``Future``。

    ``fn`` 必须是模块顶层可 pickle 的 callable（见 ``backend.runtime.entries``）。
    """
    return get_pool().submit(fn, *args, **kw)


def reset_pool() -> ProcessPoolExecutor:
    """关闭现有池并重建。

    用途：
    - 因子代码热加载后，强制拉起新 worker 以加载最新代码；
    - 集成测试之间清场，避免子进程泄漏。

    ``shutdown(wait=False, cancel_futures=False)`` 不阻塞也不取消在途任务：
    在途任务会在原 worker 里继续跑完（它们只是失去了后续被 submit 的资格），
    对调用方来说"下一次 submit 起用的是新池"，语义清晰且不会卡主线程。
    """
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=False)
        _pool = None
    return get_pool()
