"""基于 watchdog 的因子目录热加载。

设计要点：
- 只监听 ``.py`` 文件；目录事件 / 临时文件（如 ``.swp``）会被忽略；
- **路径 → 模块名映射**：watchdog 给的是绝对路径，需要转成 ``backend.factors.xxx.yyy``
  再交给 ``FactorRegistry().reload_module`` 真正 ``importlib.reload``。
  直接调 ``scan_and_register`` 不够——Python 会复用 ``sys.modules`` 缓存的旧字节码，
  修改因子源码后 ``inspect.getsource`` 拿到的仍是旧代码，热加载形同虚设。
- **Debounce 0.5 秒 + 按 module 聚合**：编辑器保存常触发 CREATE + MODIFY + MOVED 多个
  事件；0.5s 内同一模块的多次事件合并成一次 reload，跨模块的事件合并成一个窗口，
  窗口关闭时依次 reload 每个 pending module 再做一次全量 scan（兜底捕获新增文件）。
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
    """接收文件系统事件，debounce 后按 module 聚合触发 reload。"""

    def __init__(self, factors_dir: Path) -> None:
        super().__init__()
        # ``factors_dir`` 形如 ``.../backend/factors``。映射成 module 时需要
        # 包含 ``backend.factors.xxx`` 的完整路径，因此取 ``parent`` 作为 anchor
        # （即 ``.../backend``），然后 ``rel = src_path.relative_to(anchor)``。
        self._factors_dir = Path(factors_dir).resolve()
        self._anchor = self._factors_dir.parent
        self._timer: threading.Timer | None = None
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    # ---------------------------- 事件入口 ----------------------------

    def on_any_event(self, event: FileSystemEvent) -> None:  # noqa: D401
        # 目录事件一律忽略（新增子目录、重命名目录等，真正有效的仍会是其下 .py 文件事件）。
        if event.is_directory:
            return
        src_path = getattr(event, "src_path", "") or ""
        mod_name = self._path_to_module(src_path)
        if mod_name is None:
            # 非 .py / 不在 factors_dir 下 / 临时文件（.swp、.pyc 等）。
            return
        self._schedule_reload(mod_name)

    # ---------------------------- 内部工具 ----------------------------

    def _path_to_module(self, src_path: str) -> str | None:
        """把事件 ``src_path`` 映射到 ``backend.factors.xxx.yyy`` 形式的模块名。

        返回 ``None`` 表示不该触发 reload（非 .py、目录、不在 factors_dir 下、
        ``__init__.py`` 等）。
        """
        if not src_path:
            return None
        p = Path(src_path)
        if p.suffix != ".py":
            return None
        try:
            resolved = p.resolve()
        except OSError:
            # 文件已被删除 / 不可 stat；放弃映射。
            return None
        try:
            rel = resolved.relative_to(self._anchor)
        except ValueError:
            return None
        parts = list(rel.with_suffix("").parts)
        if not parts:
            return None
        # ``__init__.py`` 的模块名是它所在的包本身（丢掉最后的 ``__init__``）。
        if parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                return None
        return ".".join(parts)

    def _schedule_reload(self, mod_name: str) -> None:
        """Debounce：把 module 加入 pending 集合，重置 0.5s 定时器。"""
        with self._lock:
            self._pending.add(mod_name)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._do_reload)
            self._timer.daemon = True
            self._timer.start()

    def _do_reload(self) -> None:
        # 延后 import：避免 watchdog 线程启动阶段过早触达 DB / config。
        from backend.runtime.factor_registry import FactorRegistry

        with self._lock:
            to_reload = sorted(self._pending)
            self._pending.clear()
            self._timer = None

        try:
            registry = FactorRegistry()
            updated: list[str] = []
            for mod_name in to_reload:
                # reload_module 内部已经会调用 scan_and_register，这里收集
                # 每次返回的 updated 只为日志用；最后再兜底 scan 一次，捕获
                # 那些与 pending 模块无直接关系的变动（例如新增的 sibling 文件）。
                updated.extend(registry.reload_module(mod_name))
            final = registry.scan_and_register()
            updated.extend(final)
            # 去重保持顺序。
            seen: set[str] = set()
            uniq = [x for x in updated if not (x in seen or seen.add(x))]
            if uniq:
                logger.info(
                    "热加载完成，涉及模块=%s，变动 factor_id=%s",
                    to_reload,
                    uniq,
                )
            else:
                logger.debug(
                    "热加载完成，涉及模块=%s，无 factor_id 变动", to_reload
                )
        except Exception:  # noqa: BLE001
            logger.exception("热加载失败，涉及模块=%s", to_reload)


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
    observer.schedule(_Handler(factors_dir), str(factors_dir), recursive=True)
    observer.start()
    logger.info("因子热加载 Observer 已启动：watching %s", factors_dir)
    return observer
