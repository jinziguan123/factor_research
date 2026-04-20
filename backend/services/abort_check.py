"""协作式中断（cooperative abort）服务。

为什么做协作式而不是杀进程：
- ``ProcessPoolExecutor.Future.cancel()`` 在任务已被 worker 领走后就是 no-op；
- 硬杀 worker 会把共享解释器、numba JIT 缓存、连接池一并丢失，代价远大于等待。

所以我们用 **DB 轮询** 方式：
- 用户点"中断" → API 层把 ``status`` 从 ``running`` 改为 ``aborting``（见各 router 的
  ``POST /{resource}/{run_id}/abort``）；
- 计算主循环在阶段边界（数据加载 / 因子 compute / 回测 / 每个 grid 点之间）调用
  ``check_abort``，如果看到 ``aborting`` 就抛 ``AbortedError``；
- service 的 try/except 分支里识别 ``AbortedError``，把 status 落成终态 ``aborted``；
  其它异常仍然走 ``failed`` 分支，不混淆。

实现约束：
- ``check_abort`` 是热路径上频繁调用的函数（每个 grid 点可能每 5~30 秒查一次），
  开销要足够小。单次查询 = 一次 ``SELECT status FROM <table> WHERE run_id=%s``，
  本地 MySQL ~0.5ms，可接受。
- 我们把 ``aborting`` 也视为"继续运行但告知停"的中间态：看到这个状态立即抛。
- 若 run 记录不存在（被外部 delete），也抛 ``AbortedError`` —— 语义上等同于被
  用户取消，避免任务继续在一个没有 run 记录的 id 上空转到最后才发现写不进去。
"""
from __future__ import annotations

from backend.storage.mysql_client import mysql_conn


class AbortedError(Exception):
    """任务被用户请求中断。

    service 层应捕获该异常后把 run 状态落为 ``aborted``（不是 ``failed``，
    这俩语义完全不同：failed 需要写 traceback，aborted 只是用户主动终止）。
    """


# 三类任务的 run 表映射。抽成常量避免 service 层硬编码表名，
# 将来如果又加一类任务（比如单因子 WFO），只需要往这里加一条就能复用 check_abort。
_ABORT_TABLES = {
    "eval": "fr_factor_eval_runs",
    "backtest": "fr_backtest_runs",
    "cost_sensitivity": "fr_cost_sensitivity_runs",
    "param_sensitivity": "fr_param_sensitivity_runs",
}


def check_abort(kind: str, run_id: str) -> None:
    """查一次 DB，若 ``status`` 已被置为 ``aborting`` 或 run 不存在，抛 ``AbortedError``。

    Args:
        kind: 任务种类，必须是 ``_ABORT_TABLES`` 的 key（``eval`` / ``backtest`` /
            ``cost_sensitivity``）。未知 kind 直接抛 ValueError，避免静默放过。
        run_id: 对应表的主键。

    Raises:
        AbortedError: 用户已请求中断、或 run 记录被删除。
        ValueError: kind 未在白名单中。

    注意：仅查一行、只取一列，索引命中 PK，开销可忽略。
    不做任何写操作：落 ``aborted`` 终态是调用方（service）的职责，
    这样 service 可以在抛出 AbortedError 前先做必要的清理（例如释放
    进程内资源、关 parquet handle），之后再统一写 DB。
    """
    table = _ABORT_TABLES.get(kind)
    if table is None:
        raise ValueError(f"unknown abort kind: {kind}")
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"SELECT status FROM {table} WHERE run_id=%s",
                (run_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise AbortedError(f"run_id={run_id} not found (deleted externally)")
    if row["status"] == "aborting":
        raise AbortedError(f"run_id={run_id} aborted by user")
