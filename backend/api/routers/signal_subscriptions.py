"""实盘监控订阅 CRUD（fr_signal_subscriptions）。

端点：
- POST   /api/signal-subscriptions             创建（可选从 from_run_id 派生）
- GET    /api/signal-subscriptions             列表（active=1 过滤可选）
- GET    /api/signal-subscriptions/{id}        详情
- PUT    /api/signal-subscriptions/{id}        更新 is_active / refresh_interval_sec
- DELETE /api/signal-subscriptions/{id}        硬删

worker 不通过 HTTP 调本路由——它直接 import service 函数；本路由是给前端用的。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from backend.api.schemas import CreateSubscriptionIn, UpdateSubscriptionIn, ok
from backend.services import subscription_service
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/signal-subscriptions", tags=["signal-subscriptions"])


@router.post("")
def create_subscription(
    body: CreateSubscriptionIn,
    from_run_id: str | None = None,
) -> dict:
    """创建订阅。

    - ``from_run_id``（query 参数）非空时，会先校验该 run 存在；
      但订阅的实际 config 仍以 body 字段为准（前端调用前把 run 的 config 复制到 body）。
    - 不会立即触发 run；worker 主循环下次 tick 检测到 is_active=1 + last_refresh_at
      为 NULL，立即跑第一次。
    """
    if from_run_id:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT run_id FROM fr_signal_runs WHERE run_id=%s",
                    (from_run_id,),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"from_run_id={from_run_id} 不存在",
                    )

    # body.model_dump() 把 Pydantic 模型转成 dict；factor_items 内的
    # CompositionFactorItem 也会被递归转。
    body_dict = body.model_dump()

    # 去重：相同配置已有 active 订阅时直接复用，避免前端 5s 缓存窗口期内
    # 用户重复点"开启实盘监控"创建多份订阅 → 各自跑出多条 fr_signal_runs。
    existing = subscription_service.find_matching_active_subscription(body_dict)
    if existing is not None:
        return ok({
            "subscription_id": existing["subscription_id"],
            "is_active": True,
            "reused": True,
        })

    sub_id = subscription_service.create_subscription(body_dict)
    return ok({"subscription_id": sub_id, "is_active": True, "reused": False})


@router.get("")
def list_subscriptions(active: int | None = None) -> dict:
    """列出全部订阅。

    Args:
        active: 1=仅激活；0=仅暂停；None=全部。
    """
    only_active = active == 1
    rows = subscription_service.list_subscriptions(only_active=only_active)
    if active == 0:
        rows = [r for r in rows if not int(r.get("is_active", 0))]
    return ok(rows)


@router.get("/{subscription_id}")
def get_subscription(subscription_id: str) -> dict:
    sub = subscription_service.get_subscription(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="subscription not found")
    return ok(sub)


@router.put("/{subscription_id}")
def update_subscription(subscription_id: str, body: UpdateSubscriptionIn) -> dict:
    """切 is_active 或改 refresh_interval_sec。"""
    if body.is_active is None and body.refresh_interval_sec is None:
        raise HTTPException(status_code=400, detail="无可更新字段")

    sub = subscription_service.get_subscription(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="subscription not found")

    if body.is_active is not None:
        subscription_service.set_active(subscription_id, body.is_active)
    if body.refresh_interval_sec is not None:
        # 直接 UPDATE 单字段；service 没单独抽函数，避免方法爆炸。
        from datetime import datetime as _dt

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE fr_signal_subscriptions "
                    "SET refresh_interval_sec=%s, updated_at=%s "
                    "WHERE subscription_id=%s",
                    (body.refresh_interval_sec, _dt.now(), subscription_id),
                )
            c.commit()

    updated = subscription_service.get_subscription(subscription_id)
    return ok(updated)


@router.delete("/{subscription_id}")
def delete_subscription(subscription_id: str) -> dict:
    """硬删订阅；保留历史 fr_signal_runs。"""
    ok_ = subscription_service.delete_subscription(subscription_id)
    if not ok_:
        raise HTTPException(status_code=404, detail="subscription not found")
    return ok({"subscription_id": subscription_id, "deleted": True})
