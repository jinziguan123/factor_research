"""实盘行情常驻 worker：盘中拉 spot 快照 + 盘后归档 1m K。

启动：
    python -m backend.workers.live_market                    # 默认配置（archive 关闭）
    python -m backend.workers.live_market --archive-1m       # 开启 1m K 归档
    python -m backend.workers.live_market --once             # 单次跑（调试用）
    python -m backend.workers.live_market --spot-interval 300

主循环结构（每 ~60s 醒一次）：
1. 查 ``is_trading_day(today)`` → 判定是否需要做事；
2. ``determine_phase(now, today_is_trading)`` 决定 idle / spot / eod_archive；
3. spot：每 ``spot_interval_sec``（默认 300s）拉一次全市场快照写库，失败仅 log
   不退出；
4. eod_archive：当日尚未归档过 + 配置开启 → 调用 ``archive_today``（多线程拉
   全 A 1m K 写库），无论成功与否都标记当日"已归档"避免重复触发；
5. idle：sleep 60s + 每 30 分钟 INFO 心跳。

关闭：
- KeyboardInterrupt（Ctrl+C / launchd SIGTERM）→ 干净退出；
- 其它异常被主循环 try/except 兜住，等 60s 继续。

依赖：
- akshare（盘中环境必装）；adapter 内部 lazy import，单测和未启用 worker 时无副作用。
- ClickHouse / MySQL：DAO / 日历查询要求两者可达。
"""
from __future__ import annotations

import argparse
import logging
import sys
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
    """worker 运行配置；可由 CLI 参数覆盖。"""

    # spot 拉取
    spot_enabled: bool = True
    spot_interval_sec: int = 300  # 5min；缩短会更接近实时但 IP 风险高
    # 1m K 归档（默认关闭，与设计文档一致）
    archive_1m_enabled: bool = False
    archive_max_workers: int = 20
    # 主循环节奏
    main_loop_sleep_sec: int = 60
    idle_heartbeat_sec: int = 1800  # idle 阶段 30min 一次心跳日志
    # CLI 选项
    once: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "LiveMarketConfig":
        return cls(
            spot_enabled=not args.no_spot,
            spot_interval_sec=args.spot_interval,
            archive_1m_enabled=args.archive_1m,
            archive_max_workers=args.archive_workers,
            once=args.once,
            log_level=args.log_level,
        )


# ---------------------------- 单次任务 ----------------------------


def run_spot_once() -> int:
    """拉一次 spot 快照并写库；返回写入行数。"""
    # Lazy import：未启用 spot 阶段时不付出 akshare / adapter 启动成本
    from backend.adapters.akshare_live import fetch_spot_snapshot
    from backend.storage.realtime_dao import write_spot_snapshot

    snapshot_at = datetime.now()
    df = fetch_spot_snapshot()
    return write_spot_snapshot(df, snapshot_at)


def run_archive_once(max_workers: int = 20) -> dict:
    """调归档脚本里的纯函数；返回统计 dict。"""
    from backend.scripts.archive_today_1m import archive_today

    return archive_today(pool_id=None, max_workers=max_workers)


# ---------------------------- 主循环 ----------------------------


def main_loop(cfg: LiveMarketConfig) -> int:
    """worker 主循环（被 CLI 入口调用）。

    返回退出码：0 = 正常 / SIGTERM；其它 = 致命错误。
    """
    from backend.workers.trading_calendar import determine_phase, is_trading_day

    last_spot_at: datetime | None = None
    archived_dates: set[date] = set()
    last_idle_heartbeat: datetime | None = None

    log.info("live_market worker started: %s", cfg)

    while True:
        try:
            now = datetime.now()
            today = now.date()
            today_is_trading = is_trading_day(today)
            phase = determine_phase(now, today_is_trading)

            if phase == "spot" and cfg.spot_enabled:
                # 控制 spot 拉取频率：到下一个 interval 边界才发请求
                should_fetch = (
                    last_spot_at is None
                    or (now - last_spot_at).total_seconds()
                    >= cfg.spot_interval_sec
                )
                if should_fetch:
                    try:
                        n = run_spot_once()
                        log.info("[spot] wrote %d rows at %s", n, now.strftime("%H:%M:%S"))
                        last_spot_at = now
                    except Exception:
                        log.exception(
                            "[spot] fetch/write failed; will retry next loop"
                        )

            elif phase == "eod_archive":
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

            else:
                # idle：每 30 分钟一次心跳日志，证明 worker 还活着
                if (
                    last_idle_heartbeat is None
                    or (now - last_idle_heartbeat).total_seconds()
                    >= cfg.idle_heartbeat_sec
                ):
                    log.info(
                        "[idle] phase=%s trading_day=%s now=%s",
                        phase,
                        today_is_trading,
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    last_idle_heartbeat = now

            # 清理跨日的 archived_dates 记录（保留最近 7 天即可）
            if len(archived_dates) > 30:
                cutoff = today - timedelta(days=7)
                archived_dates = {d for d in archived_dates if d >= cutoff}

            if cfg.once:
                log.info("--once 标志已置，单次执行后退出")
                return 0
            time_mod.sleep(cfg.main_loop_sleep_sec)
        except KeyboardInterrupt:
            log.info("收到 KeyboardInterrupt，正常退出")
            return 0
        except Exception:
            log.exception("主循环异常；sleep 60s 后继续")
            time_mod.sleep(60)


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
        "--spot-interval", type=int, default=300,
        help="spot 拉取间隔秒数；默认 300（5min），过短易触发 IP 限流",
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
