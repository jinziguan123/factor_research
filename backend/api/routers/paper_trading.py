"""模拟盘(纸上交易)API。

- ``POST /api/paper-accounts``：新建账户(绑定策略)。
- ``GET /api/paper-accounts``：账户列表。
- ``GET /api/paper-accounts/{id}``：详情(账户 + 持仓 + 净值时序 + 成交)。
- ``POST /api/paper-accounts/{id}/rebalance``：调仓一次(同步：跑 signal → 快照价撮合 → 落库)。
- ``DELETE /api/paper-accounts/{id}``：删除账户及其持仓/净值/成交。

注：rebalance 同步执行(内部跑一次 signal，加载因子可能数秒~数十秒)。MVP 单用户够用；
高并发场景应改为派发到 ProcessPool(参考 backtest/eval 的 BackgroundTasks 模式)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.schemas import CreatePaperAccountIn, ok
from backend.services import paper_trading_service as pts
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/paper-accounts", tags=["paper-trading"])


@router.post("")
def create_account(body: CreatePaperAccountIn) -> dict:
    account_id = pts.create_account(
        name=body.name, factor_items=body.factor_items, method=body.method,
        pool_id=body.pool_id, n_groups=body.n_groups, top_n=body.top_n,
        init_cash=body.init_cash,
    )
    return ok({"account_id": account_id, "status": "active"})


@router.get("")
def list_accounts() -> dict:
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT account_id, name, method, pool_id, init_cash, cash, "
                "status, created_at, last_rebalance_at "
                "FROM fr_paper_accounts ORDER BY created_at DESC LIMIT 200"
            )
            return ok(cur.fetchall())


@router.get("/{account_id}")
def get_detail(account_id: str) -> dict:
    try:
        return ok(pts.get_state(account_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{account_id}/rebalance")
def rebalance(account_id: str) -> dict:
    try:
        return ok(pts.rebalance(account_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"调仓失败：{e}") from e


@router.delete("/{account_id}")
def delete_account(account_id: str) -> dict:
    with mysql_conn() as c:
        with c.cursor() as cur:
            for tbl in (
                "fr_paper_trades", "fr_paper_nav",
                "fr_paper_positions", "fr_paper_accounts",
            ):
                cur.execute(f"DELETE FROM {tbl} WHERE account_id=%s", (account_id,))
        c.commit()
    return ok({"account_id": account_id, "deleted": True})
