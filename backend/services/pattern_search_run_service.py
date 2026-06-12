"""图形相似度检索异步任务服务（needs DB）。

把 by_image（截图找相似股票）从同步改为后台任务：worker 进程内调视觉 LLM
提取曲线 + 全池 DTW 检索，结果写 ``fr_pattern_search_results``，状态写
``fr_pattern_search_runs``。

设计取舍（与 backtest_service 同构）：
- 模块顶层函数 + primitive 参数（``run_id: str``、``body: dict``），pickle 友好，
  供 ProcessPool 跨进程派发；无闭包、无类实例依赖。
- 协作式中断：在阶段边界调 ``check_abort('pattern_search', run_id)``。
- 原始截图不落库，只在 body 里随任务进子进程用完即弃；记录里只存文件名 +
  识别曲线 + 检索结果。
"""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from typing import Any

from backend.services.abort_check import AbortedError, check_abort
from backend.services.pattern_learn import search_by_learned
from backend.services.pattern_query import search_by_image, search_by_window
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def _update_status(
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """更新 ``fr_pattern_search_runs`` 状态字段（与 backtest_service._update_status 同构）。"""
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
                f"UPDATE fr_pattern_search_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


def _run_and_persist(run_id: str, search_fn) -> None:
    """跑一次检索（``search_fn(on_progress)`` 返回 {query_curves, matches}）并把结果落库。

    截图 / 走势两种 worker 共用同一套状态机与结果写入；任何异常都收口为终态。
    """
    try:
        _update_status(run_id, status="running", started=True, progress=10)
        check_abort("pattern_search", run_id)

        def _on_progress(pct: int) -> None:
            try:
                _update_status(run_id, progress=min(pct, 90))
            except Exception:
                log.debug("progress update failed (non-critical): run_id=%s pct=%s", run_id, pct)

        res = search_fn(on_progress=_on_progress)

        check_abort("pattern_search", run_id)
        _update_status(run_id, progress=95)

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "REPLACE INTO fr_pattern_search_results "
                    "(run_id, query_curves_json, matches_json) VALUES (%s,%s,%s)",
                    (
                        run_id,
                        json.dumps(res.get("query_curves", []), ensure_ascii=False),
                        json.dumps(res.get("matches", []), ensure_ascii=False),
                    ),
                )
            c.commit()

        _update_status(run_id, status="success", finished=True, progress=100)
    except AbortedError:
        _update_status(run_id, status="aborted", finished=True, error="用户已请求中断")
    except Exception:  # noqa: BLE001 - 统一收口为 failed 终态 + 落 traceback
        log.exception("pattern search run failed: run_id=%s", run_id)
        _update_status(
            run_id, status="failed", finished=True, error=traceback.format_exc()
        )


def run_pattern_search_by_image(run_id: str, body: dict) -> None:
    """worker 入口：截图检索。"""
    _run_and_persist(run_id, lambda on_progress=None: search_by_image(
        DataService(),
        pool_id=body["pool_id"],
        images=body.get("images"),
        image=body.get("image"),
        hint=body.get("hint"),
        scales=body.get("scales"),
        top_k=body.get("top_k", 20),
        agg=body.get("agg", "min"),
        min_score=body.get("min_score", 0.0),
        on_progress=on_progress,
    ))


def run_pattern_search_by_window(run_id: str, body: dict) -> None:
    """worker 入口：相似K线选股（一段或多段真实走势）。"""
    _run_and_persist(run_id, lambda on_progress=None: search_by_window(
        DataService(),
        windows=body.get("windows") or [],
        pool_id=body["pool_id"],
        scales=body.get("scales"),
        top_k=body.get("top_k", 20),
        agg=body.get("agg", "min"),
        min_score=body.get("min_score", 0.0),
        on_progress=on_progress,
    ))


def _load_labels(pattern_name: str) -> list[dict]:
    """读某形态的全部标注（正例/反例）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT symbol, start_date, end_date, label "
                "FROM fr_pattern_labels WHERE pattern_name=%s",
                (pattern_name,),
            )
            return list(cur.fetchall() or [])


def run_pattern_search_learned(run_id: str, body: dict) -> None:
    """worker 入口：学习型选股——读标注 → 训练打分器 → 给池打分。"""
    def _do(on_progress=None):
        labels = _load_labels(body["pattern_name"])
        return search_by_learned(
            DataService(),
            labels=labels,
            pool_id=body["pool_id"],
            top_k=body.get("top_k", 20),
            mode=body.get("mode", "realtime"),
            on_progress=on_progress,
        )
    _run_and_persist(run_id, _do)
