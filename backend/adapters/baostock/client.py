"""Baostock 登录 / 登出上下文。

baostock 的 API 约定：
- ``bs.login()`` / ``bs.logout()`` 必须成对；服务端按 session 维护连接；
- 登录失败时返回 ``ResultData``，``error_code != "0"`` 表示失败（例如网络不通）；
- 多次登录不会主动失败，但会占用多个 session 句柄，长期运行时会资源泄漏。

为简化调用，封装一个 contextmanager：

    with baostock_session():
        rs = bs.query_all_stock(day="2024-01-02")
        ...

非 0 错误码会抛 ``BaostockError``，让运维端直接看到失败原因而不是一堆空结果。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)


class BaostockError(RuntimeError):
    """Baostock 错误（登录失败 / 查询接口返回非 0 错误码）。"""


@contextmanager
def baostock_session() -> Iterator[None]:
    """进入时 ``bs.login()``，退出时 ``bs.logout()``。

    - 登录返回 ``error_code != "0"`` 直接抛 ``BaostockError``；
    - 退出时的 logout 失败只记 WARN，不遮盖主流程异常（``__exit__`` 里抛新异常会
      把用户代码的原始异常吞掉）。
    """
    import baostock as bs  # noqa: PLC0415 —— 按需 import，避免没装时 import backend 就炸

    lg = bs.login()
    if lg.error_code != "0":
        raise BaostockError(
            f"baostock login failed: code={lg.error_code} msg={lg.error_msg}"
        )
    log.info("baostock login ok")
    try:
        yield
    finally:
        try:
            out = bs.logout()
            if out.error_code != "0":
                log.warning(
                    "baostock logout non-zero: code=%s msg=%s",
                    out.error_code,
                    out.error_msg,
                )
        except Exception:  # noqa: BLE001
            log.exception("baostock logout raised; ignored to keep original error")


def check_rs(rs, ctx: str) -> None:
    """Baostock 查询接口返回的 ``ResultData`` 错误码兜底。

    约定：调用方拿到 ``rs`` 后立刻调 ``check_rs(rs, "query_all_stock")``，非 0 则抛；
    再迭代 ``rs.next() / rs.get_row_data()`` 取数据。
    """
    if rs.error_code != "0":
        raise BaostockError(
            f"baostock {ctx} failed: code={rs.error_code} msg={rs.error_msg}"
        )
