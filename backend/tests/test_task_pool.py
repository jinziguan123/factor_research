"""Task 8: ProcessPool 任务运行时的单元测试。

测试目标：
1. ``submit`` 真的把任务丢进子进程（PID 不同于父进程）；
2. ``Future.result()`` 能拿到子进程的返回值；
3. ``reset_pool`` 会关闭旧池、返回新池，且 ``get_pool`` 在 reset 后返回同一新池；
4. ``backend.runtime.entries`` 的 ``eval_entry`` / ``backtest_entry`` 可从模块顶层
   导入——这正是 ``ProcessPoolExecutor.submit`` 对 pickle 的硬要求。

本文件内定义的 ``_echo_pid`` / ``_sum_args`` 也必须是模块顶层函数，否则 pickle 不过。
"""
from __future__ import annotations

import os

import pytest


# --------- 测试用的 worker 工作函数（必须是模块顶层，保证 pickle 可达） ---------

def _echo_pid() -> int:
    """返回当前进程 PID，用来验证任务确实跑在另一个进程。"""
    return os.getpid()


def _sum_args(a: int, b: int) -> int:
    """返回两个整数之和，用来验证位置参数正确送达 worker。"""
    return a + b


# ---------------- autouse cleanup：避免池泄漏到别的测试文件 ----------------

@pytest.fixture(autouse=True)
def _cleanup_task_pool():
    """用例结束时强制关闭本模块的 ProcessPool 单例。

    为什么不直接调 ``reset_pool``？因为 ``reset_pool`` 内部会**立刻再建一个新池**，
    那会反过来引入新的 worker 进程、污染后续用例。这里直接 ``shutdown`` + 置 None，
    让下一次 ``get_pool`` 真正从 0 开始。

    ``wait=False``：不阻塞测试退出；子进程被 OS 回收。
    """
    yield
    import backend.runtime.task_pool as tp

    if tp._pool is not None:
        tp._pool.shutdown(wait=False, cancel_futures=False)
        tp._pool = None


# --------------------------------- 用例 ---------------------------------


def test_submit_runs_in_worker():
    """提交的任务应该在**子进程**执行：返回的 PID 必然不等于当前父进程 PID。"""
    from backend.runtime.task_pool import submit

    f = submit(_echo_pid)
    assert f.result(timeout=10) != os.getpid()


def test_submit_returns_future_with_result():
    """位置参数能正确传到 worker，Future.result 能取回子进程的返回值。"""
    from backend.runtime.task_pool import submit

    f = submit(_sum_args, 2, 3)
    assert f.result(timeout=10) == 5


def test_reset_pool_returns_new_pool():
    """reset_pool 会替换内部单例，``get_pool`` 之后拿到的是同一个新实例。"""
    from backend.runtime.task_pool import get_pool, reset_pool

    p1 = get_pool()
    p2 = reset_pool()
    assert p2 is not None
    # p2 是全新对象，和旧 p1 不应是同一个（id 比较即可）。
    assert p2 is not p1
    # reset 之后再 get，理应返回 reset 时刚建好的 _pool。
    p3 = get_pool()
    assert p3 is p2


def test_entries_importable():
    """``eval_entry`` / ``backtest_entry`` 必须能从模块顶层导入，
    这是 ``ProcessPoolExecutor.submit`` 对 pickle 的硬要求。
    """
    from backend.runtime.entries import backtest_entry, eval_entry

    assert callable(eval_entry)
    assert callable(backtest_entry)
