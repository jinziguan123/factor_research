"""股票代码搜索 / 批量匹配 API。

为股票池编辑器提供：
- 搜索补全：按代码或中文名做双端模糊匹配（``q`` 参数，50 条兜底）；
- 批量匹配：按 glob 风格规则返回**所有**符合的股票（``pattern`` 参数），
  用于"全量添加"、"全部深交所"、"60 开头"这类批量操作，上限 10000。

- 只读 ``stock_symbol``（timing_driven 维护的生产表），不做写入；
- stock_symbol 规模 ~5k 行，LIKE 全扫毫秒级，没必要上全文索引。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


# pattern 仅允许字母数字 + `.` `-` + glob 通配符 `*` `?`。
# 拒绝 SQL LIKE 的原生通配 `%` `_` / 反斜杠 / 其它特殊字符，防 LIKE 注入误伤。
_PATTERN_ALLOWED = re.compile(r"^[A-Za-z0-9.\-*?]*$")


def _glob_to_like(pattern: str) -> str:
    """把 glob 风格模式转成 SQL LIKE 模式。

    - ``*`` → ``%``（任意长度匹配）
    - ``?`` → ``_``（单字符匹配）
    - 其它字符原样（``_PATTERN_ALLOWED`` 已先拒绝掉 ``%`` / ``_`` / 反斜杠）
    """
    return pattern.replace("*", "%").replace("?", "_")


@router.get("")
def search_symbols(
    q: str = "",
    pattern: str = "",
    limit: int = 50,
) -> dict:
    """搜索或批量匹配股票。

    Args:
        q: 双端模糊搜索关键字（匹配 symbol 或 name），供下拉补全使用。
        pattern: glob 风格批量匹配（仅匹配 symbol），例：
            - ``*.SZ`` 全部深交所
            - ``*.SH`` 全部上交所
            - ``60*`` 沪市主板（60 开头）
            - ``000*.SZ`` 深市主板 A（000 开头）
            - ``300*`` 创业板
            - ``688*`` 科创板
            - ``*`` 全部
            使用 ``pattern`` 时 ``limit`` 默认并上限 10000；``q`` 模式上限 200。
        limit: 返回数量上限。``q`` 模式默认 50（搜索场景够用），``pattern``
            模式默认 10000（一次性拉齐匹配列表）。

    Returns:
        ``{code: 0, data: [{symbol, name}, ...]}``。
        ``q`` 和 ``pattern`` 都为空时返回前 limit 条（按代码升序）。
        两者同时传入时，``pattern`` 优先（批量操作语义更强）。
    """
    q = (q or "").strip()
    pattern = (pattern or "").strip()

    with mysql_conn() as c:
        with c.cursor() as cur:
            if pattern:
                # pattern 优先：批量场景，给 10000 上限兜底（A 股全市场 ~5k）。
                if not _PATTERN_ALLOWED.match(pattern):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "pattern 仅支持字母、数字、'.'、'-' 和通配符 '*' '?'；"
                            f"收到非法字符：{pattern!r}"
                        ),
                    )
                # limit 默认 50 时改成 10000（pattern 模式默认"拉齐全部匹配"）；
                # 用户显式传 limit 则按传入值夹紧到 [1, 10000]。
                raw_limit = int(limit) if limit and limit != 50 else 10000
                lim = max(1, min(raw_limit, 10000))
                like = _glob_to_like(pattern)
                cur.execute(
                    "SELECT symbol, name FROM stock_symbol "
                    "WHERE symbol LIKE %s "
                    "ORDER BY symbol LIMIT %s",
                    (like, lim),
                )
            elif q:
                # q 模式：双端通配 + 同时匹配 symbol 和 name，供搜索下拉使用。
                lim = max(1, min(int(limit), 200))
                like = f"%{q}%"
                cur.execute(
                    "SELECT symbol, name FROM stock_symbol "
                    "WHERE symbol LIKE %s OR name LIKE %s "
                    "ORDER BY symbol LIMIT %s",
                    (like, like, lim),
                )
            else:
                lim = max(1, min(int(limit), 200))
                cur.execute(
                    "SELECT symbol, name FROM stock_symbol "
                    "ORDER BY symbol LIMIT %s",
                    (lim,),
                )
            return ok(cur.fetchall())
