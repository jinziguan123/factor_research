"""兼容 shim：实现已搬到 ``backend.scripts.importers.qfq``。

保留本模块仅为向下兼容既有 ``from backend.scripts.import_qfq import run_import``
这类调用点（例如 ``backend.api.routers.admin`` 的 BackgroundTasks 回调、
``backend.scripts.run_init`` 潜在依赖、外部脚本）。待所有调用点迁到新路径后，
本文件可以整体删除。
"""
from __future__ import annotations

from backend.scripts.importers.qfq import run_import, main  # noqa: F401

__all__ = ["run_import", "main"]


if __name__ == "__main__":
    main()
