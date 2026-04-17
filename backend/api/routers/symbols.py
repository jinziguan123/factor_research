"""股票代码搜索 API。

为股票池编辑器提供按代码 / 名称模糊搜索的能力，避免用户手打股票代码。

- 只读 ``stock_symbol``（timing_driven 维护的生产表），不做写入；
- LIKE 匹配，双端通配符：前端一个字符就能出候选，后端用 LIMIT 兜底避免全表传输。
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.schemas import ok
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


@router.get("")
def search_symbols(q: str = "", limit: int = 50) -> dict:
    """按代码或中文名模糊搜索股票。

    Args:
        q: 查询关键字。空字符串返回前 ``limit`` 条（按代码升序）。
        limit: 返回数量上限，受 [1, 200] 夹紧。前端下拉一次展示 ~50 条足够。

    Returns:
        ``{code: 0, data: [{symbol, name}, ...]}``。
    """
    limit = max(1, min(int(limit), 200))
    q = (q or "").strip()
    with mysql_conn() as c:
        with c.cursor() as cur:
            if q:
                # 同时匹配 symbol 和 name；双端通配符。
                # stock_symbol 规模 ~5k 行，LIKE 全扫也在毫秒级，没必要上全文索引。
                like = f"%{q}%"
                cur.execute(
                    "SELECT symbol, name FROM stock_symbol "
                    "WHERE symbol LIKE %s OR name LIKE %s "
                    "ORDER BY symbol LIMIT %s",
                    (like, like, limit),
                )
            else:
                cur.execute(
                    "SELECT symbol, name FROM stock_symbol "
                    "ORDER BY symbol LIMIT %s",
                    (limit,),
                )
            return ok(cur.fetchall())
