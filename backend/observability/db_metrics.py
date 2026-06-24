"""从 MySQL 任务表派生 Prometheus 指标（跨进程安全）。

为什么不用进程内计数器：回测 / 评估等任务在 ProcessPool 的 **worker 子进程**执行，
其进程内 ``REGISTRY`` 计数器无法被 **主进程** 的 ``/metrics`` 读取（内存隔离）。
而任务状态已持久化在 ``fr_*_runs`` 表，主进程查 DB 聚合即可——跨进程一致、重启不丢，
且任务数单调递增，天然符合 Prometheus counter / histogram 语义。

派生指标：
- ``fr_task_total{kind,status}``：各任务表按 status 计数（counter）。
- ``fr_task_duration_seconds{kind}``：success 任务的 ``finished_at - started_at`` 直方图。
- ``fr_data_health{check}``：依赖健康度（当前导出 mysql 连通性）。
"""
from __future__ import annotations

import logging

from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

# (表名, kind 标签)。表不存在时静默跳过该 kind。
_RUN_TABLES: list[tuple[str, str]] = [
    ("fr_backtest_runs", "backtest"),
    ("fr_factor_eval_runs", "eval"),
    ("fr_cost_sensitivity_runs", "cost_sensitivity"),
    ("fr_composition_runs", "composition"),
    ("fr_param_sensitivity_runs", "param_sensitivity"),
    ("fr_signal_runs", "signal"),
]

_BUCKETS: tuple[int, ...] = (1, 5, 10, 30, 60, 120, 300, 600)


def render_db_metrics() -> str:
    """查询任务表，生成 Prometheus 文本（version 0.0.4）。"""
    total = [
        "# HELP fr_task_total 因子任务计数（按类型与结果，从 DB 派生）",
        "# TYPE fr_task_total counter",
    ]
    dur = [
        "# HELP fr_task_duration_seconds 任务时延秒（success 任务，从 DB 派生）",
        "# TYPE fr_task_duration_seconds histogram",
    ]
    health = [
        "# HELP fr_data_health 数据/依赖健康度（1=健康，0=异常）",
        "# TYPE fr_data_health gauge",
    ]

    mysql_ok = 0
    try:
        with mysql_conn() as c:
            mysql_ok = 1
            with c.cursor() as cur:
                for table, kind in _RUN_TABLES:
                    try:
                        cur.execute(
                            f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status"
                        )
                        rows = cur.fetchall()
                    except Exception:
                        # 表不存在 / 列缺失 → 跳过该 kind，不影响其它指标。
                        continue
                    for r in rows:
                        st = str(r["status"])
                        n = int(r["n"])
                        total.append(
                            f'fr_task_total{{kind="{kind}",status="{st}"}} {n}'
                        )
                    # success 任务耗时直方图
                    try:
                        cur.execute(
                            f"SELECT TIMESTAMPDIFF(SECOND, started_at, finished_at) AS d "
                            f"FROM {table} WHERE status='success' "
                            f"AND started_at IS NOT NULL AND finished_at IS NOT NULL"
                        )
                        durs = [
                            int(r["d"]) for r in cur.fetchall()
                            if r["d"] is not None and int(r["d"]) >= 0
                        ]
                    except Exception:
                        durs = []
                    if durs:
                        for b in _BUCKETS:
                            cnt = sum(1 for d in durs if d <= b)
                            dur.append(
                                f'fr_task_duration_seconds_bucket{{kind="{kind}",le="{b}"}} {cnt}'
                            )
                        dur.append(
                            f'fr_task_duration_seconds_bucket{{kind="{kind}",le="+Inf"}} {len(durs)}'
                        )
                        dur.append(
                            f'fr_task_duration_seconds_sum{{kind="{kind}"}} {sum(durs)}'
                        )
                        dur.append(
                            f'fr_task_duration_seconds_count{{kind="{kind}"}} {len(durs)}'
                        )
    except Exception:
        log.exception("render_db_metrics: MySQL 查询失败，仅导出健康度")
        mysql_ok = 0

    health.append(f'fr_data_health{{check="mysql"}} {mysql_ok}')
    return "\n".join(total + dur + health) + "\n"
