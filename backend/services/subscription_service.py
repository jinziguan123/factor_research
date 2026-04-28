"""实盘监控订阅服务（fr_signal_subscriptions）。

职责：
1. CRUD：activate / deactivate / delete / list_active / get；
2. ``find_due_subscriptions(now)``：worker 主循环用，找出需要本轮刷新的订阅；
3. ``refresh_subscription(sub, run_id)``：worker 调 ``run_signal`` 后回写
   last_refresh_at / last_run_id。

run_signal 本身不变（仍接受 body dict）；本模块负责把订阅配置转成 run_signal 的
body + 把结果回写。

纯函数 ``find_due_subscriptions`` 抽出来便于单测；DB 操作有现成 mysql_conn。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


# ---------------------------- 纯函数：调度判定 ----------------------------


def is_subscription_due(
    sub: dict, now: datetime, min_interval_floor_sec: int = 30,
) -> bool:
    """订阅当前是否需要刷新（纯函数，便于单测）。

    Args:
        sub: 订阅 row dict，必需键：``is_active``, ``last_refresh_at``,
            ``refresh_interval_sec``。
        now: 当前时刻。
        min_interval_floor_sec: 防御 refresh_interval_sec 配置过小（如 0/1s）
            导致 worker 雪崩；< 此值按 30s 处理。

    Returns:
        True = 该订阅本轮应触发；False = 跳过。

    规则：
    - is_active=0 永远 False；
    - last_refresh_at IS NULL（首次）→ True；
    - now - last_refresh_at >= max(refresh_interval_sec, floor) → True。
    """
    if not int(sub.get("is_active", 0)):
        return False
    last = sub.get("last_refresh_at")
    if last is None:
        return True
    interval = max(int(sub.get("refresh_interval_sec", 300)), min_interval_floor_sec)
    return (now - last).total_seconds() >= interval


# ---------------------------- DB CRUD ----------------------------


def create_subscription(body: dict) -> str:
    """创建一个新订阅。

    Args:
        body: 字段对齐 fr_signal_subscriptions 列：
            factor_items（list[dict]）、method、pool_id、n_groups、ic_lookback_days、
            filter_price_limit、top_n、refresh_interval_sec。

    Returns:
        新订阅的 subscription_id。
    """
    subscription_id = uuid.uuid4().hex
    now = datetime.now()
    items_json = json.dumps(body.get("factor_items") or [], ensure_ascii=False)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_signal_subscriptions
                (subscription_id, factor_items_json, method, pool_id, n_groups,
                 ic_lookback_days, filter_price_limit, top_n,
                 refresh_interval_sec, is_active,
                 created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)
                """,
                (
                    subscription_id,
                    items_json,
                    body.get("method", "equal"),
                    int(body["pool_id"]),
                    int(body.get("n_groups", 5)),
                    int(body.get("ic_lookback_days", 60)),
                    1 if body.get("filter_price_limit", True) else 0,
                    body.get("top_n"),
                    int(body.get("refresh_interval_sec", 300)),
                    now,
                    now,
                ),
            )
        c.commit()
    return subscription_id


def set_active(subscription_id: str, is_active: bool) -> bool:
    """切换 is_active；返回是否真的更新到一行（False = 不存在）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_signal_subscriptions SET is_active=%s, updated_at=%s "
                "WHERE subscription_id=%s",
                (1 if is_active else 0, datetime.now(), subscription_id),
            )
            n = cur.rowcount
        c.commit()
    return n > 0


def delete_subscription(subscription_id: str) -> bool:
    """硬删订阅；保留历史 fr_signal_runs（subscription_id 字段悬空对审计无影响）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_signal_subscriptions WHERE subscription_id=%s",
                (subscription_id,),
            )
            n = cur.rowcount
        c.commit()
    return n > 0


