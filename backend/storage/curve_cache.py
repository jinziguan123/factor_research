"""图形检索的前复权 close 序列缓存（Redis，可选）。

动机：``pattern_query`` / ``pattern_learn`` 的全池检索每次都要对几千只股票
``load_bars`` 拉全历史 + qfq 复权，这是秒级 I/O，占整个检索耗时的绝大部分；
而同一交易日内这些序列完全不变。把「每只股票的前复权 close + 日期」缓存到
Redis，检索时直接读，跳过 ClickHouse FINAL 查询与复权计算。

设计：
- key 带交易日 ``frpc:qclose:{date}:{symbol}``，TTL 26h，跨日自然过期，无需手动清理；
- 全池一次 ``mget`` 批量命中，miss 的批量回源 ``load_bars`` 再 ``pipeline`` 回写；
- Redis 不可用（未配置 / 连不上）时**静默降级**为直接回源，行为与无缓存时完全一致；
- 连接做 PID 检测：ProcessPool worker 子进程继承父进程的 client 会共用 socket，
  与 MySQL 连接池同理，借用前检测 pid 变化则重连（fork 安全）。
"""
from __future__ import annotations

import logging
import os
import pickle
from datetime import date

import numpy as np

from backend.config import settings

log = logging.getLogger(__name__)

_HISTORY_START = date(2005, 1, 1)

# 进程内 Redis client 单例 + 创建它的 pid（fork 检测用）。
_redis = None
_redis_pid: int | None = None


def _get_redis():
    """返回当前进程可用的 Redis client；不可用返回 None（触发降级）。

    pid 不匹配（fork 后的新进程）或首次调用时尝试建连；失败则记 None 不再重试。
    """
    global _redis, _redis_pid
    pid = os.getpid()
    if _redis_pid == pid:
        return _redis  # 本进程已尝试过：成功返回 client，失败返回 None
    _redis_pid = pid
    if not settings.redis_enabled:
        _redis = None
        return None
    try:
        import redis  # 延迟导入，未启用时不强依赖

        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_connect_timeout=2,
            socket_timeout=3,
        )
        client.ping()
        _redis = client
        log.info(
            "图形检索曲线缓存已连上 Redis %s:%s/db%s",
            settings.redis_host, settings.redis_port, settings.redis_db,
        )
    except Exception:  # noqa: BLE001 - 任何异常都降级，不阻断检索
        log.warning("Redis 不可用，图形检索降级为直接回源（不影响结果，仅慢）")
        _redis = None
    return _redis


def _key(symbol: str, day: str) -> str:
    return f"frpc:qclose:{day}:{symbol}"


def load_qfq_closes(data, symbols: list[str]) -> dict[str, dict]:
    """返回 ``{symbol: {"closes": np.ndarray(float64), "dates": [yyyy-mm-dd, ...]}}``。

    优先 Redis 命中；miss 的批量回源 ``load_bars`` 并回写。Redis 不可用则全部回源。
    返回值语义与原 ``load_bars`` 后逐股提取 close/dates 完全一致。
    """
    if not symbols:
        return {}

    today = date.today().isoformat()
    r = _get_redis()
    out: dict[str, dict] = {}
    miss: list[str] = list(symbols)

    if r is not None:
        try:
            cached = r.mget([_key(s, today) for s in symbols])
        except Exception:  # noqa: BLE001
            cached = [None] * len(symbols)
        miss = []
        for sym, raw in zip(symbols, cached):
            if raw:
                try:
                    out[sym] = pickle.loads(raw)
                    continue
                except Exception:  # noqa: BLE001 - 坏数据当 miss 处理
                    pass
            miss.append(sym)

    if miss:
        bars = data.load_bars(
            miss, _HISTORY_START, date.today(), freq="1d", adjust="qfq"
        )
        pipe = None
        if r is not None:
            try:
                pipe = r.pipeline(transaction=False)
            except Exception:  # noqa: BLE001
                pipe = None
        ttl = int(settings.pattern_cache_ttl_s)
        for sym, df in bars.items():
            close = df["close"].dropna()
            item = {
                "closes": close.to_numpy(dtype=float),
                "dates": [d.strftime("%Y-%m-%d") for d in close.index],
            }
            out[sym] = item
            if pipe is not None:
                try:
                    pipe.set(
                        _key(sym, today),
                        pickle.dumps(item, protocol=pickle.HIGHEST_PROTOCOL),
                        ex=ttl,
                    )
                except Exception:  # noqa: BLE001
                    pass
        if pipe is not None:
            try:
                pipe.execute()
            except Exception:  # noqa: BLE001 - 回写失败不影响本次结果
                pass

    return out
