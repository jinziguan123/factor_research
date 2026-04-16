"""因子注册表（FactorRegistry）：单例 + 扫描 + MySQL 元数据持久化 + 热加载支持。

职责边界：
- **发现**：``scan_and_register(root_pkg)`` 递归走包树、import 所有模块、
  筛出继承 ``BaseFactor`` 的类（跳过基类自身和 ``base`` 模块）。
- **持久化**：每个类的 ``inspect.getsource`` 做 SHA-1 得到 ``code_hash``，
  与 ``fr_factor_meta`` 比对；新增 → INSERT（version=1），变动 → UPDATE version+1。
- **查询**：``get(factor_id)`` 返回已注册类的**实例**；``list()`` 返回结构化
  元数据；``current_version(factor_id)`` 给 Task 6/7 的评估 run / factor_value
  写入时使用。
- **热加载接口**：``reload_module(name)`` 包 ``importlib.reload`` + 重新扫描，
  供 ``hot_reload.py`` 的 watchdog 回调使用。

关键约束：
- **严格单例**：整个进程只能有一个 registry 实例；``__new__`` 返回同一对象，
  ``__init__`` 通过 ``_initialized`` 标记防止重入覆写状态。
- **扫描幂等**：同一份代码重复扫描不应导致 version 递增；code_hash 相同即 skip。
- **MySQL 写入加锁**：watchdog 可能并发触发 ``scan_and_register``，``_persist_meta``
  在 ``threading.Lock`` 内序列化 SELECT → INSERT/UPDATE，避免 race。
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import pkgutil
import sys
import threading
from typing import Any

from backend.engine.base_factor import BaseFactor
from backend.storage.mysql_client import mysql_conn

logger = logging.getLogger(__name__)


class FactorRegistry:
    """单例因子注册表。"""

    _instance: "FactorRegistry | None" = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> "FactorRegistry":
        # 双检查锁：常规命中走 fast-path，仅首次构造时取锁。
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # 单例 ``__init__`` 会在每次 ``FactorRegistry()`` 调用都被触发；
        # ``_initialized`` 保护下面一次性的状态初始化，避免把已扫描结果清空。
        if getattr(self, "_initialized", False):
            return
        self._classes: dict[str, type[BaseFactor]] = {}
        self._code_hash: dict[str, str] = {}
        self._version: dict[str, int] = {}
        # 保护 ``_classes`` / ``_code_hash`` / ``_version`` + MySQL 写入。
        # 热加载场景下 watchdog 子线程会回调 scan_and_register，与主线程
        # 可能的 get / list 并发，统一用一把粗锁够用。
        self._lock = threading.Lock()
        self._initialized = True

    # ---------------------------- 公共 API ----------------------------

    def scan_and_register(
        self, root_pkg: str = "backend.factors"
    ) -> list[str]:
        """扫描 ``root_pkg`` 包下所有子模块，注册继承 ``BaseFactor`` 的类。

        Returns:
            被新增或 code_hash 变动的 factor_id 列表（顺序与扫描顺序一致）。
        """
        updated: list[str] = []
        root = importlib.import_module(root_pkg)
        # ``walk_packages`` 递归遍历（含子包）；filter 掉 ``.base`` 结尾的模块
        # 避免把 base.py re-export 的 BaseFactor 反向当作因子注册。
        for mod_info in pkgutil.walk_packages(
            root.__path__, prefix=f"{root_pkg}."
        ):
            mod_name = mod_info.name
            if mod_name.endswith(".base"):
                continue
            try:
                module = importlib.import_module(mod_name)
            except Exception:  # noqa: BLE001
                # 单个模块语法 / 导入错误不应阻断整个扫描；log + 跳过。
                logger.exception("导入因子模块失败：%s", mod_name)
                continue
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # 只认"在本模块里定义"的 BaseFactor 子类：
                # - 排除 re-export 的 BaseFactor 本体；
                # - 排除从 base.py 引入的基类被重复扫描。
                if obj is BaseFactor:
                    continue
                if not issubclass(obj, BaseFactor):
                    continue
                if obj.__module__ != module.__name__:
                    continue
                factor_id = getattr(obj, "factor_id", None)
                if not factor_id:
                    logger.warning(
                        "类 %s.%s 未设置 factor_id，跳过注册",
                        module.__name__,
                        name,
                    )
                    continue
                try:
                    src = inspect.getsource(obj)
                except OSError:
                    logger.exception(
                        "inspect.getsource 失败，无法计算 code_hash：%s.%s",
                        module.__name__,
                        name,
                    )
                    continue
                code_hash = hashlib.sha1(src.encode("utf-8")).hexdigest()
                changed = self._register_one(obj, code_hash)
                if changed:
                    updated.append(factor_id)
        return updated

    def get(self, factor_id: str) -> BaseFactor:
        """返回 factor_id 对应的**实例**（每次 new 一个，因子应无状态）。"""
        with self._lock:
            cls = self._classes.get(factor_id)
        if cls is None:
            raise KeyError(f"未注册的 factor_id: {factor_id!r}")
        return cls()

    def list(self) -> list[dict[str, Any]]:
        """列出所有已注册因子的结构化元数据（供前端 / API 展示）。"""
        with self._lock:
            out: list[dict[str, Any]] = []
            for factor_id, cls in self._classes.items():
                out.append(
                    {
                        "factor_id": factor_id,
                        "display_name": getattr(cls, "display_name", factor_id),
                        "category": getattr(cls, "category", "custom"),
                        "description": getattr(cls, "description", ""),
                        "params_schema": getattr(cls, "params_schema", {}),
                        "default_params": getattr(cls, "default_params", {}),
                        "supported_freqs": list(
                            getattr(cls, "supported_freqs", ("1d",))
                        ),
                        "version": self._version.get(factor_id, 1),
                        "code_hash": self._code_hash.get(factor_id, ""),
                    }
                )
            return out

    def current_version(self, factor_id: str) -> int:
        """返回 factor_id 当前的 version（未注册则抛 KeyError）。"""
        with self._lock:
            if factor_id not in self._version:
                raise KeyError(f"未注册的 factor_id: {factor_id!r}")
            return self._version[factor_id]

    def reload_module(self, module_name: str) -> list[str]:
        """热加载入口：``importlib.reload`` 指定模块后重新扫描。

        watchdog 的 on_any_event 会把变动文件映射到 module_name 传进来；
        这里用 ``reload`` 而非 ``import_module`` 以保证拿到**最新字节码**
        （否则 Python 会复用 ``sys.modules`` 缓存，热加载事实上失效）。

        设计说明：
        - 只对**已 import** 的模块调 reload（``sys.modules`` 里存在）；
          新文件未曾 import，直接交给随后的 ``scan_and_register`` 走
          ``import_module`` 首次加载即可。
        - reload 失败不阻塞后续扫描：有可能是 syntax error 等半成品状态，
          记录日志后仍然执行一次全量 scan，保证其他模块元数据正常更新。
        """
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
            except Exception:  # noqa: BLE001
                logger.exception(
                    "importlib.reload 失败：%s（将继续触发 scan）", module_name
                )
        else:
            logger.debug(
                "reload_module: %s 未在 sys.modules 中，跳过 reload 直接 scan",
                module_name,
            )
        return self.scan_and_register()

    # ---------------------------- 内部实现 ----------------------------

    def _register_one(
        self, cls: type[BaseFactor], code_hash: str
    ) -> bool:
        """把 ``cls`` 登记进内存表 + MySQL。

        Returns:
            True  —— 新增或 code_hash 变动；
            False —— 已存在且代码未变动（幂等 skip）。
        """
        factor_id: str = cls.factor_id
        with self._lock:
            prev_hash = self._code_hash.get(factor_id)
            if prev_hash == code_hash:
                # 代码未变：更新类引用（可能是 module reload 后得到的新对象，
                # 但源码 hash 一致），不 bump version，不写 DB。
                self._classes[factor_id] = cls
                return False
            # 新注册 / 代码已变。
            self._classes[factor_id] = cls
            self._code_hash[factor_id] = code_hash
            new_version = self._persist_meta(cls, code_hash)
            self._version[factor_id] = new_version
            return True

    def _persist_meta(
        self, cls: type[BaseFactor], code_hash: str
    ) -> int:
        """UPSERT ``fr_factor_meta``，返回当前（可能刚 +1 的）version。

        逻辑：
        - 表里不存在 → INSERT，version=1；
        - 存在且 code_hash 相同 → 保持 version 不变（但也更新其它元数据字段）；
        - 存在但 code_hash 变动 → version+1，update 所有字段。
        """
        factor_id: str = cls.factor_id
        display_name: str = getattr(cls, "display_name", factor_id)
        category: str = getattr(cls, "category", "custom")
        description: str = getattr(cls, "description", "") or ""
        params_schema = json.dumps(
            getattr(cls, "params_schema", {}), ensure_ascii=False
        )
        default_params = json.dumps(
            getattr(cls, "default_params", {}), ensure_ascii=False
        )
        supported_freqs = ",".join(getattr(cls, "supported_freqs", ("1d",)))

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT version, code_hash FROM fr_factor_meta WHERE factor_id = %s",
                    (factor_id,),
                )
                existing = cur.fetchone()
                if existing is None:
                    cur.execute(
                        "INSERT INTO fr_factor_meta "
                        "(factor_id, display_name, category, description, "
                        "params_schema, default_params, supported_freqs, "
                        "code_hash, version, is_active) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                        (
                            factor_id,
                            display_name,
                            category,
                            description,
                            params_schema,
                            default_params,
                            supported_freqs,
                            code_hash,
                            1,
                        ),
                    )
                    c.commit()
                    logger.info("注册新因子 %s (version=1)", factor_id)
                    return 1

                prev_version = int(existing["version"])
                prev_hash = existing["code_hash"]
                if prev_hash == code_hash:
                    # code_hash 未变：仍 UPDATE 其他元字段（例如 display_name
                    # 在不改代码的前提下……实际上类属性就是代码，所以 hash 一致
                    # 这些字段也一定一致）。保留 version 不变。
                    return prev_version
                new_version = prev_version + 1
                cur.execute(
                    "UPDATE fr_factor_meta SET "
                    "display_name=%s, category=%s, description=%s, "
                    "params_schema=%s, default_params=%s, supported_freqs=%s, "
                    "code_hash=%s, version=%s, is_active=1 "
                    "WHERE factor_id=%s",
                    (
                        display_name,
                        category,
                        description,
                        params_schema,
                        default_params,
                        supported_freqs,
                        code_hash,
                        new_version,
                        factor_id,
                    ),
                )
                c.commit()
                logger.info(
                    "更新因子 %s：version %d -> %d（code_hash 变动）",
                    factor_id,
                    prev_version,
                    new_version,
                )
                return new_version
