"""模拟盘(纸上交易)服务。

定位：回测与实盘之间的桥——用真实(快照)价、有状态逐步推进、不真下单，验证
"信号 → 下单"链路 + 回测假设是否贴实盘。MVP：调仓时点用最新快照价撮合，不接
盘中逐笔(见设计讨论：日频选股不需要 tick 保真度，免费源也到顶)。

复用：
- ``signal_service.run_signal`` 出目标持仓(已含实时快照价 + 涨跌停/停牌过滤)；
- ``SimulatedBroker`` 撮合(A 股不对称费用 + 资金/持仓约束)；
- ``realtime_dao`` 取卖出侧(不在目标组的持仓)的快照价。

核心调仓算法 ``plan_rebalance`` 是纯函数，可单测；``rebalance`` 是落库编排。
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime
from typing import Any

from backend.execution_layer import OrderSide, SimulatedBroker
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


# ---------------------------- 调仓算法(纯函数)----------------------------


def plan_rebalance(
    cash: float,
    positions: dict[str, tuple[float, float]],
    prices: dict[str, float],
    target_symbols: list[str],
    *,
    commission_bps: float = 2.5,
    stamp_tax_bps: float = 5.0,
    transfer_fee_bps: float = 0.1,
    lot_size: int = 100,
) -> dict:
    """从当前 (cash, positions) 调仓到 target 等权组合，用 prices 撮合。

    Args:
        cash: 当前现金。
        positions: 当前持仓 ``{symbol: (qty, avg_price)}``。
        prices: 快照价 ``{symbol: price}``，需覆盖"持仓 ∪ 目标"。
        target_symbols: 目标持仓(signal 的 top 组)，等权分配。
        commission_bps/stamp_tax_bps/transfer_fee_bps: A 股费率(bp)。
        lot_size: 最小交易单位(A 股 100 股/手)。

    Returns:
        ``{new_cash, new_positions, fills, nav}``。被卖出/调整后留存现金；
        缺价的票跳过交易(保留原持仓)。
    """
    broker = SimulatedBroker(
        init_cash=cash, init_positions=positions,
        commission_bps=commission_bps, stamp_tax_bps=stamp_tax_bps,
        transfer_fee_bps=transfer_fee_bps, lot_size=lot_size,
    )
    # 用快照价 mark 当前组合净值；缺价用 avg_price 兜底，避免漏算总权益
    equity = cash + sum(
        q * (prices.get(s) or avg) for s, (q, avg) in positions.items()
    )
    target_set = set(target_symbols)

    # 1) 卖出不在目标组里的持仓
    for s, (q, _avg) in list(positions.items()):
        if s not in target_set and q > 0:
            px = prices.get(s)
            if px and px > 0:
                broker.submit_order(s, OrderSide.SELL, q, px)

    # 2) 目标等权：每只目标市值 = equity / N，折算成整手目标股数，调整到位
    if target_symbols:
        per = equity / len(target_symbols)
        for s in target_symbols:
            px = prices.get(s)
            if not px or px <= 0:
                continue
            held = broker.get_positions().get(s)
            cur_qty = held.qty if held else 0.0
            tgt_qty = math.floor(per / px / lot_size) * lot_size
            diff = tgt_qty - cur_qty
            if diff > 0:
                broker.submit_order(s, OrderSide.BUY, diff, px)
            elif diff < 0:
                broker.submit_order(s, OrderSide.SELL, -diff, px)

    new_positions = {
        s: (p.qty, p.avg_price) for s, p in broker.get_positions().items()
    }
    new_cash = broker.get_account().cash
    nav = new_cash + sum(
        q * (prices.get(s) or avg) for s, (q, avg) in new_positions.items()
    )
    return {
        "new_cash": new_cash,
        "new_positions": new_positions,
        "fills": broker.get_fills(),
        "nav": nav,
    }


# ---------------------------- DB 读写 ----------------------------


def create_account(
    name: str, factor_items: list[dict], method: str, pool_id: int,
    n_groups: int = 5, top_n: int | None = None, init_cash: float = 1e6,
) -> str:
    """新建一个模拟盘账户，返回 account_id。"""
    account_id = uuid.uuid4().hex
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_paper_accounts
                (account_id, name, factor_items_json, method, pool_id, n_groups,
                 top_n, init_cash, cash, status, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)
                """,
                (
                    account_id, name, json.dumps(factor_items, ensure_ascii=False),
                    method, pool_id, n_groups, top_n, init_cash, init_cash,
                    datetime.now(),
                ),
            )
        c.commit()
    return account_id


def _load_account(account_id: str) -> dict | None:
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_paper_accounts WHERE account_id=%s", (account_id,)
            )
            return cur.fetchone()


def _load_positions(account_id: str) -> dict[str, tuple[float, float]]:
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT symbol, qty, avg_price FROM fr_paper_positions "
                "WHERE account_id=%s AND qty > 0",
                (account_id,),
            )
            return {
                r["symbol"]: (float(r["qty"]), float(r["avg_price"]))
                for r in cur.fetchall()
            }