def get_subscription(subscription_id: str) -> dict | None:
    """读单条订阅；factor_items_json 解析后展开为 factor_items 字段。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_signal_subscriptions WHERE subscription_id=%s",
                (subscription_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return _expand_row(row)


def list_subscriptions(only_active: bool = False) -> list[dict]:
    """列出全部订阅（默认含 inactive；only_active=True 只返激活的）。"""
    sql = "SELECT * FROM fr_signal_subscriptions"
    if only_active:
        sql += " WHERE is_active=1"
    sql += " ORDER BY created_at DESC"
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
    return [_expand_row(r) for r in rows]


def find_matching_active_subscription(body: dict) -> dict | None:
    """查找与 body 配置完全相同的 ``active=1`` 订阅（去重用）。

    匹配维度：``factor_items``（按顺序逐项 factor_id 比较）、``method``、``pool_id``、
    ``n_groups``、``ic_lookback_days``、``filter_price_limit``、``top_n``。

    用途：``POST /api/signal-subscriptions`` 时去重——前端可能在 useSubscriptions
    5s 轮询缓存还没更新时让用户重复点"开启实盘监控"，导致同配置创建多份。
    本函数让 router 直接返回已存在的订阅，避免重复刷新跑出重复 fr_signal_runs。

    Returns:
        匹配的订阅 dict 或 None；多条匹配返回 created_at 最早的一条（最稳定）。
    """
    target_items = body.get("factor_items") or []
    target_method = body.get("method", "equal")
    target_pool = int(body.get("pool_id", -1))
    target_ngroups = int(body.get("n_groups", 5))
    target_ic_lookback = int(body.get("ic_lookback_days", 60))
    target_flim = bool(body.get("filter_price_limit", True))
    target_topn = body.get("top_n")

    candidates = list_subscriptions(only_active=True)
    for s in sorted(candidates, key=lambda x: x.get("created_at") or ""):
        # 主键级别比较
        if int(s.get("pool_id", -2)) != target_pool:
            continue
        if str(s.get("method")) != target_method:
            continue
        if int(s.get("n_groups", 0)) != target_ngroups:
            continue
        if int(s.get("ic_lookback_days", 0)) != target_ic_lookback:
            continue
        if bool(int(s.get("filter_price_limit", 0))) != target_flim:
            continue
        s_topn = s.get("top_n")
        if (s_topn is None) != (target_topn is None):
            continue
        if s_topn is not None and target_topn is not None and int(s_topn) != int(target_topn):
            continue
        # factor_items 顺序敏感比较
        s_items = s.get("factor_items", [])
        if len(s_items) != len(target_items):
            continue
        same = True
        for a, b in zip(s_items, target_items):
            af = a.get("factor_id") if isinstance(a, dict) else None
            bf = b.get("factor_id") if isinstance(b, dict) else None
            if af != bf:
                same = False
                break
        if same:
            return s
    return None


def find_due_subscriptions(now: datetime) -> list[dict]:
    """worker 主循环用：取所有 is_active=1 且到期需刷新的订阅。

    在 SQL 层先粗筛 is_active=1，把"是否到期"判断放到 Python 端走
    ``is_subscription_due`` 纯函数，便于单测。
    """
    active = list_subscriptions(only_active=True)
    return [s for s in active if is_subscription_due(s, now)]


def mark_refreshed(
    subscription_id: str, run_id: str, refreshed_at: datetime,
) -> None:
    """worker 触发 run_signal 后回写 last_refresh_at / last_run_id。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_signal_subscriptions "
                "SET last_refresh_at=%s, last_run_id=%s, updated_at=%s "
                "WHERE subscription_id=%s",
                (refreshed_at, run_id, refreshed_at, subscription_id),
            )
        c.commit()


# ---------------------------- 内部 ----------------------------


def _expand_row(row: dict) -> dict:
    """把 longtext factor_items_json 解析为 factor_items 列表。"""
    out = dict(row)
    raw = out.pop("factor_items_json", None)
    if raw:
        try:
            out["factor_items"] = json.loads(raw)
        except (TypeError, ValueError):
            out["factor_items"] = []
    else:
        out["factor_items"] = []
    return out


