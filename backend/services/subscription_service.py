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

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from backend.storage.distributed_lock import acquire_mysql_lock
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


def compute_config_hash(body: dict) -> str:
    """订阅配置稳定 SHA-1 hash（40 字符），用于跨实例去重锁键。

    维度（与 ``find_matching_active_subscription`` 完全一致）：
    factor_items（顺序敏感）/ method / pool_id / n_groups / ic_lookback_days /
    filter_price_limit / top_n。

    refresh_interval_sec **不参与** hash——同配置不同间隔仍视为同订阅
    （间隔可以 PUT 修改而不重建）。
    """
    items = body.get("factor_items") or []
    # 序列化 factor_items：每项只取 factor_id + params（params 内 key 排序确保稳定）
    norm_items = []
    for it in items:
        if not isinstance(it, dict):
            norm_items.append({"factor_id": str(it), "params": None})
            continue
        norm_items.append(
            {
                "factor_id": it.get("factor_id"),
                "params": it.get("params") or None,
            }
        )
    items_str = json.dumps(norm_items, ensure_ascii=False, sort_keys=True)
    parts = (
        items_str,
        str(body.get("method", "equal")),
        str(int(body.get("pool_id", -1))),
        str(int(body.get("n_groups", 5))),
        str(int(body.get("ic_lookback_days", 60))),
        str(bool(body.get("filter_price_limit", True))),
        "null" if body.get("top_n") is None else str(int(body["top_n"])),
    )
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def create_subscription(body: dict) -> tuple[str, bool]:
    """创建订阅；跨实例去重。

    流程：
    1. 计算 ``config_hash``；
    2. 用 ``acquire_mysql_lock("sub_create:<hash>", timeout=5)`` 串行化跨实例
       的相同配置创建 — 两台设备同时 POST 时第二个会等到第一个完成；
    3. 锁内查 ``find_matching_active_subscription``：
       - 已存在 active 订阅 → 直接返回 ``(existing_id, True)``，不重新创建；
       - 不存在 → INSERT 新订阅。
    4. 返回 ``(subscription_id, reused)``。

    锁拿不到（5s 超时）走兜底：直接尝试 find_matching → 没有就 INSERT。
    极端竞态下可能产生 1 份重复（罕见，可接受）。

    Returns:
        ``(subscription_id, reused)``：reused=True 表示返回的是已有订阅。
    """
    config_hash = compute_config_hash(body)
    lock_name = f"sub_create:{config_hash[:16]}"  # MySQL 锁名 64 字符上限

    with acquire_mysql_lock(lock_name, timeout=5) as got_lock:
        if not got_lock:
            log.warning(
                "create_subscription 拿锁超时 (5s)；继续执行，极端情况下可能创建重复"
            )

        # 锁内（或超时后）：查现有
        existing = find_matching_active_subscription(body)
        if existing is not None:
            return existing["subscription_id"], True

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
        return subscription_id, False


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


def prepare_subscription_refresh(
    sub: dict, *, target_run_id: str | None = None,
) -> tuple[str, dict]:
    """同步：UPDATE 现有 run 或 INSERT 新 run（重置 pending）+ mark_refreshed。

    抽出来供"立即刷新"路由使用——HTTP 端点先同步走完这一步拿到 run_id，
    再把 run_signal 的耗时执行 enqueue 给 ProcessPool；前端立即跳转到详情
    页轮询状态。``process_due_subscription`` 复用本函数。

    复用语义（按优先级）：
    1. ``target_run_id`` 传入且存在 → UPDATE 它；mark_refreshed 把
       ``sub.last_run_id`` 改指它（前端"立即刷新"按钮专用：让用户在哪个
       run 详情页点的就刷新哪一个，URL 不会变）；
    2. 否则 ``sub.last_run_id`` 存在 → UPDATE 它（worker 路径 / 订阅列表
       页路径）；
    3. 都失败 → INSERT 新 run（首次刷新 / 历史 run 被删 fall back）。

    ``mark_refreshed`` 在本函数内立即执行（设 ``last_refresh_at = now`` +
    ``last_run_id``）——避免在异步 run_signal 还没跑完时 worker 主循环把同
    一订阅再判定为 due 重复触发。

    Args:
        sub: 完整 subscription dict（``get_subscription`` 返回的形态）。
        target_run_id: 可选；传入时**强制** UPDATE 这个 run（必须已存在）。
            前端"立即刷新"会传当前页 ``runId``。

    Returns:
        ``(run_id, body)``：body 已含 ISO 字符串 ``as_of_time``，可直接交给
        ``signal_service.run_signal`` / ``signal_entry``。
    """
    body = subscription_to_signal_body(sub)
    now = datetime.now()
    items_json = json.dumps(body["factor_items"], ensure_ascii=False)
    sub_id = sub["subscription_id"]
    existing_run_id: str | None = sub.get("last_run_id")

    with mysql_conn() as c:
        with c.cursor() as cur:
            run_id_to_reuse: str | None = None
            # 优先级 1：target_run_id（前端指定）—— 必须存在
            if target_run_id:
                cur.execute(
                    "SELECT run_id FROM fr_signal_runs WHERE run_id=%s",
                    (target_run_id,),
                )
                if cur.fetchone() is None:
                    raise ValueError(
                        f"target_run_id={target_run_id} 不存在；无法原地刷新"
                    )
                run_id_to_reuse = target_run_id
            # 优先级 2：sub.last_run_id（worker 路径）
            elif existing_run_id:
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

    # enqueue / 同步执行之前就 mark_refreshed —— 让 worker 主循环立即看到
    # last_refresh_at=now，本订阅不再被判为 due，防止重复触发。
    mark_refreshed(sub_id, run_id, now)
    return run_id, body


def process_due_subscription(sub: dict) -> str:
    """worker 用：同步 prepare + 同步 run_signal。

    **复用语义**：一个订阅永远只对应一条 fr_signal_runs 记录——
    - 首次刷新：INSERT 新 run，sub.last_run_id ← 新 run_id；
    - 后续刷新：UPDATE 同一条 run（重置为 pending + 清旧 payload + 同步最新订阅参数），
      run_signal 跑完会再次转成 success/failed；
    - 用户在前端删了那条 run（last_run_id 失效）：fall back 到 INSERT。

    Returns:
        run_id（首次为新 ID，后续与 sub.last_run_id 相同）。
    """
    # Lazy import：subscription_service 自身在 worker / router 导入时不应拉起
    # signal_service 的全部依赖（pandas / numpy / 因子注册表等）。
    from backend.services.signal_service import run_signal

    run_id, body = prepare_subscription_refresh(sub)

    try:
        run_signal(run_id, body)
    except Exception:
        log.exception(
            "process_due_subscription: run_signal failed for sub=%s run=%s",
            sub["subscription_id"], run_id,
        )
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