def _save_account_state(
    account_id: str, cash: float, positions: dict[str, tuple[float, float]]
) -> None:
    """覆盖式保存账户现金 + 持仓(先清空持仓再写当前非零仓)。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_paper_accounts SET cash=%s, last_rebalance_at=%s "
                "WHERE account_id=%s",
                (cash, datetime.now(), account_id),
            )
            cur.execute(
                "DELETE FROM fr_paper_positions WHERE account_id=%s", (account_id,)
            )
            for s, (q, avg) in positions.items():
                if q > 0:
                    cur.execute(
                        "INSERT INTO fr_paper_positions "
                        "(account_id, symbol, qty, avg_price) VALUES (%s,%s,%s,%s)",
                        (account_id, s, q, avg),
                    )
        c.commit()


def _record_trades(account_id: str, fills: list) -> None:
    if not fills:
        return
    ts = datetime.now()
    with mysql_conn() as c:
        with c.cursor() as cur:
            for f in fills:
                cur.execute(
                    "INSERT INTO fr_paper_trades "
                    "(account_id, ts, symbol, side, qty, price, fee) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        account_id, ts, f.symbol,
                        f.side.value if hasattr(f.side, "value") else str(f.side),
                        f.qty, f.price, f.fee,
                    ),
                )
        c.commit()


def _record_nav(account_id: str, nav: float, cash: float) -> None:
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO fr_paper_nav (account_id, ts, nav, cash, market_value) "
                "VALUES (%s,%s,%s,%s,%s)",
                (account_id, datetime.now(), nav, cash, nav - cash),
            )
        c.commit()


def _run_signal_top(acct: dict) -> tuple[list[str], dict[str, float]]:
    """按账户策略建一个 signal run、同步跑 run_signal、读 payload.top。

    返回 (目标 symbols, {symbol: last_price})——只保留有快照价的票。
    """
    from backend.services.signal_service import run_signal

    sid = uuid.uuid4().hex
    factor_items = json.loads(acct["factor_items_json"])
    body = {
        "factor_items": factor_items,
        "method": acct["method"],
        "pool_id": acct["pool_id"],
        "n_groups": acct["n_groups"],
        "top_n": acct.get("top_n"),
        "use_realtime": True,
        "filter_price_limit": True,
    }
    now = datetime.now()
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_signal_runs
                (run_id, factor_items_json, method, pool_id, n_groups,
                 ic_lookback_days, as_of_time, as_of_date, use_realtime,
                 filter_price_limit, status, progress, created_at, top_n)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,1,'pending',0,%s,%s)
                """,
                (
                    sid, json.dumps(factor_items, ensure_ascii=False),
                    acct["method"], acct["pool_id"], acct["n_groups"], 60,
                    now, now.date(), now, acct.get("top_n"),
                ),
            )
        c.commit()

    run_signal(sid, body)  # 同步执行，写 payload

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM fr_signal_runs WHERE run_id=%s", (sid,)
            )
            row = cur.fetchone()
    payload = json.loads(row["payload_json"]) if row and row.get("payload_json") else {}
    top = payload.get("top") or []
    symbols = [t["symbol"] for t in top if t.get("last_price")]
    prices = {
        t["symbol"]: float(t["last_price"]) for t in top if t.get("last_price")
    }
    return symbols, prices


def _spot_prices(symbols: list[str]) -> dict[str, float]:
    """查给定 symbol 的最新快照价(给卖出侧——不在目标组的持仓——补价)。"""
    if not symbols:
        return {}
    try:
        from backend.storage import realtime_dao
        df = realtime_dao.latest_spot_snapshot(symbols, datetime.now().date())
        if df is None or df.empty:
            return {}
        return {
            r["symbol"]: float(r["last_price"])
            for _, r in df.iterrows()
            if r.get("last_price")
        }
    except Exception:  # noqa: BLE001
        log.warning("查快照价失败，卖出侧缺价的持仓本次不动")
        return {}


# ---------------------------- 编排 ----------------------------


def rebalance(account_id: str) -> dict:
    """对一个模拟盘账户调仓一次：signal 取目标 → 查快照价 → plan_rebalance → 落库。"""
    acct = _load_account(account_id)
    if acct is None:
        raise ValueError(f"模拟盘账户 {account_id} 不存在")

    positions = _load_positions(account_id)
    target_symbols, prices = _run_signal_top(acct)
    # 卖出侧(持仓里不在目标组的)需要快照价——signal 只给了目标组的价，这里补齐
    missing = [s for s in positions if s not in prices]
    if missing:
        prices.update(_spot_prices(missing))

    plan = plan_rebalance(
        float(acct["cash"]), positions, prices, target_symbols,
    )
    _save_account_state(account_id, plan["new_cash"], plan["new_positions"])
    _record_trades(account_id, plan["fills"])
    _record_nav(account_id, plan["nav"], plan["new_cash"])
    return {
        "nav": plan["nav"],
        "cash": plan["new_cash"],
        "n_positions": len(plan["new_positions"]),
        "n_trades": len(plan["fills"]),
        "n_targets": len(target_symbols),
    }


def get_state(account_id: str) -> dict:
    """读账户快照 + 持仓 + 净值时序 + 最近成交，供详情页展示。"""
    acct = _load_account(account_id)
    if acct is None:
        raise ValueError(f"模拟盘账户 {account_id} 不存在")
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT symbol, qty, avg_price FROM fr_paper_positions "
                "WHERE account_id=%s AND qty > 0 ORDER BY symbol",
                (account_id,),
            )
            positions = cur.fetchall()
            cur.execute(
                "SELECT ts, nav, cash, market_value FROM fr_paper_nav "
                "WHERE account_id=%s ORDER BY ts",
                (account_id,),
            )
            nav_series = cur.fetchall()
            cur.execute(
                "SELECT ts, symbol, side, qty, price, fee FROM fr_paper_trades "
                "WHERE account_id=%s ORDER BY ts DESC, id DESC LIMIT 200",
                (account_id,),
            )
            trades = cur.fetchall()
    if "factor_items_json" in acct:
        acct["factor_items"] = json.loads(acct["factor_items_json"])
        acct.pop("factor_items_json", None)
    return {
        "account": acct,
        "positions": positions,
        "nav_series": nav_series,
        "trades": trades,
    }