def process_due_subscription(sub: dict) -> str:
    """对一个 due 订阅触发一次完整的 signal 计算。

    **复用语义**：一个订阅永远只对应一条 fr_signal_runs 记录——
    - 首次刷新：INSERT 新 run，sub.last_run_id ← 新 run_id；
    - 后续刷新：UPDATE 同一条 run（重置为 pending + 清旧 payload + 同步最新订阅参数），
      run_signal 跑完会再次转成 success/failed；
    - 用户在前端删了那条 run（last_run_id 失效）：fall back 到 INSERT。

    这样信号列表不会被订阅刷新刷爆，订阅详情页 URL 始终不变。

    Returns:
        run_id（首次为新 ID，后续与 sub.last_run_id 相同）。
    """
    # Lazy import：subscription_service 自身在 worker / router 导入时不应拉起
    # signal_service 的全部依赖（pandas / numpy / 因子注册表等）。
    from backend.services.signal_service import run_signal

    body = subscription_to_signal_body(sub)
    now = datetime.now()
    items_json = json.dumps(body["factor_items"], ensure_ascii=False)
    sub_id = sub["subscription_id"]
    existing_run_id: str | None = sub.get("last_run_id")

    with mysql_conn() as c:
        with c.cursor() as cur:
            run_id_to_reuse: str | None = None
            if existing_run_id:
                # 检查 last_run_id 仍存在（用户可能手动删了那条 run）
                cur.execute(
                    "SELECT run_id FROM fr_signal_runs WHERE run_id=%s",
                    (existing_run_id,),
                )
                if cur.fetchone() is not None:
                    run_id_to_reuse = existing_run_id

            if run_id_to_reuse:
                # 复用：UPDATE 同一条 run，重置状态 + 同步最新订阅参数
                # （订阅可能改了 top_n / refresh_interval 等，run 字段需要同步）
                cur.execute(
                    """
                    UPDATE fr_signal_runs
                    SET factor_items_json=%s,
                        method=%s,
                        n_groups=%s,
                        ic_lookback_days=%s,
                        filter_price_limit=%s,
                        top_n=%s,
                        as_of_time=%s,
                        as_of_date=%s,
                        status='pending',
                        progress=0,
                        error_message=NULL,
                        started_at=NULL,
                        finished_at=NULL,
                        n_holdings_top=NULL,
                        n_holdings_bot=NULL,
                        payload_json=NULL
                    WHERE run_id=%s
                    """,
                    (
                        items_json,
                        body["method"],
                        body["n_groups"],
                        body["ic_lookback_days"],
                        1 if body["filter_price_limit"] else 0,
                        body["top_n"],
                        now,
                        now.date(),
                        run_id_to_reuse,
                    ),
                )
                run_id = run_id_to_reuse
            else:
                # 首次（或 last_run_id 失效）：INSERT 新 run
                run_id = uuid.uuid4().hex
                cur.execute(
                    """
                    INSERT INTO fr_signal_runs
                    (run_id, factor_items_json, method, pool_id, n_groups,
                     ic_lookback_days, as_of_time, as_of_date,
                     use_realtime, filter_price_limit, top_n,
                     subscription_id,
                     status, progress, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,'pending',0,%s)
                    """,
                    (
                        run_id,
                        items_json,
                        body["method"],
                        body["pool_id"],
                        body["n_groups"],
                        body["ic_lookback_days"],
                        now,
                        now.date(),
                        1 if body["filter_price_limit"] else 0,
                        body["top_n"],
                        sub_id,
                        now,
                    ),
                )
        c.commit()

    # service 期望 as_of_time 是 ISO 字符串（pickle 友好）
    body["as_of_time"] = now.isoformat()

    try:
        run_signal(run_id, body)
    except Exception:
        log.exception(
            "process_due_subscription: run_signal failed for sub=%s run=%s",
            sub_id, run_id,
        )

    # 失败也要 mark：避免下一 tick 立即重试雪崩。
    mark_refreshed(sub_id, run_id, now)
    return run_id


def subscription_to_signal_body(sub: dict) -> dict[str, Any]:
    """把订阅 dict 转成 ``signal_service.run_signal`` 的 body。

    use_realtime 永远 True（订阅就是实盘监控）；as_of_time 留空让 service 用 NOW()。
    """
    return {
        "factor_items": sub.get("factor_items", []),
        "method": sub.get("method", "equal"),
        "pool_id": int(sub["pool_id"]),
        "n_groups": int(sub.get("n_groups", 5)),
        "ic_lookback_days": int(sub.get("ic_lookback_days", 60)),
        "use_realtime": True,
        "filter_price_limit": bool(sub.get("filter_price_limit", 1)),
        "top_n": sub.get("top_n"),
    }
