"""实盘行情常驻 worker：订阅驱动拉 spot 快照 + 盘后归档 1m K。

【v2 升级语义】 — 从"无脑 5min 拉全市场"改为"订阅驱动按需拉"：

主循环每 ~60s 醒一次，在 spot 时段：
1. 查 fr_signal_subscriptions 中 is_active=1 且到期需刷新的订阅；
2. **没有 due 订阅 → 不拉 spot（节省 IP 配额 / 服务器开销）**；
3. 有 due 订阅 → ``ensure_spot_fresh()``（spot age > 60s 时拉一次新快照），
   然后对每个 due 订阅调 ``process_due_subscription()`` 触发 run_signal。

用户在前端开启 / 关闭某个订阅 → 主循环下次 tick 自然响应（无需重启 worker）。

启动方式：
1. 嵌入 FastAPI 主进程（推荐）：app lifespan 调 ``start_in_thread()``，shutdown
   时 ``stop_event.set()``，worker 与主进程同生共死。
2. 独立进程（备用）：``python -m backend.workers.live_market``。

CLI 选项（独立模式）：
    --once             单次跑（调试用）
    --archive-1m       启用盘后 15:00-15:30 自动 1m K 归档（默认关闭）
    --spot-stale-sec N spot 数据"陈旧"阈值秒数（默认 60，<= 此值直接复用旧 spot）

关闭：
- KeyboardInterrupt（Ctrl+C / launchd SIGTERM）→ 干净退出；
- stop_event.set()（嵌入模式 lifespan）→ 干净退出；
- 其它异常被主循环 try/except 兜住，等 60s 继续。
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time as time_mod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# 让 `python -m backend.workers.live_market` 能找到项目根
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

log = logging.getLogger(__name__)


@dataclass
class LiveMarketConfig:
    """worker 运行配置；可由 CLI 参数 / 嵌入模式构造覆盖。"""

    # spot 拉取（按订阅驱动；spot_stale_sec 决定何时拉新快照）
    spot_enabled: bool = True
    spot_stale_sec: int = 60  # 库里最新 spot 距 NOW > 此值才拉新；省 HTTP 调用
    # 1m K 归档（默认关闭，与设计文档一致）
    archive_1m_enabled: bool = False
    archive_max_workers: int = 20
    # 主循环节奏
    main_loop_sleep_sec: int = 60
    idle_heartbeat_sec: int = 1800  # idle 阶段 30min 一次心跳日志
    # CLI 选项（独立模式专用）
    once: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "LiveMarketConfig":
        return cls(
            spot_enabled=not args.no_spot,
            spot_stale_sec=args.spot_stale_sec,
            archive_1m_enabled=args.archive_1m,
            archive_max_workers=args.archive_workers,
            once=args.once,
            log_level=args.log_level,
        )


# ---------------------------- 单次任务 ----------------------------


def run_spot_once(max_retries: int = 2, retry_sleep_sec: float = 1.0) -> int:
    """拉一次 spot 快照并写库；返回写入行数。

    akshare 后端常见 ``ConnectionAborted`` / ``ReadTimeout``，单次失败不应
    立即降级——这里做最多 ``max_retries`` 次轻量重试（默认 1 + 2 = 共 3 次
    尝试），每次间隔 ``retry_sleep_sec`` 秒。所有重试都失败才把异常抛给
    上层（``ensure_spot_fresh`` / ``signal_service`` 会兜住降级到昨日 close）。
    """
    # Lazy import：未启用 spot 阶段时不付出 akshare / adapter 启动成本
    from backend.adapters.akshare_live import fetch_spot_snapshot
    from backend.storage.realtime_dao import write_spot_snapshot

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            snapshot_at = datetime.now()
            df = fetch_spot_snapshot()
            return write_spot_snapshot(df, snapshot_at)
        except Exception as e:  # noqa: BLE001 - akshare 抛各种网络错
            last_exc = e
            if attempt < max_retries:
                log.warning(
                    "fetch_spot_snapshot 第 %d 次失败 (%s)；%.1fs 后重试",
                    attempt + 1, e, retry_sleep_sec,
                )
                time_mod.sleep(retry_sleep_sec)
            else:
                log.warning(
                    "fetch_spot_snapshot 重试 %d 次全部失败；放弃，让上层降级",
                    max_retries + 1,
                )
    assert last_exc is not None
    raise last_exc


def run_archive_once(max_workers: int = 20) -> dict:
    """调归档脚本里的纯函数；返回统计 dict。"""
    from backend.scripts.archive_today_1m import archive_today

    return archive_today(pool_id=None, max_workers=max_workers)


def ensure_spot_fresh(stale_sec: int = 60) -> bool:
    """确保 stock_spot_realtime 中最新 spot age <= stale_sec；陈旧 / 缺失则拉新。

    Returns:
        True = 调用了 run_spot_once（拉了新数据）；False = 旧数据仍新鲜，复用。
    """
    from backend.storage.realtime_dao import latest_spot_age_sec

    age = latest_spot_age_sec()
    if age is not None and age <= stale_sec:
        log.debug("spot age=%.1fs <= %ds; reuse existing", age, stale_sec)
        return False
    log.info("spot age=%s; fetching fresh snapshot", age)
    run_spot_once()
    return True


def process_due_subscriptions_once() -> int:
    """对当前所有 due 订阅各跑一次 signal 计算；返回处理订阅数。

    抽出为顶层函数便于测试 / 复用。worker 主循环和"立即触发"按钮（如有）都走它。
    """
    # Lazy import：避免在加载本模块时拉起 service / DAO 全部依赖
    from backend.services import subscription_service

    now = datetime.now()
    due = subscription_service.find_due_subscriptions(now)
    if not due:
        return 0
    log.info("[sub] %d due subscriptions to process", len(due))
    for sub in due:
        sid = sub["subscription_id"]
        try:
            run_id = subscription_service.process_due_subscription(sub)
            log.info("[sub] %s refreshed → run_id=%s", sid[:8], run_id[:8])
        except Exception:
            log.exception("[sub] failed sub=%s", sid)
    return len(due)


# ---------------------------- 主循环 ----------------------------


def main_loop(
    cfg: LiveMarketConfig, stop_event: threading.Event | None = None,
) -> int:
    """worker 主循环。

    Args:
        cfg: 运行配置。
        stop_event: 嵌入模式下由 lifespan 传入；set() 时主循环退出。
            CLI 独立模式 None，依赖 KeyboardInterrupt 退出。

    Returns:
        退出码：0 = 正常退出 / SIGTERM / stop_event.set()；其它 = 致命错误。
    """
    from backend.workers.trading_calendar import determine_phase, is_trading_day

    archived_dates: set[date] = set()
    last_idle_heartbeat: datetime | None = None

    log.info("live_market worker started: %s", cfg)

    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("stop_event set，正常退出")
            return 0
        try:
            now = datetime.now()
            today = now.date()
            today_is_trading = is_trading_day(today)
            phase = determine_phase(now, today_is_trading)

            # ---- 跨实例 leader 选举：多台后端共享同一 MySQL 时只让一个跑 ----
            # GET_LOCK 立即返回（timeout=0）：拿到锁 = 我是本轮 leader；
            # 拿不到 = 其它实例已是 leader，本实例本轮 follower（不动订阅 / archive）。
            # leader 实例崩溃时 MySQL 会话断开自动释放锁，下个 tick 任意 follower
            # 抢到即接管，无需手动切换。
            from backend.storage.distributed_lock import acquire_mysql_lock

            is_leader = False
            due_subs: list = []
            with acquire_mysql_lock("live_market_leader", timeout=0) as got_lock:
                is_leader = got_lock
                if not is_leader:
                    log.debug("worker is follower; leader 在另一实例")
                else:
                    # ---- 订阅刷新（仅 leader 跑）----
                    # 任何时刻都跑（盘外 service 自动降级用昨日 close）。
                    if cfg.spot_enabled:
                        from backend.services import subscription_service

                        due_subs = subscription_service.find_due_subscriptions(now)

                    if due_subs:
                        log.info(
                            "[sub] %d due subscriptions (phase=%s, trading=%s, leader=True)",
                            len(due_subs), phase, today_is_trading,
                        )
                        if phase == "spot":
                            try:
                                ensure_spot_fresh(cfg.spot_stale_sec)
                            except Exception:
                                log.exception(
                                    "[spot] ensure_spot_fresh failed; subscriptions "
                                    "will downgrade to yesterday close"
                                )
                        else:
                            log.info(
                                "[sub] phase=%s (offline) — service will downgrade to yesterday close",
                                phase,
                            )
                        from backend.services import subscription_service

                        for sub in due_subs:
                            sid = sub["subscription_id"]
                            try:
                                rid = subscription_service.process_due_subscription(sub)
                                log.info(
                                    "[sub] %s refreshed → run_id=%s", sid[:8], rid[:8],
                                )
                            except Exception:
                                log.exception("[sub] failed sub=%s", sid)

                    # ---- 盘后归档（仅 leader 跑；多实例同跑会让 akshare 流量翻倍）----
                    if phase == "eod_archive":
                        if not cfg.archive_1m_enabled:
                            log.debug("[eod_archive] phase reached but archive_1m_enabled=False")
                        elif today in archived_dates:
                            log.debug("[eod_archive] %s already archived, skip", today)
                        else:
                            log.info("[eod_archive] starting 1m K archive for %s", today)
                            try:
                                stats = run_archive_once(cfg.archive_max_workers)
                                log.info(
                                    "[eod_archive] done: symbols=%d bars=%d errors=%d",
                                    stats["n_symbols"],
                                    stats["n_bars_written"],
                                    stats["n_errors"],
                                )
                            except Exception:
                                log.exception(
                                    "[eod_archive] failed for %s; marking done to avoid retry",
                                    today,
                                )
                            archived_dates.add(today)

            # ---- 心跳：任何实例都打（含 follower），证明自己活着 ----
            if not due_subs and phase != "eod_archive":
                if (
                    last_idle_heartbeat is None
                    or (now - last_idle_heartbeat).total_seconds()
                    >= cfg.idle_heartbeat_sec
                ):
                    log.info(
                        "[idle] phase=%s trading_day=%s leader=%s now=%s",
                        phase, today_is_trading, is_leader,
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    last_idle_heartbeat = now

            # 清理跨日 archived_dates（保留最近 7 天）
            if len(archived_dates) > 30:
                cutoff = today - timedelta(days=7)
                archived_dates = {d for d in archived_dates if d >= cutoff}

            if cfg.once:
                log.info("--once 标志已置，单次执行后退出")
                return 0
            # 用可中断的分段 sleep 替代 time.sleep(60)，让 stop_event 能在 1s 内响应
            for _ in range(cfg.main_loop_sleep_sec):
                if stop_event is not None and stop_event.is_set():
                    return 0
                time_mod.sleep(1)
        except KeyboardInterrupt:
            log.info("收到 KeyboardInterrupt，正常退出")
            return 0
        except Exception:
            log.exception("主循环异常；sleep 60s 后继续")
            time_mod.sleep(60)


# ---------------------------- 嵌入模式入口 ----------------------------


def start_in_thread(
    cfg: LiveMarketConfig | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """在 daemon thread 中启动 worker；返回 (Thread, stop_event)。

    供 FastAPI lifespan 使用：
        thread, stop_event = start_in_thread()
        # ... yield（应用运行中）
        stop_event.set()
        thread.join(timeout=10)

    daemon=True 保证主进程退出时 worker thread 也会被强制终止（兜底，正常路径
    应通过 stop_event 干净退出）。
    """
    if cfg is None:
        cfg = LiveMarketConfig()
    stop_event = threading.Event()
    t = threading.Thread(
        target=main_loop,
        args=(cfg, stop_event),
        daemon=True,
        name="live-market-worker",
    )
    t.start()
    return t, stop_event


# ---------------------------- CLI ----------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="实盘行情常驻 worker：盘中 spot + 盘后 1m K 归档"
    )
    p.add_argument(
        "--no-spot", action="store_true",
        help="禁用 spot 拉取（debug 用，正常勿启用）",
    )
    p.add_argument(
        "--spot-stale-sec", type=int, default=60,
        help="spot 数据陈旧阈值秒数；库内最新 spot age <= 此值时复用，默认 60s",
    )
    p.add_argument(
        "--archive-1m", action="store_true",
        help="启用盘后 15:00-15:30 自动归档 1m K（默认关闭）",
    )
    p.add_argument(
        "--archive-workers", type=int, default=20,
        help="归档时的 ThreadPoolExecutor 并发数（默认 20）",
    )
    p.add_argument(
        "--once", action="store_true",
        help="单次执行后退出（调试用，跳过 sleep / 主循环）",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = LiveMarketConfig.from_args(args)
    return main_loop(cfg)


if __name__ == "__main__":
    sys.exit(main())
