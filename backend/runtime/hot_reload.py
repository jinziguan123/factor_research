"""基于 watchdog 的因子目录热加载。

设计要点：
- 只监听 ``.py`` 文件；目录事件 / 临时文件（如 ``.swp``）会被忽略；
- **Debounce 0.5 秒**：编辑器保存常触发 CREATE + MODIFY + MOVED 多个事件（原子 rename
  实现），短时间内多次触发会被合并成一次 ``scan_and_register``；
- **异常隔离**：回调里的任何异常都 ``log.exception`` 后吞掉，避免 watchdog 线程
  因单次扫描失败而永久退出；
- **返回 Observer 供调用方管理生命周期**：FastAPI 启动时 start、shutdown 时 stop。

当前模块**不负责**把扫描结果推送给其它进程（Task 8 的 ProcessPool 需要独立机制）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:  # pragma: no cover
    from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)


_DEBOUNCE_SECONDS = 0.5


class _Handler(FileSystemEventHandler):
    """接收文件系统事件，debounce 后触发 FactorRegistry 重扫描。"""

    def __init__(self) -> None:
        super().__init__()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:  # noqa: D401
        # 目录事件一律忽略（新增子目录、重命名目录等，真正有效的仍会是其下 .py 文件事件）。
        if event.is_directory:
            return
        src_path = getattr(event, "src_path", "")
        if not src_path.endswith(".py"):
            return
        # 编辑器有时会触发以 ``~`` / ``.swp`` 结尾的临时文件事件，上面 .py 过滤已足够。
        self._schedule_rescan()

    def _schedule_rescan(self) -> None:
        """Debounce：若已有定时器则取消并重置。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._do_rescan)
            self._timer.daemon = True
            self._timer.start()

    def _do_rescan(self) -> None:
        # 延后 import：避免 watchdog 线程启动阶段过早触达 DB / config。
        from backend.runtime.factor_registry import FactorRegistry

        try:
            updated = FactorRegistry().scan_and_register()
            if updated:
                logger.info("热加载扫描完成，变动 factor_id=%s", updated)
            else:
                logger.debug("热加载扫描完成，无变动")
        except Exception:  # noqa: BLE001
            logger.exception("热加载扫描失败")


def start_hot_reload(factors_dir: Path) -> "BaseObserver":
    """启动 watchdog Observer 并立刻返回。

    Args:
        factors_dir: ``backend/factors`` 的绝对路径。目录不存在会抛 ``FileNotFoundError``
            ——让调用方（FastAPI startup）立刻看到配置错误而不是静默无效。

    Returns:
        已 ``start()`` 的 Observer；调用方负责在 shutdown 时 ``stop() + join()``。
    """
    factors_dir = Path(factors_dir)
    if not factors_dir.exists():
        raise FileNotFoundError(f"factors_dir 不存在：{factors_dir}")
    observer: BaseObserver = Observer()
    observer.schedule(_Handler(), str(factors_dir), recursive=True)
    observer.start()
    logger.info("因子热加载 Observer 已启动：watching %s", factors_dir)
    return observer
