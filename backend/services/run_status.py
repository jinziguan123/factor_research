"""任务运行状态统一更新 helper。

消除 eval / backtest / composition / signal 四个 service 中
``_set_status`` / ``_update_status`` 的重复代码。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.storage.mysql_client import mysql_conn


def update_run_status(
    table: str,
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    feedback: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """更新任意运行表的 status / progress / error 字段。

    只更新显式传入的字段（非 None），避免把其他字段误写为 NULL。
    ``started`` / ``finished`` 为 True 时分别写入 ``started_at`` / ``finished_at``
    （取本地时间）。

    Args:
        table: 表名，如 ``"fr_factor_eval_runs"``。
        run_id: 对应行主键。
        feedback: LLM 友好的诊断文本（写入 ``feedback_text`` 列，若表有此列）。
            与 ``error`` 互补——error 仅 failed 时写 traceback，feedback 在
            success / failed 都可写。
    """
    sets: list[str] = []
    vals: list[Any] = []
    if status is not None:
        sets.append("status=%s")
        vals.append(status)
    if progress is not None:
        sets.append("progress=%s")
        vals.append(progress)
    if error is not None:
        sets.append("error_message=%s")
        vals.append(error)
    if feedback is not None:
        sets.append("feedback_text=%s")
        vals.append(feedback)
    if started:
        sets.append("started_at=%s")
        vals.append(datetime.now())
    if finished:
        sets.append("finished_at=%s")
        vals.append(datetime.now())
    if not sets:
        return
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()
